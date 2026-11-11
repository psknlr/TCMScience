// Result references (CONTRACTS §6): a later call is handed an earlier result's data by reference instead of having
// the model retype it. A pathway enrichment of 300 terms, a herb's 60 constituents or a network's 400 edges cannot be
// copied into the next call's arguments by a model without loss: it sees only as much of a result as fits its text, it
// pays for every item it writes, and one mistyped gene symbol is an invented datum. So every result that holds data is
// labelled (R1, R2 … across the conversation), and an argument value may be a reference to one:
//
//   {"$ref": "R3"}                                              the whole result, as the model was shown it
//   {"$ref": "R3", "path": "terms"}                             the value at a path (dots, or /a/b; [0] for an index)
//   {"$ref": "R3", "path": "terms", "field": "id"}              that field of each record of a list (dots go deeper)
//   {"$ref": "R3", "path": "rows", "fields": ["a", "b"]}        several fields of each record: rows of values
//   … "unique": true · "limit": 50 · "join": " "                 de-duplicated · the first n · one string
//
// A list written as a header row and rows of values ([["Gene symbol", "P value"], ["TNF", 0.01], …], as HERB writes
// its tables) is read by its column names too: "field": "Gene symbol" gives that column, without the header row.
//
// The reference reads the full result (the envelope's `view` when the model's view is not the result itself, else its
// `result`), also the rows the text did not show. The label is the line "Ref: R3" under the result's first line. It is resolved in the page before the call is placed, so the router,
// the approvals and the receipt (input_sha256) see the real arguments; the stored call keeps the reference as written.
// A reference that does not resolve is refused before anything runs, with what the result does hold.

import { isPlainObject } from "./util.js";

export const REF_ID = /^R[1-9]\d{0,5}$/;
const REF_KEYS = new Set(["$ref", "path", "field", "fields", "unique", "limit", "join"]);
/** The most values one reference may put into a call (a guard against a runaway argument, not a research limit). */
export const MAX_REF_ITEMS = 200000;

/** Is this argument value a reference? */
export const isRef = (v) => isPlainObject(v) && typeof v.$ref === "string";

/** Does `value` hold a reference anywhere? */
export function hasRef(value) {
  if (Array.isArray(value)) return value.some(hasRef);
  if (!isPlainObject(value)) return false;
  return isRef(value) || Object.values(value).some(hasRef);
}

class RefError extends Error {}

function segments(path) {
  const s = String(path ?? "").trim().replace(/\[(\d+)\]/g, ".$1");
  if (!s) return [];
  return (s.startsWith("/") ? s.split("/") : s.split(".")).map((x) => x.trim()).filter(Boolean);
}

const keysOf = (v) => (isPlainObject(v) ? Object.keys(v) : []);
const short = (list, n = 16) => (list.length > n ? `${list.slice(0, n).join(", ")}, … (${list.length} in all)` : list.join(", "));

/** The header of a list written as a header row and rows of values, or null: [[names…], [values…], …]. */
function headerOf(list) {
  if (!Array.isArray(list) || list.length < 2 || !list.every(Array.isArray)) return null;
  const head = list[0];
  return head.length && head.every((x) => typeof x === "string" && x.trim()) ? head.map((x) => x.trim()) : null;
}

/** Where a field names a column of such a table: {index, rest (a path inside the cell)} or null. */
function column(header, field) {
  const name = String(field).trim();
  const find = (x) => header.findIndex((h) => h.toLowerCase() === x.toLowerCase());
  let i = find(name);
  if (i >= 0) return { index: i, rest: "" };
  // "Ingredient id.title": a column, then a path inside its cells
  for (let dot = name.indexOf("."); dot > 0; dot = name.indexOf(".", dot + 1)) {
    i = find(name.slice(0, dot).trim());
    if (i >= 0) return { index: i, rest: name.slice(dot + 1) };
  }
  return null;
}

function describe(value) {
  if (Array.isArray(value)) {
    const header = headerOf(value);
    if (header) return `a table of ${value.length - 1} rows under a header row, with the columns ${short(header)}`;
    const first = value.find(isPlainObject);
    return `a list of ${value.length}${first ? ` records with the fields ${short(Object.keys(first))}` : " values"}`;
  }
  if (isPlainObject(value)) return `an object with the keys ${short(Object.keys(value))}`;
  return value === null || value === undefined ? "nothing" : `a ${typeof value}`;
}

function walk(value, path, ref) {
  const segs = segments(path);
  let cur = value;
  let at = "";
  for (let i = 0; i < segs.length; i++) {
    const seg = segs[i];
    if (Array.isArray(cur)) {
      if (/^\d+$/.test(seg)) {
        if (Number(seg) >= cur.length) throw new RefError(`${ref}: index ${seg} at '${at || "(top)"}' is past the end of a list of ${cur.length}`);
        cur = cur[Number(seg)];
      } else if (seg === "rows") {
        // the text shows a list of records as a table {columns, rows}: the list itself is what 'rows' means
      } else {
        throw new RefError(`${ref}: '${at || "(top)"}' is ${describe(cur)}; take a field of its records with "field": "${seg}", not with the path`);
      }
    } else if (isPlainObject(cur)) {
      if (Object.hasOwn(cur, seg)) cur = cur[seg];
      // "Result: {…}" in the text is the result itself, not a key of it
      else if (i === 0 && seg.toLowerCase() === "result") continue;
      else throw new RefError(`${ref}: no '${seg}' at '${at || "(top)"}', which is ${describe(cur)}`);
    } else {
      throw new RefError(`${ref}: '${at || "(top)"}' is ${describe(cur)}; the path cannot go on to '${seg}'`);
    }
    at = at ? `${at}.${seg}` : seg;
  }
  return cur;
}

function pick(record, field) {
  let cur = record;
  for (const seg of segments(field)) {
    if (Array.isArray(cur) && /^\d+$/.test(seg)) cur = cur[Number(seg)];
    else if (isPlainObject(cur) && Object.hasOwn(cur, seg)) cur = cur[seg];
    else return undefined;
  }
  return cur;
}

const isEmpty = (v) => v === undefined || v === null || v === "";

function resolveOne(spec, lookup, known) {
  const ref = spec.$ref.trim();
  const unknown = Object.keys(spec).filter((k) => !REF_KEYS.has(k));
  if (unknown.length) throw new RefError(`a reference takes only $ref, path, field, fields, unique, limit and join (got ${unknown.join(", ")})`);
  if (!REF_ID.test(ref)) throw new RefError(`'${spec.$ref}' is not a result reference (R1, R2, …, as the Ref line of a result gives it)`);
  const data = lookup(ref);
  if (data === undefined) {
    const have = known();
    throw new RefError(`${ref} is not a result of this conversation${have.length ? ` (the results that hold data: ${short(have, 24)})` : " (no result holds data yet)"}`);
  }
  let value = walk(data, spec.path, ref);
  const where = spec.path ? `${ref} at '${spec.path}'` : ref;
  let dropped = 0;
  if (spec.field !== undefined || spec.fields !== undefined) {
    if (spec.field !== undefined && spec.fields !== undefined) throw new RefError("a reference takes field or fields, not both");
    const fields = spec.fields !== undefined ? spec.fields : [spec.field];
    if (!Array.isArray(fields) || !fields.length || !fields.every((f) => typeof f === "string" && f.trim())) throw new RefError("field is a field name, fields a list of them");
    if (!Array.isArray(value)) throw new RefError(`${where} is ${describe(value)}, not a list of records: field needs a list (give the path to one)`);
    // a header row and rows of values: a field that names a column reads that column, below the header
    const header = headerOf(value);
    const columns = header ? fields.map((f) => column(header, f)) : [];
    const tabled = Boolean(header) && columns.every(Boolean);
    const records = tabled ? value.slice(1) : value;
    const get = tabled
      ? (r, k) => (columns[k].rest ? pick(r[columns[k].index], columns[k].rest) : r[columns[k].index])
      : (r, k) => pick(r, fields[k]);
    fields.forEach((f, k) => {
      if (records.length && !records.some((r) => get(r, k) !== undefined)) throw new RefError(`${where}: no record has the field '${f}'; it is ${describe(value)}`);
    });
    if (spec.fields !== undefined) value = records.map((r) => fields.map((f, k) => get(r, k) ?? null));
    else {
      value = [];
      for (const r of records) {
        const v = get(r, 0);
        if (isEmpty(v)) dropped++;
        else value.push(v);
      }
    }
  }
  if (spec.unique) {
    if (!Array.isArray(value)) throw new RefError(`${where}: unique needs a list`);
    const seen = new Set();
    value = value.filter((v) => {
      const k = typeof v === "object" && v !== null ? JSON.stringify(v) : `${typeof v}:${v}`;
      return seen.has(k) ? false : (seen.add(k), true);
    });
  }
  if (spec.limit !== undefined) {
    if (!Number.isInteger(spec.limit) || spec.limit < 1) throw new RefError("limit is a positive integer");
    if (!Array.isArray(value)) throw new RefError(`${where}: limit needs a list`);
    value = value.slice(0, spec.limit);
  }
  if (Array.isArray(value) && value.length > MAX_REF_ITEMS) throw new RefError(`${where} holds ${value.length} items, more than one call takes (${MAX_REF_ITEMS}); narrow it with limit`);
  const n = Array.isArray(value) ? value.length : null; // counted before a join makes one string of them
  if (spec.join !== undefined) {
    if (typeof spec.join !== "string") throw new RefError("join is the separator, a string");
    if (!Array.isArray(value) || value.some((v) => typeof v === "object" && v !== null)) throw new RefError(`${where}: join needs a list of plain values (take one with field)`);
    value = value.join(spec.join);
  }
  if (value === undefined) throw new RefError(`${where} holds nothing`);
  return { value, used: { ref, ...(spec.path ? { path: String(spec.path) } : {}), ...(spec.field ? { field: spec.field } : {}), ...(spec.fields ? { fields: spec.fields } : {}), ...(n !== null ? { items: n } : {}), ...(dropped ? { without_value: dropped } : {}) } };
}

/**
 * The arguments with every reference replaced by the data it names. lookup(ref) → the result's data, or undefined;
 * known() → the refs that exist (for the message of an unknown one). → {args, used: [{ref, path?, field?, items?,
 * without_value?}]} or {error: message}. The arguments given are not changed.
 */
export function resolveRefs(args, lookup, known = () => []) {
  const used = [];
  const go = (value) => {
    if (Array.isArray(value)) return value.map(go);
    if (!isPlainObject(value)) return value;
    if (isRef(value)) {
      const r = resolveOne(value, lookup, known);
      used.push(r.used);
      return r.value;
    }
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = go(v);
    return out;
  };
  try {
    return { args: go(args), used };
  } catch (err) {
    if (err instanceof RefError) return { error: err.message };
    throw err;
  }
}

/** The data a result offers to a reference: the model's view in full when the envelope carries one, else the result. */
export function refData(envelope) {
  if (!envelope || envelope.status !== "succeeded") return undefined;
  const data = envelope.view ?? envelope.result;
  if (data === null || data === undefined) return undefined;
  if (typeof data !== "object") return undefined; // a bare number or word is read, not passed on
  return data;
}

/** One line for the model on what a call's references gave it. */
export function refNote(used) {
  if (!used?.length) return "";
  const parts = used.map((u) => `${u.ref}${u.path ? ` ${u.path}` : ""}${u.field ? ` · ${u.field}` : u.fields ? ` · ${u.fields.join(", ")}` : ""}${u.items !== undefined ? `: ${u.items} item${u.items === 1 ? "" : "s"}` : ""}${u.without_value ? ` (${u.without_value} record${u.without_value === 1 ? "" : "s"} without a value left out)` : ""}`);
  return `References resolved: ${parts.join("; ")}.`;
}
