"""``tcmstudio corpus build|fetch|info`` (docs/V2.md §11).

    tcmstudio corpus build --out DIR [--xlsx FILE] [--data DIR] [--no-cache] [--packs core,…]
    tcmstudio corpus fetch [--home DIR] [--url LATEST_URL]
    tcmstudio corpus info  [--source DIR|URL] [--home DIR] [--json]

``build`` writes ``DIR/corpus/`` and ``DIR/attribution.html`` (the site build does the same
into its staging directory). ``fetch`` downloads every object of the latest published
snapshot into the runner's cache (``<home>/corpus``), verifying each SHA-256, so the corpus
tools work offline. ``info`` says what a corpus holds.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urljoin

from . import packs as P

__all__ = ["add_arguments", "run"]

_DEFAULT_HOME = "~/.tcmscience/studio"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.description = "Build, fetch or describe the published TCM corpus (docs/V2.md §11)."
    sub = parser.add_subparsers(dest="action", metavar="ACTION")
    b = sub.add_parser("build", help="build the corpus into DIR/corpus and DIR/attribution.html")
    b.add_argument("--out", required=True, help="the site directory to write into")
    b.add_argument("--xlsx", default=None, help="the formula table (default: the repository's "
                                                "中医方剂数据表.xlsx)")
    b.add_argument("--data", default=None, help="the open-data extracts (default: "
                                                "studio/corpus/data)")
    b.add_argument("--packs", default=",".join(P.PACK_ORDER),
                   help=f"packs to build (default: {','.join(P.PACK_ORDER)})")
    b.add_argument("--no-cache", action="store_true", help="read the xlsx again even when the "
                                                          "build cache has its objects")
    b.add_argument("--json", action="store_true", help="print the summary as JSON")
    f = sub.add_parser("fetch", help="download the latest published snapshot into the runner's "
                                     "cache for offline use")
    f.add_argument("--home", default=_DEFAULT_HOME, help=f"runner home (default {_DEFAULT_HOME})")
    f.add_argument("--url", default=P.LATEST_URL, help=f"the pointer to follow (default "
                                                       f"{P.LATEST_URL})")
    f.add_argument("--json", action="store_true", help="print the summary as JSON")
    i = sub.add_parser("info", help="show a corpus's snapshot, packs, licences and sizes")
    i.add_argument("--source", default=None, help="a built site or corpus directory, or a URL "
                                                  "(default: as the runner's tools read it)")
    i.add_argument("--home", default=_DEFAULT_HOME, help=f"runner home (default {_DEFAULT_HOME})")
    i.add_argument("--json", action="store_true", help="print the manifest summary as JSON")


def _err(text: str) -> None:
    print(text, file=sys.stderr)


def run(args: argparse.Namespace) -> int:
    action = getattr(args, "action", None)
    if action == "build":
        return _build(args)
    if action == "fetch":
        return _fetch(args)
    if action == "info":
        return _info(args)
    _err("tcmstudio corpus: choose build, fetch or info (see --help)")
    return 2


def _build(args: argparse.Namespace) -> int:
    from .build import BuildError, build_corpus
    try:
        summary = build_corpus(args.out, xlsx=args.xlsx, data_dir=args.data,
                               cache=not args.no_cache,
                               packs=[p.strip() for p in args.packs.split(",") if p.strip()],
                               log=_err)
    except (BuildError, P.GateError) as exc:
        _err(f"tcmstudio corpus build: {exc}")
        return 2
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _fetch(args: argparse.Namespace) -> int:
    from .reader import Corpus, CorpusError, HttpFetcher, reset_default_corpora
    cache = Path(args.home).expanduser() / "corpus"
    base = urljoin(args.url, ".")
    fetcher = HttpFetcher(base, cache, network=True)
    try:
        corpus = Corpus(fetcher)
    except CorpusError as exc:
        _err(f"tcmstudio corpus fetch: {exc}" + (f" — {exc.hint}" if exc.hint else ""))
        return 1
    objects = corpus.objects
    urls: dict[str, str] = {}
    for path, meta in objects.items():
        urls.setdefault(meta["url"], path)
    fetched = kept = 0
    size = 0
    for n, (url, path) in enumerate(sorted(urls.items()), 1):
        was_cached = fetcher.cached(url) is not None
        try:
            corpus.raw(path)                       # downloads if absent; verifies either way
        except CorpusError:
            if not was_cached:
                _err(f"tcmstudio corpus fetch: {path} could not be fetched")
                return 1
            fetcher.cached(url).unlink()           # a damaged copy: fetch it again
            try:
                corpus.raw(path)
            except CorpusError as exc:
                _err(f"tcmstudio corpus fetch: {exc}")
                return 1
            was_cached = False
        size += int(objects[path]["bytes"])
        fetched += 0 if was_cached else 1
        kept += 1 if was_cached else 0
        if n % 100 == 0 or n == len(urls):
            _err(f"  {n}/{len(urls)} objects ({size:,} bytes)")
    reset_default_corpora()
    summary = {"snapshot_id": corpus.snapshot_id, "manifest": corpus.manifest_name,
               "cache": str(cache), "objects": len(urls), "downloaded": fetched,
               "already_cached": kept, "bytes": size}
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"{corpus.snapshot_id}: {len(urls)} objects in {cache} ({fetched} downloaded, "
              f"{kept} already cached, {size:,} bytes); the corpus tools now work offline")
    return 0


def _info(args: argparse.Namespace) -> int:
    from .reader import Corpus, CorpusError, DirFetcher, HttpFetcher, default_corpus
    cache = Path(args.home).expanduser() / "corpus"
    try:
        if args.source and args.source.startswith(("http://", "https://")):
            base = urljoin(args.source, ".") if args.source.endswith(".json") else args.source
            corpus = Corpus(HttpFetcher(base, cache, network=True))
        elif args.source:
            corpus = Corpus(DirFetcher(args.source))
        else:
            corpus = default_corpus({"where": "runner", "network": True,
                                     "corpus": {"cache_dir": str(cache)}})
    except CorpusError as exc:
        _err(f"tcmstudio corpus info: {exc}" + (f" — {exc.hint}" if exc.hint else ""))
        return 1
    m = corpus.manifest
    if args.json:
        print(json.dumps({"snapshot_id": corpus.snapshot_id, "manifest_sha256": corpus.manifest_sha256,
                          "data_date": m.get("data_date"), "totals": m.get("totals"),
                          "skipped": m.get("skipped"), "source": corpus.fetcher.describe(),
                          "packs": {k: {"licence": v.get("licence"), "counts": v.get("counts"),
                                        "objects": v.get("objects"), "bytes": v.get("bytes"),
                                        "publication": v.get("publication")}
                                    for k, v in (m.get("packs") or {}).items()}},
                         ensure_ascii=False, indent=2))
        return 0
    t = m.get("totals") or {}
    print(f"{corpus.snapshot_id}  (data {m.get('data_date')}; {corpus.fetcher.describe()})")
    print(f"  {t.get('objects', 0)} objects, {t.get('bytes', 0):,} bytes gzipped; largest "
          f"{(t.get('largest') or {}).get('path')} {(t.get('largest') or {}).get('bytes', 0):,} bytes")
    for name, meta in (m.get("packs") or {}).items():
        pub = meta.get("publication") or {}
        basis = (pub.get("basis") if pub.get("basis") == "open-licence"
                 else f"owner decision, {pub.get('date', '')}")
        counts = ", ".join(f"{k} {v:,}" for k, v in (meta.get("counts") or {}).items()
                           if isinstance(v, int))
        print(f"  {name:<9} {meta.get('licence')}  ({basis}); {meta.get('objects')} objects, "
              f"{meta.get('bytes', 0):,} bytes; {counts}")
    for name, why in (m.get("skipped") or {}).items():
        print(f"  {name:<9} skipped: {why}")
    print(f"  attribution: {P.ATTRIBUTION_URL}")
    return 0
