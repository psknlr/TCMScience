"""Quote receipts: content-addressed proof that an excerpt was located, re-checkable.

``EvidenceItem.quote_verified`` used to be a boolean any caller could set. An
item with an invented quote, ``quote_verified=True`` and no content hash
satisfied the claim gate (audit F02): the flag attested to a check nobody could
repeat.

A quote now counts as verified only with a *receipt* — the SHA-256 of the
content it was found in and the character offset it was found at — and the
receipt is issued by :meth:`EvidenceItem.located_in`, which performs the search
rather than trusting the caller. The content itself is kept in a
:class:`ContentStore` under its hash, so a verifier can later fetch it and
confirm that the quoted bytes really sit at that offset. A receipt with no
retrievable content is *present but unverified*: enough to publish a draft, not
enough to authorise a release (see :func:`bioagent.contracts.validate_artifact`).
"""

from __future__ import annotations

import hashlib
import threading
from typing import Mapping

__all__ = ["ContentStore", "content_sha256", "default_store"]


def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class ContentStore:
    """Retrieved content by SHA-256. Append-only; a hash names one text forever."""

    def __init__(self, items: Mapping[str, str] | None = None) -> None:
        self._items: dict[str, str] = {}
        self._lock = threading.Lock()
        for content in (items or {}).values():
            self.put(content)

    def put(self, content: str) -> str:
        digest = content_sha256(content)
        with self._lock:
            self._items.setdefault(digest, content)
        return digest

    def get(self, digest: str) -> str | None:
        with self._lock:
            return self._items.get(digest)

    def __contains__(self, digest: object) -> bool:
        with self._lock:
            return digest in self._items

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


#: The process-wide store the P0 skills write to. A governed run verifies
#: against it; a reviewer re-checking a published artifact supplies their own,
#: filled from the pinned snapshot.
default_store = ContentStore()
