"""The call envelope (CONTRACTS §3): one shape for every tool call, in both runtimes.

``shape`` turns what an executor produced into the envelope: the compact ``text`` the
model reads, the full ``result`` for the UI, the citations, the governance fields and a
receipt saying where and on what the call ran. It never raises: a value it cannot render
becomes its ``repr``, and a failure inside it becomes a ``failed`` envelope.

The ``text`` restates the limits that apply to the result (no record ≠ safe, predicted ≠
measured, a draft for a licensed practitioner, a classical record is an attribution),
because the model reads only the text and must not lose them to truncation.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import math
import platform
import re
import sys
from datetime import date, datetime, timezone
from pathlib import PurePath
from typing import Any, Iterable, Mapping

__all__ = ["TEXT_LIMIT", "STATUSES", "ERROR_TYPES", "KINDS", "canonical_json", "jsonable",
           "sha256_json", "sha256_bytes", "utc_now", "runtime_string", "versions",
           "package_version", "extract_citations", "identifier_url", "shape", "compose_text",
           "fit_json"]

TEXT_LIMIT = 16_000
STATUSES = ("succeeded", "failed", "refused", "needs_approval", "job_submitted", "cancelled")
ERROR_TYPES = ("bad_arguments", "unavailable", "not_found", "refused", "runtime_error",
               "timeout", "network_off")
KINDS = ("native", "skill", "connector", "clinic", "tcmdb", "study", "job", "system")

_MAX_DEPTH = 48
_MAX_CITATIONS = 40


# ----------------------------------------------------------------------------- JSON

def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _float(value: float) -> Any:
    # JSON has no NaN or infinity; JSON.parse in the page would reject the document.
    # The strings keep the meaning that a null would lose.
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    return value


def jsonable(value: Any, _depth: int = 0, _stack: tuple[int, ...] = ()) -> Any:
    """A JSON-compatible copy of ``value``. Never raises."""
    try:
        if value is None or isinstance(value, (bool, str)):
            return value
        if isinstance(value, enum.Enum):
            inner = value.value
            return inner if isinstance(inner, str) else value.name
        if isinstance(value, int):
            return int(value)
        if isinstance(value, float):
            return _float(float(value))
        if _depth > _MAX_DEPTH:
            return "…"
        if id(value) in _stack:
            return "<cycle>"
        stack = _stack + (id(value),)
        if isinstance(value, Mapping):
            return {str(k) if not isinstance(k, enum.Enum) else str(jsonable(k)):
                    jsonable(v, _depth + 1, stack) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [jsonable(v, _depth + 1, stack) for v in value]
        if isinstance(value, (set, frozenset)):
            items = [jsonable(v, _depth + 1, stack) for v in value]
            try:
                return sorted(items, key=lambda x: json.dumps(x, sort_keys=True,
                                                              ensure_ascii=False))
            except Exception:                                   # noqa: BLE001
                return items
        if isinstance(value, PurePath):
            return str(value)
        if isinstance(value, (bytes, bytearray, memoryview)):
            data = bytes(value)
            return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        for method in ("document", "as_dict", "to_dict"):
            fn = getattr(value, method, None)
            if callable(fn):
                try:
                    return jsonable(fn(), _depth + 1, stack)
                except Exception:                               # noqa: BLE001
                    break
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return {f.name: jsonable(getattr(value, f.name, None), _depth + 1, stack)
                    for f in dataclasses.fields(value)}
        tolist = getattr(value, "tolist", None)          # numpy arrays and scalars
        if callable(tolist):
            try:
                return jsonable(tolist(), _depth + 1, stack)
            except Exception:                                   # noqa: BLE001
                pass
        text = repr(value)
        return text if len(text) <= 500 else text[:499] + "…"
    except Exception:                                           # noqa: BLE001
        try:
            return f"<unrenderable {type(value).__name__}>"
        except Exception:                                       # noqa: BLE001
            return "<unrenderable>"


def canonical_json(value: Any) -> str:
    """Sorted keys, no whitespace, non-ASCII kept: the bytes every receipt hash covers."""
    return json.dumps(jsonable(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------- environment

def runtime_string() -> str:
    """``pyodide-314.0.7 / CPython 3.14.2`` in the browser, ``CPython 3.13.16 / linux-x86_64``
    on the runner."""
    impl = {"cpython": "CPython", "pypy": "PyPy"}.get(sys.implementation.name,
                                                     sys.implementation.name)
    py = "%d.%d.%d" % sys.version_info[:3]
    if sys.platform == "emscripten":
        try:
            import pyodide                                  # type: ignore[import-not-found]
            version = str(getattr(pyodide, "__version__", "") or "unknown")
        except Exception:                                       # noqa: BLE001
            version = "unknown"
        return f"pyodide-{version} / {impl} {py}"
    try:
        machine = f"{platform.system().lower()}-{platform.machine().lower()}"
    except Exception:                                           # noqa: BLE001
        machine = sys.platform
    return f"{impl} {py} / {machine}"


def _pyproject_version(module: Any, dist: str) -> str | None:
    """The version in the ``pyproject.toml`` of a source tree the module was imported from
    (the browser bundle mirrors the repository and carries no installed metadata)."""
    try:
        import tomllib
        from pathlib import Path
        here = Path(getattr(module, "__file__", "") or "").resolve()
        for parent in list(here.parents)[:5]:
            candidate = parent / "pyproject.toml"
            if candidate.is_file():
                data = tomllib.loads(candidate.read_text(encoding="utf-8"))
                project = data.get("project") or {}
                if project.get("name") == dist and project.get("version"):
                    return str(project["version"])
    except Exception:                                           # noqa: BLE001
        return None
    return None


def package_version(dist: str, module_name: str | None = None) -> str:
    """Installed metadata first; then the package's ``__version__``; then its pyproject."""
    try:
        from importlib.metadata import version
        return version(dist)
    except Exception:                                           # noqa: BLE001
        pass
    module = sys.modules.get(module_name or dist)
    if module is None:
        try:
            import importlib
            module = importlib.import_module(module_name or dist)
        except Exception:                                       # noqa: BLE001
            return "unknown"
    found = getattr(module, "__version__", None)
    if found:
        return str(found)
    return _pyproject_version(module, dist) or "unknown"


_VERSIONS: dict[str, str] | None = None


def versions() -> dict[str, str]:
    global _VERSIONS
    if _VERSIONS is None:
        _VERSIONS = {"tcmstudio": package_version("tcmstudio"),
                     "bioagent": package_version("bioagent"),
                     "psh": package_version("psh"),
                     "python": "%d.%d.%d" % sys.version_info[:3]}
    return dict(_VERSIONS)


# --------------------------------------------------------------------- citations

_ID_URL = {
    "pmid": "https://pubmed.ncbi.nlm.nih.gov/{}/",
    "pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/{}/",
    "doi": "https://doi.org/{}",
    "nct": "https://clinicaltrials.gov/study/{}",
    "isrctn": "https://www.isrctn.com/{}",
    "chictr": "https://www.chictr.org.cn/searchprojEN.html?regno={}",
}
_CITATION_KIND = {"pmid": "pmid", "pmcid": "pmid", "doi": "doi", "nct": "nct", "chictr": "nct",
                  "isrctn": "nct", "classical_passage": "classical"}

_RE_PMID = re.compile(r"\bpmid\s*[:：]?\s*(\d{1,9})\b", re.I)
_RE_DOI = re.compile(r"\bdoi\s*[:：]\s*(10\.\d{4,9}/[^\s\"'<>，。；、]+)", re.I)
_RE_BARE_DOI = re.compile(r"^(10\.\d{4,9}/[^\s\"'<>]+)$")
_RE_NCT = re.compile(r"\b(NCT\d{8})\b", re.I)
_RE_PASSAGE = re.compile(r"^passage\.[A-Za-z0-9_.-]+$")


def identifier_url(kind: str, value: str) -> str:
    value = str(value or "").strip()
    template = _ID_URL.get(str(kind or "").lower())
    if not value or not template:
        return ""
    if kind == "isrctn" and not value.upper().startswith("ISRCTN"):
        value = "ISRCTN" + value
    from urllib.parse import quote
    return template.format(quote(value, safe="/:._-()"))


class _Citations:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self._seen: set[tuple[str, str]] = set()

    def add(self, kind: str, key: str, label: str, url: str = "", ref: str = "") -> None:
        key = str(key).strip()
        ref = str(ref or key).strip()
        if not key or len(self.items) >= _MAX_CITATIONS:
            return
        mark = (kind, ref.lower())
        if mark in self._seen:
            return
        self._seen.add(mark)
        label = " ".join(str(label or key).split())
        self.items.append({"id": "", "kind": kind, "label": label[:300], "url": url,
                           "evidence_ref": ref})

    def alias(self, kind: str, ref: str) -> None:
        """Mark another identifier of a record already cited, so it is not cited twice."""
        self._seen.add((kind, str(ref).strip().lower()))


def _label_from(record: Mapping[str, Any]) -> str:
    for key in ("title", "name", "label", "citation", "description"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, list) and value and isinstance(value[0], str):
            return value[0]                       # Crossref titles are lists
    return ""


def _scan(value: Any, out: _Citations, budget: list[int], depth: int = 0) -> None:
    if budget[0] <= 0 or depth > 24 or len(out.items) >= _MAX_CITATIONS:
        return
    budget[0] -= 1
    if isinstance(value, Mapping):
        rec = value
        label = _label_from(rec)
        rid = rec.get("id")
        if isinstance(rid, str) and _RE_PASSAGE.match(rid) and (rec.get("source")
                                                               or rec.get("text")):
            source = str(rec.get("source") or "").strip()
            chapter = str(rec.get("chapter") or "").strip()
            text = f"《{source}》{chapter}" if source else rid
            out.add("classical", rid, text, "", rid)
        # One citation per record: a paper with a PMID and a DOI is one paper. The PMID
        # is preferred; the record's other identifiers are marked as already cited.
        found: list[tuple[str, str, str, str]] = []
        for key in ("pmid", "PMID", "pubmed_id"):
            v = rec.get(key)
            if isinstance(v, (str, int)) and str(v).strip().isdigit():
                pid = str(v).strip()
                found.append(("pmid", pid, identifier_url("pmid", pid), f"pmid:{pid}"))
        for key in ("doi", "DOI"):
            v = rec.get(key)
            if isinstance(v, str) and v.strip().startswith("10."):
                doi = v.strip()
                found.append(("doi", doi, identifier_url("doi", doi), f"doi:{doi}"))
        for key in ("nct_id", "nctId", "NCTId", "nct"):
            v = rec.get(key)
            if isinstance(v, str) and _RE_NCT.fullmatch(v.strip()):
                nct = v.strip().upper()
                found.append(("nct", nct, identifier_url("nct", nct), f"nct:{nct}"))
        if found:
            kind, key, url, ref = found[0]
            default = f"PMID {key}" if kind == "pmid" else (f"doi:{key}" if kind == "doi" else key)
            out.add(kind, key, label or default, url, ref)
            for other in found[1:]:
                out.alias(other[0], other[3])
        for v in rec.values():
            _scan(v, out, budget, depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value[:400]:
            _scan(v, out, budget, depth + 1)
    elif isinstance(value, str) and len(value) <= 4000:
        if _RE_PASSAGE.match(value):
            out.add("classical", value, value, "", value)
            return
        for m in _RE_PMID.finditer(value):
            out.add("pmid", m.group(1), f"PMID {m.group(1)}", identifier_url("pmid", m.group(1)),
                    f"pmid:{m.group(1)}")
        for m in _RE_DOI.finditer(value):
            doi = m.group(1).rstrip(".,;)")
            out.add("doi", doi, f"doi:{doi}", identifier_url("doi", doi), f"doi:{doi}")
        bare = _RE_BARE_DOI.match(value.strip())
        if bare:
            out.add("doi", bare.group(1), f"doi:{bare.group(1)}",
                    identifier_url("doi", bare.group(1)), f"doi:{bare.group(1)}")
        for m in _RE_NCT.finditer(value):
            nct = m.group(1).upper()
            out.add("nct", nct, nct, identifier_url("nct", nct), f"nct:{nct}")


def extract_citations(result: Any, governance: Mapping[str, Any] | None = None,
                      given: Iterable[Mapping[str, Any]] = ()) -> list[dict[str, Any]]:
    """Citations: the evidence items of an artifact first (each ``evidence_ref`` is the
    item's id), then identifiers found in the result (PMID, DOI, NCT, classical passages).
    Numbered E1, E2 … in that order. Never raises."""
    out = _Citations()
    try:
        for c in given or ():
            if isinstance(c, Mapping):
                out.add(str(c.get("kind") or "source"),
                        str(c.get("evidence_ref") or c.get("label") or c.get("id") or ""),
                        str(c.get("label") or ""), str(c.get("url") or ""),
                        str(c.get("evidence_ref") or ""))
        evidence = (governance or {}).get("evidence") or []
        for item in evidence if isinstance(evidence, list) else []:
            if not isinstance(item, Mapping) or not item.get("id"):
                continue
            itype = str(item.get("identifier_type") or "").lower()
            ident = str(item.get("identifier") or "")
            kind = _CITATION_KIND.get(itype, "source")
            label = str(item.get("citation") or item.get("title") or item["id"])
            out.add(kind, str(item["id"]), label, identifier_url(itype, ident), str(item["id"]))
        _scan(jsonable(result), out, [20_000])
    except Exception:                                           # noqa: BLE001
        pass
    for n, c in enumerate(out.items, 1):
        c["id"] = f"E{n}"
    return out.items


# -------------------------------------------------------------------------- text

def fit_json(value: Any, budget: int) -> str:
    """Compact JSON of ``value`` within ``budget`` characters, shortened structurally
    (long lists and strings first) and, as a last resort, cut with a marker."""
    value = jsonable(value)
    if budget <= 0:
        return ""
    plain = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(plain) <= budget:
        return plain
    for items, chars in ((40, 2000), (20, 800), (10, 400), (6, 200), (3, 120), (2, 80),
                         (1, 60)):
        text = json.dumps(_shrink(value, items, chars), ensure_ascii=False,
                          separators=(",", ":"))
        if len(text) <= budget:
            return text
    marker = "…[truncated; the full result is in the UI]"
    return text[:max(0, budget - len(marker))] + marker


def _shrink(value: Any, items: int, chars: int, depth: int = 0) -> Any:
    if isinstance(value, str):
        return value if len(value) <= chars else value[:chars] + f"…(+{len(value) - chars} chars)"
    if isinstance(value, list):
        head = [_shrink(v, items, chars, depth + 1) for v in value[:items]]
        if len(value) > items:
            head.append(f"…(+{len(value) - items} more)")
        return head
    if isinstance(value, dict):
        keys = list(value)
        limit = max(items * 3, 12)
        out = {k: _shrink(value[k], items, chars, depth + 1) for k in keys[:limit]}
        if len(keys) > limit:
            out["…"] = f"+{len(keys) - limit} more keys"
        return out
    return value


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def compose_text(*, tool: str, via: str, status: str, summary: str,
                 governance: Mapping[str, Any], citations: list[Mapping[str, Any]],
                 model_view: Any, error: Mapping[str, Any] | None,
                 job: Mapping[str, Any] | None, notes: Iterable[str] = (),
                 limit: int = TEXT_LIMIT) -> str:
    """The model's view of a call: status, limits, what the kernel decided, citations to
    use as [E1], then the result as compact JSON fitted to what is left of ``limit``."""
    lines: list[str] = [f"{tool} → {via or tool}: {status}"]
    if summary:
        lines.append(f"Summary: {_clip(summary, 400)}")
    if error:
        err = f"Error ({error.get('type', 'runtime_error')}): {_clip(error.get('message', ''), 1200)}"
        if error.get("hint"):
            err += f" Hint: {_clip(error['hint'], 600)}"
        lines.append(err)
    limitations = [str(x) for x in (governance.get("limitations") or []) if x]
    if limitations:
        lines.append("Limits (state these when you use this result):")
        for x in limitations[:8]:
            lines.append(f"- {_clip(x, 320)}")
        if len(limitations) > 8:
            lines.append(f"- … {len(limitations) - 8} more in the UI")
    released = governance.get("released")
    verdict = governance.get("verdict") or {}
    if released is not None or verdict:
        states = verdict.get("states") if isinstance(verdict, Mapping) else None
        line = "Release: " + ("released (release_authorized)" if released
                              else "NOT released")
        if isinstance(states, Mapping) and states:
            ok = sum(1 for v in states.values() if v)
            line += f"; states {ok}/{len(states)}"
            missing = [k for k, v in states.items() if not v]
            if missing:
                line += " (unmet: " + ", ".join(missing) + ")"
        lines.append(line)
        for why in (verdict.get("unverified") or [])[:4] if isinstance(verdict, Mapping) else []:
            lines.append(f"- unverified: {_clip(why, 240)}")
    claims = governance.get("claims") or []
    if claims:
        verdicts = {}
        if isinstance(verdict, Mapping):
            verdicts = {v.get("claim_id"): v for v in verdict.get("claim_verdicts") or []
                        if isinstance(v, Mapping)}
        lines.append("Claims (the kernel's verdict on each):")
        for c in claims[:10]:
            if not isinstance(c, Mapping):
                continue
            v = verdicts.get(c.get("id"), {})
            mark = ("allowed" if v.get("allowed") else "REFUSED") if v else "unchecked"
            codes = ",".join(v.get("codes") or []) if v else ""
            lines.append(f"- [{c.get('claim_kind', '?')}] {_clip(c.get('text', ''), 260)}"
                         f" — {mark}{' (' + codes + ')' if codes else ''}")
    refusals = governance.get("refusals") or []
    if refusals:
        lines.append("Refused (a refusal is a result; report it and its reason):")
        for r in refusals[:8]:
            if isinstance(r, Mapping):
                remedy = f" Remedy: {_clip(r['remedy'], 200)}" if r.get("remedy") else ""
                lines.append(f"- {r.get('code', '?')}: {_clip(r.get('message', ''), 260)}{remedy}")
    if job:
        lines.append(f"Job: {job.get('id')} {job.get('kind', '')} state={job.get('state')}"
                     " (pending work is not a result; follow it with job_status)")
    for note in notes:
        lines.append(f"Note: {_clip(note, 300)}")
    if citations:
        lines.append("Citations (cite as [E1] …; do not invent others):")
        for c in citations[:_MAX_CITATIONS]:
            url = f" <{c['url']}>" if c.get("url") else ""
            lines.append(f"[{c['id']}] {_clip(c.get('label', ''), 160)}{url}")
    head = "\n".join(lines)
    if len(head) > limit - 200:
        head = head[: limit - 260] + "\n…[header truncated]"
    if model_view is None:
        return head[:limit]
    budget = limit - len(head) - len("\nResult: ")
    return (head + "\nResult: " + fit_json(model_view, budget))[:limit]


# ------------------------------------------------------------------------ shape

_STATUS_ZH = {"succeeded": "完成", "failed": "未完成", "refused": "已拒绝",
              "needs_approval": "待批准", "job_submitted": "已提交任务", "cancelled": "已取消"}


def _governance(kind: str, given: Mapping[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": kind, "released": None, "artifact": None, "verdict": None,
                           "claims": [], "evidence": [], "refusals": [], "labels": [],
                           "licences": [], "limitations": [], "outputs": []}
    for key, value in dict(given or {}).items():
        out[key] = jsonable(value)
    for key in ("claims", "evidence", "refusals", "labels", "licences", "limitations", "outputs"):
        if not isinstance(out.get(key), list):
            out[key] = [] if out.get(key) is None else [out[key]]
    if out["kind"] not in KINDS:
        out["kind"] = kind if kind in KINDS else "system"
    seen: set[str] = set()
    unique = []
    for x in out["limitations"]:                         # keep order, drop repeats
        key = str(x)
        if key not in seen:
            seen.add(key)
            unique.append(x)
    out["limitations"] = unique
    return out


def shape(tool: str, *, via: str | None = None, kind: str = "system",
          status: str = "succeeded", result: Any = None, model_view: Any = ...,
          summary: str = "", text: str | None = None,
          citations: Iterable[Mapping[str, Any]] | None = None,
          governance: Mapping[str, Any] | None = None,
          error: Mapping[str, Any] | None = None, job: Mapping[str, Any] | None = None,
          approval: Mapping[str, Any] | None = None, arguments: Any = None,
          started_at: str | None = None, duration_ms: int | float | None = None,
          where: str = "runner", device: str = "cpu", content_hash: str | None = None,
          audit_head: str | None = None, composite_version: str | None = None,
          receipt_extra: Mapping[str, Any] | None = None, notes: Iterable[str] = (),
          text_limit: int = TEXT_LIMIT) -> dict[str, Any]:
    """The §3 envelope. Never raises."""
    try:
        status = status if status in STATUSES else "failed"
        result_j = jsonable(result)
        gov = _governance(kind, governance)
        err = None
        if error:
            err = {"type": str(error.get("type") or "runtime_error"),
                   "message": str(error.get("message") or ""),
                   "hint": str(error.get("hint") or "")}
            if err["type"] not in ERROR_TYPES:
                err["type"] = "runtime_error"
        cites = extract_citations(result_j, gov, given=citations or ())
        job_j = jsonable(job) if job else None
        if not summary:
            summary = _STATUS_ZH.get(status, status)
            if err and err["message"]:
                summary += "：" + _clip(err["message"], 120)
        view = result_j if model_view is ... else jsonable(model_view)
        if text is None:
            text = compose_text(tool=tool, via=via or tool, status=status, summary=summary,
                                governance=gov, citations=cites, model_view=view, error=err,
                                job=job_j, notes=notes, limit=text_limit)
        text = str(text)[:text_limit]
        receipt: dict[str, Any] = {
            "where": where if where in ("browser", "runner") else "runner",
            "runtime": runtime_string(), "device": device or "cpu", "versions": versions(),
            "composite_version": composite_version, "content_hash": content_hash,
            "audit_head": audit_head or None, "input_sha256": sha256_json(arguments or {}),
            "output_sha256": sha256_json(result_j), "started_at": started_at or utc_now()}
        for key, value in dict(receipt_extra or {}).items():
            receipt[key] = jsonable(value)
        return {"ok": status in ("succeeded", "job_submitted"), "tool": str(tool),
                "via": via or str(tool), "status": status,
                "duration_ms": int(duration_ms) if duration_ms is not None else 0,
                "summary": summary, "text": text, "result": result_j, "citations": cites,
                "governance": gov, "receipt": receipt, "job": job_j,
                "approval": jsonable(approval) if approval else None, "error": err}
    except BaseException as exc:                                # noqa: BLE001
        if isinstance(exc, KeyboardInterrupt):
            raise
        return _fallback(tool, via, kind, exc, started_at, where)


def _fallback(tool: Any, via: Any, kind: str, exc: BaseException, started_at: str | None,
              where: str) -> dict[str, Any]:
    message = f"the envelope could not be built: {type(exc).__name__}: {exc}"[:2000]
    return {"ok": False, "tool": str(tool), "via": str(via or tool), "status": "failed",
            "duration_ms": 0, "summary": "未完成：结果无法封装", "text": f"{tool}: failed. {message}",
            "result": None, "citations": [],
            "governance": {"kind": kind if kind in KINDS else "system", "released": None,
                           "artifact": None, "verdict": None, "claims": [], "evidence": [],
                           "refusals": [], "labels": [], "licences": [], "limitations": [],
                           "outputs": []},
            "receipt": {"where": where if where in ("browser", "runner") else "runner",
                        "runtime": "unknown", "device": "cpu", "versions": {},
                        "composite_version": None, "content_hash": None, "audit_head": None,
                        "input_sha256": None, "output_sha256": None,
                        "started_at": started_at or utc_now()},
            "job": None, "approval": None,
            "error": {"type": "runtime_error", "message": message, "hint": ""}}
