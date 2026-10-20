// The browser chemistry's prepared set on disk (tcmstudio.admet-prepared/1), which bioagent.admet.engine's
// PrecomputedEngine reads: prepared.json, features.f32 and bits.u8 in one directory.
//
//   prepared.json  {schema, engine {name, version, detail}, n, smiles_sha256 (SHA-256 of the input SMILES joined by
//                  "\n", UTF-8), rows: [{ok, error, canonical, descriptors, atoms_with_h, alerts}]}, one row per
//                  input in input order. A row that is not ok has error (why) and null for the rest; a molecule the
//                  browser chemistry declined (a step native RDKit can do) also has need "runner" and missing (the
//                  step), which an invalid SMILES has not. descriptors are chem.descriptors' 24 + FormalCharge in that
//                  order, in float64;
//                  JSON has no NaN, so a NaN descriptor is written as null. alerts are [filter_set, description] pairs
//                  in rules.alerts' order, or null when the set was prepared without alerts (model building).
//   features.f32   the ok rows' featurize() rows, n_ok × 2073 float32, little-endian, in row order.
//   bits.u8        the ok rows' bits(), n_ok × 256 bytes (np.packbits, most significant bit first).
//
// This module is page-side: it needs no RDKit, only the rows and buffers chem.worker.js returns.

import { Sha256 } from "../data/digest.js";
import { FEATURE_KEYS, N_BITS_BYTES, N_FEATURES } from "./features.js";

export const PREPARED_SCHEMA = "tcmstudio.admet-prepared/1";
export const PORTS_VERSION = 1;
export const ENGINE = Object.freeze({
  name: "rdkit.js",
  version: "2026.09.1",
  detail: `RDKit.js 2026.09.1 MinimalLib + bioagent ports ${PORTS_VERSION}`,
});
export const FILES = Object.freeze({ prepared: "prepared.json", features: "features.f32", bits: "bits.u8" });

const LITTLE_ENDIAN = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1;

/** SHA-256 of the input SMILES joined by "\n" (UTF-8), as prepared.json's smiles_sha256. */
export function smilesSha256(smiles) {
  return new Sha256().update(smiles.join("\n")).digestHex();
}

/** A row as prepared.json holds it (the keys in a fixed order; NaN descriptors become null). */
export function jsonRow(row) {
  const descriptors = row.descriptors
    ? Object.fromEntries(FEATURE_KEYS.map((k) => [k, Number.isFinite(row.descriptors[k]) ? row.descriptors[k] : null]))
    : null;
  const out = {
    ok: Boolean(row.ok),
    error: row.ok ? null : String(row.error || "not prepared"),
    canonical: row.ok ? row.canonical : null,
    descriptors: row.ok ? descriptors : null,
    atoms_with_h: row.ok ? row.atoms_with_h : null,
    alerts: row.ok && row.alerts ? row.alerts.map(([c, d]) => [c, d]) : null,
  };
  if (!row.ok && row.refused) {
    out.need = row.refused.need;
    out.missing = row.refused.missing;
  }
  return out;
}

function littleEndianFloat32(features) {
  if (LITTLE_ENDIAN) return features.buffer.slice(features.byteOffset, features.byteOffset + features.byteLength);
  const out = new ArrayBuffer(features.length * 4);
  const view = new DataView(out);
  for (let i = 0; i < features.length; i++) view.setFloat32(i * 4, features[i], true);
  return out;
}

/**
 * The directory's three files from the inputs and what chem.worker.js returned for them → {json (prepared.json's
 * text), features (ArrayBuffer), bits (ArrayBuffer), n, n_ok}. `rows` are one per input, in order; `features`
 * (Float32Array) and `bits` (Uint8Array) hold the ok rows' values in row order. Sizes that do not agree are refused.
 */
export function preparedFiles({ smiles, rows, features, bits, engine = ENGINE }) {
  if (!Array.isArray(smiles) || !Array.isArray(rows) || smiles.length !== rows.length) {
    throw new TypeError(`a prepared set needs one row per input SMILES (${smiles?.length} inputs, ${rows?.length} rows)`);
  }
  const nOk = rows.filter((r) => r.ok).length;
  if (!(features instanceof Float32Array) || features.length !== nOk * N_FEATURES) {
    throw new TypeError(`features must be a Float32Array of ${nOk} × ${N_FEATURES} values (${features?.length} given)`);
  }
  if (!(bits instanceof Uint8Array) || bits.length !== nOk * N_BITS_BYTES) {
    throw new TypeError(`bits must be a Uint8Array of ${nOk} × ${N_BITS_BYTES} bytes (${bits?.length} given)`);
  }
  const doc = {
    schema: PREPARED_SCHEMA,
    engine: { name: engine.name, version: engine.version, detail: engine.detail },
    n: rows.length,
    smiles_sha256: smilesSha256(smiles.map(String)),
    rows: rows.map(jsonRow),
  };
  return {
    json: JSON.stringify(doc) + "\n",
    features: littleEndianFloat32(features),
    bits: bits.buffer.slice(bits.byteOffset, bits.byteOffset + bits.byteLength),
    n: rows.length,
    n_ok: nOk,
  };
}
