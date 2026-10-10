"""Call-scoped help from an optional local compute backend.

Functions still validate their arguments and run through the BioScience policy kernel.
The default is their original Python implementation. ContextVars keep the help scoped to
one call, so nothing is shared across calls or native runner threads.

Two kinds of help, both optional and both checked by the caller before use:

``COUNTS_SOURCE``
    Integer reductions prepared before the call (a GPU computes them asynchronously):
    ``source(tool, sequences, window) -> list[int] | None``. The source compares the
    prepared counts with the call's own normalised input and declines (``None``) when
    they were computed from anything else.

``KERNELS``
    Synchronous kernels the call invokes with its own normalised input, by name:

    ``sequence.counts(tool, sequences, window) -> list[int] | None``
        the same layout as ``COUNTS_SOURCE``;
    ``align.nw_linear(x, y, alpha_x, alpha_y, table, gap) -> (aligned_x, aligned_y, score)``
    ``align.sw_linear(x, y, alpha_x, alpha_y, table, gap) -> (aligned_x, aligned_y, score,
    start_a, end_a, start_b, end_b)``
        Needleman–Wunsch and Smith–Waterman with a linear gap, ``table[i*len(alpha_y)+j]``
        the score of ``alpha_x[i]`` against ``alpha_y[j]``; the score has the type and the
        value (to the bit) the Python implementation would compute;
    ``sequence.levenshtein(x, y) -> int``.

    A kernel may return ``None`` to decline; the original implementation then runs.

``KERNEL_EVENTS``
    When set to a list, :func:`note` appends what happened to each kernel result
    (``used``, ``declined``, ``rejected`` with the reason), so a receipt can say what ran.
"""
from contextvars import ContextVar

COUNTS_SOURCE = ContextVar("bioagent_integer_counts", default=None)
KERNELS = ContextVar("bioagent_cpu_kernels", default=None)
KERNEL_EVENTS = ContextVar("bioagent_kernel_events", default=None)


def note(name, outcome, reason=""):
    """Record what became of a kernel's result in this call (no-op unless someone listens)."""
    events = KERNEL_EVENTS.get()
    if events is not None:
        events.append({"kernel": name, "outcome": outcome, **({"reason": reason} if reason else {})})


def counts_for(tool, sequences, window=0):
    """Prepared counts when they belong to this input, else a CPU kernel's, else ``None``."""
    source = COUNTS_SOURCE.get()
    counts = source(tool, sequences, window) if source is not None else None
    if counts is not None:
        return counts
    counter = kernel("sequence.counts")
    return counter(tool, sequences, window) if counter is not None else None


def kernel(name):
    """The kernel installed for this call under ``name``, or ``None``."""
    table = KERNELS.get()
    return table.get(name) if table else None
