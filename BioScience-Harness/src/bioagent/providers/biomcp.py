"""BioMCP's literature, trial and variant tools: a reviewed configuration, and records.

BioMCP (biomcp-python 0.7.3, MIT) is an MCP server, run over stdio, in front of PubTator3
and PubMed, bioRxiv and Europe PMC, ClinicalTrials.gov and MyVariant.info. Listed on
2026-10-07 it offered 36 tools; five are admitted here. This module does not start the
server or speak MCP — the transport, and the dispatcher behind ``MCPBackend``, are built
separately. It holds, as data, what that transport must apply, and what to make of the
answers.

**The configuration** (``registry/biomcp_server.yaml``, a draft until the transport reads
it) says how the server is started and with which environment, which tools are admitted,
the SHA-256 of each one's input schema as listed, the arguments fixed for each, the
arguments each accepts and ignores, and the hosts each contacts. :func:`check_listing`
compares a ``tools/list`` answer with it: a tool it does not name is not admitted, and an
admitted tool whose schema changed is refused rather than called with arguments shaped for
the old one. :func:`arguments_for` applies the fixed arguments and refuses the ignored
ones. BioMCP 0.7.3 accepts ``page`` on its article search and ``sex`` on its trial search
and drops both; a query recorded with them would claim a filter that was never applied.

**The adapter** (:func:`adapt`) turns one reply into :class:`SourceRecord` objects: the
identifiers that name each record, canonical (``sources.identity``), so that one paper
reached through PubMed, BioMCP and Open Targets counts once; the query as sent; when it was
retrieved; the server package and version; and the record's text exactly as returned, so a
quote taken from it carries a receipt (:meth:`SourceRecord.evidence`). Three things in the
replies of 0.7.3 would otherwise be recorded wrong:

* errors arrive as successful replies. ``article_getter`` answers a PMCID — which its own
  description says it accepts — with ``[{"error": "Invalid identifier format ..."}]`` and
  ``isError`` false, and the trial and variant tools render errors as ``Error:`` lines.
  Such a reply is a failure with no records, never an empty success;
* ``serverInfo.version`` in the ``initialize`` answer is the MCP SDK's version (1.30.0),
  not BioMCP's. The version recorded is the configured package version;
* a variant's position is in MyVariant's default assembly, GRCh37, which the record states
  only inside a UCSC link. Its genomic HGVS id is recorded with the assembly; without it
  the id would match the GRCh38 position of a different base.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..sources.identity import (SCHEMES, SourceCount, SourceId, canonical, canonical_many,
                                group_sources)
from ..status import ExecutionStatus

__all__ = ["ToolPolicy", "ServerConfig", "ListingCheck", "SourceRecord", "AdaptedReply",
           "load_server_config", "schema_digest", "check_listing", "arguments_for", "adapt",
           "group_records"]

#: The kind of source each record kind's card describes (``contracts.source_card``).
_CARD_KIND = {"article": "literature_index", "trial": "trial_registry",
              "variant": "database"}
#: Which upstream each tool's records come from, when the record does not say.
_ORIGIN = {"trial": "ClinicalTrials.gov", "variant": "MyVariant.info"}
_RECORD = re.compile(r"^# Record \d+[ \t]*$", re.M)
_HEADING = re.compile(r"^(#+)\s+(.*?)\s*$")
_FIELD = re.compile(r"^(?P<key>[A-Z][\w ()/.-]*?):(?:\s(?P<value>.*))?$")
_UCSC_BUILD = re.compile(r"[?&]db=(hg19|hg38)\b")


# --------------------------------------------------------------------- configuration
@dataclass(frozen=True)
class ToolPolicy:
    """One admitted tool, as reviewed."""

    name: str
    input_schema_sha256: str
    record_kind: str
    hosts: tuple[str, ...]
    fixed_arguments: Mapping[str, Any] = field(default_factory=dict)
    ignored_arguments: tuple[str, ...] = ()
    assembly: str = ""
    notes: str = ""


@dataclass(frozen=True)
class ServerConfig:
    """The reviewed server: how it starts, and which of its tools are admitted."""

    id: str
    package: str
    version: str
    licence: str
    repo: str
    command: tuple[str, ...]
    cwd: str
    env_pass: tuple[str, ...]
    env_set: Mapping[str, str]
    destination: str
    tools: Mapping[str, ToolPolicy]
    not_admitted: Mapping[str, str]
    status: str
    reviewed_on: str
    path: str = ""

    @property
    def server(self) -> str:
        """``biomcp-python 0.7.3``: what is recorded as the version that answered."""
        return f"{self.package} {self.version}"


def default_config_path() -> Path:
    from ..config import registry_dir
    return registry_dir() / "biomcp_server.yaml"


def load_server_config(path: str | Path | None = None) -> ServerConfig:
    """Read and check the configuration. A tool entry missing a field is an error."""
    import yaml

    target = Path(path) if path else default_config_path()
    doc = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    server = doc.get("server") or {}
    tools: dict[str, ToolPolicy] = {}
    for name, raw in (doc.get("tools") or {}).items():
        missing = [k for k in ("input_schema_sha256", "record_kind", "hosts")
                   if not raw.get(k)]
        if missing:
            raise ValueError(f"{target}: tool {name!r} lacks {missing}")
        if raw["record_kind"] not in _CARD_KIND:
            raise ValueError(f"{target}: tool {name!r} has record_kind "
                             f"{raw['record_kind']!r}, not one of {sorted(_CARD_KIND)}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(raw["input_schema_sha256"])):
            raise ValueError(f"{target}: tool {name!r}: input_schema_sha256 is not a sha256")
        tools[str(name)] = ToolPolicy(
            name=str(name), input_schema_sha256=str(raw["input_schema_sha256"]),
            record_kind=str(raw["record_kind"]), hosts=tuple(raw["hosts"]),
            fixed_arguments=dict(raw.get("fixed_arguments") or {}),
            ignored_arguments=tuple(raw.get("ignored_arguments") or ()),
            assembly=str(raw.get("assembly") or ""),
            notes=" ".join(str(raw.get("notes") or "").split()))
    env = server.get("env") or {}
    return ServerConfig(
        id=str(server["id"]), package=str(server["package"]), version=str(server["version"]),
        licence=str(server.get("licence") or ""), repo=str(server.get("repo") or ""),
        command=tuple(str(c) for c in server.get("command") or ()),
        cwd=str(server.get("cwd") or ""), env_pass=tuple(env.get("pass") or ()),
        env_set={str(k): str(v) for k, v in (env.get("set") or {}).items()},
        destination=str(server.get("destination") or ""), tools=tools,
        not_admitted={str(k): " ".join(str(v).split())
                      for k, v in (doc.get("not_admitted") or {}).items()},
        status=str(doc.get("status") or ""), reviewed_on=str(doc.get("reviewed_on") or ""),
        path=str(target))


def schema_digest(schema: Mapping[str, Any]) -> str:
    """SHA-256 of an input schema as canonical JSON: what the configuration pins."""
    blob = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ListingCheck:
    """A ``tools/list`` answer held against the configuration."""

    admitted: tuple[str, ...]
    refused: tuple[tuple[str, str], ...]
    missing: tuple[str, ...]
    not_admitted: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """Every configured tool was listed with its reviewed schema."""
        return not self.refused and not self.missing


def check_listing(tools: Iterable[Mapping[str, Any]],
                  config: ServerConfig | None = None) -> ListingCheck:
    """Which listed tools may be admitted: configured, and with the schema reviewed.

    Takes the protocol's ``inputSchema`` or a snake-case ``input_schema``. A tool listed
    twice is refused: which of the two a call would reach is the server's choice.
    """
    config = config or load_server_config()
    seen: dict[str, list[Mapping[str, Any]]] = {}
    for tool in tools:
        seen.setdefault(str(tool.get("name") or ""), []).append(tool)
    admitted, refused = [], []
    for name, policy in sorted(config.tools.items()):
        entries = seen.get(name) or []
        if len(entries) > 1:
            refused.append((name, f"listed {len(entries)} times"))
            continue
        if not entries:
            continue
        schema = entries[0].get("inputSchema", entries[0].get("input_schema"))
        digest = schema_digest(schema or {})
        if digest != policy.input_schema_sha256:
            refused.append((name, f"input schema sha256 {digest[:16]}…, reviewed "
                                  f"{policy.input_schema_sha256[:16]}…"))
            continue
        admitted.append(name)
    missing = tuple(sorted(n for n in config.tools if n not in seen))
    return ListingCheck(tuple(admitted), tuple(refused), missing,
                        tuple(sorted(n for n in seen if n and n not in config.tools)))


def arguments_for(tool: str, arguments: Mapping[str, Any] | None = None,
                  config: ServerConfig | None = None) -> dict[str, Any]:
    """The arguments to send: the caller's, with the tool's fixed arguments applied.

    Raises ``ValueError`` for a tool that is not admitted, for an argument the tool
    ignores, and for a caller value that contradicts a fixed one.
    """
    config = config or load_server_config()
    policy = config.tools.get(tool)
    if policy is None:
        raise ValueError(f"BioMCP tool {tool!r} is not admitted "
                         f"({config.not_admitted.get(tool) or 'not in the configuration'})")
    given = dict(arguments or {})
    ignored = sorted(set(given) & set(policy.ignored_arguments))
    if ignored:
        raise ValueError(f"{config.server} {tool} accepts and ignores {ignored}; a query "
                         "recorded with them would claim filters that were not applied")
    clash = sorted(k for k, v in policy.fixed_arguments.items()
                   if k in given and given[k] != v)
    if clash:
        raise ValueError(f"{tool}: {clash} are fixed by the review to "
                         f"{ {k: policy.fixed_arguments[k] for k in clash} }")
    return {**given, **policy.fixed_arguments}


# --------------------------------------------------------------------------- records
@dataclass(frozen=True)
class SourceRecord:
    """One record a BioMCP tool returned: what names it, and its text as returned."""

    kind: str
    identifiers: tuple[SourceId, ...]
    title: str
    content: str
    tool: str
    query: Mapping[str, Any]
    retrieved_at: str
    server: str
    origin: str = ""
    fields: Mapping[str, str] = field(default_factory=dict)

    @property
    def key(self) -> SourceId | None:
        """The identifier the record is cited by. A variant record is cited by its allele
        (HGVS), not by the rsID it shares with the other alleles at that position."""
        order = ("hgvs", "rsid") if self.kind == "variant" else SCHEMES
        ranked = sorted(self.identifiers, key=lambda s: (
            order.index(s.scheme) if s.scheme in order else len(order), s.value))
        return ranked[0] if ranked else None

    def evidence(self, *, design: str, source_card_id: str, quote: str | None = None,
                 item_id: str = "", run_id: str = "", store: Any = None) -> Any:
        """A candidate ``EvidenceItem`` with a receipt for its quote in this record.

        ``design`` is the caller's assessment: a search hit does not say whether it is a
        trial or a review, and this adapter does not guess. ``quote`` defaults to the whole
        record and must be a verbatim excerpt of it; the record is kept in ``store``
        (``contracts.receipts``, the default store when None) so the receipt can be checked
        later. Retraction stays ``unverified``.
        """
        from ..contracts import EvidenceItem
        from ..contracts.evidence_item import IDENTIFIER_TYPES

        key = self.key
        kind = key.scheme if key and key.scheme in IDENTIFIER_TYPES else (
            "registry_record" if key else "")
        when = datetime.fromisoformat(self.retrieved_at.replace("Z", "+00:00")).timestamp()
        item = EvidenceItem(
            id=item_id or f"biomcp.{self.tool}.{key or 'unidentified'}", design=design,
            quote=quote if quote is not None else self.content,
            citation=f"{self.title} ({key})" if key else self.title, title=self.title,
            identifier=(key.value if kind in ("pmid", "pmcid", "doi", "nct") else str(key))
            if key else "", identifier_type=kind, source_card_id=source_card_id,
            retrieved_by=f"{self.server} {self.tool}", retrieval_run=run_id,
            retrieved_at=when)
        return item.located_in(self.content, store=store)


@dataclass(frozen=True)
class AdaptedReply:
    """One reply, adapted: its status, its records, and what was asked."""

    tool: str
    query: Mapping[str, Any]
    retrieved_at: str
    server: str
    status: ExecutionStatus
    reason: str
    records: tuple[SourceRecord, ...]
    text: str

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def source_card(self, config: ServerConfig | None = None) -> Any:
        """The reply as a source pinned by its content at the moment it was retrieved."""
        from ..contracts import SourceCard

        config = config or load_server_config()
        policy = config.tools[self.tool]
        return SourceCard(
            id=f"{config.id}.{self.tool}.{self.content_sha256[:12]}",
            name=f"BioMCP {self.tool}", kind=_CARD_KIND[policy.record_kind],
            maintainer="GenomOncology (BioMCP)", version=self.server, home_url=config.repo,
            access_method="mcp", allowed_hosts=policy.hosts, license_spdx="",
            license_note=(f"{config.licence} is BioMCP's code; the records are under the "
                          "terms of the services it queries, which the reply does not state"),
            integration_mode="federated", snapshot_hash=self.content_sha256,
            snapshot_at=self.retrieved_at,
            known_limits=tuple(x for x in (policy.notes,) if x),
            notes=f"query: {json.dumps(dict(self.query), sort_keys=True, default=str)}")


def adapt(tool: str, reply: Any, *, arguments: Mapping[str, Any], retrieved_at: str,
          config: ServerConfig | None = None) -> AdaptedReply:
    """``reply`` to ``tool`` called with ``arguments`` at ``retrieved_at``, as records.

    ``reply`` is the protocol's ``{"content": [...], "isError": ...}``, the
    ``{"result": text}`` structured content BioMCP also sends (what PSH's MCP adapter
    returns), or the text itself. ``retrieved_at`` is when the call was made, ISO 8601.
    """
    config = config or load_server_config()
    datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))     # refuse a non-date
    query = dict(arguments)

    def done(status: ExecutionStatus, reason: str = "",
             records: tuple[SourceRecord, ...] = (), text: str = "") -> AdaptedReply:
        return AdaptedReply(tool, query, retrieved_at, config.server, status, reason,
                            records, text)

    policy = config.tools.get(tool)
    if policy is None:
        return done(ExecutionStatus.DENIED, f"{tool!r} is not an admitted BioMCP tool")
    text, is_error = _reply_text(reply)
    if is_error:
        return done(_error_status(text), f"the server reported an error: {text[:300]}",
                    text=text)
    error = _in_band_error(text)
    if error:
        return done(_error_status(error), f"error returned as a result: {error[:300]}",
                    text=text)
    records = tuple(_records(tool, policy, text, query, retrieved_at, config.server))
    if not records and tool.endswith("_getter"):
        return done(ExecutionStatus.FAILED, "the reply names no record", text=text)
    return done(ExecutionStatus.SUCCEEDED, "" if records else "no records matched",
                records, text)


def group_records(records: Iterable[SourceRecord], *,
                  link_on: Iterable[str] = SCHEMES) -> SourceCount:
    """Records grouped into the sources they name (``identity.group_sources``)."""
    return group_sources({i: r.identifiers for i, r in enumerate(records)},
                         link_on=tuple(link_on))


def _reply_text(reply: Any) -> tuple[str, bool]:
    if isinstance(reply, str):
        return reply, False
    if isinstance(reply, Mapping):
        if "content" in reply:
            parts = [str(p.get("text", "")) for p in reply.get("content") or ()
                     if isinstance(p, Mapping) and p.get("type") == "text"]
            text = "".join(parts)
            structured = reply.get("structuredContent")
            if not text and isinstance(structured, Mapping):
                text = str(structured.get("result") or "")
            return text, bool(reply.get("isError"))
        if isinstance(reply.get("result"), str):
            return reply["result"], False
    raise TypeError(f"not an MCP tool reply: {type(reply).__name__}")


def _in_band_error(text: str) -> str:
    """The message of an error the server returned as an ordinary result, or ""."""
    stripped = text.strip()
    if stripped[:1] in ("[", "{"):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        items = data if isinstance(data, list) else [data]
        errors = [str(i["error"]) for i in items if isinstance(i, Mapping) and i.get("error")]
        if errors and not any(isinstance(i, Mapping) and not i.get("error") for i in items):
            return "; ".join(errors)
    blocks = _blocks(text)
    errors = [f.get("Error", "") for f in map(_fields, blocks) if f.get("Error")]
    return "; ".join(errors) if errors and len(errors) == len(blocks) else ""


def _error_status(message: str) -> ExecutionStatus:
    lowered = message.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return ExecutionStatus.TIMEOUT
    if any(k in lowered for k in ("offline mode", "connection", "proxy", "unreachable",
                                  "name resolution")):
        return ExecutionStatus.UNAVAILABLE
    return ExecutionStatus.FAILED


def _blocks(text: str) -> list[str]:
    """The ``# Record N`` blocks of a Markdown reply, or the whole reply as one block."""
    starts = [m.start() for m in _RECORD.finditer(text)]
    if not starts:
        return [text] if text.strip() else []
    return [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]


def _fields(block: str) -> dict[str, str]:
    """``Key: value`` lines of a block, keyed below their ``##`` headings
    (``Dbsnp/Rsid``); continuation lines are joined to the value they continue."""
    out: dict[str, str] = {}
    headings: dict[int, str] = {}
    current = ""
    for line in block.splitlines():
        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            headings = {k: v for k, v in headings.items() if k < level}
            headings[level] = heading.group(2)
            current = ""
            continue
        if line.startswith("  ") and current:
            out[current] = f"{out[current]} {line.strip()}".strip()
            continue
        found = _FIELD.match(line)
        if not found:
            current = ""
            continue
        prefix = "/".join(v for k, v in sorted(headings.items()) if k >= 2)
        current = f"{prefix}/{found['key']}" if prefix else found["key"]
        out[current] = (found["value"] or "").strip()
    return out


def _records(tool: str, policy: ToolPolicy, text: str, query: Mapping[str, Any],
             retrieved_at: str, server: str) -> Iterable[SourceRecord]:
    def record(content: str, ids: Iterable[Any], title: str, origin: str,
               fields: Mapping[str, str], assembly: str | None = None) -> SourceRecord:
        return SourceRecord(kind=policy.record_kind,
                            identifiers=canonical_many(ids, assembly=assembly),
                            title=title, content=content, tool=tool, query=query,
                            retrieved_at=retrieved_at, server=server, origin=origin,
                            fields=dict(fields))

    if policy.record_kind == "article" and text.strip()[:1] == "[":
        for item in json.loads(text):                      # article_getter: one JSON array
            ids = [canonical(item.get(k), k) for k in ("pmid", "pmcid", "doi")]
            scalars = {k: str(v) for k, v in item.items() if not isinstance(v, (list, dict))}
            yield record(text, ids, str(item.get("title") or ""),
                         str(item.get("source") or "PubMed"), scalars)
        return
    for block in _blocks(text):
        f = _fields(block)
        if policy.record_kind == "article":
            ids = [canonical(f.get(k), s) for k, s in (("Pmid", "pmid"), ("Pmcid", "pmcid"),
                                                      ("Doi", "doi"))]
            yield record(block, ids, f.get("Title", ""), f.get("Source", ""), f)
        elif policy.record_kind == "trial":
            nct = f.get("Nct Number") or f.get("Identification Module/Nct Id")
            title = f.get("Study Title") or f.get("Identification Module/Brief Title", "")
            if nct or "# Record" in block:
                yield record(block, [canonical(nct, "nct")], title, _ORIGIN["trial"], f)
        else:
            build = _UCSC_BUILD.search(block)
            assembly = build.group(1) if build else (policy.assembly or None)
            ids = [canonical(f.get("Id"), "hgvs", assembly=assembly),
                   canonical(f.get("Dbsnp/Rsid"), "rsid")]
            yield record(block, ids, f.get("Id", ""), _ORIGIN["variant"], f,
                         assembly=assembly)
