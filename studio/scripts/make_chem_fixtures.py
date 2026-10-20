#!/usr/bin/env python3
"""The browser chemistry's RDKit data and its parity fixture (studio/web/js/scientific/chem).

The browser computes ADMET features with RDKit.js 2026.09.1 (MinimalLib) and JavaScript ports of what MinimalLib does
not expose. Everything those ports take from RDKit is written here from native RDKit 2026.09.1 and from RDKit's own
sources at the release tag, never typed in by hand, and each piece is checked against native RDKit before it is kept:

  web/js/scientific/chem/filter_catalogs.json
      the FilterCatalog entries ``bioagent.admet.rules.alerts`` uses (PAINS_A, PAINS_B, PAINS_C, BRENK, NIH, in that
      order), read from the release's Filters.cpp and *.in files. RDKit builds each entry with
      ``SmartsToMol(smarts, 0, mergeHs=true)``; RDKit.js's get_qmol has no mergeHs, so an entry whose pattern has
      explicit hydrogens to merge is given as native RDKit's own SMARTS of the merged pattern. An entry is kept only
      when (a) the source SMARTS, parsed as MakeFilterCatalogEntry parses it, serialises to the same bytes as the
      entry native RDKit's FilterCatalog holds, and (b) the SMARTS RDKit.js will parse gives that very pattern: the
      source text itself when parsing it without mergeHs already serialises identically, else the merged pattern's
      SMARTS, which must be a fixed point of the writer and describe every atom and bond query the same way. An entry
      that fails either is listed under "unported", and the browser refuses the alerts stage while any is listed.
  web/js/scientific/chem/rdkit_tables.json
      the periodic table (weights, outer electrons, valences, every isotope mass RDKit knows), QED's tables
      (rdkit.Chem.QED, whose installed file must be the release's), Gasteiger's parameters and MolStandardize's data (the 22 default Normalizer transforms, the acid-base pairs and charge
      corrections of the Reionizer, the Uncharger's and MetalDisconnector's patterns, Atom.cpp's early-atom table),
      each from the release sources, the parsed lists checked against native RDKit where it can build them from data.
  web/js/scientific/chem/vendor/LICENSE-RDKit.txt
      RDKit's licence at the tag, shipped beside the vendored loader.
  web/test/fixtures/chem_parity.json
      the parity corpus (the RDKit.js probe's 392 molecules, its 52 Normalizer/Reionizer/Uncharger stress molecules,
      and stress cases for the ports' refusals) with what native RDKit gives for each: bioagent.admet.chem's
      standardised canonical SMILES, the raw Morgan counts and the 25 descriptor columns of featurize()'s float32 row,
      the packed bits, the descriptors in float64, AddHs's atom count and rules.alerts' (catalog, alert) pairs. It
      also says which molecules the browser must refuse, from the native intermediates: the first step of
      chem.standardize that RDKit.js cannot reproduce exactly (MetalDisconnector or Reionizer acting in either
      Cleanup pass; an Uncharger choice that depends on Canon::rankMolAtoms), or a bond type the feature ports do not
      take. web/test/scientific/chem.test.mjs runs the browser chemistry on every molecule and requires these answers.

Needs rdkit==2026.9.1 (any other version is refused: the browser is pinned to RDKit.js 2026.9.1) and, unless
--rdkit-src names a local copy, network access to raw.githubusercontent.com for the release's sources; each source
file's SHA-256 is recorded in the output.

    python studio/scripts/make_chem_fixtures.py                       # rewrites the four files
    python studio/scripts/make_chem_fixtures.py --check               # fails when a committed file differs
    python studio/scripts/make_chem_fixtures.py --corpus a.json ...   # replaces the corpus ([{id, name, smiles, source}])

Without --corpus the corpus is the one the committed fixture lists. The native results are Linux x86-64's: QED and
BertzCT call libm's exp and log, whose last bit differs between C libraries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

sys.dont_write_bytecode = True

STUDIO = Path(__file__).resolve().parents[1]
REPO = STUDIO.parent
CHEM = STUDIO / "web" / "js" / "scientific" / "chem"
CATALOGS = CHEM / "filter_catalogs.json"
TABLES = CHEM / "rdkit_tables.json"
LICENCE = CHEM / "vendor" / "LICENSE-RDKit.txt"
FIXTURE = STUDIO / "web" / "test" / "fixtures" / "chem_parity.json"

BSH_SRC = REPO / "BioScience-Harness" / "src"
if BSH_SRC.is_dir() and str(BSH_SRC) not in sys.path:
    sys.path.insert(0, str(BSH_SRC))

RDKIT_VERSION = "2026.09.1"
RDKIT_TAG = "Release_2026_09_1"
RDKIT_RAW = "https://raw.githubusercontent.com/rdkit/rdkit/{tag}/{path}"
FC_DIR = "Code/GraphMol/FilterCatalog"
MS_DIR = "Code/GraphMol/MolStandardize"
SOURCES = (
    "license.txt",
    f"{FC_DIR}/Filters.cpp", f"{FC_DIR}/pains_a.in", f"{FC_DIR}/pains_b.in", f"{FC_DIR}/pains_c.in",
    f"{FC_DIR}/nih.in", f"{FC_DIR}/FilterCatalogEntry.cpp",
    f"{MS_DIR}/TransformCatalog/normalizations.in", f"{MS_DIR}/AcidBaseCatalog/acid_base_pairs.in",
    f"{MS_DIR}/Charge.cpp", f"{MS_DIR}/Metal.cpp", "Code/GraphMol/Atom.cpp",
    "Code/GraphMol/PartialCharges/GasteigerParams.cpp", "rdkit/Chem/QED.py",
)
# rules.alerts adds the catalogs in this order; FilterCatalog keeps the entries in it
ALERT_CATALOGS = ("PAINS_A", "PAINS_B", "PAINS_C", "BRENK", "NIH")
CATALOG_FILES = {"PAINS_A": "pains_a.in", "PAINS_B": "pains_b.in", "PAINS_C": "pains_c.in", "NIH": "nih.in",
                 "BRENK": "Filters.cpp"}
# Charge.cpp's Uncharger() initialiser
UNCHARGER_PATTERNS = ("pos_h", "pos_noh", "neg", "neg_acid")
MAX_ISOTOPE = 400
# the bond types the browser's feature ports take (Bond::BondType names); anything else is refused there
PORT_BOND_TYPES = {"SINGLE", "DOUBLE", "TRIPLE", "AROMATIC"}

REFUSE_METAL = "MetalDisconnector"
REFUSE_REIONIZER = "Reionizer"
REFUSE_RANKING = "Uncharger canonical atom ranking"
REFUSE_UNSANITISABLE = "Normalizer unsanitisable product"
REFUSE_BOND = "feature ports: bond type {}"


class FixtureError(RuntimeError):
    pass


# ----------------------------------------------------------------------------------------------- RDKit and sources

def rdkit():
    try:
        import rdkit as rd
    except ImportError as exc:
        raise FixtureError("needs rdkit==2026.9.1 (pip install rdkit==2026.9.1)") from exc
    if rd.__version__ != RDKIT_VERSION:
        raise FixtureError(f"needs rdkit {RDKIT_VERSION} (rdkit==2026.9.1), the version RDKit.js is pinned to; "
                           f"found {rd.__version__}")
    from rdkit import RDLogger
    RDLogger.DisableLog("rdApp.*")
    return rd


class Sources:
    """RDKit's sources at the release tag: from a local copy laid out as the repository is, or fetched."""

    def __init__(self, local: Path | None):
        self.local = local
        self.texts: dict[str, str] = {}

    def text(self, path: str) -> str:
        if path not in SOURCES:
            raise FixtureError(f"{path} is not one of the sources this script reads")
        if path not in self.texts:
            try:
                if self.local is not None:
                    data = (self.local / path).read_bytes()
                else:
                    url = RDKIT_RAW.format(tag=RDKIT_TAG, path=path)
                    with urllib.request.urlopen(url, timeout=60) as res:      # noqa: S310 (a fixed https URL)
                        data = res.read()
            except OSError as exc:                                            # urllib's URLError is an OSError
                where = self.local / path if self.local is not None else RDKIT_RAW.format(tag=RDKIT_TAG, path=path)
                raise FixtureError(f"RDKit's {path} at {RDKIT_TAG} could not be read from {where}: {exc}") from exc
            self.texts[path] = data.decode("utf-8")
        return self.texts[path]

    def read_all(self) -> None:
        for path in SOURCES:
            self.text(path)

    def record(self, paths: Iterable[str]) -> dict[str, Any]:
        return {"repository": "https://github.com/rdkit/rdkit", "tag": RDKIT_TAG,
                "files": {p: hashlib.sha256(self.text(p).encode("utf-8")).hexdigest() for p in sorted(paths)}}


_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|(-?\d+)|([{}(),;])|(//[^\n]*)|(/\*.*?\*/)|(\s+)|([A-Za-z_:][\w:]*)|(.)',
                    re.S)


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), s)


def _c_groups(text: str, start: int) -> list[list[Any]]:
    """The brace groups one level inside the brace opened just before ``start``: each a list of its string and
    integer values, adjacent string literals joined as C joins them."""
    groups: list[list[Any]] = []
    depth, cur, strs = 1, None, []
    for t in _TOKEN.finditer(text, start):
        s, num, punct, c1, c2, ws, _word, other = t.groups()
        if c1 or c2 or ws:
            continue
        if s is not None:
            strs.append(_unescape(s))
            continue
        if strs:
            cur.append("".join(strs))
            strs = []
        if num is not None and cur is not None:
            cur.append(int(num))
        elif punct == "{":
            depth += 1
            if depth == 2:
                cur = []
        elif punct == "}":
            depth -= 1
            if depth == 0:
                return groups
            if depth == 1:
                groups.append(cur)
                cur = None
        elif other or (punct == ";" and depth > 0):
            raise FixtureError(f"unexpected {(other or punct)!r} in a C initialiser at offset {t.start()}")
    raise FixtureError("unterminated C initialiser")


def filter_data(text: str, array: str) -> list[tuple[str, str, int, str]]:
    """``const FilterData_t <array>[] = {{name, smarts, max, comment}, ...};`` (Filters.h)."""
    m = re.search(r"const\s+FilterData_t\s+" + re.escape(array) + r"\[\]\s*=\s*\{", text)
    if not m:
        raise FixtureError(f"no FilterData_t {array}[] in the source")
    out = []
    for g in _c_groups(text, m.end()):
        if len(g) != 4 or not isinstance(g[0], str) or not isinstance(g[1], str) or not isinstance(g[2], int):
            raise FixtureError(f"{array}: an entry is not {{name, smarts, max, comment}}: {g!r}")
        out.append((g[0], g[1], g[2], g[3]))
    return out


def normalizations(text: str) -> list[tuple[str, str]]:
    m = re.search(r"defaultNormalizations\s*=\s*\{", text)
    if not m:
        raise FixtureError("no defaultNormalizations in normalizations.in")
    out = [tuple(g) for g in _c_groups(text, m.end())]
    if not all(len(g) == 2 and all(isinstance(x, str) for x in g) for g in out):
        raise FixtureError("normalizations.in: an entry is not {name, smirks}")
    return out


def acid_base_pairs(text: str) -> list[tuple[str, str, str]]:
    lit = r'std::string\(\s*"((?:[^"\\]|\\.)*)"\s*\)'
    out = [tuple(_unescape(x) for x in m.groups())
           for m in re.finditer(r"std::make_tuple\(\s*" + lit + r"\s*,\s*" + lit + r"\s*,\s*" + lit + r"\s*\)", text)]
    if not out:
        raise FixtureError("no acid-base pairs in acid_base_pairs.in")
    return out


def charge_corrections(text: str) -> list[tuple[str, str, int]]:
    m = re.search(r"std::vector<ChargeCorrection>\s+CHARGE_CORRECTIONS\s*=\s*\{", text)
    if not m:
        raise FixtureError("no CHARGE_CORRECTIONS in Charge.cpp")
    end = text.index("};", m.end())
    out = [(_unescape(a), _unescape(b), int(c)) for a, b, c in
           re.findall(r'ChargeCorrection\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*,\s*(-?\d+)\s*\)',
                      text[m.end():end])]
    if not out:
        raise FixtureError("CHARGE_CORRECTIONS has no entries")
    return out


def uncharger_patterns(text: str) -> dict[str, str]:
    m = re.search(r"Uncharger::Uncharger\(\)\s*:", text)
    if not m:
        raise FixtureError("no Uncharger() initialiser in Charge.cpp")
    out = {}
    for name in UNCHARGER_PATTERNS:
        k = re.compile(r"\b" + name + r"\(\s*SmartsToMol\(((?:\s|//[^\n]*|\"(?:[^\"\\]|\\.)*\")*)\)\)").search(
            text, m.end())
        if not k:
            raise FixtureError(f"no {name}(SmartsToMol(<literals>)) in Uncharger()")
        out[name] = "".join(_unescape(x) for x in re.findall(r'"((?:[^"\\]|\\.)*)"',
                                                             re.sub(r"//[^\n]*", "", k.group(1))))
    return out


def metal_patterns(text: str) -> dict[str, str]:
    """MetalDisconnector's two patterns with its default options (splitGrignards and splitAromaticC false)."""
    lits = lambda s: "".join(_unescape(x) for x in re.findall(r'"((?:[^"\\]|\\.)*)"', s))   # noqa: E731
    nof = re.search(r"dp_metal_nof\(\s*SmartsToMol\(((?:\s|\"(?:[^\"\\]|\\.)*\")*)\)\)", text)
    metals = re.search(r"std::string\s+metalList\s*=((?:\s|\"(?:[^\"\\]|\\.)*\")*);", text)
    nonmetals = re.search(r"std::string\s+nonMetalList\s*=((?:\s|\"(?:[^\"\\]|\\.)*\")*);", text)
    plain = re.search(r'nonMetalList\s*=\s*"\[B,C,"\s*\+\s*nonMetalList', text)
    bracket = re.search(r'metalList\s*=\s*"\["\s*\+\s*metalList', text)
    if not (nof and metals and nonmetals and plain and bracket):
        raise FixtureError("Metal.cpp's MetalDisconnector patterns are not where they were")
    return {"metal_nof": lits(nof.group(1)),
            "metal_non": "[" + lits(metals.group(1)) + "[B,C," + lits(nonmetals.group(1))}


def gasteiger_params(text: str) -> list[list[Any]]:
    """GasteigerParams.cpp's defaultParamData then additionalParamData, as GasteigerParams() reads them when given no
    data: one (element, mode, a, b, c) per line, a later line for the same (element, mode) replacing an earlier."""
    rows = []
    for name in ("defaultParamData", "additionalParamData"):
        m = re.search(r"std::string\s+" + name + r'\s*=\s*"((?:[^"\\]|\\.)*)"\s*;', text, re.S)
        if not m:
            raise FixtureError(f"no {name} in GasteigerParams.cpp")
        data = re.sub(r"\\\n", "", m.group(1)).replace("\\n", "\n")
        for line in data.split("\n"):
            parts = line.split()
            if not parts:
                continue
            if len(parts) != 5:
                raise FixtureError(f"GasteigerParams.cpp {name}: {line!r} is not 'element mode a b c'")
            rows.append([parts[0], parts[1], *(float(x) for x in parts[2:])])
    return rows


def early_atoms(text: str) -> list[int]:
    m = re.search(r"bool\s+isEarlyAtom\(int\s+atomicNum\)\s*\{\s*static\s+const\s+bool\s+table\[(\d+)\]\s*=\s*\{",
                  text)
    if not m:
        raise FixtureError("no isEarlyAtom table in Atom.cpp")
    end = text.index("};", m.end())
    flags = re.findall(r"\b(true|false)\b", re.sub(r"//[^\n]*", "", text[m.end():end]))
    if len(flags) != int(m.group(1)):
        raise FixtureError(f"isEarlyAtom: {len(flags)} entries, the table says {m.group(1)}")
    return [z for z, f in enumerate(flags) if f == "true"]


# ------------------------------------------------------------------------------------------------- filter catalogs

def _entry(FC, name: str, pattern, min_count: int, props: list[tuple[str, str]]):
    entry = FC.FilterCatalogEntry(name, FC.SmartsMatcher(name, pattern, min_count))
    for k, v in props:
        entry.SetProp(k, v)
    return entry


def _describes_alike(P, smarts: str) -> str | None:
    """None when parsing ``smarts`` (no mergeHs) gives P's atoms and bonds with the same queries, else what differs.
    P must have just been written by MolToSmarts (its _smilesAtomOutputOrder maps the written order back to P's)."""
    from rdkit import Chem
    Q = Chem.MolFromSmarts(smarts)
    if Q is None:
        return "the written SMARTS does not parse"
    if Chem.MolToSmarts(Q) != smarts:
        return "the written SMARTS is not a fixed point of MolToSmarts"
    order = list(P.GetPropsAsDict(True, True)["_smilesAtomOutputOrder"])
    if (Q.GetNumAtoms(), Q.GetNumBonds()) != (P.GetNumAtoms(), P.GetNumBonds()) or \
            sorted(order) != list(range(P.GetNumAtoms())):
        return "different atoms or bonds"
    for k, qa in enumerate(Q.GetAtoms()):
        if P.GetAtomWithIdx(order[k]).DescribeQuery() != qa.DescribeQuery():
            return f"atom {k} query differs"
    for b in Q.GetBonds():
        pb = P.GetBondBetweenAtoms(order[b.GetBeginAtomIdx()], order[b.GetEndAtomIdx()])
        if pb is None or pb.DescribeQuery() != b.DescribeQuery():
            return f"bond {b.GetIdx()} query differs"
    return None


def filter_catalogs(src: Sources) -> dict[str, Any]:
    from rdkit import Chem
    from rdkit.Chem import FilterCatalog as FC
    params = FC.FilterCatalogParams()
    for cat in ALERT_CATALOGS:
        params.AddCatalog(getattr(FC.FilterCatalogParams.FilterCatalogs, cat))
    native = FC.FilterCatalog(params)
    listed = [(cat, *e) for cat in ALERT_CATALOGS
              for e in filter_data(src.text(f"{FC_DIR}/{CATALOG_FILES[cat]}"), cat)]
    if len(listed) != native.GetNumEntries():
        raise FixtureError(f"the sources list {len(listed)} entries; native FilterCatalog holds "
                           f"{native.GetNumEntries()}")
    entries, unported = [], []
    for i, (cat, name, smarts, max_count, _comment) in enumerate(listed):
        ne = native.GetEntryWithIdx(i)
        if ne.GetDescription() != name:
            raise FixtureError(f"entry {i}: the sources say {name!r}, native FilterCatalog {ne.GetDescription()!r}")
        props = [(k, ne.GetProp(k)) for k in ne.GetPropList() if k != "description"]
        min_count = max_count + 1 if max_count else 1          # MakeFilterCatalogEntry: minCount = max + 1
        row = {"catalog": cat, "filter_set": ne.GetProp("FilterSet"), "description": name, "min_count": min_count}
        merged = Chem.MolFromSmarts(smarts, mergeHs=True)
        if merged is None or _entry(FC, name, merged, min_count, props).Serialize() != ne.Serialize():
            unported.append({**row, "source_smarts": smarts,
                             "reason": "the source SMARTS does not rebuild the native entry"})
            continue
        plain = Chem.MolFromSmarts(smarts)
        if plain is not None and _entry(FC, name, plain, min_count, props).Serialize() == ne.Serialize():
            js, how = smarts, "verbatim"
        else:
            js, how = Chem.MolToSmarts(merged), "merged"
            why = _describes_alike(merged, js)
            if why:
                unported.append({**row, "source_smarts": smarts, "reason": f"merged pattern: {why}"})
                continue
        written = Chem.MolToSmarts(Chem.MolFromSmarts(js))
        entries.append({**row, "smarts": js, "written": written, "how": how,
                        **({"source_smarts": smarts} if how == "merged" else {})})
    return {
        "schema": "tcmstudio.filter-catalogs/1",
        "rdkit": RDKIT_VERSION,
        "source": src.record(f"{FC_DIR}/{f}" for f in sorted(set(CATALOG_FILES.values())) + ["FilterCatalogEntry.cpp"]),
        "catalogs": list(ALERT_CATALOGS),
        "method": ("FilterCatalog entries of the release in FilterCatalog order. 'smarts' is what RDKit.js parses: the "
                   "source SMARTS ('verbatim', when parsing it without mergeHs rebuilds the native entry byte for "
                   "byte) or native RDKit's SMARTS of the mergeHs pattern ('merged', a writer fixed point with every "
                   "atom and bond query described alike). 'written' is MolToSmarts of that parse, which RDKit.js's "
                   "get_smarts must reproduce. An entry matches when the molecule has at least min_count unique "
                   "matches (SmartsMatcher); the alerts are (filter_set, description) in entry order, the first of "
                   "each pair kept, as rules.alerts keeps them."),
        "entries": entries,
        "unported": unported,
    }


# ---------------------------------------------------------------------------------------------------------- tables

def rdkit_tables(src: Sources, corpus: list[dict[str, Any]]) -> dict[str, Any]:
    from rdkit import Chem
    from rdkit.Chem import QED
    from rdkit.Chem.MolStandardize import rdMolStandardize as S
    pt = Chem.GetPeriodicTable()
    periodic = []
    for z in range(119):
        isotopes = {}
        if z:
            for a in range(1, MAX_ISOTOPE + 1):
                m = pt.GetMassForIsotope(z, a)
                if m > 0:
                    isotopes[str(a)] = m
        periodic.append({"z": z, "symbol": pt.GetElementSymbol(z), "weight": pt.GetAtomicWeight(z),
                         "n_outer": pt.GetNOuterElecs(z), "default_valence": pt.GetDefaultValence(z),
                         "valence_list": list(pt.GetValenceList(z)), "isotopes": isotopes})
    qed_py = src.text("rdkit/Chem/QED.py")
    if Path(QED.__file__).read_bytes() != qed_py.encode("utf-8"):
        raise FixtureError(f"the installed {QED.__file__} is not the release's rdkit/Chem/QED.py")
    aliphatic = re.search(r"AliphaticRings\s*=\s*Chem\.MolFromSmarts\('([^']+)'\)", qed_py)
    if not aliphatic:
        raise FixtureError("QED.py's AliphaticRings pattern is not where it was")
    qed = {"acceptors": list(QED.AcceptorSmarts), "alerts": list(QED.StructuralAlertSmarts),
           "aliphatic_rings": aliphatic.group(1),
           "ads": {k: dict(v._asdict()) for k, v in QED.adsParameters.items()},
           "properties": list(QED.QEDproperties._fields), "weight_mean": list(QED.WEIGHT_MEAN)}

    norms = normalizations(src.text(f"{MS_DIR}/TransformCatalog/normalizations.in"))
    pairs = acid_base_pairs(src.text(f"{MS_DIR}/AcidBaseCatalog/acid_base_pairs.in"))
    charge_cpp = src.text(f"{MS_DIR}/Charge.cpp")
    ccs = charge_corrections(charge_cpp)
    unch = uncharger_patterns(charge_cpp)
    metal = metal_patterns(src.text(f"{MS_DIR}/Metal.cpp"))
    early = early_atoms(src.text("Code/GraphMol/Atom.cpp"))
    gasteiger = gasteiger_params(src.text("Code/GraphMol/PartialCharges/GasteigerParams.cpp"))
    _check_molstandardize(S, norms, pairs, ccs, metal, corpus)
    return {
        "schema": "tcmstudio.rdkit-tables/1",
        "rdkit": RDKIT_VERSION,
        "source": src.record(["rdkit/Chem/QED.py", f"{MS_DIR}/TransformCatalog/normalizations.in",
                              f"{MS_DIR}/AcidBaseCatalog/acid_base_pairs.in", f"{MS_DIR}/Charge.cpp",
                              f"{MS_DIR}/Metal.cpp", "Code/GraphMol/Atom.cpp",
                              "Code/GraphMol/PartialCharges/GasteigerParams.cpp"]),
        "periodic": periodic,
        "qed": qed,
        "gasteiger": gasteiger,
        "molstandardize": {"normalizations": [list(x) for x in norms], "acid_base_pairs": [list(x) for x in pairs],
                           "charge_corrections": [list(x) for x in ccs], **metal, "uncharger": unch,
                           "early_atoms": early},
    }


def _check_molstandardize(S, norms, pairs, ccs, metal, corpus) -> None:
    """The parsed MolStandardize data does what native RDKit's defaults do, on every corpus molecule."""
    from rdkit import Chem
    custom_norm = S.NormalizerFromData("\n".join(f"{n}\t{s}" for n, s in norms) + "\n", S.CleanupParameters())
    custom_reion = S.ReionizerFromData("\n".join("\t".join(p) for p in pairs) + "\n",
                                       [S.ChargeCorrection(n, s, c) for n, s, c in ccs])
    md = S.MetalDisconnector()
    for key, native in (("metal_nof", md.MetalNof), ("metal_non", md.MetalNon)):
        if Chem.MolToSmarts(Chem.MolFromSmarts(metal[key])) != Chem.MolToSmarts(Chem.MolFromSmarts(native)):
            raise FixtureError(f"Metal.cpp's {key} is not native MetalDisconnector's ({native})")
    default_norm, default_reion = S.Normalizer(), S.Reionizer()
    for rec in corpus:
        m = Chem.MolFromSmiles(rec["smiles"])
        if m is None:
            continue
        a, b = Chem.MolToSmiles(default_norm.normalize(m)), Chem.MolToSmiles(custom_norm.normalize(m))
        if a != b:
            raise FixtureError(f"{rec['id']}: the parsed normalizations give {b}, the default Normalizer {a}")
        a, b = Chem.MolToSmiles(default_reion.reionize(m)), Chem.MolToSmiles(custom_reion.reionize(m))
        if a != b:
            raise FixtureError(f"{rec['id']}: the parsed acid-base pairs give {b}, the default Reionizer {a}")


# --------------------------------------------------------------------------------------- native results per molecule

class Native:
    """chem.standardize step by step, to say where RDKit.js would have to refuse, then the native answers."""

    def __init__(self, tables: dict[str, Any]):
        from rdkit import Chem
        from rdkit.Chem.MolStandardize import rdMolStandardize as S
        self.Chem, self.S = Chem, S
        ms = tables["molstandardize"]
        md = S.MetalDisconnector()
        self.metal = [Chem.MolFromSmarts(md.MetalNof), Chem.MolFromSmarts(md.MetalNon)]
        self.ccs = [Chem.MolFromSmarts(s) for _, s, _ in ms["charge_corrections"]]
        self.acids = [Chem.MolFromSmarts(a) for _, a, _ in ms["acid_base_pairs"]]
        self.bases = [Chem.MolFromSmarts(b) for _, _, b in ms["acid_base_pairs"]]
        self.unch = {k: Chem.MolFromSmarts(v) for k, v in ms["uncharger"].items()}
        self.early = set(ms["early_atoms"])
        from rdkit.Chem import rdChemReactions
        self.transforms = [rdChemReactions.ReactionFromSmarts(smirks) for _, smirks in ms["normalizations"]]
        self.sanitize_ops = Chem.SANITIZE_ALL ^ Chem.SANITIZE_CLEANUP ^ Chem.SANITIZE_PROPERTIES

    # Cleanup (MolStandardize.cpp cleanupInPlace): removeHs, MetalDisconnector, Normalizer, Reionizer, stereo
    def cleanup(self, mol) -> tuple[Any, str | None, bool]:
        Chem, S = self.Chem, self.S
        work = Chem.RWMol(Chem.RemoveHs(mol))
        if any(work.HasSubstructMatch(q) for q in self.metal):
            return None, REFUSE_METAL, False
        before = Chem.MolToSmiles(work)
        if self.normalize_fails_sanitize(work):
            return None, REFUSE_UNSANITISABLE, True
        S.NormalizeInPlace(work)
        normalized = Chem.MolToSmiles(work) != before
        if self.reionizer_would_act(work):
            return None, REFUSE_REIONIZER, normalized
        S.ReionizeInPlace(work)
        Chem.AssignStereochemistry(work, cleanIt=True, force=True)
        if Chem.MolToSmiles(work) != Chem.MolToSmiles(S.Cleanup(mol)):
            raise FixtureError(f"the Cleanup steps replayed give {Chem.MolToSmiles(work)}, Cleanup "
                               f"{Chem.MolToSmiles(S.Cleanup(mol))}")
        return work, None, normalized

    def normalize_fails_sanitize(self, mol) -> bool:
        """Normalize.cpp normalizeInPlace replayed on a copy of ``mol``: whether a transform it applies gives a product
        that does not sanitise (it logs "FAILED sanitizeMol." and goes on with the unsanitised molecule). The replay
        must end where NormalizeInPlace does."""
        Chem, S = self.Chem, self.S
        work = Chem.RWMol(mol)
        Chem.GetSymmSSSR(work)          # normalizeInPlace symmetrises the ring information first
        failed = False
        for _ in range(200):            # Normalizer's MAX_RESTARTS
            for rxn in self.transforms:
                if rxn.RunReactantInPlace(work, removeUnmatchedAtoms=False):
                    if Chem.SanitizeMol(work, sanitizeOps=self.sanitize_ops, catchErrors=True) != Chem.SANITIZE_NONE:
                        failed = True
                    break
            else:
                break
        ref = Chem.RWMol(mol)
        S.NormalizeInPlace(ref)
        if Chem.MolToSmiles(work) != Chem.MolToSmiles(ref):
            raise FixtureError(f"the Normalizer replayed gives {Chem.MolToSmiles(work)}, NormalizeInPlace "
                               f"{Chem.MolToSmiles(ref)}")
        return failed

    def reionizer_would_act(self, mol) -> bool:
        """Charge.cpp reionizeInPlace changes the molecule (or gives up mid-way) exactly when a charge correction
        matches or the strongest protonated acid is stronger than the weakest ionised one."""
        if any(mol.HasSubstructMatch(q) for q in self.ccs):
            return True
        sp = next((k for k, q in enumerate(self.acids) if mol.HasSubstructMatch(q)), None)
        wi = next((k for k in range(len(self.bases) - 1, -1, -1) if mol.HasSubstructMatch(self.bases[k])), None)
        return sp is not None and wi is not None and sp < wi

    def ranking_matters(self, mol) -> bool:
        """Whether Uncharger::unchargeInPlace's result depends on Canon::rankMolAtoms: it ranks the atoms only when
        needsNeutralization, and the ranks then choose which acid anions keep their charge against a positive
        neighbour they share, and which negative sites are neutralised when only some of them are."""
        first = lambda q: [m[0] for m in mol.GetSubstructMatches(q, maxMatches=1000)]   # noqa: E731
        atom = mol.GetAtomWithIdx
        p, q, n, a = (first(self.unch[k]) for k in UNCHARGER_PATTERNS)
        early = lambda z: z in self.early                                                 # noqa: E731

        def h_delta_pos(at):
            ce = at.GetAtomicNum() == 6 or early(at.GetAtomicNum())
            return 0 if (not ce and not at.GetTotalNumHs()) else (1 if ce else -1)

        def h_delta_neg(at):
            e = early(at.GetAtomicNum())
            return 0 if (e and not at.GetTotalNumHs()) else (-1 if e else 1)

        q_matched = sum(atom(i).GetFormalCharge() for i in q) + \
            sum(atom(i).GetFormalCharge() for i in p if h_delta_pos(atom(i)) == 0)
        if not (q_matched > 0 and (n or a)):
            return False
        groups: dict[int, list[int]] = {}
        for i in a:
            for nb in atom(i).GetNeighbors():
                if nb.GetFormalCharge() > 0:
                    groups.setdefault(nb.GetIdx(), []).append(i)
                    break
        if any(len(g) > atom(k).GetFormalCharge() for k, g in groups.items()):
            return True
        skipped = {i for g in groups.values() for i in g}
        acids = set(a)
        negs = [i for i in n if i not in acids] + [i for i in a if i not in skipped]
        surplus = len(negs) - q_matched
        removable = sum(1 for i in negs if h_delta_neg(atom(i)) != 0)
        return 0 < surplus < removable

    def refusal(self, smiles: str) -> tuple[str | None, bool]:
        """The first step the browser chemistry must refuse for ``smiles`` (None: none), and whether a Normalizer
        transform applied."""
        Chem, S = self.Chem, self.S
        mol = Chem.MolFromSmiles(smiles)
        normalized = False
        for _ in range(2):              # Cleanup, then FragmentParent's own Cleanup
            mol, why, changed = self.cleanup(mol)
            normalized |= changed
            if why:
                return why, normalized
        parent = S.LargestFragmentChooser().choose(mol)
        if self.ranking_matters(parent):
            return REFUSE_RANKING, normalized
        return None, normalized


def _f32_hex(values: Iterable[float]) -> str:
    return b"".join(struct.pack(">f", float(v)) for v in values).hex()


def _float(v: float) -> float | None:
    return None if v != v else float(v)


def native_record(rec: dict[str, Any], nat: Native) -> dict[str, Any]:
    import numpy as np
    from bioagent.admet import chem, rules
    Chem = nat.Chem
    out = {k: rec[k] for k in ("id", "name", "smiles", "source")}
    try:
        std = chem.standardize(rec["smiles"])
    except chem.ChemError as exc:
        return {**out, "expect": "error", "error": str(exc)}
    refusal, normalized = nat.refusal(rec["smiles"])
    if refusal is None:
        odd = sorted({str(b.GetBondType()) for b in std.GetBonds()} - PORT_BOND_TYPES)
        if odd:
            refusal = REFUSE_BOND.format(odd[0])
    counts = chem._morgan(std).GetCountFingerprintAsNumPy(std)
    row = chem.featurize([std])[0]
    desc = chem.descriptors(std)
    if not np.array_equal(row[:chem.FP_BITS], np.minimum(counts, 255).astype(np.float32)):
        raise FixtureError(f"{rec['id']}: featurize's counts are not the clipped Morgan counts")
    out.update({
        "expect": "refused" if refusal else "ok",
        **({"refusal": refusal} if refusal else {}),
        "normalized": normalized,
        "canonical": Chem.MolToSmiles(std),
        "counts": {str(int(i)): int(counts[i]) for i in np.flatnonzero(counts)},
        "row_tail_f32": _f32_hex(row[chem.FP_BITS:]),
        "bits": chem.bits([std])[0].tobytes().hex(),
        "descriptors": {k: _float(desc[k]) for k in (*chem.DESCRIPTORS, "FormalCharge")},
        "atoms_with_h": Chem.AddHs(std).GetNumAtoms(),
        "alerts": [[a["catalog"], a["alert"]] for a in rules.alerts(std)],
    })
    return out


def fixture(corpus: list[dict[str, Any]], tables: dict[str, Any]) -> dict[str, Any]:
    from rdkit import Chem
    ids = Counter(r["id"] for r in corpus)
    if any(c > 1 for c in ids.values()):
        raise FixtureError(f"duplicate corpus ids: {sorted(k for k, c in ids.items() if c > 1)}")
    nat = Native(tables)
    molecules = [native_record(r, nat) for r in corpus]
    return {
        "schema": "tcmstudio.chem-parity/1",
        "native": {"rdkit": RDKIT_VERSION, "legacy_stereo_perception": bool(Chem.GetUseLegacyStereoPerception()),
                   "reference": "bioagent.admet.chem (standardize, featurize, bits, descriptors) and rules.alerts"},
        "counts": dict(sorted(Counter(m["expect"] for m in molecules).items())),
        "refusals": dict(sorted(Counter(m["refusal"] for m in molecules if m.get("refusal")).items())),
        "molecules": molecules,
    }


# ----------------------------------------------------------------------------------------------------------- output

def _json_lines(doc: dict[str, Any], listed: str) -> str:
    """``doc`` as JSON with one element of ``doc[listed]`` per line, so a diff shows which entry changed."""
    head = {k: v for k, v in doc.items() if k != listed}
    parts = [f" {json.dumps(k)}: {json.dumps(v, ensure_ascii=False, sort_keys=False)}" for k, v in head.items()]
    items = ",\n".join("  " + json.dumps(x, ensure_ascii=False, separators=(",", ":")) for x in doc[listed])
    parts.append(f' {json.dumps(listed)}: [\n{items}\n ]')
    return "{\n" + ",\n".join(parts) + "\n}\n"


def outputs(src: Sources, corpus: list[dict[str, Any]]) -> dict[Path, str]:
    rdkit()
    src.read_all()
    tables = rdkit_tables(src, corpus)
    return {
        CATALOGS: _json_lines(filter_catalogs(src), "entries"),
        TABLES: json.dumps(tables, ensure_ascii=False, separators=(",", ":")) + "\n",
        LICENCE: src.text("license.txt"),
        FIXTURE: _json_lines(fixture(corpus, tables), "molecules"),
    }


def read_corpus(paths: list[str] | None) -> list[dict[str, Any]]:
    if paths:
        rows = [r for p in paths for r in json.loads(Path(p).read_text(encoding="utf-8"))]
    elif FIXTURE.is_file():
        rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["molecules"]
    else:
        raise FixtureError(f"no corpus: {FIXTURE} does not exist yet; give --corpus")
    return [{k: r[k] for k in ("id", "name", "smiles", "source")} for r in rows]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="fail when a committed file differs")
    ap.add_argument("--corpus", nargs="+", metavar="JSON", help="replace the corpus with these lists")
    ap.add_argument("--rdkit-src", metavar="DIR", help=f"RDKit sources at {RDKIT_TAG}, laid out as the repository")
    args = ap.parse_args(argv)
    if args.check and args.corpus:
        ap.error("--check compares with the committed corpus; it takes no --corpus")
    try:
        files = outputs(Sources(Path(args.rdkit_src) if args.rdkit_src else None), read_corpus(args.corpus))
    except FixtureError as exc:
        print(f"make_chem_fixtures: {exc}", file=sys.stderr)
        return 2
    stale = 0
    for path, text in files.items():
        if args.check:
            current = path.read_text(encoding="utf-8") if path.is_file() else None
            if current != text:
                stale += 1
                print(f"out of date: {path.relative_to(REPO)}", file=sys.stderr)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(REPO)}")
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
