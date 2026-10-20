// RDKit.js for the browser chemistry (studio/docs/BROWSER_JOBS.md, ADMET): the pinned MinimalLib build, started only
// from wasm bytes whose SHA-256 is the pin.
//
// The loader is npm's @rdkit/rdkit@2026.9.1 dist/RDKit_minimal.js, vendored unchanged as
// vendor/RDKit_minimal-2026.9.1.mjs with one line appended ("export default initRDKitModule;") so a module worker can
// import it; web/test/scientific/chem.test.mjs checks that everything before that line is the npm file, byte for byte.
// RDKit's licence is vendor/LICENSE-RDKit.txt. The wasm (7.4 MB) is not vendored: the page fetches it from its exact
// npm version on jsDelivr (or a local mirror), and nothing compiles it before its SHA-256 and size are the pinned ones.
// The compiled module reaches the loader through instantiateWasm: this build of the loader has no wasmBinary option
// (it would fetch RDKit_minimal.wasm again, from next to itself, unchecked), so bytes handed over any other way would
// not be the bytes that run.

import { Sha256 } from "../data/digest.js";

export const RDKIT = Object.freeze({
  package: "@rdkit/rdkit",
  version: "2026.9.1", // npm's
  release: "2026.09.1", // RDKit's own, RDKit.version()
  loader: Object.freeze({
    path: "vendor/RDKit_minimal-2026.9.1.mjs",
    npm_file: "dist/RDKit_minimal.js",
    npm_sha256: "ea427db9cc39be245065603de0df10c3053cb49af3b63fdf6bc2bf73d6fc17da",
    npm_bytes: 106667,
    suffix: "export default initRDKitModule;\n",
  }),
  wasm: Object.freeze({
    url: "https://cdn.jsdelivr.net/npm/@rdkit/rdkit@2026.9.1/dist/RDKit_minimal.wasm",
    sha256: "594ceaed387adc70ea13bfbb2e8eace916f6c782254ed5a6f0f0a0a7a4fe4061",
    bytes: 7379879,
  }),
  licence: Object.freeze({ path: "vendor/LICENSE-RDKit.txt", spdx: "BSD-3-Clause" }),
});

/** A molecule or a step the browser chemistry cannot do. `code`: "bad_wasm" (bytes that are not the pinned build),
 * "invalid_smiles", "refused" (a step RDKit.js cannot reproduce exactly: `need` is "runner", `missing` names it),
 * "rdkit" (RDKit.js itself failed). */
export class ChemError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = "ChemError";
    this.code = code;
    Object.assign(this, details);
  }
}

const HEX = Array.from({ length: 256 }, (_, i) => i.toString(16).padStart(2, "0"));

/** SHA-256 of `bytes` as lower-case hex: Web Crypto where the context has it, else ../data/digest.js. */
export async function sha256Hex(bytes) {
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  if (globalThis.crypto?.subtle) {
    const d = new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", view));
    let s = "";
    for (const b of d) s += HEX[b];
    return s;
  }
  return new Sha256().update(view).digestHex();
}

/** `bytes` (ArrayBuffer or a view), checked to be the pinned RDKit_minimal.wasm → Uint8Array. `sha256` is what the
 * caller expects; anything but the pin is refused before the bytes are looked at, since this code is written for that
 * one build. */
export async function checkWasm(bytes, sha256 = RDKIT.wasm.sha256) {
  if (String(sha256 || "").toLowerCase() !== RDKIT.wasm.sha256) {
    throw new ChemError("bad_wasm", `the browser chemistry is pinned to RDKit.js ${RDKIT.version} (RDKit_minimal.wasm sha256 ` +
      `${RDKIT.wasm.sha256}); it cannot run a build with sha256 ${sha256 || "(none given)"}`);
  }
  let view;
  if (bytes instanceof Uint8Array) view = bytes;
  else if (ArrayBuffer.isView(bytes)) view = new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  else if (bytes instanceof ArrayBuffer) view = new Uint8Array(bytes);
  else throw new ChemError("bad_wasm", "RDKit_minimal.wasm must be given as bytes (an ArrayBuffer or a typed array)");
  if (view.byteLength !== RDKIT.wasm.bytes) {
    throw new ChemError("bad_wasm", `RDKit_minimal.wasm is ${view.byteLength} bytes; RDKit.js ${RDKIT.version}'s is ${RDKIT.wasm.bytes}`);
  }
  const got = await sha256Hex(view);
  if (got !== RDKIT.wasm.sha256) {
    throw new ChemError("bad_wasm", `RDKit_minimal.wasm has sha256 ${got}, not RDKit.js ${RDKIT.version}'s ${RDKIT.wasm.sha256}: ` +
      "it is not used");
  }
  return view;
}

/** The wasm from `url` (the pinned jsDelivr URL unless a local mirror is named), checked → Uint8Array. Nothing else
 * is tried when it fails: a different host is the caller's decision. */
export async function fetchWasm(url = RDKIT.wasm.url, { fetch = globalThis.fetch, signal, sha256 = RDKIT.wasm.sha256 } = {}) {
  if (typeof fetch !== "function") throw new ChemError("bad_wasm", "no fetch here to download RDKit_minimal.wasm");
  let res;
  try {
    res = await fetch(url, { signal, credentials: "omit" });
  } catch (err) {
    if (err?.name === "AbortError") throw err;
    throw new ChemError("bad_wasm", `RDKit_minimal.wasm could not be downloaded from ${url}: ${err?.message || err}`);
  }
  if (!res.ok) throw new ChemError("bad_wasm", `RDKit_minimal.wasm: ${url} answered HTTP ${res.status}`);
  return checkWasm(await res.arrayBuffer(), sha256);
}

/**
 * RDKit.js started from `wasmBytes` (checked against the pin first) → the module. `factory` is the loader's
 * initRDKitModule (default: the vendored ES module; Node's tests give npm's CommonJS file, which is the same bytes).
 * Logging is turned off (RDKit's warnings would otherwise go to the console for every odd molecule) and legacy stereo
 * perception is set on, as native RDKit 2026.09.1 has it by default. The module's version must be the pin's.
 */
export async function loadRDKit(wasmBytes, { sha256 = RDKIT.wasm.sha256, factory = null } = {}) {
  const bytes = await checkWasm(wasmBytes, sha256);
  const compiled = await WebAssembly.compile(bytes);
  const init = factory || (await import("./vendor/RDKit_minimal-2026.9.1.mjs")).default;
  let failed;
  const instantiateFailed = new Promise((_, reject) => { failed = reject; });
  const RDKit = await Promise.race([
    init({
      // the loader awaits the callback forever when instantiation throws, so a failure is raced against it
      instantiateWasm(imports, receive) {
        WebAssembly.instantiate(compiled, imports).then((instance) => receive(instance), (err) => failed(err));
        return {};
      },
    }),
    instantiateFailed,
  ]).catch((err) => {
    throw err instanceof ChemError ? err : new ChemError("rdkit", `RDKit.js ${RDKIT.version} did not start: ${err?.message || err}`);
  });
  const version = RDKit.version();
  if (version !== RDKIT.release) {
    throw new ChemError("rdkit", `the loader started RDKit ${version}, not ${RDKIT.release}: the vendored loader and the pin disagree`);
  }
  RDKit.disable_logging();
  RDKit.use_legacy_stereo_perception(true);
  return RDKit;
}
