"""The optional compute kernels of bioagent.tools (_compute.KERNELS): a kernel's answer is used
only when it checks out, and the tool's output is then exactly the original's."""
import json
import math
import random

import pytest

from bioagent.tools import _compute, align, sequence

pytestmark = pytest.mark.unit


def with_kernels(table, fn, *args, **kwargs):
    """fn(*args, **kwargs) with `table` installed; → (its output, what became of each kernel answer)."""
    events = []
    tokens = (_compute.KERNELS.set(table), _compute.KERNEL_EVENTS.set(events))
    try:
        return fn(*args, **kwargs), events
    finally:
        _compute.KERNELS.reset(tokens[0])
        _compute.KERNEL_EVENTS.reset(tokens[1])


def plain(fn, *args, **kwargs):
    """The original implementation: no kernel installed, whatever the caller has."""
    token = _compute.KERNELS.set(None)
    try:
        return fn(*args, **kwargs)
    finally:
        _compute.KERNELS.reset(token)


def honest(fn, **kw):
    """A kernel that answers what the original implementation computes for the same call."""
    def run(x, y, *_ignored):
        out = plain(fn, x, y, **kw)
        found = (out["aligned_a"], out["aligned_b"], out["score"])
        if out["mode"] == "local":
            found += (out["start_a"], out["end_a"], out["start_b"], out["end_b"])
        return found
    return run


def as_json(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True)


@pytest.mark.parametrize("kw", [{}, {"match": 1, "mismatch": -1, "gap": -1}, {"match": 1.0, "mismatch": -1.0, "gap": -0.0},
                                {"match": 2, "mismatch": -1.0, "gap": -2}, {"gap": -4.0, "matrix": "BLOSUM62"}])
def test_a_correct_kernel_is_used_and_changes_nothing(kw):
    rng = random.Random(1)
    letters = "ARNDCQEGHILKMFPSTWYV" if "matrix" in kw else "ACGT"
    for _ in range(15):
        a = "".join(rng.choice(letters) for _ in range(rng.randint(1, 40)))
        b = "".join(rng.choice(letters) for _ in range(rng.randint(1, 40)))
        for fn, name in ((align.global_alignment, "align.nw_linear"), (align.local_alignment, "align.sw_linear")):
            reference = plain(fn, a, b, **kw)
            fast, events = with_kernels({name: honest(fn, **kw)}, fn, a, b, **kw)
            assert as_json(fast) == as_json(reference)
            assert [e["outcome"] for e in events] == ["used"]


def test_a_wrong_score_type_or_alignment_is_rejected_and_the_original_runs():
    a, b = "GATTACA", "GCATGCU"
    reference = plain(align.global_alignment, a, b)
    good = honest(align.global_alignment)
    lies = {
        "score": lambda *args: (*good(*args)[:2], good(*args)[2] + 1.0),
        "type": lambda *args: (*good(*args)[:2], int(good(*args)[2])),
        "strings that spell other sequences": lambda *args: ("GATTAC-", "GCATGCU", good(*args)[2]),
        "a column of two gaps": lambda *args: ("GATTACA-", "GCATGCU-", good(*args)[2]),
        "too few fields": lambda *args: ("GATTACA",),
        "not a tuple": lambda *args: object(),
    }
    for what, k in lies.items():
        fast, events = with_kernels({"align.nw_linear": k}, align.global_alignment, a, b)
        assert fast == reference, what
        assert [e["outcome"] for e in events] == ["rejected"], what


def test_negative_zero_keeps_its_sign():
    # "A" against "C" with gaps of -0.0: two gaps (-0.0) beat a mismatch (-1.0), and the score is -0.0
    kw = {"match": 1.0, "mismatch": -1.0, "gap": -0.0}
    reference = plain(align.global_alignment, "A", "C", **kw)
    assert math.copysign(1.0, reference["score"]) == -1.0
    sign_lost = lambda *args: (*honest(align.global_alignment, **kw)(*args)[:2], 0.0)  # noqa: E731
    fast, events = with_kernels({"align.nw_linear": sign_lost}, align.global_alignment, "A", "C", **kw)
    assert as_json(fast) == as_json(reference)
    assert events[0]["outcome"] == "rejected"


def test_a_local_alignment_through_a_cell_at_zero_is_rejected():
    # AC against AC with a mismatch: a path A·G·… whose running score falls to zero would have been cut there
    reference = plain(align.local_alignment, "AGAC", "ACAC", match=1.0, mismatch=-1.0, gap=-1.0)
    def through_zero(*args):
        # A/A (+1) G/C (−1 → 0: a stop) A/A C/C: score 2.0 like the true best, but not a Smith–Waterman path
        return ("AGAC", "ACAC", 2.0, 1, 4, 1, 4)
    fast, events = with_kernels({"align.sw_linear": through_zero}, align.local_alignment, "AGAC", "ACAC",
                                match=1.0, mismatch=-1.0, gap=-1.0)
    assert fast == reference
    assert events[0]["outcome"] == "rejected"


def test_an_equally_good_but_different_alignment_is_beyond_the_runtime_check():
    # The check proves validity and the exact score, not which of several best alignments the traceback picks:
    # that is what the kernels' parity tests are for. This documents the boundary rather than hiding it.
    a, b = "ACGTTTTACGT", "ACGTACGT"
    reference = plain(align.local_alignment, a, b)
    other = lambda *args: ("ACGTTTTACGT", "ACGT---ACGT", reference["score"], 1, 11, 1, 8)  # noqa: E731
    fast, events = with_kernels({"align.sw_linear": other}, align.local_alignment, a, b)
    assert fast["score"] == reference["score"] and fast["aligned_a"] != reference["aligned_a"]
    assert events[0]["outcome"] == "used"


def test_inputs_the_kernel_does_not_take_are_declined():
    x, y = "AC", "AG"
    score = align._score_fn(2.0, -1.0, None)
    assert align._kernel_inputs(x, y, score, -2.0) == ("AC", "AG", [2.0, -1.0, -1.0, -1.0])
    assert align._kernel_inputs(x, y, score, float("nan")) is None
    assert align._kernel_inputs(x, y, score, float("inf")) is None
    assert align._kernel_inputs(x, y, score, True) is None, "a bool is not a score"
    assert align._kernel_inputs(x, y, align._score_fn(2 ** 31, -1, None), -1) is None, "beyond the exact range"
    blosum = align._score_fn(2.0, -1.0, "BLOSUM62")
    assert align._kernel_inputs("AJ", "AC", blosum, -4.0) is None, "a pair the matrix lacks"
    # and the original then raises its own error for that pair
    with pytest.raises(ValueError, match="no entry"):
        with_kernels({"align.nw_linear": honest(align.global_alignment)}, align.global_alignment, "AJ", "AC",
                     gap=-4.0, matrix="BLOSUM62")


def test_edit_distance_kernel_answers_are_bounded_or_not_used():
    reference = plain(sequence.edit_distance, "kitten", "sitting")
    for answer, outcome in ((3, "used"), (None, "declined"), (99, "rejected"), (0, "rejected"), (3.0, "rejected")):
        fast, events = with_kernels({"sequence.levenshtein": lambda x, y, d=answer: d}, sequence.edit_distance,
                                    "kitten", "sitting")
        assert fast == reference, answer
        assert [e["outcome"] for e in events] == [outcome], answer


def test_counts_for_prefers_prepared_counts_then_the_cpu_kernel():
    seen = []
    source = lambda tool, seqs, w: seen.append("source") or None  # noqa: E731
    counter = lambda tool, seqs, w: seen.append("kernel") or [1, 2, 3, 4]  # noqa: E731
    token = _compute.COUNTS_SOURCE.set(source)
    try:
        got, _ = with_kernels({"sequence.counts": counter}, _compute.counts_for, "hamming_distance", ("A", "C"))
    finally:
        _compute.COUNTS_SOURCE.reset(token)
    assert got == [1, 2, 3, 4]
    assert seen == ["source", "kernel"]
    assert _compute.counts_for("hamming_distance", ("A", "C")) is None, "nothing installed: the original counts"
