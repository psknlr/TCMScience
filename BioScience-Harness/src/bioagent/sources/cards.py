"""Source cards: what each third-party database is, how it may be reached, under what terms.

One card per source. A card says how the data can be obtained, in order of preference
(``bulk`` download > documented ``api`` > ``manual`` import of a file the user obtained
> ``web`` lookups of single public pages), what licence governs its records, and which
edges it produces with which default evidence labels.

Two rules are enforced here rather than left to a README:

* **web access is off unless a person approved it.** A card may list a ``web`` access
  path, but it is usable only when the card carries a ``web_approval`` naming who checked
  the source's terms and when. Web lookups are single records at low rate, never bulk,
  and never past a login, a CAPTCHA or any other access control.
* **a skill can only narrow access, never widen it.** ``effective_sources`` intersects
  what a skill asks for with the cards that are enabled and with what the run is allowed.
  Skills live in agent-writable directories; if a skill's own file were the whitelist, an
  agent could grant itself any source by editing it.

This module is part of the trusted plane (``evolution.boundary``): an evolution proposal
may not edit it, so an agent cannot even propose enabling a source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .schema import AGENT_TYPES, EDGE_PREDICATES, KNOWLEDGE_LEVELS, NODE_CATEGORIES, STUDY_DESIGNS

__all__ = ["ACCESS_MODES", "Access", "EdgeDefault", "Approval", "SourceCard",
           "SourceCardError", "SOURCE_CARDS", "card", "effective_sources", "parse_ref"]

#: In order of preference.
ACCESS_MODES: tuple[str, ...] = ("bulk", "api", "manual", "web")

_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


class SourceCardError(ValueError):
    """A card that would let data in without the facts needed to use it lawfully."""


@dataclass(frozen=True)
class Access:
    mode: str
    url: str = ""                  # bulk file URL / API base URL; empty for manual
    checksum: str = ""             # provider-published checksum, when there is one
    cadence: str = ""              # how often the provider releases (monthly, frozen, ...)
    rps: float = 0.0               # request rate for api/web; 0 = backend default
    notes: str = ""

    def __post_init__(self) -> None:
        if self.mode not in ACCESS_MODES:
            raise SourceCardError(f"access mode {self.mode!r} is not one of {ACCESS_MODES}")
        if self.mode != "manual" and not self.url:
            raise SourceCardError(f"{self.mode} access needs a url")
        if self.mode == "web" and not 0 < self.rps <= 1.0:
            raise SourceCardError("web access must declare a rate of at most 1 request/s")


@dataclass(frozen=True)
class EdgeDefault:
    """An edge type a source produces, with the evidence labels its rows get by default."""

    predicate: str
    subject: str
    object: str
    knowledge_level: str
    agent_type: str
    study_design: str
    composition_level: str = ""

    def __post_init__(self) -> None:
        problems = []
        if self.predicate not in EDGE_PREDICATES:
            problems.append(f"predicate {self.predicate!r}")
        if self.subject not in NODE_CATEGORIES or self.object not in NODE_CATEGORIES:
            problems.append(f"categories {self.subject!r} -> {self.object!r}")
        if self.knowledge_level not in KNOWLEDGE_LEVELS:
            problems.append(f"knowledge_level {self.knowledge_level!r}")
        if self.agent_type not in AGENT_TYPES:
            problems.append(f"agent_type {self.agent_type!r}")
        if self.study_design not in STUDY_DESIGNS:
            problems.append(f"study_design {self.study_design!r}")
        if (self.knowledge_level == "prediction") != (self.study_design == "in_silico"):
            problems.append("a prediction must be in_silico and in_silico must be a prediction")
        if problems:
            raise SourceCardError("invalid edge default: " + "; ".join(problems))


@dataclass(frozen=True)
class Approval:
    """A person's sign-off, with the date and what they checked."""

    by: str
    on: str                        # ISO date
    note: str = ""

    def __post_init__(self) -> None:
        if not self.by.strip() or not re.match(r"^\d{4}-\d{2}-\d{2}$", self.on):
            raise SourceCardError("an approval names a person and an ISO date")


@dataclass(frozen=True)
class SourceCard:
    key: str                                   # CURIE prefix and snapshot directory name
    name: str
    citation: str                              # doi:... of the paper the provider asks for
    license: str                               # licence of the records by default
    terms_url: str
    access: tuple[Access, ...]
    provides: tuple[EdgeDefault, ...] = ()
    #: field name -> {field value -> licence}: records whose licence differs from the
    #: default, e.g. BindingDB rows curated by ChEMBL are CC-BY-SA-3.0.
    per_record_license: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    commercial_use: str = "unknown"            # allowed | forbidden | unknown
    enabled: bool = True
    web_approval: Approval | None = None
    #: QC thresholds for this source's snapshots (see ``snapshot.QCThresholds``).
    qc: Mapping[str, float] = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self) -> None:
        if not _KEY.match(self.key):
            raise SourceCardError(f"card key {self.key!r} must match {_KEY.pattern}")
        if not self.access:
            raise SourceCardError(f"card {self.key!r} declares no access path")
        if not self.license.strip():
            raise SourceCardError(f"card {self.key!r} states no licence")
        if not self.citation.strip():
            raise SourceCardError(f"card {self.key!r} names no citation")
        if self.commercial_use not in ("allowed", "forbidden", "unknown"):
            raise SourceCardError("commercial_use is allowed, forbidden or unknown")
        modes = [a.mode for a in self.access]
        if modes != sorted(modes, key=ACCESS_MODES.index):
            raise SourceCardError(f"card {self.key!r} lists access out of preference order")

    # ----------------------------------------------------------------- access
    def usable_access(self) -> tuple[Access, ...]:
        """Access paths that may be used now: web only with a person's approval."""
        if not self.enabled:
            return ()
        return tuple(a for a in self.access
                     if a.mode != "web" or self.web_approval is not None)

    def preferred(self) -> Access | None:
        usable = self.usable_access()
        return usable[0] if usable else None

    def record_license(self, row: Mapping[str, Any]) -> str:
        for fld, overrides in self.per_record_license.items():
            value = row.get(fld)
            if value is not None and str(value) in overrides:
                return overrides[str(value)]
        return self.license


#: Sources with a verified bulk path in ``acquisition/sources.py``. Evidence defaults are
#: deliberately conservative; a parser may label a row more precisely, never more
#: strongly than the row itself supports.
SOURCE_CARDS: tuple[SourceCard, ...] = (
    SourceCard(
        key="lotus", name="LOTUS natural products occurrences (frozen export)",
        citation="doi:10.7554/eLife.70780",
        # The frozen Zenodo export is CC-BY-4.0; LOTUS's Wikidata statements are CC0.
        license="CC-BY-4.0", terms_url="https://zenodo.org/records/19360665",
        access=(Access("bulk", "https://zenodo.org/api/records/19360665/files/"
                       "260413_frozen.csv.gz/content", checksum="md5:cf0cf2afa2ca4d758b68f2e39d466f5d",
                       cadence="frozen 2026-04-13"),
                Access("api", "https://query.wikidata.org/sparql", rps=1.0)),
        provides=(EdgeDefault("contains", "organism", "ingredient", "knowledge_assertion",
                              "automated_agent", "chemical_analysis", composition_level="C1"),),
        commercial_use="allowed", qc={"min_inchikey_coverage": 0.95}),
    SourceCard(
        key="npass", name="NPASS 2.0", citation="doi:10.1093/nar/gkac1069",
        license="Free for academic use", terms_url="https://bidd.group/NPASS/",
        access=(Access("bulk", "https://bidd.group/NPASS/downloadFiles/", cadence="2.0"),),
        provides=(
            EdgeDefault("contains", "organism", "ingredient", "knowledge_assertion",
                        "manual_agent", "chemical_analysis", composition_level="C1"),
            EdgeDefault("targets", "ingredient", "target", "knowledge_assertion",
                        "manual_agent", "in_vitro"),
        ),
        commercial_use="unknown", qc={"min_inchikey_coverage": 0.8}),
    SourceCard(
        key="cmaup", name="CMAUP 2.0", citation="doi:10.1093/nar/gkad921",
        license="Free for academic use", terms_url="https://bidd.group/CMAUP/",
        access=(Access("bulk", "https://bidd.group/CMAUP/downloadFiles/", cadence="2.0"),),
        provides=(
            EdgeDefault("contains", "organism", "ingredient", "knowledge_assertion",
                        "not_provided", "chemical_analysis", composition_level="C1"),
            EdgeDefault("targets", "ingredient", "target", "knowledge_assertion",
                        "manual_agent", "in_vitro"),
        ),
        commercial_use="unknown", qc={"min_inchikey_coverage": 0.8}),
    SourceCard(
        key="bindingdb", name="BindingDB", citation="doi:10.1093/nar/gkae1075",
        license="CC-BY-4.0", terms_url="https://www.bindingdb.org/rwd/bind/info.jsp",
        # The monthly TSV is downloaded by a person (the page has an interactive step) and
        # imported; the REST service answers single lookups.
        access=(Access("api", "https://bindingdb.org/rest", rps=1.0),
                Access("manual", notes="BindingDB_All_<YYYYMM>_tsv.zip from the download page")),
        provides=(EdgeDefault("targets", "ingredient", "target", "knowledge_assertion",
                              "manual_agent", "in_vitro"),),
        per_record_license={"curation": {"ChEMBL": "CC-BY-SA-3.0"}},
        commercial_use="allowed", qc={"min_uniprot_coverage": 1.0}),
    SourceCard(
        key="string", name="STRING v12 (human)", citation="doi:10.1093/nar/gkac1000",
        license="CC-BY-4.0", terms_url="https://string-db.org/cgi/access",
        access=(Access("bulk", "https://stringdb-downloads.org/download/", cadence="12.0"),
                Access("api", "https://string-db.org/api", rps=1.0)),
        # The combined score is a computed confidence over mixed evidence channels.
        provides=(EdgeDefault("interacts_with", "target", "target", "prediction",
                              "computational_model", "in_silico"),),
        commercial_use="allowed", qc={"min_uniprot_coverage": 1.0}),
    SourceCard(
        key="reactome", name="Reactome (UniProt to lowest-level pathway)",
        citation="doi:10.1093/nar/gkad1025", license="CC0-1.0",
        terms_url="https://reactome.org/license",
        access=(Access("bulk", "https://reactome.org/download/current/UniProt2Reactome.txt",
                       cadence="quarterly"),
                Access("api", "https://reactome.org/ContentService", rps=5.0)),
        provides=(EdgeDefault("participates_in", "target", "pathway", "knowledge_assertion",
                              "manual_agent", "expert_consensus"),
                  EdgeDefault("participates_in", "target", "pathway", "prediction",
                              "automated_agent", "in_silico")),
        commercial_use="allowed", qc={"min_uniprot_coverage": 1.0}),
    SourceCard(
        key="opentargets", name="Open Targets Platform (target-disease associations)",
        citation="doi:10.1093/nar/gkae1128", license="CC0-1.0",
        terms_url="https://platform-docs.opentargets.org/licence",
        # The whole table is published as Parquet with every release; one indication's
        # associations are one paged GraphQL query, which is what fetch_opentargets saves.
        access=(Access("bulk", "https://ftp.ebi.ac.uk/pub/databases/opentargets/platform/",
                       cadence="quarterly"),
                Access("api", "https://api.platform.opentargets.org/api/v4/graphql", rps=1.0)),
        # An aggregate score per evidence type; the edge's knowledge level follows the type
        # (genetic association: statistical_association, literature: text_co_occurrence).
        provides=(EdgeDefault("associated_with", "target", "disease", "statistical_association",
                              "data_analysis_pipeline", "evidence_aggregate"),),
        commercial_use="allowed", qc={"min_uniprot_coverage": 0.9}),
)

_BY_KEY = {c.key: c for c in SOURCE_CARDS}
assert len(_BY_KEY) == len(SOURCE_CARDS), "duplicate source card key"


def card(key: str) -> SourceCard:
    try:
        return _BY_KEY[key]
    except KeyError:
        raise KeyError(f"no source card {key!r}; have {sorted(_BY_KEY)}") from None


_REF = re.compile(r"^(?P<key>[a-z][a-z0-9_]*)(?:@(?P<version>[^\s@]+))?$")


def parse_ref(ref: str) -> tuple[str, str]:
    """``npass@2.0`` -> ("npass", "2.0"); a bare key means "latest approved"."""
    m = _REF.match(ref.strip())
    if not m:
        raise ValueError(f"source reference {ref!r} is not key or key@version")
    return m.group("key"), m.group("version") or "latest-approved"


def effective_sources(requested: Iterable[str], *,
                      cards: Iterable[SourceCard] = SOURCE_CARDS,
                      allowed: Iterable[str] | None = None
                      ) -> tuple[dict[str, str], dict[str, str]]:
    """Which requested sources a run may use: request ∩ enabled cards ∩ run allowance.

    Returns ``(granted, refused)``: ``granted`` maps key -> requested version, ``refused``
    maps each dropped reference to the reason. Nothing is ever granted that the request
    did not name, so a skill cannot widen access by what it leaves out either.
    """
    by_key = {c.key: c for c in cards}
    allowance = None if allowed is None else set(allowed)
    granted: dict[str, str] = {}
    refused: dict[str, str] = {}
    for ref in requested:
        key, version = parse_ref(ref)
        c = by_key.get(key)
        if c is None:
            refused[ref] = "no source card"
        elif not c.enabled:
            refused[ref] = "source card disabled"
        elif not c.usable_access():
            refused[ref] = "no usable access path (web access needs a person's approval)"
        elif allowance is not None and key not in allowance:
            refused[ref] = "not allowed for this run"
        else:
            granted[key] = version
    return granted, refused
