"""The environment record: what the skill pin does not cover (audit F08)."""

from __future__ import annotations

import copy

from bioagent.environment import environment_digest, environment_record


def test_the_record_names_the_interpreter_and_the_dependency_closure():
    record = environment_record()
    assert record["python"]["version"].count(".") == 2
    packages = record["packages"]
    assert {"bioagent", "psh", "pandas", "pyarrow", "pyyaml"} <= set(packages)
    assert all(v for v in packages.values())
    assert record["digest"].startswith("sha256:")


def test_the_digest_moves_with_any_package_version_and_nothing_else():
    record = environment_record()
    changed = copy.deepcopy(record)
    changed["packages"]["pandas"] = "0.0.0"
    assert environment_digest(changed) != record["digest"]
    reworded = copy.deepcopy(record)
    reworded["note"] = "a different note"
    assert environment_digest(reworded) == record["digest"]


def test_a_governed_run_records_its_environment(tmp_path):
    from pathlib import Path

    from bioagent.governed import run_governed
    skills = Path(__file__).resolve().parents[1] / "skills" / "tcm"
    run = run_governed("normalize-tcm-entities", {"names": ["黄芪"]}, skill_dir=skills,
                       state_dir=tmp_path / "psh", output_dir=tmp_path / "out")
    assert run.artifact.provenance["environment"]["digest"] == environment_record()["digest"]
