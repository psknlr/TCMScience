"""The published TCM corpus (docs/V2.md §11): packs, the build, the reader, the tools.

    from tcmstudio.corpus import Corpus, DirFetcher, default_corpus
    corpus = Corpus(DirFetcher("studio/_site"))
    corpus.get("core/core.json")                  # verified, gunzipped, parsed, cached

``tcmstudio.corpus.packs`` holds the allowlist and the denylist, ``build`` writes the
objects and the manifest, ``reader`` reads them back in the runner and in the browser
(the same code, with a fetcher for each), and ``tools`` answers the seven corpus tools.
Importing the package loads only the reader.
"""

from __future__ import annotations

from .reader import (Corpus, CorpusError, DirFetcher, HttpFetcher, Session, XHRFetcher,
                     default_corpus, reset_default_corpora)

__all__ = ["Corpus", "CorpusError", "DirFetcher", "HttpFetcher", "Session", "XHRFetcher",
           "default_corpus", "reset_default_corpora"]
