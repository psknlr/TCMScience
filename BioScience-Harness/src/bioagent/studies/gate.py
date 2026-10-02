"""The design gate: can this question be answered from these data, before any tool runs?

Each question type needs particular data. Composition against state needs cell-level
measurements with cell-type labels from enough patients per arm; isoform use cannot be
read from 3'-tag single-cell libraries; a microbial "function is expressed" claim needs
DNA and RNA from the same stool sample; response prediction needs a real outcome measured
after baseline. When the data cannot answer the question the gate says so and names what
is missing; when they can answer only a weaker question it says which.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Sequence

from .contract import CohortManifest, ContrastSpec, OmicsArtifact, StudySpec

__all__ = ["GateResult", "design_gate", "subjects_per_arm"]


@dataclass
class GateResult:
    question_type: str
    status: str = "answerable"          # answerable, downgraded, not_answerable
    missing: list[str] = field(default_factory=list)
    downgrades: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def need(self, what: str) -> None:
        self.missing.append(what)
        self.status = "not_answerable"

    def weaken(self, why: str) -> None:
        self.downgrades.append(why)
        if self.status == "answerable":
            self.status = "downgraded"

    @property
    def answerable(self) -> bool:
        return self.status != "not_answerable"

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def subjects_per_arm(manifest: CohortManifest, contrast: ContrastSpec) -> dict[str, int]:
    return {lvl: len(u) for lvl, u in contrast.units(manifest).items()}


def _mods(artifacts: Sequence[OmicsArtifact], *names: str) -> list[OmicsArtifact]:
    return [a for a in artifacts if a.modality in names]


def _has_attr(manifest: CohortManifest, name: str) -> bool:
    return any(s.get(name) not in ("", "NA", "nan") for s in manifest)


def _composition(g, spec, manifest, contrast, artifacts):
    cells = [a for a in _mods(artifacts, "scrna", "snrna", "spatial", "cytometry")
             if a.level == "cell"]
    if not cells:
        g.need("cell-level measurements (scRNA-seq, snRNA-seq, imaging or cytometry)")
    elif not any("cell_type" in a.annotations for a in cells):
        g.need("cell-type labels on the cell-level data")
    if any(not a.measured for a in cells):
        g.weaken("some cell-level data are imputed or predicted, not measured")
    if not any(a.modality in ("bulk_rna", "bulk_proteome") for a in artifacts):
        g.notes.append("no measured bulk profile: the tissue-level signal is the mean over "
                       "the same cells, so cell types lost in dissociation (epithelium, "
                       "neutrophils) bias both terms alike and cannot be detected")


def _spatial(g, spec, manifest, contrast, artifacts):
    sp = _mods(artifacts, "spatial")
    if not sp:
        g.need("spatial measurements")
    elif not any("coordinates" in a.annotations for a in sp):
        g.need("cell or spot coordinates")
    elif not any("cell_type" in a.annotations for a in sp):
        g.need("cell-type labels on the spatial data")
    for a in sp:
        if a.feature_space == "targeted_panel":
            g.notes.append(f"{a.id} is a {len(a.features) or a.n_features}-feature panel: "
                           "any gene background is the panel, never the genome")


def _inflammation(g, spec, manifest, contrast, artifacts):
    sev = [n for n in ("inflammation_score", "mayo_endoscopic", "histology_score",
                       "nancy_index", "geboes") if _has_attr(manifest, n)]
    if not sev:
        g.need("a measured inflammation severity per sample (endoscopic or histological "
               "score): without it repair cannot be separated from inflammation resolving")
    if not _has_attr(manifest, "timepoint") and not _has_attr(manifest, "healed"):
        g.weaken("no follow-up samples or healing status: only cross-sectional association "
                 "with severity can be estimated")


def _genetic(g, spec, manifest, contrast, artifacts):
    if not _mods(artifacts, "eqtl", "sqtl", "pqtl", "coloc"):
        g.need("tissue- or cell-type-specific QTL summary statistics")
    if not _mods(artifacts, "gwas"):
        g.need("disease GWAS summary statistics for colocalisation")
    g.notes.append("colocalisation supports a shared causal variant, not that the gene "
                   "mediates disease, and says nothing about an intervention")


def _isoform(g, spec, manifest, contrast, artifacts):
    ok = [a for a in artifacts if a.modality in ("long_read_rna", "bulk_rna", "scrna")
          and ({"isoform", "junctions"} & set(a.annotations))]
    if not ok:
        g.need("isoform- or junction-resolved RNA measurements")
    for a in artifacts:
        p = a.protocol.lower()
        if a.modality == "scrna" and ("3'" in p or "5'" in p or "tag" in p):
            g.notes.append(f"{a.id} is an end-tag single-cell library ({a.protocol}): it "
                           "cannot resolve full isoforms")


def _microbial(g, spec, manifest, contrast, artifacts):
    dna = _mods(artifacts, "metagenome")
    rna = _mods(artifacts, "metatranscriptome")
    if not dna:
        g.need("metagenomes (functional potential)")
    if not rna:
        g.weaken("no metatranscriptomes: only functional potential (DNA), not expression")
    if dna and rna:
        paired = set(dna[0].samples) & set(rna[0].samples) if dna[0].samples and \
            rna[0].samples else None
        if paired is not None and len(paired) == 0:
            g.need("metagenome and metatranscriptome from the same stool sample (none "
                   "paired)")
    if not _mods(artifacts, "metabolome"):
        g.notes.append("no metabolome: a function cannot be followed to its products")


def _multiomics(g, spec, manifest, contrast, artifacts):
    mods = {a.modality for a in artifacts}
    if len(mods) < 2:
        g.need("at least two measured layers on the same subjects")
    if not _has_attr(manifest, "visit_id") and not _has_attr(manifest, "day"):
        g.weaken("no visit identifier or collection day: layers cannot be matched to the "
                 "same visit, only to the same subject")


def _perturbation(g, spec, manifest, contrast, artifacts):
    if not _mods(artifacts, "perturbation"):
        g.need("perturbation signatures (e.g. LINCS, Perturb-seq) with their cell context")
    g.notes.append("compare against no-change, mean and linear baselines before any model; "
                   "transfer across cell types is the claim to test, not to assume")


def _specificity(g, spec, manifest, contrast, artifacts):
    diseases = {s.get("disease") for s in manifest if s.get("disease")}
    if len(diseases) < 2:
        g.need("comparison diseases measured in the same tissue and platform")


def _response(g, spec, manifest, contrast, artifacts):
    outcome = contrast.variable if contrast else "response"
    vals = {s.get(outcome) for s in manifest} - {"", "NA"}
    if len(vals) < 2:
        g.need(f"a real outcome ({outcome!r}) with at least two observed values")
    days = [s for s in manifest if s.timepoint]
    if not days:
        g.weaken("no timepoints: the outcome cannot be placed after the predictors")


_RULES: dict[str, Callable] = {
    "composition_vs_state": _composition, "spatial_neighbourhood": _spatial,
    "inflammation_vs_repair": _inflammation, "genetic_target": _genetic,
    "isoform": _isoform, "microbial_function": _microbial,
    "multiomics_discordance": _multiomics, "perturbation_transfer": _perturbation,
    "disease_specificity": _specificity, "response_prediction": _response,
}


def design_gate(spec: StudySpec, manifest: CohortManifest, contrasts: Sequence[ContrastSpec],
                artifacts: Sequence[OmicsArtifact], *, min_subjects_per_arm: int = 5) -> GateResult:
    g = GateResult(spec.question_type)
    by_id = {c.id: c for c in contrasts}
    primary = by_id.get(spec.primary_contrast)
    if primary is None:
        g.need(f"the primary contrast {spec.primary_contrast!r} is not specified")
    _RULES[spec.question_type](g, spec, manifest, primary, artifacts)
    if primary is not None:
        n = subjects_per_arm(manifest, primary)
        g.notes.append(f"independent {primary.unit} per arm: {n}")
        low = min(n.values()) if n else 0
        if low < 2:
            g.need(f"at least two independent {primary.unit}s per arm (have {n})")
        elif low < min_subjects_per_arm:
            g.weaken(f"fewer than {min_subjects_per_arm} {primary.unit}s in an arm ({n}): "
                     "estimates only, no confirmatory test")
        if primary.paired:
            per = defaultdict(set)
            for lvl, samples in primary.select(manifest).items():
                for s in samples:
                    per[s.get(primary.unit)].add(lvl)
            complete = sum(1 for v in per.values() if len(v) == 2)
            g.notes.append(f"complete pairs: {complete}")
            if complete < 2:
                g.need("at least two subjects measured under both levels of the paired "
                       "contrast")
    if spec.chain == "bridge":
        g.notes.append("a bridge claim also needs intervention-chain evidence; this gate "
                       "checks only the data for the disease side")
    return g
