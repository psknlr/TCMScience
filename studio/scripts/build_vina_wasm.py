#!/usr/bin/env python3
"""AutoDock Vina 1.2.7 compiled to WebAssembly, for the browser's docking jobs (studio/web/js/scientific/dock).

The native docking pipeline (bioagent.docking) calls Vina 1.2.7 through its Python package. The browser runs the same
C++ library, built here from pinned inputs, each checked against its SHA-256 before it is used:

  Emscripten 4.0.23   the release binaries (LLVM, Binaryen, emscripten) from Google's emscripten-releases bucket
  Vina 1.2.7          the source distribution on PyPI (src/lib: the library the Python package wraps)
  Boost 1.83.0        the headers of Ubuntu 24.04's libboost1.83-dev (Vina uses header-only Boost for its maths,
                      random numbers, containers and serialisation declarations)

Vina's library also uses four compiled Boost libraries, all for system services, none for its science: threads (its
parallel_for runs the Monte Carlo tasks on worker threads), filesystem (paths and file streams), log (included, not
used) and timer (a progress display). This build has no threads, so studio/vina-wasm/ replaces those pieces and
nothing else:

  parallel.h          replaces src/lib/parallel.h: every task index runs on the calling thread, in index order. Vina
                      seeds each Monte Carlo task from the master generator before the run and merges the task
                      outputs in task order after it, so the result does not depend on how tasks are scheduled
                      (natively, cpu=1 and all cores give identical poses).
  shim/boost/...      boost::mutex without locking (one thread), hardware_concurrency 1, Boost.Filesystem as
                      std::filesystem (Emscripten's in-memory file system), empty Boost.Log headers.
  vina_wasm.cpp       the JavaScript binding (embind): Vina's own class API — set_receptor, set_ligand_from_string,
                      compute_vina_maps, then score(), optimize() or global_search() + get_poses() — as the Python
                      package calls it, with cpu 1 and an explicit seed.

Every other source file is Vina's, byte for byte (each file's SHA-256 is recorded). Written:

  web/js/scientific/dock/vendor/vina-1.2.7.mjs    the Emscripten loader (ES module; the page instantiates the module
                                                  from bytes it has checked against the pin)
  web/js/scientific/dock/vendor/vina-1.2.7.wasm   the module
  web/js/scientific/dock/vendor/LICENSES-vina.txt Vina's Apache-2.0 licence, Boost's (studio/vina-wasm/LICENSE-Boost-1.0.txt,
                                                  SPDX's text), Emscripten's runtime notice
  web/js/scientific/dock/vina_build.json          the pins, flags, inputs' and outputs' SHA-256 and sizes

The build is reproducible: --check rebuilds from the same pins and fails when any written file differs by a byte.
Needs dpkg-deb (to unpack the Boost .deb), node, and network access to the three hosts above unless --cache already
holds the inputs.

    python studio/scripts/build_vina_wasm.py                 # build and write the four files
    python studio/scripts/build_vina_wasm.py --check         # rebuild and compare with the committed files
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[1]
PORT = STUDIO / "vina-wasm"
OUT = STUDIO / "web" / "js" / "scientific" / "dock"
VENDOR = OUT / "vendor"
MANIFEST = OUT / "vina_build.json"
NAME = "vina-1.2.7"

EMSCRIPTEN = {
    "version": "4.0.23",
    "release": "aaa43392544d695232b70eda706d751f18980c2a",
    "url": "https://storage.googleapis.com/webassembly/emscripten-releases-builds/linux/"
           "aaa43392544d695232b70eda706d751f18980c2a/wasm-binaries.tar.xz",
    "sha256": "5f1565fe45a1223cedf3b0300f5089c2c64954d2895b2aaedc85043c719be965",
}
VINA = {
    "version": "1.2.7",
    "url": "https://files.pythonhosted.org/packages/d2/2a/6746ef5e57b1c643e9fb24ad9e4fa520add7338736d50954e0fbc12ae52e/"
           "vina-1.2.7.tar.gz",
    "sha256": "79e5288d10207b85f20adac3dd6f4708eac761c2d8070fbad76ff997cf48e4f9",
}
BOOST = {
    "version": "1.83.0",
    "package": "libboost1.83-dev 1.83.0-2.1ubuntu3 (Ubuntu 24.04)",
    "url": "http://archive.ubuntu.com/ubuntu/pool/main/b/boost1.83/libboost1.83-dev_1.83.0-2.1ubuntu3_amd64.deb",
    "sha256": "747b364a7d578babed9efa4c40446be4ba3470ac5b2c9951b0e3bde3d7bb47d6",
}

# Vina's own build adds -std=c++11 -DBOOST_ERROR_CODE_HEADER_ONLY to Python's flags (-O3 -DNDEBUG -fwrapv); C++17 is
# for std::filesystem. -fwasm-exceptions: Vina reports bad input by throwing. No fast-math, no FMA (WebAssembly has
# none), so arithmetic is IEEE double as natively.
CXXFLAGS = ["-std=c++17", "-O3", "-DNDEBUG", "-fwrapv", "-DBOOST_ERROR_CODE_HEADER_ONLY",
            "-DBOOST_TIMER_ENABLE_DEPRECATED", "-fwasm-exceptions", "-Wno-deprecated-declarations"]
LDFLAGS = ["-O3", "-fwasm-exceptions", "-lembind", "-sMODULARIZE=1", "-sEXPORT_ES6=1", "-sEXPORT_NAME=createVina",
           "-sENVIRONMENT=web,worker,node", "-sALLOW_MEMORY_GROWTH=1", "-sMAXIMUM_MEMORY=4GB", "-sSTACK_SIZE=8MB",
           "-sFILESYSTEM=1", "-sEXIT_RUNTIME=0"]


class BuildError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(pin: dict, cache: Path) -> Path:
    """The pinned file, from the cache or downloaded; refused unless its SHA-256 is the pin's."""
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / f"{pin['sha256'][:16]}-{pin['url'].rsplit('/', 1)[-1]}"
    if not dest.is_file():
        print(f"downloading {pin['url']}", file=sys.stderr)
        part = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(pin["url"], timeout=600) as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        part.replace(dest)
    h = hashlib.sha256()
    with open(dest, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    if h.hexdigest() != pin["sha256"]:
        dest.unlink()
        raise BuildError(f"{pin['url']}: SHA-256 {h.hexdigest()}, the pin is {pin['sha256']}")
    return dest


def _toolchain(archive: Path, work: Path) -> tuple[Path, dict[str, str]]:
    root = work / "emsdk"
    with tarfile.open(archive) as t:
        t.extractall(root, filter="tar")
    install = root / "install"
    config = root / ".emscripten"
    node = shutil.which("node")
    if not node:
        raise BuildError("node is not on PATH (Emscripten runs parts of the build with it)")
    config.write_text(f"LLVM_ROOT = {str(install / 'bin')!r}\nBINARYEN_ROOT = {str(install)!r}\nNODE_JS = {node!r}\n")
    env = {**os.environ, "EM_CONFIG": str(config), "EM_CACHE": str(root / "cache")}
    emxx = install / "emscripten" / "em++"
    version = subprocess.run([str(emxx), "--version"], env=env, capture_output=True, text=True, check=True).stdout
    if f" {EMSCRIPTEN['version']}" not in version.splitlines()[0]:
        raise BuildError(f"the pinned release is not Emscripten {EMSCRIPTEN['version']}: {version.splitlines()[0]}")
    return emxx, env


def _sources(sdist: Path, deb: Path, work: Path) -> tuple[Path, Path, dict[str, str]]:
    with tarfile.open(sdist) as t:
        t.extractall(work / "vina", filter="tar")
    lib = work / "vina" / f"vina-{VINA['version']}" / "src" / "lib"
    if not lib.is_dir():
        raise BuildError(f"{sdist.name} has no src/lib")
    if shutil.which("dpkg-deb") is None:
        raise BuildError("dpkg-deb is not on PATH (it unpacks the Boost headers' .deb)")
    subprocess.run(["dpkg-deb", "-x", str(deb), str(work / "boost")], check=True)
    include = work / "boost" / "usr" / "include"
    if not (include / "boost" / "version.hpp").is_file():
        raise BuildError(f"{deb.name} has no boost/version.hpp")
    build = work / "src"
    build.mkdir()
    digests = {}
    for f in sorted(lib.iterdir()):
        data = f.read_bytes()
        digests[f"src/lib/{f.name}"] = _sha256(data)
        if f.name != "parallel.h":
            (build / f.name).write_bytes(data)
    (build / "parallel.h").write_bytes((PORT / "parallel.h").read_bytes())
    (build / "vina_wasm.cpp").write_bytes((PORT / "vina_wasm.cpp").read_bytes())
    return build, include, digests


def build(cache: Path) -> tuple[dict[str, bytes], dict]:
    """Every file this script writes → its bytes, and the manifest."""
    tool = fetch(EMSCRIPTEN, cache)
    sdist = fetch(VINA, cache)
    deb = fetch(BOOST, cache)
    with tempfile.TemporaryDirectory(prefix="vina-wasm-") as tmp:
        work = Path(tmp)
        emxx, env = _toolchain(tool, work)
        src, boost_include, vina_digests = _sources(sdist, deb, work)
        objs = work / "obj"
        objs.mkdir()
        flags = CXXFLAGS + [f"-ffile-prefix-map={work}=.", f"-I{PORT / 'shim'}", "-isystem", str(boost_include),
                            f"-I{src}"]
        cpps = sorted(src.glob("*.cpp"))

        def compile_one(cpp: Path) -> Path:
            obj = objs / (cpp.stem + ".o")
            r = subprocess.run([str(emxx), *flags, "-c", str(cpp), "-o", str(obj)], env=env, capture_output=True,
                               text=True)
            if r.returncode:
                raise BuildError(f"{cpp.name}: {r.stderr[-4000:]}")
            return obj

        with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
            built = list(pool.map(compile_one, cpps))
        out = work / "out"
        out.mkdir()
        r = subprocess.run([str(emxx), *sorted(map(str, built)), "-o", str(out / f"{NAME}.mjs"), *LDFLAGS], env=env,
                           capture_output=True, text=True)
        if r.returncode:
            raise BuildError(f"link: {r.stderr[-4000:]}")
        mjs = (out / f"{NAME}.mjs").read_bytes()
        wasm = (out / f"{NAME}.wasm").read_bytes()
        licence_vina = (work / "vina" / f"vina-{VINA['version']}" / "LICENSE").read_text(encoding="utf-8")
    port = {str(p.relative_to(PORT)): _sha256(p.read_bytes()) for p in sorted(PORT.rglob("*")) if p.is_file()}
    licences = (
        "AutoDock Vina 1.2.7 compiled to WebAssembly by studio/scripts/build_vina_wasm.py.\n\n"
        "=== AutoDock Vina (Apache License 2.0) ===\n" + licence_vina +
        "\n=== Boost 1.83.0 headers (Boost Software License 1.0) ===\n" +
        (PORT / "LICENSE-Boost-1.0.txt").read_text(encoding="utf-8") +
        "\n=== Emscripten runtime and system libraries ===\n"
        "The loader and the module include code from Emscripten 4.0.23 (MIT / University of Illinois/NCSA), musl libc\n"
        "(MIT) and LLVM libc++/libc++abi (Apache-2.0 WITH LLVM-exception), compiled into the output.\n")
    files = {
        f"vendor/{NAME}.mjs": mjs,
        f"vendor/{NAME}.wasm": wasm,
        "vendor/LICENSES-vina.txt": licences.encode("utf-8"),
    }
    manifest = {
        "schema": "tcmstudio.vina-wasm/1",
        "engine": {"name": "AutoDock Vina", "version": VINA["version"], "threads": False,
                   "scoring": ["vina", "vinardo"], "api": "studio/vina-wasm/vina_wasm.cpp"},
        "inputs": {"emscripten": EMSCRIPTEN, "vina": VINA, "boost": BOOST},
        "flags": {"compile": CXXFLAGS, "link": LDFLAGS},
        "port": port,
        "vina_sources": vina_digests,
        "replaced": {"src/lib/parallel.h": "studio/vina-wasm/parallel.h"},
        "outputs": {name: {"bytes": len(data), "sha256": _sha256(data)} for name, data in files.items()},
    }
    files["vina_build.json"] = (json.dumps(manifest, indent=1, sort_keys=False) + "\n").encode("utf-8")
    return files, manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="rebuild and fail when a committed file differs")
    ap.add_argument("--cache", default=os.environ.get("VINA_WASM_CACHE", str(Path.home() / ".cache" / "tcmstudio-vina-wasm")),
                    help="where the pinned inputs are kept between builds")
    args = ap.parse_args(argv)
    try:
        files, manifest = build(Path(args.cache))
    except BuildError as exc:
        print(f"build_vina_wasm: {exc}", file=sys.stderr)
        return 2
    stale = 0
    for rel, data in files.items():
        path = OUT / rel
        if args.check:
            current = path.read_bytes() if path.is_file() else None
            if current != data:
                stale += 1
                print(f"differs: {path.relative_to(STUDIO.parent)}"
                      + ("" if current is None else f" (committed {len(current)} bytes, rebuilt {len(data)})"), file=sys.stderr)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            print(f"wrote {path.relative_to(STUDIO.parent)} ({len(data):,} bytes)")
    wasm = manifest["outputs"][f"vendor/{NAME}.wasm"]
    print(f"{NAME}.wasm: {wasm['bytes']:,} bytes, SHA-256 {wasm['sha256']}")
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
