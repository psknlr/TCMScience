"""What the kernel decided about a call, in the envelope's ``governance`` shape.

Everything here reads decisions that were already made — a governed run's artifact and
verdict, the policy kernel's rulings on a ``Runtime.invoke``, a tool's own result — and
lays them out for the UI and the model: claims with their verdicts, evidence items, the
six release states, refusals with codes and remedies, licences, limitations, advisory
labels and output files. Nothing here decides anything.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .envelope import jsonable, sha256_bytes

__all__ = ["empty", "refusal", "remedy_for", "from_governed_run", "from_verdict_refusals",
           "from_call_result", "outputs_from", "licence_entry", "advisory_labels",
           "LIMIT_NO_RECORD", "LIMIT_PREDICTED", "LIMIT_DRAFT", "LIMIT_CLASSICAL",
           "LIMIT_SEED", "LIMIT_NOT_SIGNIFICANT", "LIMIT_SIGNIFICANT", "LIMIT_PENDING",
           "LIMIT_LIVE",
           "LIMIT_HUB", "LIMIT_CALCULATOR", "LIMIT_NOT_EXHAUSTIVE_CHECK"]

# The limits a result carries, in the words the model must repeat when it uses the result.
LIMIT_NO_RECORD = ("Absence of a record is not evidence of safety: 'no record found' "
                   "means unknown, never 'safe'.")
LIMIT_PREDICTED = ("Predicted ≠ measured: a computational prediction or network inference "
                   "supports a mechanism hypothesis at most, never a mechanism or efficacy.")
LIMIT_DRAFT = ("This is a draft for a licensed TCM practitioner to review and sign; until "
               "signed it is not a prescription, and no tool can sign it.")
LIMIT_CLASSICAL = ("A classical record is an attribution (the text records it), not clinical "
                   "evidence of efficacy or safety.")
LIMIT_SEED = ("Seed corpus only (23 herbs, 5 processed forms, 6 formulas, 8 syndromes, 9 "
              "passages): it is illustrative, and absence here is not absence of evidence.")
LIMIT_NOT_SIGNIFICANT = ("Not significant ≠ irrelevant: a non-significant result is not "
                         "evidence of no effect.")
LIMIT_SIGNIFICANT = ("Significant ≠ effective or causal: a small p-value says the data would "
                     "be unusual under the null model, not that the effect is large, clinically "
                     "relevant or caused by the factor tested.")
LIMIT_PENDING = "A job that has not succeeded is pending work, not a result."
LIMIT_LIVE = ("Live third-party data, as the source returned it; TCMScience has not "
              "reviewed it, and the source's licence applies.")
LIMIT_HUB = ("Rows keep their source's evidence kind: 'predicted', 'aggregated' and "
             "'signal' rows support hypotheses only, and a missing row is not evidence of "
             "absence.")
LIMIT_CALCULATOR = ("A calculator applies a published formula to the values given; it "
                    "informs and does not replace clinical judgement.")
LIMIT_NOT_EXHAUSTIVE_CHECK = ("No issue found by the knowledge pack's rules is not a safety "
                              "guarantee; the pack is a draft, not reviewed by a licensed "
                              "practitioner.")

# What the user (or the model) can do about each refusal code. The kernel's own message
# says what is wrong; the remedy says how a claim or call could pass.
_REMEDIES: dict[str, str] = {
    "CLM001": "Cite at least one evidence item for the claim, or drop the claim.",
    "CLM002": "Cite only evidence items that are in the artifact.",
    "CLM003": "Support the claim with evidence that has not been retracted.",
    "CLM004": "State it as a mechanism hypothesis, or add clinical evidence; a prediction "
              "cannot carry a clinical claim.",
    "CLM005": "Weaken the claim to a kind its evidence licenses (tcm_applicability shows "
              "which), or add evidence of the required tier.",
    "CLM006": "Assess the cited studies' risk of bias, or weaken the claim.",
    "CLM007": "Remove the normative wording, or make it a recommendation backed by a "
              "systematic review.",
    "CLM008": "Declare the cross-species extrapolation, or restrict the claim to the species "
              "studied.",
    "CLM009": "Declare the extrapolation (population, outcome, dose), or narrow the claim to "
              "what the evidence covers.",
    "CLM010": "Quote the source verbatim so its receipt can locate the quote.",
    "CLM011": "Reword the claim so it asserts no more than its claim kind.",
    "CLM012": "Cite the evidence that validates the extrapolation, or mark it declared only.",
    "CLM013": "Match the claim's stated scope to what its cited evidence covers.",
    "CLM014": "Name exactly the substance the evidence studied.",
    "CLM015": "Do not carry constituent evidence to the whole formula, or the reverse.",
    "CLM016": "Name the processing state (e.g. 炙 vs 生) the evidence studied.",
    "CLM017": "Show the effective concentration is reached in people, or keep the claim at "
              "the bench.",
    "CLM018": "Count independent sources, not copies of one source.",
    "CLM019": "Say 'not tested' rather than stating an absence no evidence tested.",
    "ART101": "Name the sources the artifact draws on.",
    "ART102": "Pin each source to a snapshot (content hash) before release.",
    "ART103": "Identify the licence of each source a claim rests on.",
    "ART104": "Cite only evidence present in the artifact.",
    "ART105": "Fix or drop the unsupported claims (see each claim's codes).",
    "ART106": "A clinical claim needs clinical evidence; keep predictions as hypotheses.",
    "ART107": "Hash every declared output file.",
    "ART108": "Complete the composite version (runtime, skill, source, benchmark).",
    "ART109": "Remove or replace retracted evidence.",
    "ART110": "State the artifact's limitations.",
    "ART111": "Give each evidence item a source card present in the artifact.",
    "ART112": "Assess evidence quality before declaring a confidence basis.",
    "ART113": "Re-run so the declared output files exist where the artifact says.",
    "ART114": "Re-run: an output file no longer matches its hash.",
    "ART115": "Re-check the quote receipts against their content.",
    "ART116": "Validate the extrapolation with evidence, or keep it as a declared caveat.",
    "ART117": "Run under the audit chain so the attestation is recorded.",
    "ART118": "Allow the refused operation (declare its host in the manifest, enable web "
              "access) or run without it; an unrecorded operation blocks release.",
    "GovernedRunRefused": "The skill was not admitted. Check that its code matches its "
                          "reviewed pin in the lockfile; a changed skill must be reviewed "
                          "and re-pinned before it can be released.",
    "UNPINNED": "A development run is recorded but never released: the skill must be "
                "reviewed and pinned in registry/skills.lock.yaml first.",
    "HUMAN_ONLY": "This act belongs to a person, not to a tool or the model. Studio does not "
                  "sign: the licensed practitioner reviews a clinic draft and signs it outside "
                  "Studio (bioagent clinic sign <session directory> on the runner machine, or "
                  "their own clinic system); the maintainers review a release.",
    "NETWORK_OFF": "Turn on web access for this project; network calls then run under the "
                   "biomedical-research permission profile.",
    "NEEDS_RUNNER": "Start the local runner (tcmstudio serve) and connect it in Settings.",
    "MISSING_DEPENDENCY": "Install the optional dependency on the runner machine; Studio "
                          "never substitutes an approximation.",
}

_RULE_REMEDIES = (
    ("perm.network", _REMEDIES["NETWORK_OFF"]),
    ("perm.subprocess", "This needs subprocesses; run it as a job on the local runner."),
    ("perm.fs_", "The call reads or writes outside the profile's roots; use a project "
                 "directory the runner allows."),
    ("usage.", "Set the project's purpose to academic, or record a reviewed licence for the "
               "asset (registry/licence_records.yaml) before commercial use."),
    ("license.", "The component's licence does not permit this integration mode."),
)


def remedy_for(code: str) -> str:
    code = str(code or "")
    if code in _REMEDIES:
        return _REMEDIES[code]
    for prefix, text in _RULE_REMEDIES:
        if code.startswith(prefix):
            return text
    return ""


def refusal(code: str, message: str, remedy: str | None = None,
            **extra: Any) -> dict[str, Any]:
    out = {"code": str(code), "message": str(message),
           "remedy": remedy if remedy is not None else remedy_for(code)}
    out.update({k: jsonable(v) for k, v in extra.items() if v is not None})
    return out


def empty(kind: str) -> dict[str, Any]:
    return {"kind": kind, "released": None, "artifact": None, "verdict": None, "claims": [],
            "evidence": [], "refusals": [], "labels": [], "licences": [], "limitations": [],
            "outputs": []}


# ----------------------------------------------------------------------- licences

def licence_entry(asset: str, licence: str | None, *, note: str = "",
                  commercial: bool | None = None) -> dict[str, Any]:
    """One licence line. ``commercial`` follows the hub's conservative reading of the
    licence text (open or share-alike) unless a reviewed decision is given."""
    text = (licence or "").strip() or "not stated"
    klass = "unknown"
    try:
        from bioagent.tcmdb.spec import allows_commercial, licence_class
        klass = licence_class(text)
        if commercial is None:
            commercial = bool(allows_commercial(text))
    except Exception:                                           # noqa: BLE001
        if commercial is None:
            commercial = False
    return {"asset": str(asset), "licence": text, "class": klass,
            "commercial": bool(commercial), "note": note}


def _licences_from_sources(sources: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for s in sources or ():
        if not isinstance(s, Mapping):
            continue
        note = s.get("license_note") or ""
        if not s.get("licensed", True):
            note = (note + "; " if note else "") + "licence not identified"
        out.append(licence_entry(str(s.get("id") or s.get("name") or "?"),
                                 s.get("license_spdx") or "", note=note))
    return out


def _licences_from_usage(usage: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The commercial usage gate's per-asset decisions (purpose = commercial)."""
    out = []
    for asset in (usage or {}).get("assets") or ():
        if not isinstance(asset, Mapping):
            continue
        terms = asset.get("terms") or {}
        licence = asset.get("declared") or terms.get("licence") or terms.get("spdx") or ""
        out.append(licence_entry(f"{asset.get('kind', 'asset')}:{asset.get('ref', '?')}",
                                 licence, note=str(asset.get("reason") or ""),
                                 commercial=str(asset.get("verdict", "")).upper() == "ALLOW"))
    return out


# ------------------------------------------------------------------------ outputs

_JSON_LIMIT = 512 * 1024
_TEXT_LIMIT = 64 * 1024
_MEDIA = {".json": "application/json", ".md": "text/markdown", ".html": "text/html",
          ".tsv": "text/tab-separated-values", ".csv": "text/csv", ".txt": "text/plain",
          ".svg": "image/svg+xml", ".pdb": "chemical/x-pdb", ".sdf": "chemical/x-mdl-sdfile",
          ".fasta": "text/x-fasta", ".fa": "text/x-fasta"}


def _media_type(path: Path, declared: str = "") -> str:
    return declared or _MEDIA.get(path.suffix.lower(), "application/octet-stream")


def outputs_from(root: str | Path, entries: Iterable[Mapping[str, Any] | str],
                 *, include_content: bool = True) -> list[dict[str, Any]]:
    """Output files with their sha256 and size, and their content when small: parsed JSON
    up to 512 KB, text (Markdown, TSV, CSV, plain) up to 64 KB, nothing for HTML or binary.
    A declared file that is missing is listed with ``missing: true``."""
    base = Path(root)
    out = []
    for entry in entries:
        if isinstance(entry, Mapping):
            rel = str(entry.get("path") or "")
            declared = {k: entry.get(k) for k in ("description",) if entry.get(k)}
            media = str(entry.get("media_type") or "")
            expected = entry.get("sha256")
        else:
            rel, declared, media, expected = str(entry), {}, "", None
        if not rel:
            continue
        path = Path(rel) if Path(rel).is_absolute() else base / rel
        name = path.name if Path(rel).is_absolute() else rel
        item: dict[str, Any] = {"path": name, "media_type": _media_type(path, media), **declared}
        try:
            data = path.read_bytes()
        except OSError:
            item.update({"sha256": expected, "bytes": None, "content": None, "missing": True})
            out.append(item)
            continue
        digest = sha256_bytes(data)
        item.update({"sha256": digest, "bytes": len(data), "content": None})
        if expected and expected != digest:
            item["hash_mismatch"] = True
        if include_content:
            mt = item["media_type"]
            try:
                if mt == "application/json" and len(data) <= _JSON_LIMIT:
                    item["content"] = json.loads(data.decode("utf-8"))
                elif mt.startswith("text/") and mt != "text/html" and len(data) <= _TEXT_LIMIT:
                    item["content"] = data.decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                item["content"] = None
        out.append(item)
    return out


# ------------------------------------------------------------------------- labels

_CLASSIFIER: Any = None


def advisory_labels(value: Any, *, origin: str = "") -> tuple[list[str], dict[str, Any] | None]:
    """PSH's label for a value (PUBLIC < INTERNAL < RESEARCH_DEIDENTIFIED < SENSITIVE < PHI <
    SECRET), with the destinations it may reach. Advisory only: Studio shows it and never
    enforces it on the way to the model (the classifier reads Chinese herb names as name
    cues, so enforcing it would block ordinary lookups)."""
    global _CLASSIFIER
    try:
        if _CLASSIFIER is None:
            from psh.kernel.classify import Classifier
            _CLASSIFIER = Classifier()
        text = value if isinstance(value, str) else json.dumps(jsonable(value),
                                                                ensure_ascii=False)
        if len(text) > 200_000:
            text = text[:200_000]
        result = _CLASSIFIER.classify_text(text, origin=origin)
        label = result.label
        name = getattr(label.sensitivity, "name", str(label.sensitivity))
        permits: dict[str, bool] = {}
        try:
            from psh.labels import Destination
            permits = {d.name: bool(label.permits(d)) for d in Destination}
        except Exception:                                       # noqa: BLE001
            permits = {}
        detail = {"sensitivity": name, "categories": list(label.categories),
                  "classifier": getattr(label, "classifier", ""),
                  "shareable": bool(getattr(label, "shareable", False)),
                  "permits": permits, "advisory": True}
        return [name], detail
    except Exception:                                           # noqa: BLE001
        return [], None


# ------------------------------------------------------------------ governed runs

def from_verdict_refusals(verdict: Mapping[str, Any] | None,
                          claims: Sequence[Mapping[str, Any]] = ()) -> list[dict[str, Any]]:
    """Refusals the verdict holds: errors on the artifact, then every refused claim's
    reasons. Warnings are not refusals; they stay in the verdict."""
    out: list[dict[str, Any]] = []
    if not isinstance(verdict, Mapping):
        return out
    seen: set[tuple[str, str]] = set()
    for v in verdict.get("violations") or ():
        if isinstance(v, Mapping) and (v.get("severity") or "error") == "error":
            key = (str(v.get("code")), str(v.get("detail")))
            if key not in seen:
                seen.add(key)
                out.append(refusal(v.get("code", "ART"), v.get("detail", "")))
    text_of = {c.get("id"): c.get("text") for c in claims if isinstance(c, Mapping)}
    for cv in verdict.get("claim_verdicts") or ():
        if not isinstance(cv, Mapping) or cv.get("allowed", True):
            continue
        for reason in cv.get("reasons") or ():
            if not isinstance(reason, Mapping):
                continue
            key = (str(reason.get("code")), f"{cv.get('claim_id')}:{reason.get('detail')}")
            if key in seen:
                continue
            seen.add(key)
            out.append(refusal(reason.get("code", "CLM"), reason.get("detail", ""),
                               claim_id=cv.get("claim_id"),
                               claim=text_of.get(cv.get("claim_id"))))
    return out


def from_governed_run(run: Any, *, include_content: bool = True,
                      limits: Sequence[str] = ()) -> dict[str, Any]:
    """Governance of a ``GovernedRun``: the attested artifact, its verdict, the claims and
    evidence it carries, refusals, licences of its sources, limitations, output files."""
    doc = jsonable(run.artifact.document())
    verdict = jsonable(run.verdict.as_dict())
    governed = (doc.get("provenance") or {}).get("governed") or {}
    refusals = from_verdict_refusals(verdict, doc.get("claims") or [])
    for why in governed.get("release_refused") or ():
        code = "UNPINNED" if str(why).startswith("unpinned") else "ART118"
        refusals.append(refusal(code, str(why)))
    outputs = outputs_from(run.output_dir, doc.get("outputs") or [],
                           include_content=include_content)
    known = {o["path"] for o in outputs}
    extra = []
    for written in run.written or ():
        try:
            rel = Path(written).resolve().relative_to(Path(run.output_dir).resolve()).as_posix()
        except (ValueError, OSError):
            rel = Path(written).name
        if rel not in known:
            extra.append(rel)
    outputs += outputs_from(run.output_dir, extra, include_content=include_content)
    return {"kind": "skill", "released": bool(run.released), "artifact": doc,
            "verdict": verdict, "claims": doc.get("claims") or [],
            "evidence": doc.get("evidence") or [], "refusals": refusals,
            "labels": [], "licences": _licences_from_sources(doc.get("sources") or []),
            "limitations": [*limits, *(doc.get("limitations") or [])], "outputs": outputs}


# --------------------------------------------------------------- Runtime.invoke

def from_call_result(res: Any, *, kind: str, licences: Sequence[Mapping[str, Any]] = (),
                     limits: Sequence[str] = ()) -> dict[str, Any]:
    """Governance of a ``CallResult``: the policy kernel's denials as refusals (with the
    stable rule ids as codes), the commercial usage gate's per-asset licence decisions."""
    gov = empty(kind)
    gov["licences"] = [dict(x) for x in licences]
    gov["limitations"] = list(limits)
    auth = getattr(res, "authorization", None)
    meta = dict(getattr(res, "metadata", None) or {})
    if auth is not None:
        rulings = [{"decision": getattr(r.decision, "value", str(r.decision)), "rule": r.rule,
                    "reason": r.reason} for r in getattr(auth, "rulings", ()) or ()]
        gov["policy"] = {"allowed": bool(auth.allowed), "reason": auth.reason,
                         "rulings": rulings}
        for r in rulings:
            if str(r["decision"]).upper() == "DENY":
                gov["refusals"].append(refusal(r["rule"], r["reason"]))
    usage = meta.get("usage")
    if isinstance(usage, Mapping):
        gov["licences"] += _licences_from_usage(usage)
        if str(usage.get("verdict", "")).lower() == "deny":
            for asset in usage.get("assets") or ():
                if isinstance(asset, Mapping) and str(asset.get("verdict", "")).upper() == "DENY":
                    gov["refusals"].append(refusal(asset.get("rule") or "usage.deny",
                                                   asset.get("reason") or usage.get("reason", "")))
    return gov
