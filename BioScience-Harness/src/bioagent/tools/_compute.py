"""Call-scoped integer reductions supplied by an optional local compute backend.

Functions still validate their arguments and run through the BioScience policy kernel.
The default is their original Python implementation. A ContextVar avoids sharing a
prepared GPU buffer across calls or native runner threads.
"""
from contextvars import ContextVar

COUNTS_SOURCE = ContextVar("bioagent_integer_counts", default=None)


def counts_for(tool, sequences, window=0):
    source = COUNTS_SOURCE.get()
    return source(tool, sequences, window) if source is not None else None
