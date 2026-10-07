#!/usr/bin/env python3
"""Build a release, refusing to ship a package that is known to be broken.

Two failures reached the v2.1 release tarball and both are mechanical, so both
are checked here rather than trusted to whoever runs the build:

1. **AppleDouble sidecars.** Archiving a source tree on macOS with the system
   `tar` writes a `._<name>` resource-fork file beside every real file. They are
   ordinary files to everyone else, and `._test_foo.py` is a filename pytest
   collects — so unpacking the release and running the suite failed at
   collection with ~80 unimportable modules before a single test ran.

2. **Missing runtime data.** The catalogue the registry loads was not declared
   as package data, so the wheel installed cleanly and then had nothing to
   index.

A third reached a wheel later and passed both checks: the wheel carried no skill
manifest and no lockfile, so it installed cleanly and could not run one governed
skill (audit AUD-25). Listing contents could not have caught it — the defect was
in what the installed package could do — so the release is accepted only once a
governed skill has run from the wheel alone (:func:`accept_wheel`).

The build therefore: sanitizes the tree, builds sdist + wheel, verifies each
artifact contains the runtime data and no sidecars, and runs a governed skill from
the wheel outside this checkout. A failed check is a non-zero exit, not a warning —
a release that only warns is a release that ships.

    python scripts/make_release.py            # sanitize, build, verify, accept
    python scripts/make_release.py --check    # verify the tree only, build nothing
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Filenames that must never appear in a source tree or a release artifact.
JUNK_PATTERNS = ("._*", ".DS_Store", "Thumbs.db")
JUNK_DIRS = ("__MACOSX", ".ipynb_checkpoints")

#: Where setup.py copies the reviewed skills and registry inside the package.
BUNDLED = "bioagent/_bundled/"

#: Paths that must be present inside every built wheel, relative to the wheel root. Every
#: skill manifest in the tree is required too (:func:`required_in_wheel`).
REQUIRED_IN_WHEEL = ("bioagent/data/unified_capability_catalogue.csv",
                     BUNDLED + "registry/skills.lock.yaml",
                     BUNDLED + "registry/materia_taxa.json")

#: The release acceptance: this skill, with this argument, run from the wheel alone.
ACCEPTANCE_RUN = ("normalize-tcm-entities", "names=黄芪")

#: The public API surface, declared rather than discovered.
#:
#: Walking the tree for `__init__.py` cannot detect a package that is *missing* —
#: a deleted package has no files to enumerate, which is precisely how a tree
#: with no `bioagent.workspace` passed both the junk sweep and the syntax parse.
#: A package removed from this list is a deliberate API change; a package that
#: stops importing while still listed is a broken release.
REQUIRED_PACKAGES = (
    "bioagent",
    "bioagent.acquisition",
    "bioagent.adapters",
    "bioagent.admet",
    "bioagent.analysis",
    "bioagent.backends",
    "bioagent.benchmarks",
    "bioagent.cases",
    "bioagent.clinic",
    "bioagent.contracts",
    "bioagent.core",
    "bioagent.data",
    "bioagent.docking",
    "bioagent.evolution",
    "bioagent.literature",
    "bioagent.mcp",
    "bioagent.omics",
    "bioagent.omics.sc",
    "bioagent.planners",
    "bioagent.providers",
    "bioagent.providers.supplement",
    "bioagent.psh",
    "bioagent.research",
    "bioagent.runtime",
    "bioagent.sources",
    "bioagent.sources.parsers",
    "bioagent.studies",
    "bioagent.skills",
    "bioagent.skills.clinic",
    "bioagent.skills.literature",
    "bioagent.skills.molecular",
    "bioagent.skills.omics",
    "bioagent.skills.p0",
    "bioagent.structure",
    "bioagent.tools",
    "bioagent.updates",
    "bioagent.tcm",
    "bioagent.tcmdb",
    "bioagent.tcmdb.extra",
    "bioagent.workspace",
)

SKIP_DIRS = {".git", ".venv", "venv", "build", "dist", ".mypy_cache",
             ".pytest_cache", "__pycache__", ".cache", ".eggs"}


def iter_tree(root: Path):
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.parts):
            continue
        yield path


def find_junk(root: Path) -> list[Path]:
    """Every AppleDouble sidecar and OS metadata file in the tree."""
    out: list[Path] = []
    for path in iter_tree(root):
        if path.is_dir():
            if path.name in JUNK_DIRS:
                out.append(path)
            continue
        if any(path.match(pat) for pat in JUNK_PATTERNS):
            out.append(path)
    return sorted(out)


def find_unparsable(root: Path) -> list[tuple[Path, str]]:
    """Shipped python files that do not even parse.

    `demo_run.py` went out in v2.1 with an import spliced into the middle of a
    string literal, so the file raised SyntaxError on import. Nothing caught it
    because nothing tried to parse the demos.
    """
    import ast

    bad: list[tuple[Path, str]] = []
    for path in iter_tree(root):
        if path.suffix != ".py" or path.is_dir():
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            bad.append((path.relative_to(root), f"{type(exc).__name__}: {exc}"))
    return bad


#: Set when the import probe could not check some modules (missing dependency).
_SKIPPED: list[int] = []


def find_unimportable(root: Path) -> list[tuple[str, str]]:
    """Public modules that do not import.

    `--check` used to be a junk-file sweep plus a syntax parse, and both pass on
    a tree that is missing an entire package: syntax is checked per surviving
    file, so a package that is simply absent has no files to fail. It printed OK
    while `bioagent.workspace` did not exist. Importing every package is what
    actually answers "is the public API intact".
    """
    import importlib
    import subprocess as _sp

    pkg_root = root / "src" / "bioagent"
    if not pkg_root.is_dir():
        return [("bioagent", "src/bioagent does not exist")]
    discovered = []
    for init in sorted(pkg_root.rglob("__init__.py")):
        if "__pycache__" in init.parts:
            continue
        discovered.append(".".join(("bioagent", *init.relative_to(pkg_root).parent.parts)))
    # The declared list first: it is the only part that can catch a package that
    # has vanished. Discovered packages are checked too, so a new one that does
    # not import is caught before anyone has to remember to declare it.
    names = list(REQUIRED_PACKAGES) + [n for n in discovered if n not in REQUIRED_PACKAGES]
    # Import in a child interpreter against src/, so the result reflects the tree
    # on disk rather than whatever happens to be installed in this environment.
    code = (
        "import importlib, json, sys\n"
        f"names = {names!r}\n"
        "bad = []\n"
        "for n in names:\n"
        "    try:\n"
        "        importlib.import_module(n)\n"
        "    except ModuleNotFoundError as exc:\n"
        "        missing = getattr(exc, 'name', '') or ''\n"
        # A missing third-party dependency is an unprepared environment, not a
        # broken release; a missing *bioagent* module is the defect this gate
        # exists to catch. Conflating them would make the gate either
        # unrunnable without a full install or blind to the real failure.
        "        kind = 'env' if not missing.startswith('bioagent') else 'defect'\n"
        "        bad.append([n, f'{type(exc).__name__}: {exc}', kind])\n"
        "    except Exception as exc:\n"
        "        bad.append([n, f'{type(exc).__name__}: {exc}', 'defect'])\n"
        "sys.stdout.write(json.dumps(bad))\n"
    )
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    proc = _sp.run([sys.executable, "-c", code], capture_output=True, text=True,
                   cwd=str(root), env=env, timeout=300)
    if proc.returncode != 0:
        return [("<interpreter>", (proc.stderr or "").strip()[:300])]
    try:
        rows = json.loads(proc.stdout or "[]")
    except ValueError:
        return [("<interpreter>", "could not parse import probe output")]
    defects = [(n, why) for n, why, kind in rows if kind == "defect"]
    env = [(n, why) for n, why, kind in rows if kind == "env"]
    if env and not defects:
        dep = env[0][1].split("'")[-2] if "'" in env[0][1] else "a dependency"
        print(f"[import]   {len(names) - len(env)}/{len(names)} public modules import; "
              f"{len(env)} not checkable here ({dep} is not installed — "
              "run `pip install -e .` to check them)")
        _SKIPPED.append(len(env))
    return defects


def in_git_work_tree(root: Path) -> bool:
    """Whether ``root`` is inside a git work tree (it need not be the tree's top)."""
    import subprocess as _sp

    try:
        probe = _sp.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=str(root),
                        capture_output=True, text=True, timeout=60)
    except (OSError, _sp.SubprocessError):
        return False
    return probe.returncode == 0 and probe.stdout.strip() == "true"


def find_untracked_sources(root: Path) -> list[str]:
    """Source files present on disk but absent from git.

    The incident this exists for: an unanchored `workspace/` ignore rule matched
    `src/bioagent/workspace/`, so the package was excluded from the commit while
    the working tree still had it. Every tree-based check passed.
    """
    import subprocess as _sp

    # ``root/.git`` was the test for "is this a checkout", and in the monorepo the
    # package directory has no .git of its own: the check returned [] on every run and
    # printed that every source file was tracked. Ask git instead.
    if not in_git_work_tree(root):
        return []
    try:
        tracked = _sp.run(["git", "ls-files", "src", "tests", "scripts"], cwd=str(root),
                          capture_output=True, text=True, timeout=60)
    except (OSError, _sp.SubprocessError):
        return []
    if tracked.returncode != 0:
        return []
    known = {ln.strip() for ln in tracked.stdout.splitlines() if ln.strip()}
    on_disk = {
        str(path.relative_to(root))
        for base in ("src", "tests", "scripts")
        if (root / base).is_dir()
        for path in (root / base).rglob("*.py")
        if "__pycache__" not in path.parts and not path.name.startswith("._")
    }
    return sorted(on_disk - known)


def sanitize(root: Path, *, dry_run: bool = False) -> list[Path]:
    """Delete the junk files, returning what was (or would be) removed."""
    junk = find_junk(root)
    if dry_run:
        return junk
    for path in junk:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    return junk


def _archive_names(artifact: Path) -> list[str]:
    if artifact.suffix == ".whl":
        with zipfile.ZipFile(artifact) as zf:
            return zf.namelist()
    with tarfile.open(artifact) as tf:
        return tf.getnames()


def required_in_wheel(root: Path = REPO) -> tuple[str, ...]:
    """``REQUIRED_IN_WHEEL`` and every skill manifest in the tree, as the wheel holds them."""
    manifests = sorted(p.relative_to(root).as_posix()
                       for p in (root / "skills").rglob("skill.yaml"))
    return REQUIRED_IN_WHEEL + tuple(BUNDLED + m for m in manifests)


def _in_sdist(wheel_path: str) -> str:
    """Where a wheel path comes from in the source tree, and so in the sdist."""
    return wheel_path[len(BUNDLED):] if wheel_path.startswith(BUNDLED) else "src/" + wheel_path


def verify_artifact(artifact: Path) -> list[str]:
    """Problems found inside a built artifact; empty means it is releasable."""
    names = _archive_names(artifact)
    problems: list[str] = []
    junk = [n for n in names if Path(n).name.startswith("._")
            or Path(n).name in (".DS_Store", "Thumbs.db")
            or "__MACOSX" in Path(n).parts]
    if junk:
        problems.append(f"{len(junk)} AppleDouble/OS metadata entries, e.g. {junk[:3]}")
    if artifact.suffix == ".whl":
        for required in required_in_wheel():
            if required not in names:
                problems.append(f"missing runtime data: {required}")
    else:
        # The wheel is built from the sdist, so what the sdist lacks the wheel lacks.
        inside = {n.split("/", 1)[1] for n in names if "/" in n}
        for required in map(_in_sdist, required_in_wheel()):
            if required not in inside:
                problems.append(f"missing runtime data: {required}")
    return problems


def accept_wheel(wheel: Path) -> list[str]:
    """Run one governed skill from ``wheel`` alone: the problems found, empty if it ran.

    The wheel is unpacked into an empty directory and run by a fresh interpreter whose
    working directory is another empty one. Its path holds the unpacked wheel, PSH and
    the third-party site directories, read without their ``.pth`` files (``-S``), so an
    editable install of this checkout cannot reach it; ``-P`` keeps the working
    directory off the path. The run must import bioagent from the wheel, load the skill
    and the lockfile packaged inside it, match the pin, and be recorded in an audit chain
    and authorised for release.
    """
    import importlib.util
    import site
    import tempfile

    psh = importlib.util.find_spec("psh")
    if psh is None or not psh.origin:
        return ["PSH is not importable here, and a governed run needs it (set PYTHONPATH "
                "to PSH-Harness/src)"]
    skill, argument = ACCEPTANCE_RUN
    with tempfile.TemporaryDirectory(prefix="bioagent-accept-") as tmp:
        base = Path(tmp).resolve()
        unpacked, cwd = base / "site", base / "work"
        cwd.mkdir()
        with zipfile.ZipFile(wheel) as zf:
            zf.extractall(unpacked)
        path = [str(unpacked), str(Path(psh.origin).resolve().parents[1]),
                *site.getsitepackages(), site.getusersitepackages()]
        env = {k: v for k, v in os.environ.items() if not k.startswith("BIOAGENT_")}
        env["PYTHONPATH"] = os.pathsep.join(path)
        python = [sys.executable, "-S", "-P"]

        probe = subprocess.run(
            python + ["-c", "import bioagent, json, sys; "
                            "print(json.dumps([bioagent.__file__, sys.path]))"],
            cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
        if probe.returncode != 0:
            return [f"the wheel does not import: {probe.stderr.strip()[-400:]}"]
        imported, sys_path = json.loads(probe.stdout)
        problems = []
        if not Path(imported).resolve().is_relative_to(unpacked):
            problems.append(f"bioagent was imported from {imported}, not from the wheel")
        leaked = [p for p in sys_path if p and Path(p).resolve().is_relative_to(REPO)]
        if leaked:
            problems.append(f"this checkout is on the acceptance path: {leaked}")

        run = subprocess.run(
            python + ["-m", "bioagent.cli", "skill", skill, "--arg", argument, "--json",
                      "--state-dir", str(base / "psh"), "--out-dir", str(base / "out")],
            cwd=cwd, env=env, capture_output=True, text=True, timeout=600)
        try:
            document = json.loads(run.stdout)
        except ValueError:
            return problems + [f"the governed run of {skill} exited {run.returncode}: "
                               f"{(run.stderr or run.stdout).strip()[-400:]}"]
        lockfile = document.get("governed", {}).get("lockfile") or ""
        if not lockfile or not Path(lockfile).resolve().is_relative_to(unpacked):
            problems.append(f"the run was not checked against the packaged lockfile "
                            f"(pinned by {lockfile or 'nothing'})")
        states = document.get("validation", {}).get("states", {})
        if run.returncode != 0 or not states.get("release_authorized"):
            failed = sorted(k for k, ok in states.items() if not ok)
            problems.append(f"the governed run of {skill} was not released "
                            f"(exit {run.returncode}; failed: {failed})")
    return problems


def build(outdir: Path) -> list[Path]:
    if outdir.exists():
        shutil.rmtree(outdir)
    subprocess.run([sys.executable, "-m", "build", "--sdist", "--wheel",
                    "--outdir", str(outdir), str(REPO)], check=True)
    return sorted(p for p in outdir.iterdir() if p.suffix in (".whl", ".gz"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="verify the working tree only; build nothing and delete nothing")
    ap.add_argument("--outdir", default=str(REPO / "dist"), help="where to write artifacts")
    args = ap.parse_args(argv)

    failures = 0

    junk = sanitize(REPO, dry_run=args.check)
    if junk:
        verb = "found" if args.check else "removed"
        print(f"[sanitize] {verb} {len(junk)} junk file(s):")
        for p in junk[:10]:
            print(f"           {p.relative_to(REPO)}")
        if len(junk) > 10:
            print(f"           ... and {len(junk) - 10} more")
        if args.check:
            failures += 1
    else:
        print("[sanitize] tree is clean")

    unparsable = find_unparsable(REPO)
    if unparsable:
        failures += 1
        print(f"[syntax]   {len(unparsable)} shipped python file(s) do not parse:")
        for rel, msg in unparsable:
            print(f"           {rel}: {msg}")
    else:
        print("[syntax]   every shipped python file parses")

    untracked = find_untracked_sources(REPO)
    if untracked:
        failures += 1
        print(f"[git]      {len(untracked)} source file(s) exist but are not tracked:")
        for rel in untracked[:8]:
            print(f"           {rel}")
    elif in_git_work_tree(REPO):
        print("[git]      every source file is tracked")
    else:
        print("[git]      not a git work tree; tracking not checked")

    unimportable = find_unimportable(REPO)
    if unimportable:
        failures += 1
        print(f"[import]   {len(unimportable)} public module(s) do not import:")
        for name, why in unimportable[:8]:
            print(f"           {name}: {why}")
    elif not _SKIPPED:
        print("[import]   every public module imports")

    if args.check:
        print("OK" if not failures else "FAILED")
        return 1 if failures else 0

    artifacts = build(Path(args.outdir))
    for art in artifacts:
        problems = verify_artifact(art)
        if problems:
            failures += 1
            print(f"[verify]   {art.name}: FAILED")
            for pr in problems:
                print(f"           {pr}")
        else:
            print(f"[verify]   {art.name}: ok")

    wheels = [art for art in artifacts if art.suffix == ".whl"]
    if not wheels:
        failures += 1
        print("[accept]   no wheel was built")
    for wheel in wheels:
        problems = accept_wheel(wheel)
        if problems:
            failures += 1
            print(f"[accept]   {wheel.name}: FAILED")
            for pr in problems:
                print(f"           {pr}")
        else:
            print(f"[accept]   {wheel.name}: {ACCEPTANCE_RUN[0]} ran from the wheel alone, "
                  "pinned, attested and released")

    print("OK" if not failures else "FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
