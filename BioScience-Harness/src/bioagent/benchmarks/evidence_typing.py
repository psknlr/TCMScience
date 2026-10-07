"""How accurately the evidence-typing rules read real abstracts.

``bioagent.literature.evidence.read_fields`` types a document by fixed rules: its study
design, and the population, comparator and outcome it covers. A claim check downstream is
only as good as that design: a cohort read as a trial licenses efficacy, and a correct type
check on a wrongly labelled study still reaches the wrong conclusion. So the rules are
measured here on their own, on real literature, apart from the type system they feed.

* **The sample** (``benchmarks/evidence_typing/sample.jsonl``) is real abstracts from
  Europe PMC, restricted to open-access articles under CC BY or CC0, each with its
  attribution and licence. It is stratified by design, with negatives (narrative reviews,
  editorials, trial protocols) and a Chinese stratum.
* **The reference** is PubMed's publication types, plus MeSH ``Animals``/``Humans`` and
  cell-culture headings for the two designs PubMed has no publication type for
  (:func:`reference_for`). It is derived from the PubMed metadata stored with each record,
  and re-derived on load, so a label cannot be edited by hand. It is imperfect: indexers
  assign the types, some are missing, and a secondary analysis of a trial may carry the
  trial's type. The benchmark measures agreement with that reference, nothing better.
* **The split** is fixed by a seeded hash of the PMID (:func:`split_of`): a third is dev,
  where the rules may be tuned, and two thirds are test, which is only reported. It is
  re-derived on load as well.

What is reported, per split: precision, recall and abstention per design with Wilson 95%
intervals and the confusion matrix; how often each negative is typed, as a trial or a
review in particular; which wrong readings would license a claim the study cannot; how
often population, comparator and outcome are filled, and how many of a random draw of the
filled ones a reader found correct. Precision is for this stratified mix, not for
literature at large: where trials are rarer than here, a rule that sometimes reads a review
as a trial is right less often than it is here.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

# The ablation's interval, so the two benchmarks report the same computation.
from .ablation import _wilson

__all__ = ["DESIGNS", "LICENCES", "MANUAL_SEED", "NEGATIVES", "SAMPLE_SEED", "SPLIT_SEED",
           "VALUE_FIELDS", "Label", "Outcome", "Record", "compare", "draw_key",
           "draw_manual_check", "load_rules", "load_sample", "manual_check_summary",
           "reference_for", "render_markdown", "results", "rules_digest", "run",
           "sample_digest", "split_of", "summarise"]

#: The designs the rules can read, in the order they are reported.
DESIGNS = ("systematic_review", "randomized_trial", "observational", "case_report",
           "animal", "in_vitro")
#: Kinds of record that report no study of their own, or no results yet. Any design read
#: from one is wrong: a protocol typed as a trial would license efficacy from no data.
NEGATIVES = ("narrative_review", "editorial", "protocol")
#: The fields with no reference: the benchmark reports how often a rule fills them, and a
#: reader checks a random draw of the filled ones.
VALUE_FIELDS = ("population", "comparator", "outcome")
#: The only licences a committed record may carry (SPDX, and ``CC-BY`` for an article that
#: names the Creative Commons Attribution licence but no version: its own statement is kept
#: with the record, and no version is made up for it). NC, ND and SA variants are refused,
#: and so is a record whose licence is unstated.
LICENCES = ("CC-BY", "CC-BY-2.0", "CC-BY-2.5", "CC-BY-3.0", "CC-BY-4.0", "CC0-1.0")

#: Seeds of the hashes. Changing one draws another sample, split or manual check.
SAMPLE_SEED = "evidence-typing/sample/v1"
SPLIT_SEED = "evidence-typing/split/v1"
MANUAL_SEED = "evidence-typing/manual-check/v1"

#: PubMed publication types that state a design, and the design each states.
DESIGN_TYPES: Mapping[str, str] = {
    "Systematic Review": "systematic_review", "Meta-Analysis": "systematic_review",
    "Network Meta-Analysis": "systematic_review",
    "Randomized Controlled Trial": "randomized_trial",
    "Observational Study": "observational", "Case Reports": "case_report",
}
#: Publication types that make a record a negative. A protocol stays one when it is also
#: typed as a trial: it reports no results either way.
NEGATIVE_TYPES: Mapping[str, str] = {
    "Clinical Trial Protocol": "protocol", "Editorial": "editorial",
    "Review": "narrative_review",
}
#: Publication types that leave a record without a reference: retractions and errata, and
#: designs outside the six (veterinary trials, scoping reviews) a label would mis-state.
UNLABELLED_TYPES = frozenset({
    "Retracted Publication", "Retraction of Publication", "Published Erratum",
    "Expression of Concern", "Randomized Controlled Trial, Veterinary",
    "Clinical Trial, Veterinary", "Observational Study, Veterinary", "Scoping Review",
})
#: The only publication types an animal or in-vitro reference may carry: a research
#: article, with nothing that would make it a clinical study, a letter or a guideline.
RESEARCH_TYPES = frozenset({
    "Journal Article", "Comparative Study", "Evaluation Study", "Validation Study",
    "English Abstract", "Video-Audio Media", "Dataset",
})
#: MeSH headings that mark a study done in cultured cells.
IN_VITRO_MESH = frozenset({
    "In Vitro Techniques", "Cells, Cultured", "Cell Line", "Cell Line, Tumor",
    "Cell Culture Techniques", "Primary Cell Culture", "Organoids", "Spheroids, Cellular",
    "HeLa Cells", "HEK293 Cells", "Hep G2 Cells", "MCF-7 Cells", "A549 Cells",
    "Caco-2 Cells", "HCT116 Cells", "HT29 Cells", "Jurkat Cells", "THP-1 Cells",
    "U937 Cells", "K562 Cells", "HL-60 Cells", "HaCaT Cells", "PC-3 Cells",
    "Human Umbilical Vein Endothelial Cells",
})
#: MeSH age groups. Indexers give them to studies of people, not of cell lines, so a
#: cell-culture heading beside one marks a clinical study that also cultured cells.
AGE_MESH = frozenset({
    "Infant, Newborn", "Infant", "Child, Preschool", "Child", "Adolescent", "Young Adult",
    "Adult", "Middle Aged", "Aged", "Aged, 80 and over",
})


def _digest(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16)


def split_of(pmid: str) -> str:
    """``dev`` for a third of identifiers, ``test`` for the rest, by a seeded hash.

    Fixed by the identifier alone, before any rule is run, so no result can move a record
    from one split to the other.
    """
    return "dev" if _digest(f"{SPLIT_SEED}:{pmid}") % 3 == 0 else "test"


def draw_key(stratum: str, pmid: str) -> int:
    """The order a stratum's frame is drawn in: a seeded hash, not the search's ranking."""
    return _digest(f"{SAMPLE_SEED}:{stratum}:{pmid}")


def reference_for(publication_types: Sequence[str],
                  mesh: Sequence[str]) -> tuple[str, str]:
    """``(kind, basis)``: the reference a record's PubMed metadata gives, or ``("", why)``.

    ``kind`` is a design in :data:`DESIGNS` or a negative in :data:`NEGATIVES`. A record
    whose types name two designs (a trial also typed observational) has no reference
    rather than the first one's.
    """
    types, headings = set(publication_types), set(mesh)
    unlabelled = sorted(types & UNLABELLED_TYPES)
    if unlabelled:
        return "", f"publication type {unlabelled[0]!r} is outside the reference"
    if "Clinical Trial Protocol" in types:
        return "protocol", "publication type 'Clinical Trial Protocol'"
    designs = {DESIGN_TYPES[t]: t for t in sorted(types) if t in DESIGN_TYPES}
    if len(designs) > 1:
        return "", f"publication types name two designs: {sorted(designs.values())}"
    if designs:
        (design, named), = designs.items()
        return design, f"publication type {named!r}"
    for name, kind in NEGATIVE_TYPES.items():
        if name in types:
            return kind, f"publication type {name!r} and no design type"
    other = sorted(t for t in types
                   if t not in RESEARCH_TYPES and not t.startswith("Research Support"))
    if other:
        return "", f"publication type {other[0]!r} gives no design"
    cells = sorted(headings & IN_VITRO_MESH)
    if "Animals" in headings and "Humans" not in headings and not cells:
        return "animal", "MeSH 'Animals' without 'Humans' or a cell-culture heading"
    if cells and "Animals" not in headings and not headings & AGE_MESH:
        return "in_vitro", f"MeSH {cells[0]!r} without 'Animals' or an age group"
    return "", "no publication type or MeSH heading gives a design"


# ------------------------------------------------------------------ the sample
@dataclass(frozen=True)
class Record:
    """One abstract of the sample, with its PubMed metadata and its attribution."""

    pmid: str
    language: str
    title: str
    abstract: str
    stratum: str
    licence: str
    publication_types: tuple[str, ...]
    mesh: tuple[str, ...]
    attribution: Mapping[str, Any]

    @property
    def text(self) -> str:
        """What the rules read: the title and the abstract, as a document would hold them."""
        return f"{self.title}\n\n{self.abstract}"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Record":
        pubmed = data["pubmed"]
        return cls(pmid=str(data["pmid"]), language=str(data["language"]),
                   title=str(data["title"]), abstract=str(data["abstract"]),
                   stratum=str(data["stratum"]), licence=str(data["licence"]),
                   publication_types=tuple(pubmed["publication_types"]),
                   mesh=tuple(pubmed["mesh"]), attribution=data["attribution"])


@dataclass(frozen=True)
class Label:
    """A record's reference and split, as committed in ``labels.jsonl``."""

    pmid: str
    split: str
    kind: str
    basis: str


#: Attribution every committed record must carry, so its licence's terms can be met; and
#: the licence's deed or, where the article names no version, its licence statement.
ATTRIBUTION = ("authors", "journal", "year", "source_url")


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def load_sample(directory: str | Path) -> tuple[list[Record], dict[str, Label]]:
    """The records and labels in ``directory``, refused unless they are what they claim.

    Raises ``ValueError`` for a record whose licence is not CC BY or CC0, that lacks its
    attribution, or whose committed reference or split is not the one its metadata and
    identifier give: a label edited by hand, or a record moved between splits after a
    result was seen, would make every number downstream mean something else.
    """
    directory = Path(directory)
    records = [Record.from_dict(r) for r in _jsonl(directory / "sample.jsonl")]
    labels = {str(r["pmid"]): Label(str(r["pmid"]), str(r["split"]), str(r["kind"]),
                                    str(r["basis"]))
              for r in _jsonl(directory / "labels.jsonl")}
    problems = []
    if len({r.pmid for r in records}) != len(records):
        problems.append("a PMID occurs twice in sample.jsonl")
    if set(labels) != {r.pmid for r in records}:
        problems.append("labels.jsonl and sample.jsonl name different records")
    for record in records:
        if record.licence not in LICENCES:
            problems.append(f"{record.pmid}: licence {record.licence!r} may not be committed")
        missing = [k for k in ATTRIBUTION if not record.attribution.get(k)]
        if not (record.attribution.get("licence_url")
                or record.attribution.get("licence_statement")):
            missing.append("licence_url or licence_statement")
        if missing:
            problems.append(f"{record.pmid}: attribution lacks {missing}")
        label = labels.get(record.pmid)
        if label is None:
            continue
        kind, basis = reference_for(record.publication_types, record.mesh)
        if (label.kind, label.basis) != (kind, basis) or not kind:
            problems.append(f"{record.pmid}: labelled {label.kind!r} ({label.basis}), but "
                            f"its PubMed metadata gives {kind!r} ({basis})")
        if label.split != split_of(record.pmid):
            problems.append(f"{record.pmid}: in {label.split}, but its hash puts it in "
                            f"{split_of(record.pmid)}")
    if problems:
        raise ValueError(f"{directory}: " + "; ".join(problems[:10])
                         + (f" (and {len(problems) - 10} more)" if len(problems) > 10 else ""))
    return records, labels


def sample_digest(directory: str | Path) -> str:
    """One digest over the sample and its labels: what a set of results was computed on."""
    directory = Path(directory)
    h = hashlib.sha256()
    for name in ("sample.jsonl", "labels.jsonl"):
        h.update((directory / name).read_bytes())
    return h.hexdigest()


# ------------------------------------------------------------------ reading
Reader = Callable[[str], Mapping[str, Any]]


def load_rules(path: str | Path) -> Reader:
    """``read_fields`` from another version of ``bioagent/literature/evidence.py``.

    Loaded as a module of ``bioagent.literature``, so its relative imports resolve, but under
    its own name, so the package's rules are untouched. This is how the rules at an earlier
    commit are measured on the same sample (the "before" of a change).
    """
    name = "bioagent.literature._rules_" + hashlib.sha256(
        Path(path).read_bytes()).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"{path} is not a Python module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.read_fields


def rules_digest(read: Reader) -> str:
    """The SHA-256 of the file the rules are defined in: which rules produced a result."""
    return hashlib.sha256(Path(inspect.getsourcefile(read) or "").read_bytes()).hexdigest()


@dataclass(frozen=True)
class Outcome:
    """What the rules read from one record, beside the record's reference."""

    pmid: str
    split: str
    stratum: str
    language: str
    kind: str
    design: str
    rule: str
    #: Why the design was left unassessed: "ambiguous" or "no rule"; "" when it was read.
    abstained: str
    #: Whether the design was read from the title rather than the abstract.
    from_title: bool
    values: Mapping[str, str]
    states: Mapping[str, str]

    @property
    def verdict(self) -> str:
        """``correct``, ``abstained`` or ``wrong``. On a negative, abstaining is right and
        any design is wrong."""
        if not self.design:
            return "abstained"
        return "correct" if self.design == self.kind else "wrong"

    def as_dict(self) -> dict[str, Any]:
        return {"split": self.split, "stratum": self.stratum, "kind": self.kind,
                "design": self.design, "rule": self.rule, "abstained": self.abstained,
                "verdict": self.verdict, **{f: self.values[f] for f in VALUE_FIELDS}}


def _state(reading: Any) -> str:
    if reading.assessed:
        return "read"
    return "ambiguous" if reading.note.startswith("ambiguous") else "no rule"


def run(records: Sequence[Record], labels: Mapping[str, Label],
        read: Reader | None = None) -> list[Outcome]:
    """Read every record with ``read`` (default: the package's ``read_fields``)."""
    if read is None:
        from ..literature.evidence import read_fields as read
    out = []
    for record in records:
        readings = read(record.text)
        design, label = readings["design"], labels[record.pmid]
        out.append(Outcome(
            pmid=record.pmid, split=label.split, stratum=record.stratum,
            language=record.language, kind=label.kind, design=design.value,
            rule=design.rule, abstained="" if design.assessed else _state(design),
            from_title=design.assessed and design.offset < len(record.title),
            values={f: readings[f].value for f in VALUE_FIELDS},
            states={f: _state(readings[f]) for f in VALUE_FIELDS}))
    return out


# ------------------------------------------------------------------ measuring
def _rate(k: int, n: int) -> dict[str, Any]:
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None,
            "ci95": list(_wilson(k, n))}


def _licensed(kind: str) -> frozenset[str]:
    """The claim kinds a design licenses (``tcm.model.CLAIM_SUPPORT``); none for a negative."""
    from ..contracts import EVIDENCE_TIER_FOR_DESIGN
    from ..tcm.model import CLAIM_SUPPORT
    if kind not in DESIGNS:
        return frozenset()
    tier = EVIDENCE_TIER_FOR_DESIGN[kind]
    return frozenset(claim for claim, tiers in CLAIM_SUPPORT.items() if tier in tiers)


def summarise(outcomes: Sequence[Outcome]) -> dict[str, Any]:
    """The metrics of one split. Every proportion carries its count and a Wilson interval."""
    labelled = [o for o in outcomes if o.kind in DESIGNS]
    negatives = [o for o in outcomes if o.kind in NEGATIVES]
    read = [o for o in outcomes if o.design]
    designs = {}
    for d in DESIGNS:
        reference = [o for o in labelled if o.kind == d]
        typed = [o for o in read if o.design == d]
        correct = sum(o.kind == d for o in typed)
        designs[d] = {"precision": _rate(correct, len(typed)),
                      "recall": _rate(correct, len(reference)),
                      "abstention": _rate(sum(not o.design for o in reference),
                                          len(reference)),
                      "wrong": _rate(sum(o.verdict == "wrong" for o in reference),
                                     len(reference))}
    columns = DESIGNS + ("ambiguous", "no rule")
    confusion = {kind: dict.fromkeys(columns, 0) for kind in DESIGNS + NEGATIVES}
    for o in outcomes:
        confusion[o.kind][o.design or o.abstained] += 1
    typed_negatives = {}
    for kind, group in [(k, [o for o in negatives if o.kind == k]) for k in NEGATIVES] + [
            ("all", negatives)]:
        typed_negatives[kind] = {
            "typed": _rate(sum(bool(o.design) for o in group), len(group)),
            "as_trial": _rate(sum(o.design == "randomized_trial" for o in group), len(group)),
            "as_review": _rate(sum(o.design == "systematic_review" for o in group),
                               len(group))}
    wrong = [o for o in outcomes if o.verdict == "wrong"]
    widening = [o for o in wrong if _licensed(o.design) - _licensed(o.kind)]
    fields = {}
    for f in VALUE_FIELDS:
        filled = sum(o.states[f] == "read" for o in outcomes)
        fields[f] = {"read": _rate(filled, len(outcomes)),
                     "ambiguous": sum(o.states[f] == "ambiguous" for o in outcomes),
                     "no_rule": sum(o.states[f] == "no rule" for o in outcomes),
                     "by_kind": {kind: _rate(sum(o.states[f] == "read" for o in group),
                                             len(group))
                                 for kind in DESIGNS + NEGATIVES
                                 for group in [[o for o in outcomes if o.kind == kind]]}}
    strata: dict[str, dict[str, int]] = {}
    for o in outcomes:
        row = strata.setdefault(o.stratum, {"n": 0, "correct": 0, "abstained": 0, "wrong": 0})
        row["n"] += 1
        row[o.verdict] += 1
    return {
        "records": len(outcomes), "labelled": len(labelled), "negatives": len(negatives),
        "precision": _rate(sum(o.verdict == "correct" for o in read), len(read)),
        "coverage": _rate(sum(bool(o.design) for o in labelled), len(labelled)),
        "correct_from_title": sum(o.from_title for o in read if o.verdict == "correct"),
        "designs": designs, "confusion": confusion, "negatives_typed": typed_negatives,
        "wrong": len(wrong),
        "widening": {"n": len(widening),
                     "efficacy": sum("efficacy" in _licensed(o.design) - _licensed(o.kind)
                                     for o in widening),
                     "ids": sorted(o.pmid for o in widening)},
        "fields": fields, "strata": dict(sorted(strata.items())),
    }


def results(outcomes: Sequence[Outcome], *, sample: str, rules: str) -> dict[str, Any]:
    """The committed form: the sample and rules digests, each split's metrics, each record."""
    return {
        "benchmark": "evidence-typing",
        "reference": ("PubMed publication types, with MeSH Animals/Humans and cell-culture "
                      "headings for animal and in-vitro studies; an imperfect reference "
                      "assigned by indexers"),
        "sample_sha256": sample, "rules_sha256": rules,
        "splits": {split: summarise([o for o in outcomes if o.split == split])
                   for split in ("dev", "test")},
        "records": {o.pmid: o.as_dict() for o in sorted(outcomes, key=lambda o: int(o.pmid))},
    }


# ------------------------------------------------------------------ the manual check
def draw_manual_check(outcomes: Sequence[Outcome], n: int = 30) -> list[dict[str, str]]:
    """``n`` filled population, comparator or outcome readings of the test split, drawn by
    a seeded hash of (PMID, field): which ones are checked is fixed before anyone reads
    them."""
    filled = [(o, f) for o in outcomes if o.split == "test"
              for f in VALUE_FIELDS if o.states[f] == "read"]
    filled.sort(key=lambda pair: _digest(f"{MANUAL_SEED}:{pair[0].pmid}:{pair[1]}"))
    return [{"pmid": o.pmid, "field": f, "value": o.values[f]} for o, f in filled[:n]]


def manual_check_summary(check: Mapping[str, Any],
                         outcomes: Sequence[Outcome]) -> dict[str, Any]:
    """The verdicts of a manual check, refused when they judged values the rules no longer
    read: a check of other values says nothing about these."""
    current = {o.pmid: o for o in outcomes}
    entries = check["fields"]
    stale = [e for e in entries
             if e["pmid"] not in current
             or current[e["pmid"]].values[e["field"]] != e["value"]]
    if stale:
        raise ValueError(f"the manual check judged {len(stale)} values the rules no longer "
                         f"read (first: {stale[0]['pmid']} {stale[0]['field']}); draw and "
                         "check again")
    verdicts = [e["verdict"] for e in entries]
    return {"checked": len(entries), "method": check["method"],
            "correct": _rate(verdicts.count("correct"), len(entries)),
            "partly": verdicts.count("partly"), "wrong": verdicts.count("wrong"),
            "by_field": {f: {v: sum(e["field"] == f and e["verdict"] == v for e in entries)
                             for v in ("correct", "partly", "wrong")} for f in VALUE_FIELDS},
            "fields": [{k: e[k] for k in ("pmid", "field", "value", "verdict")}
                       for e in entries]}


# ------------------------------------------------------------------ regression check
_WORSE = {("correct", "abstained"), ("correct", "wrong"), ("abstained", "wrong")}


def compare(committed: Mapping[str, Any],
            now: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """``(problems, notes)``: what got worse against the committed results, and what changed.

    A problem is a record read worse than the committed results show (correct to abstained
    or wrong, abstained to wrong), a population, comparator or outcome the manual check
    found correct that now reads otherwise, or a different sample. Anything else that
    changed is a note asking for the results to be regenerated, so the committed numbers
    stay the ones the code produces.
    """
    problems, notes = [], []
    if committed["sample_sha256"] != now["sample_sha256"]:
        problems.append("the sample or its labels changed; regenerate the results with --out")
        return problems, notes
    was, is_ = committed["records"], now["records"]
    worse = [p for p in was if (was[p]["verdict"], is_[p]["verdict"]) in _WORSE]
    better = [p for p in was if was[p]["verdict"] != is_[p]["verdict"] and p not in worse]
    if worse:
        problems.append(f"{len(worse)} records now read worse: " + ", ".join(
            f"{p} ({was[p]['kind']}: {was[p]['design'] or was[p]['verdict']} -> "
            f"{is_[p]['design'] or is_[p]['verdict']})" for p in worse[:12]))
    if better:
        notes.append(f"{len(better)} records now read better ({', '.join(better[:12])}); "
                     "regenerate the results with --out")
    moved = [p for p in was if p not in worse and p not in better
             and was[p]["design"] != is_[p]["design"]]
    if moved:
        notes.append(f"{len(moved)} wrong readings changed design ({', '.join(moved[:12])}); "
                     "regenerate the results with --out")
    verified = {(e["pmid"], e["field"]): e["value"]
                for e in committed.get("manual_check", {}).get("fields", [])
                if e["verdict"] == "correct"}
    lost = [f"{p} {f}" for (p, f), value in verified.items() if is_[p][f] != value]
    if lost:
        problems.append("values the manual check found correct now read otherwise: "
                        + ", ".join(lost))
    changed = sum(was[p][f] != is_[p][f] for p in was for f in VALUE_FIELDS)
    if changed > len(lost):
        notes.append(f"{changed - len(lost)} population, comparator or outcome values "
                     "changed; regenerate the results with --out")
    return problems, notes


# ------------------------------------------------------------------ the report
_NAMES = {"systematic_review": "systematic review", "randomized_trial": "randomized trial",
          "observational": "observational", "case_report": "case report",
          "animal": "animal", "in_vitro": "in vitro", "narrative_review": "narrative review",
          "editorial": "editorial", "protocol": "trial protocol", "ambiguous": "ambiguous",
          "no rule": "no rule"}


def _pct(rate: Mapping[str, Any]) -> str:
    if not rate["n"]:
        return "–"
    lo, hi = rate["ci95"]
    return f"{rate['k']}/{rate['n']} ({rate['rate']:.0%}; {lo:.0%}–{hi:.0%})"


def _design_table(split: Mapping[str, Any], before: Mapping[str, Any] | None) -> list[str]:
    """Per design: precision, recall, abstention and wrong readings, each with its count and
    Wilson interval, and the earlier rules' figure before an arrow when there are some."""
    metrics = ("precision", "recall", "abstention", "wrong")
    then = " (before → after)" if before is not None else ""
    rows = [f"| Design | n | Precision{then} | Recall{then} | Abstained{then} | Wrong{then} |",
            "| --- | ---: | --- | --- | --- | --- |"]
    for d in DESIGNS:
        m = split["designs"][d]
        cells = [_pct(m[k]) for k in metrics]
        if before is not None:
            cells = [f"{_pct(before['designs'][d][k])} → {cell}"
                     for k, cell in zip(metrics, cells)]
        rows.append(f"| {_NAMES[d]} | {m['recall']['n']} | " + " | ".join(cells) + " |")
    return rows


def render_markdown(data: Mapping[str, Any], *, sampling: Mapping[str, Any] | None = None,
                    baseline: Mapping[str, Any] | None = None) -> str:
    """``results.md``: the test split first, then dev, then the change, if a baseline is
    given."""
    test, dev = data["splits"]["test"], data["splits"]["dev"]
    out = ["# Evidence typing on real abstracts", ""]
    out.append(
        f"{test['records'] + dev['records']} abstracts from Europe PMC (open access, CC BY "
        f"or CC0), {test['records']} in the test split and {dev['records']} in dev. The "
        "reference is PubMed's publication types (MeSH for animal and in-vitro studies): "
        "indexers assign them, some are missing, and a secondary analysis can carry its "
        "trial's type, so this measures agreement with an imperfect reference. Precision is "
        "for this stratified mix, not for literature at large.")
    if sampling is not None:
        out += ["", "## The sample", "",
                "| Stratum | Language | Reference | Drawn | Frame | Examined |",
                "| --- | --- | --- | ---: | ---: | ---: |"]
        out += [f"| {s['name']} | {s['language']} | {_NAMES[s['kind']]} | {s['drawn']} | "
                f"{s['frame_hits']} | {s['examined']} |" for s in sampling["strata"]]
        licences = ", ".join(f"{k} {v}" for k, v in sampling["licences"].items())
        out += ["", f"Licences: {licences} (`CC-BY`: the article names the licence but no "
                "version). Each record in `sample.jsonl` carries its attribution: authors, "
                "journal, year, DOI, PMID, PMCID, licence, licence URL or statement, and "
                "copyright line. Abstract markup was removed; the text is otherwise as "
                "published."]
    out += ["", "## Test split", "", "### Design", ""]
    out += _design_table(test, baseline["splits"]["test"] if baseline else None)
    out += ["", f"Of {test['labelled']} studies, {_pct(test['coverage'])} were typed; of "
            f"every design read (negatives included), {_pct(test['precision'])} was the "
            f"reference's. {test['correct_from_title']} correct readings came from the title.",
            "", "Confusion matrix (rows: reference; columns: what the rules read):", ""]
    columns = DESIGNS + ("ambiguous", "no rule")
    out += ["| Reference | " + " | ".join(_NAMES[c] for c in columns) + " |",
            "| --- | " + " | ".join("---:" for _ in columns) + " |"]
    for kind, row in test["confusion"].items():
        if sum(row.values()):
            out.append(f"| {_NAMES[kind]} | " + " | ".join(str(row[c]) for c in columns)
                       + " |")
    out += ["", "### Negatives", "", "| Kind | n | Typed at all | As a trial | As a review |",
            "| --- | ---: | --- | --- | --- |"]
    for kind, row in test["negatives_typed"].items():
        name = "all negatives" if kind == "all" else _NAMES[kind]
        out.append(f"| {name} | {row['typed']['n']} | {_pct(row['typed'])} | "
                   f"{_pct(row['as_trial'])} | {_pct(row['as_review'])} |")
    w = test["widening"]
    out += ["", f"{test['wrong']} readings were wrong; {w['n']} of them would license a claim "
            f"kind the reference does not ({w['efficacy']} of those efficacy): "
            + (", ".join(w["ids"]) or "none") + "."]
    out += ["", "### Population, comparator, outcome", "",
            "| Field | Filled | Ambiguous | No rule |", "| --- | --- | ---: | ---: |"]
    for f, row in test["fields"].items():
        out.append(f"| {f} | {_pct(row['read'])} | {row['ambiguous']} | {row['no_rule']} |")
    out += ["", "Filled, by reference:", "",
            "| Reference | " + " | ".join(VALUE_FIELDS) + " |", "| --- | ---: | ---: | ---: |"]
    for kind in DESIGNS + NEGATIVES:
        cells = [test["fields"][f]["by_kind"][kind] for f in VALUE_FIELDS]
        if cells[0]["n"]:
            out.append(f"| {_NAMES[kind]} | " + " | ".join(f"{c['k']}/{c['n']}" for c in cells)
                       + " |")
    manual = data.get("manual_check")
    if manual:
        out += ["", "### Manual check of filled fields", "", manual["method"], "",
                f"Correct: {_pct(manual['correct'])}; partly correct: {manual['partly']}; "
                f"wrong: {manual['wrong']}.", "",
                "| Field | Correct | Partly | Wrong |", "| --- | ---: | ---: | ---: |"]
        out += [f"| {f} | {v['correct']} | {v['partly']} | {v['wrong']} |"
                for f, v in manual["by_field"].items()]
    out += ["", "### By stratum", "", "| Stratum | n | Correct | Abstained | Wrong |",
            "| --- | ---: | ---: | ---: | ---: |"]
    out += [f"| {s} | {r['n']} | {r['correct']} | {r['abstained']} | {r['wrong']} |"
            for s, r in test["strata"].items()]
    out += ["", "On a negative, abstaining is the right answer and counts under Abstained.",
            "", "## Dev split", ""]
    out += _design_table(dev, baseline["splits"]["dev"] if baseline else None)
    out += ["", f"Negatives typed: {_pct(dev['negatives_typed']['all']['typed'])}; as a "
            f"trial {_pct(dev['negatives_typed']['all']['as_trial'])}, as a review "
            f"{_pct(dev['negatives_typed']['all']['as_review'])}."]
    if baseline:
        out += ["", "## Before and after the rule changes", "",
                f"Before: rules `{baseline['rules_sha256'][:12]}`; after: rules "
                f"`{data['rules_sha256'][:12]}` (SHA-256 of the `evidence.py` each was read "
                "from). How and why they changed: `docs/evidence-typing-accuracy.md`.", "",
                "| Split | Studies typed | Readings right (negatives included) | Wrong "
                "readings | Negatives typed |",
                "| --- | --- | --- | ---: | --- |"]
        for name in ("dev", "test"):
            b, a = baseline["splits"][name], data["splits"][name]
            out.append(
                f"| {name} | {_pct(b['coverage'])} → {_pct(a['coverage'])} | "
                f"{_pct(b['precision'])} → {_pct(a['precision'])} | {b['wrong']} → "
                f"{a['wrong']} | {_pct(b['negatives_typed']['all']['typed'])} → "
                f"{_pct(a['negatives_typed']['all']['typed'])} |")
        moves: dict[str, int] = {}
        for pmid, after in data["records"].items():
            prior = baseline["records"][pmid]
            if after["split"] == "test" and prior["verdict"] != after["verdict"]:
                key = f"{prior['verdict']} → {after['verdict']}"
                moves[key] = moves.get(key, 0) + 1
        out += ["", "Test records whose verdict changed: " + (", ".join(
            f"{k}: {v}" for k, v in sorted(moves.items())) or "none") + "."]
    return "\n".join(out) + "\n"
