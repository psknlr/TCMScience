import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { MAX_REF_ITEMS, hasRef, isRef, refData, refNote, resolveRefs } from "../js/core/refs.js";

const R1 = {
  disease: { id: "MONDO_0005148", name: "type 2 diabetes mellitus" },
  targets: [
    { symbol: "KCNJ11", ensembl_id: "ENSG00000187486", score: 0.88, target: { id: "x1" } },
    { symbol: "PPARG", ensembl_id: "ENSG00000132170", score: 0.86, target: { id: "x2" } },
    { symbol: "PPARG", ensembl_id: "ENSG00000132170", score: 0.5, target: { id: "x2" } },
    { symbol: null, ensembl_id: "ENSG0", score: 0.1 },
  ],
};
const R2 = [{ preferredName_A: "TNF", preferredName_B: "IL6", score: "0.99" }, { preferredName_A: "TNF", preferredName_B: "AKT1", score: "0.95" }];
const lookup = (ref) => ({ R1, R2 }[ref]);
const known = () => ["R1", "R2"];
const resolve = (args) => resolveRefs(args, lookup, known);

test("a reference is replaced by the data it names; the arguments given are not changed", () => {
  const args = { connector: "gprofiler", operation: "gost", arguments: { query: { $ref: "R1", path: "targets", field: "symbol", unique: true, join: " " }, organism: "hsapiens" } };
  const before = JSON.stringify(args);
  const r = resolve(args);
  assert.equal(r.error, undefined);
  assert.deepEqual(r.args, { connector: "gprofiler", operation: "gost", arguments: { query: "KCNJ11 PPARG", organism: "hsapiens" } });
  assert.equal(JSON.stringify(args), before);
  // what was used is recorded: the record without a value is counted, not passed on as "null"
  assert.deepEqual(r.used, [{ ref: "R1", path: "targets", field: "symbol", items: 2, without_value: 1 }]);
  assert.match(refNote(r.used), /R1 targets · symbol: 2 items \(1 record without a value left out\)/);
  assert.equal(refNote([]), "");
});

test("the whole result, a path, an index, a nested field, several fields as rows, and a limit", () => {
  assert.deepEqual(resolve({ edges: { $ref: "R2" } }).args, { edges: R2 });
  assert.deepEqual(resolve({ d: { $ref: "R1", path: "disease.id" } }).args, { d: "MONDO_0005148" });
  assert.deepEqual(resolve({ d: { $ref: "R1", path: "/disease/name" } }).args, { d: "type 2 diabetes mellitus" });
  assert.deepEqual(resolve({ d: { $ref: "R1", path: "targets[1].symbol" } }).args, { d: "PPARG" });
  assert.deepEqual(resolve({ ids: { $ref: "R1", path: "targets", field: "target.id" } }).args, { ids: ["x1", "x2", "x2"] });
  assert.deepEqual(resolve({ e: { $ref: "R2", fields: ["preferredName_A", "preferredName_B"] } }).args, { e: [["TNF", "IL6"], ["TNF", "AKT1"]] });
  assert.deepEqual(resolve({ s: { $ref: "R1", path: "targets", field: "symbol", limit: 1 } }).args, { s: ["KCNJ11"] });
  // references inside lists and beside ordinary values; several in one call
  const two = resolve({ sets: { a: { $ref: "R1", path: "targets", field: "symbol", unique: true }, b: ["TNF"], c: [{ $ref: "R2", field: "preferredName_B" }] } });
  assert.deepEqual(two.args.sets, { a: ["KCNJ11", "PPARG"], b: ["TNF"], c: [["IL6", "AKT1"]] });
  assert.equal(two.used.length, 2);
});

test("the names the text shows are understood: 'rows' of a table is the list, and 'Result' is the result", () => {
  assert.deepEqual(resolve({ s: { $ref: "R1", path: "targets.rows", field: "symbol", limit: 2 } }).args, { s: ["KCNJ11", "PPARG"] });
  assert.deepEqual(resolve({ s: { $ref: "R1", path: "Result.disease.id" } }).args, { s: "MONDO_0005148" });
  // a result that really has a key named result keeps it
  assert.deepEqual(resolveRefs({ s: { $ref: "R9", path: "result.x" } }, () => ({ result: { x: 1 } })).args, { s: 1 });
});

test("a table written as a header row and rows of values is read by its column names, below the header", () => {
  const herb = {
    herb_target: [["Target id", "Gene symbol", "P value"], [{ link: "/d?v=HBTAR1", title: "HBTAR1" }, "TNF", 0.01], [{ link: "/d?v=HBTAR2", title: "HBTAR2" }, "IL6", 0.2], [{ title: "HBTAR3" }, "", 0.5]],
    edges: [["TNF", "IL6"], ["TNF", "AKT1"]],
  };
  const look = (ref) => (ref === "R1" ? herb : undefined);
  const r = resolveRefs({ genes: { $ref: "R1", path: "herb_target", field: "Gene symbol", join: " " } }, look);
  assert.deepEqual(r.args, { genes: "TNF IL6" });
  assert.deepEqual(r.used, [{ ref: "R1", path: "herb_target", field: "Gene symbol", items: 2, without_value: 1 }]);
  // a column's name whatever its case, a path inside its cells, and several columns as rows
  assert.deepEqual(resolveRefs({ ids: { $ref: "R1", path: "herb_target", field: "target id.title" } }, look).args, { ids: ["HBTAR1", "HBTAR2", "HBTAR3"] });
  assert.deepEqual(resolveRefs({ t: { $ref: "R1", path: "herb_target", fields: ["Gene symbol", "P value"], limit: 2 } }, look).args, { t: [["TNF", 0.01], ["IL6", 0.2]] });
  // a field that is no column says which columns there are
  assert.match(resolveRefs({ g: { $ref: "R1", path: "herb_target", field: "symbol" } }, look).error, /no record has the field 'symbol'; it is a table of 3 rows under a header row, with the columns Target id, Gene symbol, P value/);
  // rows of values that only look like a header are still read by position, the first row included
  assert.deepEqual(resolveRefs({ a: { $ref: "R1", path: "edges", field: "0", unique: true } }, look).args, { a: ["TNF"] });
  assert.deepEqual(resolveRefs({ e: { $ref: "R1", path: "edges" } }, look).args, { e: [["TNF", "IL6"], ["TNF", "AKT1"]] });
});

test("a reference that does not resolve is an error that says what the result does hold; nothing is guessed", () => {
  assert.match(resolve({ a: { $ref: "R7" } }).error, /R7 is not a result of this conversation \(the results that hold data: R1, R2\)/);
  assert.match(resolveRefs({ a: { $ref: "R1" } }, () => undefined).error, /no result holds data yet/);
  assert.match(resolve({ a: { $ref: "result 1" } }).error, /not a result reference/);
  assert.match(resolve({ a: { $ref: "R1", path: "genes" } }).error, /R1: no 'genes' at '\(top\)', which is an object with the keys disease, targets/);
  assert.match(resolve({ a: { $ref: "R1", path: "targets.symbol" } }).error, /take a field of its records with "field": "symbol"/);
  assert.match(resolve({ a: { $ref: "R1", path: "targets", field: "gene" } }).error, /no record has the field 'gene'; it is a list of 4 records with the fields symbol, ensembl_id, score, target/);
  assert.match(resolve({ a: { $ref: "R1", path: "disease", field: "id" } }).error, /not a list of records/);
  assert.match(resolve({ a: { $ref: "R1", path: "targets[9]" } }).error, /index 9 .* past the end of a list of 4/);
  assert.match(resolve({ a: { $ref: "R1", path: "targets", join: "," } }).error, /join needs a list of plain values/);
  assert.match(resolve({ a: { $ref: "R1", field: "x", fields: ["y"] } }).error, /field or fields, not both/);
  assert.match(resolve({ a: { $ref: "R1", limit: 0 } }).error, /limit is a positive integer/);
  assert.match(resolve({ a: { $ref: "R1", where: { score: 1 } } }).error, /takes only \$ref, path, field, fields, unique, limit and join \(got where\)/);
  const big = resolveRefs({ a: { $ref: "R1" } }, () => Array.from({ length: MAX_REF_ITEMS + 1 }, (_, i) => i));
  assert.match(big.error, /more than one call takes/);
});

test("what counts as a reference, and which results offer data", () => {
  assert.equal(isRef({ $ref: "R1" }), true);
  assert.equal(isRef({ ref: "R1" }), false);
  assert.equal(isRef({ $ref: 1 }), false);
  assert.equal(hasRef({ a: [1, { b: { $ref: "R2" } }] }), true);
  assert.equal(hasRef({ a: [1, { b: "R2" }], c: null }), false);
  // the model's view in full when the envelope carries one, else the result; nothing for a failure or a bare value
  assert.deepEqual(refData({ status: "succeeded", result: { raw: 1 }, view: { terms: [] } }), { terms: [] });
  assert.deepEqual(refData({ status: "succeeded", result: { raw: 1 } }), { raw: 1 });
  assert.equal(refData({ status: "failed", result: { raw: 1 } }), undefined);
  assert.equal(refData({ status: "succeeded", result: null }), undefined);
  assert.equal(refData({ status: "succeeded", result: 42 }), undefined);
  assert.equal(refData(null), undefined);
});
