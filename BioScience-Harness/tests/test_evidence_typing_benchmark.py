"""The evidence-typing benchmark: real abstracts, PubMed's reference, measured offline.

The committed sample is held to what it claims: a CC BY or CC0 licence and attribution on
every record, the reference its PubMed metadata gives, the split its PMID's hash gives. The
script runs on a small committed subset of it (``tests/fixtures/evidence_typing``, dev
records only), and its ``--check`` is shown to refuse a rule that got worse.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

import bioagent.literature.evidence as evidence
from bioagent.benchmarks import evidence_typing as et

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "benchmarks" / "evidence_typing"
SUBSET = Path(__file__).resolve().parent / "fixtures" / "evidence_typing"
sys.path.insert(0, str(ROOT / "scripts"))

import run_evidence_typing_benchmark as script  # noqa: E402


def _copy(src: Path, dst: Path) -> Path:
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("sample.jsonl", "labels.jsonl"):
        shutil.copy(src / name, dst / name)
    return dst


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")


# ---------------------------------------------------------------------------
# the reference and the split
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("types,mesh,kind", [
    (["Journal Article", "Randomized Controlled Trial"], ["Humans"], "randomized_trial"),
    (["Review", "Systematic Review", "Meta-Analysis"], ["Humans"], "systematic_review"),
    (["Observational Study"], ["Cohort Studies", "Humans"], "observational"),
    (["Case Reports", "Review"], ["Humans"], "case_report"),
    # a protocol reports no results, even when it is also typed as the trial it plans
    (["Clinical Trial Protocol", "Randomized Controlled Trial"], ["Humans"], "protocol"),
    (["Review"], ["Humans"], "narrative_review"),
    (["Editorial"], [], "editorial"),
    (["Journal Article"], ["Animals", "Mice"], "animal"),
    (["Journal Article"], ["Cell Line, Tumor", "Humans"], "in_vitro"),
    # two designs, a design outside the six, a clinical study that cultured cells, a
    # bench study with an animal arm: no reference rather than a guessed one
    (["Observational Study", "Randomized Controlled Trial"], ["Humans"], ""),
    (["Randomized Controlled Trial, Veterinary"], ["Animals", "Dogs"], ""),
    (["Journal Article"], ["Cells, Cultured", "Humans", "Adult"], ""),
    (["Journal Article"], ["Animals", "Mice", "Cell Line, Tumor"], ""),
    (["Letter"], ["Humans"], ""),
])
def test_the_reference_is_what_pubmed_says_and_nothing_it_does_not(types, mesh, kind):
    found, basis = et.reference_for(types, mesh)
    assert found == kind
    assert basis


def test_the_split_is_a_seeded_hash_of_the_pmid():
    splits = [et.split_of(str(pmid)) for pmid in range(35_000_000, 35_003_000)]
    assert splits == [et.split_of(str(pmid)) for pmid in range(35_000_000, 35_003_000)]
    assert 0.30 < splits.count("dev") / len(splits) < 0.37


# ---------------------------------------------------------------------------
# the committed sample is what it claims
# ---------------------------------------------------------------------------

def test_the_committed_sample_is_licensed_attributed_labelled_and_split():
    records, labels = et.load_sample(SAMPLE)
    assert 300 <= len(records) <= 500
    assert {r.licence for r in records} <= set(et.LICENCES)
    assert all(r.attribution["source_url"].endswith(r.pmid) for r in records)
    kinds = {label.kind for label in labels.values()}
    assert kinds == set(et.DESIGNS) | set(et.NEGATIVES)
    assert {r.language for r in records} == {"en", "zh"}
    dev = sum(label.split == "dev" for label in labels.values())
    assert 0.25 < dev / len(labels) < 0.42


@pytest.mark.parametrize("edit,message", [
    (lambda rows: rows[1].update(kind="randomized_trial", basis="by hand"), "labelled"),
    (lambda rows: rows[1].update(split="test" if rows[1]["split"] == "dev" else "dev"),
     "its hash puts it"),
])
def test_a_label_or_split_edited_by_hand_is_refused(tmp_path, edit, message):
    target = _copy(SUBSET, tmp_path / "s")
    rows = _rows(target / "labels.jsonl")
    edit(rows)
    _write(target / "labels.jsonl", rows)
    with pytest.raises(ValueError, match=message):
        et.load_sample(target)


def test_a_record_without_an_open_licence_or_attribution_is_refused(tmp_path):
    target = _copy(SUBSET, tmp_path / "s")
    rows = _rows(target / "sample.jsonl")
    rows[0]["licence"] = "CC-BY-NC-4.0"
    rows[2]["attribution"]["authors"] = ""
    _write(target / "sample.jsonl", rows)
    with pytest.raises(ValueError, match="may not be committed") as caught:
        et.load_sample(target)
    assert "attribution lacks ['authors']" in str(caught.value)


# ---------------------------------------------------------------------------
# the script, on a committed subset
# ---------------------------------------------------------------------------

@pytest.fixture()
def subset_results(tmp_path, capsys):
    out = tmp_path / "run"
    assert script.main(["--sample", str(SUBSET), "--out", str(out)]) == 0
    capsys.readouterr()
    return out / "results.json"


def test_the_script_measures_a_committed_subset(subset_results):
    data = json.loads(subset_results.read_text(encoding="utf-8"))
    records, labels = et.load_sample(SUBSET)
    assert set(data["records"]) == {r.pmid for r in records}
    assert all(label.split == "dev" for label in labels.values())
    dev = data["splits"]["dev"]
    assert dev["records"] == len(records) and data["splits"]["test"]["records"] == 0
    for pmid, row in data["records"].items():
        assert row["kind"] == labels[pmid].kind
        assert row["verdict"] in ("correct", "abstained", "wrong")
    assert (subset_results.parent / "results.md").read_text(encoding="utf-8").startswith(
        "# Evidence typing on real abstracts")


def test_the_check_passes_on_results_the_code_produces(subset_results, capsys):
    assert script.main(["--sample", str(SUBSET), "--check", str(subset_results)]) == 0
    assert "REGRESSION" not in capsys.readouterr().err


def test_the_check_refuses_a_rule_that_got_worse(subset_results, capsys, monkeypatch):
    """A check that cannot fail is decoration: drop the trial rules and it must fail."""
    rules = tuple(r for r in evidence._DESIGN_RULES if r[1] != "randomized_trial")
    monkeypatch.setattr(evidence, "_DESIGN_RULES", rules)
    assert script.main(["--sample", str(SUBSET), "--check", str(subset_results)]) == 1
    err = capsys.readouterr().err
    assert "REGRESSION" in err and "randomized_trial: randomized_trial -> " in err


def test_another_version_of_the_rules_is_measured_on_the_same_sample(tmp_path):
    """``--rules`` loads an evidence.py from anywhere: how a change's "before" is measured."""
    copy = tmp_path / "evidence_copy.py"
    copy.write_bytes(Path(evidence.__file__).read_bytes())
    records, labels = et.load_sample(SUBSET)
    other = et.run(records, labels, et.load_rules(copy))
    assert [o.as_dict() for o in other] == [o.as_dict() for o in et.run(records, labels)]
    assert et.rules_digest(et.load_rules(copy)) == et.rules_digest(evidence.read_fields)


def test_a_manual_check_of_values_no_longer_read_is_refused(tmp_path, capsys):
    """Verdicts on values the rules no longer read say nothing about the ones they do."""
    target = _copy(SUBSET, tmp_path / "s")
    records, labels = et.load_sample(target)
    outcome, field = next((o, f) for o in et.run(records, labels) for f in et.VALUE_FIELDS
                          if o.states[f] == "read")
    stale = {"pmid": outcome.pmid, "field": field, "verdict": "correct",
             "value": outcome.values[field] + " (as once read)"}
    (target / "manual_check.json").write_text(
        json.dumps({"method": "test", "fields": [stale]}), encoding="utf-8")
    assert script.main(["--sample", str(target), "--out", str(tmp_path / "run")]) == 1
    assert "no longer read" in capsys.readouterr().err


def test_the_check_refuses_results_of_another_sample(subset_results, tmp_path, capsys):
    target = _copy(SUBSET, tmp_path / "other")
    rows = _rows(target / "sample.jsonl")
    _write(target / "sample.jsonl", rows[1:])
    _write(target / "labels.jsonl", _rows(target / "labels.jsonl")[1:])
    assert script.main(["--sample", str(target), "--check", str(subset_results)]) == 1
    assert "the sample or its labels changed" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# the committed results
# ---------------------------------------------------------------------------

def test_the_committed_results_are_the_ones_the_code_produces():
    committed = json.loads((SAMPLE / "results.json").read_text(encoding="utf-8"))
    records, labels = et.load_sample(SAMPLE)
    now = et.results(et.run(records, labels), sample=et.sample_digest(SAMPLE),
                     rules=et.rules_digest(evidence.read_fields))
    assert et.compare(committed, now) == ([], [])
    assert committed["splits"] == now["splits"]


def test_the_manual_check_judged_the_values_the_rules_read():
    check = json.loads((SAMPLE / "manual_check.json").read_text(encoding="utf-8"))
    records, labels = et.load_sample(SAMPLE)
    summary = et.manual_check_summary(check, et.run(records, labels))
    assert summary["checked"] == 30
    drawn = et.draw_manual_check(et.run(records, labels), 30)
    assert [(e["pmid"], e["field"]) for e in check["fields"]] == [
        (e["pmid"], e["field"]) for e in drawn]
    assert {e["verdict"] for e in check["fields"]} <= {"correct", "partly", "wrong"}
