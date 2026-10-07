#!/usr/bin/env python
"""Freeze the evidence-typing sample: real abstracts, PubMed's labels, a hashed split.

    python scripts/freeze_evidence_typing_sample.py --cache CACHE \\
        --out benchmarks/evidence_typing

This reaches the network and is not run in CI. The benchmark reads what it wrote, offline
(``scripts/run_evidence_typing_benchmark.py``). For each stratum it takes:

1. **the frame**: every PubMed record in Europe PMC (source ``MED``) that matches the
   stratum's query, is open access under a licence Europe PMC reports as CC BY or CC0, and
   has an abstract. English strata are limited to records first published in 2022, which
   keeps each frame small enough to list in full. The Chinese strata take every year;
2. **an order**: a seeded hash of each PMID (``evidence_typing.draw_key``), not the
   search's ranking, which follows its own relevance and date rules;
3. **records in that order** until the quota is met. A record is kept only when
   - its PubMed publication types and MeSH, fetched from E-utilities, give the stratum's
     reference (``evidence_typing.reference_for``);
   - the article's own licence statement, in Europe PMC's article XML, is CC BY or CC0;
   - its abstract has at least 400 characters (150 in Chinese) and is not a teaser cut
     off with "[...]";
   - an earlier stratum has not drawn it.

No rule is run here, so nothing the rules read can decide what is drawn. The split is the
identifier's hash (``evidence_typing.split_of``). Every exclusion is counted by reason in
``sampling.json``. English abstracts are Europe PMC's ``abstractText``. Chinese ones are the
Chinese ``<abstract>`` of the article XML, because Europe PMC's abstract for a Chinese
article is its English translation.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import _bootstrap

_bootstrap.bootstrap()

from bioagent.benchmarks.evidence_typing import (  # noqa: E402
    LICENCES, SAMPLE_SEED, SPLIT_SEED, draw_key, reference_for, split_of,
)

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
#: Seconds between requests to one host: NCBI asks for at most three a second without
#: an API key, and Europe PMC for restraint.
PACE = {"www.ebi.ac.uk": 0.5, "eutils.ncbi.nlm.nih.gov": 0.4}
OPEN = 'SRC:MED AND OPEN_ACCESS:y AND (LICENSE:"cc by" OR LICENSE:"cc0") AND HAS_ABSTRACT:y'
ENGLISH = f'{OPEN} AND LANG:"eng" AND FIRST_PDATE:[2022-01-01 TO 2022-12-31]'
CHINESE = f'{OPEN} AND LANG:"chi"'
CELLS = ('(KW:"Cells, Cultured" OR KW:"Cell Line" OR KW:"In Vitro Techniques" OR '
         'KW:"HEK293 Cells" OR KW:"HeLa Cells" OR KW:"Hep G2 Cells" OR KW:"MCF-7 Cells" '
         'OR KW:"A549 Cells") AND NOT KW:"Animals"')
REVIEWS = '(PUB_TYPE:"Systematic Review" OR PUB_TYPE:"Meta-Analysis")'
NARRATIVE = ('PUB_TYPE:"Review" AND NOT PUB_TYPE:"Systematic Review" AND NOT '
             'PUB_TYPE:"Meta-Analysis"')
OBSERVATIONAL = 'PUB_TYPE:"Observational Study"'
#: How a cached "the server would not give this" is told from a body.
_UNAVAILABLE = b"unavailable: HTTP "


@dataclass(frozen=True)
class Stratum:
    name: str
    kind: str
    quota: int
    query: str
    language: str = "en"
    #: A MeSH heading the record must carry: the observational strata's subtype.
    mesh: str = ""


STRATA = (
    Stratum("randomized_trial", "randomized_trial", 60,
            f'{ENGLISH} AND PUB_TYPE:"Randomized Controlled Trial"'),
    Stratum("systematic_review", "systematic_review", 60, f"{ENGLISH} AND {REVIEWS}"),
    Stratum("cohort", "observational", 20,
            f'{ENGLISH} AND {OBSERVATIONAL} AND KW:"Cohort Studies"', mesh="Cohort Studies"),
    Stratum("case_control", "observational", 20,
            f'{ENGLISH} AND {OBSERVATIONAL} AND KW:"Case-Control Studies"',
            mesh="Case-Control Studies"),
    Stratum("cross_sectional", "observational", 20,
            f'{ENGLISH} AND {OBSERVATIONAL} AND KW:"Cross-Sectional Studies"',
            mesh="Cross-Sectional Studies"),
    Stratum("observational", "observational", 15, f"{ENGLISH} AND {OBSERVATIONAL}"),
    Stratum("case_report", "case_report", 50, f'{ENGLISH} AND PUB_TYPE:"Case Reports"'),
    Stratum("animal", "animal", 50, f'{ENGLISH} AND KW:"Animals" AND NOT KW:"Humans"'),
    Stratum("in_vitro", "in_vitro", 50, f"{ENGLISH} AND {CELLS}"),
    Stratum("narrative_review", "narrative_review", 30, f"{ENGLISH} AND {NARRATIVE}"),
    Stratum("editorial", "editorial", 25, f'{ENGLISH} AND PUB_TYPE:"Editorial"'),
    Stratum("protocol", "protocol", 30, f'{ENGLISH} AND PUB_TYPE:"Clinical Trial Protocol"'),
    Stratum("zh_randomized_trial", "randomized_trial", 12,
            f'{CHINESE} AND PUB_TYPE:"Randomized Controlled Trial"', "zh"),
    Stratum("zh_systematic_review", "systematic_review", 8, f"{CHINESE} AND {REVIEWS}", "zh"),
    Stratum("zh_case_report", "case_report", 8, f'{CHINESE} AND PUB_TYPE:"Case Reports"',
            "zh"),
    Stratum("zh_animal", "animal", 6, f'{CHINESE} AND KW:"Animals" AND NOT KW:"Humans"',
            "zh"),
    Stratum("zh_in_vitro", "in_vitro", 6, f"{CHINESE} AND {CELLS}", "zh"),
    Stratum("zh_narrative_review", "narrative_review", 8, f"{CHINESE} AND {NARRATIVE}", "zh"),
)


class Fetcher:
    """GET with a cache keyed by URL, paced per host, retried on transient failure."""

    def __init__(self, cache: Path) -> None:
        self.cache = cache
        cache.mkdir(parents=True, exist_ok=True)
        self.last: dict[str, float] = {}

    def get(self, url: str, *, unavailable: tuple[int, ...] = (404,)) -> bytes | None:
        """The body, or ``None`` when the server keeps answering a status in
        ``unavailable``: an article whose XML cannot be had is excluded, not fatal. The
        answer is cached too, so a second run from the cache draws the same sample."""
        path = self.cache / hashlib.sha256(url.encode()).hexdigest()
        if path.exists():
            data = path.read_bytes()
            return None if data.startswith(_UNAVAILABLE) else data
        host = urllib.parse.urlsplit(url).hostname or ""
        request = urllib.request.Request(url, headers={
            "User-Agent": "TCMScience evidence-typing benchmark (sample freeze)"})
        for attempt in range(4):
            wait = PACE.get(host, 1.0) - (time.monotonic() - self.last.get(host, 0.0))
            if wait > 0:
                time.sleep(wait)
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    data = response.read()
                break
            except urllib.error.HTTPError as exc:
                if exc.code in unavailable and (exc.code == 404 or attempt == 3):
                    data = _UNAVAILABLE + str(exc.code).encode()
                    break
                if attempt == 3:
                    raise
            except (urllib.error.URLError, TimeoutError):
                if attempt == 3:
                    raise
            finally:
                self.last[host] = time.monotonic()
            time.sleep(5 * (attempt + 1))
        path.write_bytes(data)
        return None if data.startswith(_UNAVAILABLE) else data

    def json(self, url: str) -> dict:
        return json.loads(self.get(url) or b"{}")


def search_url(query: str, result_type: str, cursor: str = "*", size: int = 1000) -> str:
    return EPMC + "search?" + urllib.parse.urlencode({
        "query": query, "format": "json", "resultType": result_type, "pageSize": size,
        "cursorMark": cursor})


def frame(fetch: Fetcher, query: str) -> tuple[list[str], int]:
    """Every PMID the query matches, and the hit count Europe PMC reported."""
    pmids, cursor, hits = [], "*", 0
    while True:
        page = fetch.json(search_url(query, "idlist", cursor))
        hits = int(page.get("hitCount", 0))
        rows = page.get("resultList", {}).get("result", [])
        pmids += [str(r["pmid"]) for r in rows if r.get("pmid")]
        following = page.get("nextCursorMark", cursor)
        if not rows or following == cursor:
            return list(dict.fromkeys(pmids)), hits
        cursor = following


def europepmc_core(fetch: Fetcher, pmids: list[str]) -> dict[str, dict]:
    query = "SRC:MED AND EXT_ID:(" + " OR ".join(pmids) + ")"
    page = fetch.json(search_url(query, "core", size=len(pmids)))
    return {str(r["pmid"]): r for r in page.get("resultList", {}).get("result", [])
            if r.get("pmid")}


def pubmed(fetch: Fetcher, pmids: list[str]) -> dict[str, dict]:
    """Publication types, MeSH descriptors, language and indexing state, from PubMed."""
    url = EUTILS + "?" + urllib.parse.urlencode({
        "db": "pubmed", "id": ",".join(pmids), "retmode": "xml",
        "tool": "tcmscience-evidence-typing"})
    root = ET.fromstring(fetch.get(url) or b"<PubmedArticleSet/>")
    out = {}
    for article in root.iter("PubmedArticle"):
        citation = article.find("MedlineCitation")
        pmid = citation.findtext("PMID", "")
        corrections = {c.get("RefType") for c in citation.iter("CommentsCorrections")}
        title = citation.find("Article/ArticleTitle")
        out[pmid] = {
            "publication_types": sorted(p.text or ""
                                        for p in citation.iter("PublicationType")),
            "mesh": sorted(d.text or "" for d in citation.iter("DescriptorName")),
            "languages": [x.text for x in citation.iter("Language")],
            "status": citation.get("Status", ""),
            "indexing_method": citation.get("IndexingMethod", ""),
            "title": _text(title) if title is not None else "",
            "retracted": bool(corrections & {"RetractionIn", "ExpressionOfConcernIn"}),
        }
    return out


_TAGS = re.compile(r"</?(?:i|b|u|em|strong|sup|sub|span|sc|bold|italic)\b[^>]*>", re.I)


def plain(markup: str) -> str:
    """Europe PMC's lightly marked-up text as plain text: headings kept, tags dropped.

    Only known tags are removed: an abstract's "<390 nm" is text, not a tag.
    """
    text = html.unescape(markup)
    text = re.sub(r"<h4>(.*?)</h4>", lambda m: f"\n{m.group(1).strip()}: ", text, flags=re.S)
    text = re.sub(r"<br\s*/?>|</?p>", "\n", text, flags=re.I)
    text = _TAGS.sub("", text)
    return "\n".join(" ".join(line.split()) for line in text.split("\n") if line.strip())


_CJK = re.compile("[\u4e00-\u9fff]")


def _chinese(text: str) -> bool:
    return len(_CJK.findall(text)) >= 0.3 * max(1, len(text.replace(" ", "")))


def _text(element: ET.Element) -> str:
    return " ".join("".join(element.itertext()).split())


def chinese_abstract(root: ET.Element) -> tuple[str, str]:
    """The article's Chinese title and abstract, section headings kept, keywords dropped."""
    meta = root.find(".//article-meta")
    if meta is None:
        return "", ""
    titles = [meta.find("title-group/article-title")]
    titles += meta.findall("title-group/trans-title-group/trans-title")
    title = next((_text(t) for t in titles if t is not None and _chinese(_text(t))), "")
    for abstract in meta.findall("abstract") + meta.findall("trans-abstract"):
        parts = []
        for child in abstract:
            if child.tag == "sec" and child.get("sec-type") != "kwd-group":
                heading = child.findtext("title", "").strip()
                body = " ".join(_text(p) for p in child.findall("p"))
                parts.append(f"{heading}：{body}" if heading else body)
            elif child.tag == "p":
                parts.append(_text(child))
        text = "\n".join(p for p in parts if p)
        if text and _chinese(text):
            return title, text
    return title, ""


_BY = re.compile(r"creativecommons\.org/licenses/by/(\d\.\d)", re.I)
_ZERO = re.compile(r"creativecommons\.org/publicdomain/zero/1\.0", re.I)
_CC_URL = re.compile(r"https?://creativecommons\.org/[\w./-]+", re.I)
# Not \b before "CC": in "遵循CC BY 4.0协议" the Chinese character before it is a word
# character, so there is no word boundary to find.
_RESTRICTED = re.compile(r"non-?commercial|no-?deriv|share-?alike|\bby-(?:nc|nd|sa)\b|"
                         r"(?<![a-z])CC[ -]?BY[ -](?:NC|ND|SA)\b", re.I)
_BY_TEXT = re.compile(r"Creative Commons Attribution(?: License)?[^.]{0,40}?(\d\.\d)|"
                      r"(?<![a-z])CC[ -]?BY[ -]?(\d\.\d)", re.I)
_BY_UNVERSIONED = re.compile(r"Creative Commons Attribution\b|(?<![a-z])CC[ -]?BY\b", re.I)


def _spdx(url: str) -> str:
    by, zero = _BY.search(url), _ZERO.search(url)
    return f"CC-BY-{by.group(1)}" if by else "CC0-1.0" if zero else f"other: {url}"


def licence_of(root: ET.Element) -> tuple[str, str, str, str]:
    """``(spdx, url, statement, why_not)`` from the article's own ``<license>``.

    The licence is the one the ``<license>`` element links, else the first Creative
    Commons link in its statement, else the statement's words. Only the first link: a BMC
    article's statement goes on to waive copyright in its *data* under CC0, which is not
    the article's licence. A statement that names the Creative Commons Attribution licence
    and no version (Frontiers, Cureus, Hindawi) gives ``CC-BY``, with no version made up
    for it. Every licence named must agree, and a statement with non-commercial,
    no-derivatives or share-alike terms is not CC BY whatever it links.
    """
    licences = root.findall(".//article-meta/permissions/license")
    if not licences:
        return "", "", "", "the article XML states no licence"
    statement = " ".join(_text(p) for lic in licences for p in lic.iter("license-p"))
    urls = [v.strip() for lic in licences for k, v in lic.attrib.items() if k.endswith("href")]
    urls += [(e.text or "").strip() for lic in licences for e in lic.iter()
             if e.tag.endswith("license_ref")]
    if not urls:
        urls = [v for lic in licences for e in lic.iter("ext-link")
                for k, v in e.attrib.items()
                if k.endswith("href") and "creativecommons" in v][:1]
    if not urls:
        urls = _CC_URL.findall(statement)[:1]
    found = {_spdx(u) for u in urls if u}
    if not found:
        match = _BY_TEXT.search(statement)
        if match:
            found = {f"CC-BY-{match.group(1) or match.group(2)}"}
        elif _BY_UNVERSIONED.search(statement):
            found = {"CC-BY"}
    if _RESTRICTED.search(statement):
        found.add("restricted terms in the statement")
    if len(found) != 1:
        return "", "", statement, f"licence statements: {sorted(found) or 'none readable'}"
    (spdx,) = found
    if spdx not in LICENCES:
        return "", "", statement, f"licence {spdx}"
    url = next((u for u in urls if _spdx(u) == spdx), "")
    return spdx, url or _canonical_url(spdx), statement, ""


def _canonical_url(spdx: str) -> str:
    """The deed of a licence the statement names in words; none when it names no version."""
    if spdx == "CC0-1.0":
        return "https://creativecommons.org/publicdomain/zero/1.0/"
    if spdx == "CC-BY":
        return ""
    return f"https://creativecommons.org/licenses/by/{spdx.rsplit('-', 1)[1]}/"


def copyright_of(root: ET.Element) -> str:
    return " ".join(_text(c) for c in root.findall(".//article-meta/permissions/"
                                                  "copyright-statement"))


def examine(fetch: Fetcher, stratum: Stratum, pmid: str, core: dict | None,
            meta: dict | None) -> tuple[dict | None, str]:
    """The record to commit, or ``None`` and the reason it is excluded."""
    language = "chi" if stratum.language == "zh" else "eng"
    if meta is None or core is None:
        return None, "missing from PubMed's or Europe PMC's response"
    if meta["languages"] != [language]:
        return None, f"language {meta['languages']}"
    if meta["retracted"]:
        return None, "retracted, or under an expression of concern"
    kind, basis = reference_for(meta["publication_types"], meta["mesh"])
    if kind != stratum.kind:
        return None, f"reference {kind!r}" if kind else f"no reference ({basis})"
    if stratum.mesh and stratum.mesh not in meta["mesh"]:
        return None, f"no MeSH {stratum.mesh!r}"
    if core.get("license") not in ("cc by", "cc0"):
        return None, f"Europe PMC licence {core.get('license')!r}"
    pmcid = core.get("pmcid", "")
    if stratum.language == "en":
        title, abstract = plain(core.get("title", "")), plain(core.get("abstractText", ""))
        if len(abstract) < 400:
            return None, "abstract under 400 characters"
        if abstract.rstrip().endswith("[...]"):
            return None, "abstract is a teaser cut off with [...]"
    xml = fetch.get(EPMC + f"{pmcid}/fullTextXML", unavailable=(404, 500)) if pmcid else None
    if xml is None:
        return None, "no article XML, so no licence statement to verify"
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None, "the article XML does not parse, so no licence statement to verify"
    spdx, url, statement, why_not = licence_of(root)
    if why_not:
        return None, why_not
    if (spdx == "CC0-1.0") != (core["license"] == "cc0"):
        return None, f"Europe PMC says {core['license']!r}, the article XML {spdx}"
    if stratum.language == "zh":
        title, abstract = chinese_abstract(root)
        if not title or len(abstract) < 150:
            return None, "no Chinese title and abstract of 150 characters in the article XML"
    journal = core.get("journalInfo", {}).get("journal", {})
    record = {
        "pmid": pmid, "language": stratum.language, "stratum": stratum.name,
        "title": title, "abstract": abstract, "licence": spdx,
        "attribution": {
            "authors": core.get("authorString", ""),
            "journal": journal.get("title") or journal.get("medlineAbbreviation", ""),
            "year": int(core.get("pubYear") or 0), "doi": core.get("doi", ""),
            "pmcid": pmcid, "source_url": f"https://europepmc.org/article/MED/{pmid}",
            "licence_url": url, "licence_statement": statement,
            "copyright": copyright_of(root),
            **({"title_en": meta["title"]} if stratum.language == "zh" else {})},
        "pubmed": {"publication_types": meta["publication_types"], "mesh": meta["mesh"],
                   "status": meta["status"], "indexing_method": meta["indexing_method"]},
    }
    return record, ""


def draw(fetch: Fetcher, stratum: Stratum, taken: set[str]) -> tuple[list[dict], dict]:
    pmids, hits = frame(fetch, stratum.query)
    order = sorted(pmids, key=lambda p: draw_key(stratum.name, p))
    drawn: list[dict] = []
    excluded: Counter[str] = Counter()
    examined = 0
    for start in range(0, len(order), 50):
        if len(drawn) >= stratum.quota:
            break
        batch = order[start:start + 50]
        cores, metas = europepmc_core(fetch, batch), pubmed(fetch, batch)
        for pmid in batch:
            if len(drawn) >= stratum.quota:
                break
            examined += 1
            if pmid in taken:
                excluded["drawn by an earlier stratum"] += 1
                continue
            record, why = examine(fetch, stratum, pmid, cores.get(pmid), metas.get(pmid))
            if record is None:
                excluded[why] += 1
                continue
            taken.add(pmid)
            drawn.append(record)
        print(f"  {stratum.name}: {len(drawn)}/{stratum.quota} after {examined} examined",
              flush=True)
    report = {"name": stratum.name, "language": stratum.language, "kind": stratum.kind,
              "quota": stratum.quota, "query": stratum.query, "frame_hits": hits,
              "frame_listed": len(pmids), "examined": examined, "drawn": len(drawn),
              "excluded": dict(excluded.most_common())}
    print(f"{stratum.name}: {len(drawn)}/{stratum.quota} drawn from a frame of {hits}, "
          f"{examined} examined, {sum(excluded.values())} excluded", flush=True)
    return drawn, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="directory for sample.jsonl and labels.jsonl")
    ap.add_argument("--cache", required=True, help="directory for cached HTTP responses")
    args = ap.parse_args(argv)
    fetch = Fetcher(Path(args.cache))
    taken: set[str] = set()
    records, strata = [], []
    for stratum in STRATA:
        drawn, report = draw(fetch, stratum, taken)
        records += drawn
        strata.append(report)
    records.sort(key=lambda r: int(r["pmid"]))
    labels = []
    for record in records:
        kind, basis = reference_for(record["pubmed"]["publication_types"],
                                    record["pubmed"]["mesh"])
        labels.append({"pmid": record["pmid"], "split": split_of(record["pmid"]),
                       "kind": kind, "basis": basis})
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("sample.jsonl", records), ("labels.jsonl", labels)):
        (out / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                                        for r in rows), encoding="utf-8")
    sampling = {
        "frozen_on": date.today().isoformat(), "sample_seed": SAMPLE_SEED,
        "split_seed": SPLIT_SEED,
        "sources": {"frames_and_abstracts": EPMC + "search",
                    "licence_and_chinese_abstracts": EPMC + "{PMCID}/fullTextXML",
                    "publication_types_and_mesh": EUTILS + " (db=pubmed)"},
        "records": len(records),
        "licences": dict(Counter(r["licence"] for r in records).most_common()),
        "strata": strata,
    }
    (out / "sampling.json").write_text(json.dumps(sampling, ensure_ascii=False, indent=1)
                                       + "\n", encoding="utf-8")
    print(f"wrote {len(records)} records to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
