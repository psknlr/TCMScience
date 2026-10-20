"""The chemistry seam: what the ADMET models and pipeline need from a chemistry engine.

``ChemEngine.prepare`` turns SMILES into a ``PreparedSet``: for each molecule, whether it
was prepared (and why not), its standardised canonical SMILES, its feature row
(``chem.featurize``), its packed Morgan bits (``chem.bits``), its descriptors, its atom
count with hydrogens (Ghose's rule) and its structural alerts (``rules.alerts``). Two
engines:

* ``RDKitEngine``: ``chem`` and ``rules`` on RDKit, the native chemistry and the
  reference every other engine is held to.
* ``PrecomputedEngine``: what another engine wrote to disk (a browser's RDKit.js
  chemistry), read back for exactly the molecules it was prepared for and held to the
  same shapes. A set prepared for other molecules, or whose rows do not agree with its
  arrays, is refused (``ChemError``), never patched up.

The applicability-domain search takes the same seam: ``PrecomputedSimilarity`` returns
what a browser computed (on its GPU or CPU) only after checking it against the
fingerprints and, for a sample of queries, against the exact search on this CPU
(``chem.tanimoto_best``); anything that does not hold is not used, and the CPU computes
every endpoint's similarities itself.

On disk (the browser writes it; ``write_prepared`` and ``write_similarity`` write the same
from Python):

* a prepared set is a directory with ``prepared.json`` = ``{schema
  "tcmstudio.admet-prepared/1", engine {name, version, detail}, n, smiles_sha256, rows}``,
  ``features.f32`` (n_ok x 2073 little-endian float32, the prepared rows in input order)
  and ``bits.u8`` (n_ok x 256). ``smiles_sha256`` is the SHA-256 of the inputs joined by
  newlines (UTF-8). A row is ``{ok, error, canonical, descriptors, atoms_with_h, alerts}``
  and may say ``need``: ``"runner"`` when the engine refused a molecule that the native
  chemistry can standardise (MolStandardize steps it does not port). Descriptors that are
  not a number are null, the others finite; ``alerts`` is a list of ``[catalog,
  description]`` or null when they were not computed. A ``PrecomputedEngine`` may also be
  given a directory of such directories, one per set (an endpoint's training molecules, a
  run's queries).
* the similarities are a directory with ``similarity.json`` = ``{schema
  "tcmstudio.admet-similarity/1", backend, endpoints: {endpoint: {refs_sha256, n_query,
  n_ref}}}`` and ``<endpoint>.u32`` (n_query x 3 little-endian uint32: the nearest
  reference's index, the intersection, the union). ``refs_sha256`` is the SHA-256 of the
  reference bits searched (n_ref x 256 bytes, row by row: the model's domain).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

import numpy as np

from .chem import (DESCRIPTORS, FEATURES, FP_BITS, ChemError, bits, descriptors, feature_rows,
                   need, standardize, tanimoto_best)
from .rules import alerts as find_alerts

__all__ = ["PreparedSet", "ChemEngine", "RDKitEngine", "PrecomputedEngine",
           "PrecomputedSimilarity", "Similarity", "default_engine", "identity",
           "smiles_sha256", "sample_queries", "write_prepared", "write_similarity",
           "PREPARED_SCHEMA", "SIMILARITY_SCHEMA", "CPU_SIMILARITY"]

PREPARED_SCHEMA = "tcmstudio.admet-prepared/1"
SIMILARITY_SCHEMA = "tcmstudio.admet-similarity/1"
_DESCRIBED = (*DESCRIPTORS, "FormalCharge")
_BYTES = FP_BITS // 8
#: The applicability-domain search: ``(endpoint, query_bits, ref_bits) -> (arg, inter,
#: union)``, as ``chem.tanimoto_best`` returns them.
Similarity = Callable[[str, np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray, np.ndarray]]
#: What run.json records when this CPU searched the domain (``chem.tanimoto_best``).
CPU_SIMILARITY = {"backend": "cpu", "checked": None, "rejected": None}


@dataclass(frozen=True)
class PreparedSet:
    """The chemistry of ``smiles``, in their order. The arrays hold the prepared molecules
    only (``ok``), in input order; the tuples have one entry per input, None where the
    molecule was not prepared (and ``alerts`` None where they were not asked for)."""
    smiles: tuple[str, ...]
    ok: np.ndarray                                       # bool (n,)
    errors: tuple[str | None, ...]                       # why not prepared; None when ok
    canonical: tuple[str | None, ...]                    # standardised canonical SMILES
    features: np.ndarray                                 # float32 (n_ok, 2073)
    bits: np.ndarray                                     # uint8 (n_ok, 256), MSB first
    descriptors: tuple[dict[str, float] | None, ...]     # chem.descriptors (NaN kept)
    atoms_with_h: tuple[int | None, ...]                 # Chem.AddHs(mol).GetNumAtoms()
    alerts: tuple[tuple[tuple[str, str], ...] | None, ...]   # (catalog, description)
    needs: tuple[str | None, ...] = ()                   # "runner": refused, not unreadable

    def __post_init__(self) -> None:
        n = len(self.smiles)
        if not self.needs:
            object.__setattr__(self, "needs", (None,) * n)
        n_ok = int(np.count_nonzero(self.ok))
        lengths = {len(getattr(self, k)) for k in ("ok", "errors", "canonical", "descriptors",
                                                   "atoms_with_h", "alerts", "needs")}
        if (lengths != {n} or self.ok.dtype != bool
                or self.features.shape != (n_ok, FEATURES) or self.features.dtype != np.float32
                or self.bits.shape != (n_ok, _BYTES) or self.bits.dtype != np.uint8):
            raise ChemError(f"a prepared set of {n} molecules ({n_ok} prepared) needs one entry "
                            f"per molecule and {n_ok} x {FEATURES} float32 features and "
                            f"{n_ok} x {_BYTES} uint8 bits (got features "
                            f"{self.features.shape} {self.features.dtype}, bits "
                            f"{self.bits.shape} {self.bits.dtype})")

    @property
    def index(self) -> np.ndarray:
        """The input positions of the prepared molecules (the arrays' rows)."""
        return np.flatnonzero(self.ok)


class ChemEngine(Protocol):
    name: str          # "rdkit" | "rdkit.js"
    version: str       # rdkit.__version__ | "2026.09.1"
    detail: str        # "RDKit 2026.09.1 (Python)" | "RDKit.js 2026.09.1 MinimalLib + ..."

    def prepare(self, smiles: Sequence[str], *, alerts: bool = True) -> PreparedSet: ...


def identity(engine: ChemEngine) -> dict[str, Any]:
    """What run.json and a model card record of the chemistry."""
    return {"engine": engine.name, "version": engine.version, "detail": engine.detail}


def smiles_sha256(smiles: Sequence[str]) -> str:
    """The digest a prepared set names its inputs by: SHA-256 of them joined by newlines."""
    return hashlib.sha256("\n".join(smiles).encode("utf-8")).hexdigest()


class RDKitEngine:
    """The native chemistry: ``chem.standardize``, ``chem.featurize``, ``chem.bits``,
    ``chem.descriptors``, ``rules.alerts`` and the atom count with hydrogens, on RDKit
    (imported when a set is prepared, so a run without it fails in its first stage)."""
    name = "rdkit"

    @property
    def version(self) -> str | None:
        try:
            import rdkit
        except ImportError:
            return None                     # prepare() says what to install
        return str(rdkit.__version__)

    @property
    def detail(self) -> str:
        return f"RDKit {self.version} (Python)"

    def prepare(self, smiles: Sequence[str], *, alerts: bool = True) -> PreparedSet:
        Chem = need("rdkit.Chem")
        mols, ok, errors, canonical, described, atoms, found = [], [], [], [], [], [], []
        for s in smiles:
            try:
                mol = standardize(s)
            except ChemError as exc:
                ok.append(False)
                errors.append(str(exc))
                for column in (canonical, described, atoms, found):
                    column.append(None)
                continue
            mols.append(mol)
            ok.append(True)
            errors.append(None)
            canonical.append(Chem.MolToSmiles(mol))
            described.append(descriptors(mol))
            atoms.append(Chem.AddHs(mol).GetNumAtoms())
            found.append(tuple((a["catalog"], a["alert"]) for a in find_alerts(mol))
                         if alerts else None)
        kept = [d for d in described if d is not None]
        return PreparedSet(smiles=tuple(smiles), ok=np.array(ok, dtype=bool),
                           errors=tuple(errors), canonical=tuple(canonical),
                           features=feature_rows(mols, kept), bits=bits(mols),
                           descriptors=tuple(described), atoms_with_h=tuple(atoms),
                           alerts=tuple(found))


def default_engine() -> ChemEngine:
    return RDKitEngine()


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _number(value: Any) -> bool:
    """A finite number (JSON's NaN and Infinity, which ``json`` reads, are not)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:                   # an integer too large for a float
        return False


def _array(path: Path, dtype: str, shape: tuple[int, int]) -> np.ndarray:
    if not path.is_file():
        raise ChemError(f"the prepared set has no {path.name} ({path.parent})")
    data = np.fromfile(path, dtype=dtype)
    if data.size != shape[0] * shape[1]:
        raise ChemError(f"{path} holds {data.size} values, not the {shape[0]} x {shape[1]} "
                        "its prepared.json says")
    return data.reshape(shape).astype(np.dtype(dtype).newbyteorder("="), copy=False)


def _not_counts(counts: np.ndarray, *, rows: int = 1024) -> np.ndarray:
    """The rows of fingerprint counts that are not ``chem.featurize``'s, whole numbers
    clipped at 255 (a NaN, which the models would read as missing, is not one), checked a
    block of rows at a time so that the check's temporaries stay small."""
    bad = np.zeros(len(counts), dtype=bool)
    for s in range(0, len(counts), rows):
        c = counts[s:s + rows]
        bad[s:s + rows] = ~((c >= 0) & (c <= 255) & (c == np.round(c))).all(axis=1)
    return bad


def _read_prepared(directory: Path) -> dict[str, Any]:
    path = directory / "prepared.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ChemError(f"{path} cannot be read: {exc}") from exc
    engine = doc.get("engine") if isinstance(doc, dict) else None
    if not isinstance(doc, dict) or doc.get("schema") != PREPARED_SCHEMA:
        raise ChemError(f"{path} is not a prepared set ({PREPARED_SCHEMA})")
    if not (isinstance(engine, dict) and all(_text(engine.get(k))
                                             for k in ("name", "version", "detail"))):
        raise ChemError(f"{path} does not name its engine (name, version, detail)")
    rows = doc.get("rows")
    if not (_count(doc.get("n")) and isinstance(rows, list) and len(rows) == doc["n"]
            and _text(doc.get("smiles_sha256"))):
        raise ChemError(f"{path} needs n, smiles_sha256 and one row per molecule")
    return doc


def _row_problem(row: Any, alerts: bool) -> str | None:
    if not isinstance(row, dict) or not isinstance(row.get("ok"), bool):
        return "it is not a row with ok true or false"
    if row.get("need") is not None and not _text(row.get("need")):
        return "its need is not a name"
    if not row["ok"]:
        return None if _text(row.get("error")) else "it was not prepared and says no reason"
    d = row.get("descriptors")
    if not (isinstance(d, dict) and set(d) == set(_DESCRIBED)
            and all(v is None or _number(v) for v in d.values())):
        return (f"its descriptors are not the {len(_DESCRIBED)} of chem.descriptors (finite "
                "numbers, null where not a number)")
    # the empty SMILES is a molecule to RDKit, with no atoms and "" for its canonical SMILES
    if not isinstance(row.get("canonical"), str) or not _count(row.get("atoms_with_h")):
        return "it has no canonical SMILES or no atom count"
    found = row.get("alerts")
    if found is None:
        return "its structural alerts were not computed" if alerts else None
    if not (isinstance(found, list) and all(
            isinstance(a, list) and len(a) == 2 and all(isinstance(t, str) for t in a)
            for a in found)):
        return "its alerts are not [catalog, description] pairs"
    return None


class PrecomputedEngine:
    """The chemistry another engine prepared and wrote (format: the module docstring), in
    ``directory`` or in its subdirectories, one set each. ``prepare`` gives back the set
    whose inputs are exactly ``smiles`` in that order (``smiles_sha256``), or, when no one
    set is, the sets whose inputs follow one another to make exactly ``smiles`` (a build
    prepares an endpoint's ``train_val`` SMILES followed by its ``test`` SMILES as one list,
    and a browser writes the two splits as two sets). Anything else is refused: other
    molecules, rows that do not agree with the arrays (a feature row whose fingerprint
    counts are not counts or do not give its bits, or whose descriptor columns are not its
    descriptors), alerts asked for and not computed."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        if not self.directory.is_dir():
            raise ChemError(f"{self.directory} is not a directory of prepared chemistry")
        dirs = ([self.directory] if (self.directory / "prepared.json").is_file()
                else sorted(p for p in self.directory.iterdir()
                            if (p / "prepared.json").is_file()))
        if not dirs:
            raise ChemError(f"{self.directory} holds no prepared set (prepared.json)")
        self._sets: dict[str, tuple[Path, dict[str, Any]]] = {}
        engines = set()
        for d in dirs:
            doc = _read_prepared(d)
            # endpoints that share their molecules share a set; the first, in name order
            self._sets.setdefault(doc["smiles_sha256"], (d, doc))
            engines.add(tuple(doc["engine"][k] for k in ("name", "version", "detail")))
        if len(engines) > 1:
            raise ChemError(f"the prepared sets in {self.directory} come from different "
                            f"engines: {sorted(engines)}")
        self.name, self.version, self.detail = engines.pop()

    def prepare(self, smiles: Sequence[str], *, alerts: bool = True) -> PreparedSet:
        smiles = tuple(smiles)
        digest = smiles_sha256(smiles)
        if digest in self._sets:
            return self._one(digest, smiles, alerts)
        pieces = self._pieces(smiles)
        if pieces is None:
            raise ChemError(f"no prepared set in {self.directory} is for these {len(smiles)} "
                            f"molecules (SHA-256 {digest[:12]} of their SMILES); prepare them "
                            "again")
        parts = [self._one(d, smiles[at:at + n], alerts) for at, n, d in pieces]
        return PreparedSet(
            smiles=smiles, ok=np.concatenate([p.ok for p in parts]),
            errors=sum((p.errors for p in parts), ()),
            canonical=sum((p.canonical for p in parts), ()),
            features=np.concatenate([p.features for p in parts]),
            bits=np.concatenate([p.bits for p in parts]),
            descriptors=sum((p.descriptors for p in parts), ()),
            atoms_with_h=sum((p.atoms_with_h for p in parts), ()),
            alerts=sum((p.alerts for p in parts), ()), needs=sum((p.needs for p in parts), ()))

    def _pieces(self, smiles: tuple[str, ...]) -> list[tuple[int, int, str]] | None:
        """``(start, length, digest)`` of two or more sets whose inputs, one after another,
        are exactly ``smiles``, or None. Longer sets are tried first; a set of no molecules
        is never a piece."""
        lengths = sorted({doc["n"] for _, doc in self._sets.values() if doc["n"]}, reverse=True)
        dead: set[int] = set()

        def walk(at: int) -> list[tuple[int, int, str]] | None:
            if at == len(smiles):
                return []
            if at in dead:
                return None
            for n in lengths:
                if at + n > len(smiles) or (at == 0 and n == len(smiles)):
                    continue
                digest = smiles_sha256(smiles[at:at + n])
                if digest in self._sets:
                    rest = walk(at + n)
                    if rest is not None:
                        return [(at, n, digest), *rest]
            dead.add(at)
            return None
        return walk(0) if smiles else None

    def _one(self, digest: str, smiles: tuple[str, ...], alerts: bool) -> PreparedSet:
        path, doc = self._sets[digest]
        if doc["n"] != len(smiles):
            raise ChemError(f"{path / 'prepared.json'} says {doc['n']} molecules for "
                            f"{len(smiles)} SMILES")
        rows = doc["rows"]
        for i, row in enumerate(rows):
            problem = _row_problem(row, alerts)
            if problem:
                raise ChemError(f"{path / 'prepared.json'}: row {i} ({smiles[i]!r}): {problem}")
        ok = np.array([r["ok"] for r in rows], dtype=bool)
        n_ok = int(ok.sum())
        features = _array(path / "features.f32", "<f4", (n_ok, FEATURES))
        packed = _array(path / "bits.u8", "u1", (n_ok, _BYTES))
        described = tuple({k: float("nan") if r["descriptors"][k] is None
                           else float(r["descriptors"][k]) for k in _DESCRIBED}
                          if r["ok"] else None for r in rows)
        kept = [d for d in described if d is not None]
        tail = np.nan_to_num(np.array([[d[k] for k in _DESCRIBED] for d in kept],
                                      dtype=np.float32).reshape(n_ok, len(_DESCRIBED)),
                             nan=0.0, posinf=0.0, neginf=0.0)
        for what, bad in (
                ("its fingerprint counts are not whole numbers from 0 to 255",
                 _not_counts(features[:, :FP_BITS])),
                ("its fingerprint counts do not give its bits",
                 ~(np.packbits(features[:, :FP_BITS] > 0, axis=1) == packed).all(axis=1)),
                ("its descriptor columns are not its descriptors",
                 ~(features[:, FP_BITS:] == tail).all(axis=1))):
            if bad.any():
                at = int(np.flatnonzero(ok)[np.flatnonzero(bad)[0]])
                raise ChemError(f"{path}: the feature row of row {at} ({smiles[at]!r}) does not "
                                f"agree with the set: {what}")
        return PreparedSet(
            smiles=smiles, ok=ok,
            errors=tuple(None if r["ok"] else r["error"] for r in rows),
            canonical=tuple(r["canonical"] if r["ok"] else None for r in rows),
            features=features, bits=packed, descriptors=described,
            atoms_with_h=tuple(r["atoms_with_h"] if r["ok"] else None for r in rows),
            alerts=tuple(tuple((c, a) for c, a in r["alerts"]) if r["ok"] and alerts else None
                         for r in rows),
            needs=tuple(r.get("need") for r in rows))


def write_prepared(prepared: PreparedSet, engine: ChemEngine, directory: str | Path) -> Path:
    """``prepared`` in the on-disk format ``PrecomputedEngine`` reads."""
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(len(prepared.smiles)):
        row: dict[str, Any] = {"ok": bool(prepared.ok[i]), "error": prepared.errors[i]}
        d = prepared.descriptors[i]
        row.update({
            "canonical": prepared.canonical[i],
            "descriptors": None if d is None else {
                k: (v if np.isfinite(v) else None) for k, v in d.items()},
            "atoms_with_h": prepared.atoms_with_h[i],
            "alerts": (None if prepared.alerts[i] is None
                       else [list(a) for a in prepared.alerts[i]])})
        if prepared.needs[i] is not None:
            row["need"] = prepared.needs[i]
        rows.append(row)
    doc = {"schema": PREPARED_SCHEMA, "engine": {"name": engine.name, "version": engine.version,
                                                 "detail": engine.detail},
           "n": len(prepared.smiles), "smiles_sha256": smiles_sha256(prepared.smiles),
           "rows": rows}
    (out / "features.f32").write_bytes(prepared.features.astype("<f4").tobytes())
    (out / "bits.u8").write_bytes(np.ascontiguousarray(prepared.bits).tobytes())
    (out / "prepared.json").write_text(json.dumps(doc, allow_nan=False), encoding="utf-8")
    return out


def sample_queries(n: int, *, seed: int = 0) -> np.ndarray:
    """The queries whose similarity is recomputed in full on the CPU: every one when there
    are at most 32, else the first, the last and 8 more chosen by a fixed seed."""
    if n <= 32:
        return np.arange(n)
    rng = np.random.default_rng(seed)
    more = rng.choice(np.arange(1, n - 1), size=8, replace=False)
    return np.unique(np.concatenate([[0, n - 1], more]))


class PrecomputedSimilarity:
    """The applicability-domain search as a browser computed it (format: the module
    docstring), used only when it holds up. For each endpoint it is called for, the file's
    answer must be for these queries' count and these references (their digest); every
    nearest index must be a reference, and every intersection and union those of the query
    and that reference (which bounds the intersection by the smaller popcount and makes
    the union the two popcounts less it); and for the ``sample_queries`` the CPU's exact
    search over every reference must give the same index, intersection and union. The
    first thing that does not hold rejects the file: that call and every later one is
    answered by the CPU, and ``record()`` says why, so that the pipeline recomputes the
    endpoints already answered as well. Without ``similarity.json`` every call is answered
    by the CPU, as if no similarity had been given."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.offered: str | None = None
        self.rejected: str | None = None
        self.checked: dict[str, int] = {}
        self._doc: dict[str, Any] | None = None
        path = self.directory / "similarity.json"
        if not path.is_file():
            return
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.rejected = f"similarity.json cannot be read ({exc})"
            return
        if not (isinstance(doc, dict) and doc.get("schema") == SIMILARITY_SCHEMA
                and _text(doc.get("backend")) and isinstance(doc.get("endpoints"), dict)):
            self.rejected = f"similarity.json is not {SIMILARITY_SCHEMA} with a backend"
            return
        self.offered = doc["backend"]
        self._doc = doc

    def __call__(self, endpoint: str, query_bits: np.ndarray, ref_bits: np.ndarray
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if self._doc is None or self.rejected:
            return tanimoto_best(query_bits, ref_bits)
        try:
            found = self._checked(endpoint, np.asarray(query_bits), np.asarray(ref_bits))
        except ChemError as exc:
            self.rejected = f"{endpoint}: {exc}"
            return tanimoto_best(query_bits, ref_bits)
        return found

    def _checked(self, endpoint: str, query: np.ndarray, ref: np.ndarray
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        entry = self._doc["endpoints"].get(endpoint)
        if not isinstance(entry, dict):
            raise ChemError("no similarities for this endpoint")
        n_q, n_r = len(query), len(ref)
        if entry.get("n_query") != n_q or entry.get("n_ref") != n_r:
            raise ChemError(f"computed for {entry.get('n_query')} queries x {entry.get('n_ref')} "
                            f"references, not {n_q} x {n_r}")
        if entry.get("refs_sha256") != hashlib.sha256(np.ascontiguousarray(ref).tobytes()
                                                      ).hexdigest():
            raise ChemError("computed against other reference fingerprints (refs_sha256)")
        path = self.directory / f"{endpoint}.u32"
        if not path.is_file():
            raise ChemError(f"{path.name} is missing")
        data = np.fromfile(path, dtype="<u4")
        if data.size != n_q * 3:
            raise ChemError(f"{path.name} holds {data.size} values, not {n_q} x 3")
        found = data.reshape(n_q, 3).astype(np.int64)
        # bounded before the index becomes the platform's int, which is 32-bit in a browser's
        # Python: 0xFFFFFFFF there would read as -1, the last reference
        outside = found[:, 0] >= n_r
        if outside.any():
            raise ChemError(f"query {int(np.argmax(outside))}: its nearest index is not a "
                            "reference")
        arg, inter, union = found[:, 0].astype(int), found[:, 1], found[:, 2]
        nearest = ref[arg]
        q_pop = np.bitwise_count(query).sum(axis=1, dtype=np.int64)
        r_pop = np.bitwise_count(nearest).sum(axis=1, dtype=np.int64)
        common = np.bitwise_count(query & nearest).sum(axis=1, dtype=np.int64)
        bad = (inter != common) | (union != q_pop + r_pop - common)
        if bad.any():
            raise ChemError(f"query {int(np.argmax(bad))}: its intersection and union with its "
                            "nearest reference are not those of the fingerprints")
        sample = sample_queries(n_q)
        if len(sample):
            exact = tanimoto_best(query[sample], ref)
            for i, (a, c, u) in enumerate(zip(*exact)):
                q = int(sample[i])
                if (arg[q], inter[q], union[q]) != (a, c, u):
                    raise ChemError(f"query {q}: the exact search gives reference {a} "
                                    f"({c}/{u}), not {arg[q]} ({inter[q]}/{union[q]})")
        self.checked[endpoint] = int(len(sample))
        return arg, inter, union

    def record(self) -> dict[str, Any]:
        """What run.json keeps: the backend whose similarities the run used, the queries
        recomputed on the CPU per endpoint, and why the offered ones were not used."""
        if self.rejected:
            return {"backend": "cpu", "checked": dict(self.checked) or None,
                    "rejected": f"{self.offered or 'the browser'}: {self.rejected}"}
        if self._doc is None:
            return dict(CPU_SIMILARITY)
        return {"backend": self.offered, "checked": dict(self.checked), "rejected": None}


def write_similarity(directory: str | Path, backend: str,
                     results: Mapping[str, tuple[np.ndarray, np.ndarray, tuple[np.ndarray, ...]]]
                     ) -> Path:
    """Similarities in the on-disk format ``PrecomputedSimilarity`` reads: ``results`` maps
    an endpoint to ``(query_bits, ref_bits, (arg, inter, union))``."""
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    endpoints = {}
    for endpoint, (query, ref, (arg, inter, union)) in results.items():
        table = np.stack([np.asarray(arg), np.asarray(inter), np.asarray(union)], axis=1)
        (out / f"{endpoint}.u32").write_bytes(table.astype("<u4").tobytes())
        endpoints[endpoint] = {
            "refs_sha256": hashlib.sha256(np.ascontiguousarray(ref).tobytes()).hexdigest(),
            "n_query": int(len(query)), "n_ref": int(len(ref))}
    (out / "similarity.json").write_text(json.dumps(
        {"schema": SIMILARITY_SCHEMA, "backend": backend, "endpoints": endpoints}),
        encoding="utf-8")
    return out
