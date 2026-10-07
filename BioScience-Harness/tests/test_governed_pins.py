"""A governed run is checked against a pin, or it is not released.

``run_governed`` used to skip the pin check when no lockfile was given and none was
found above the skill directory: a skill copied anywhere outside the registry ran, was
attested and was released with nothing checking that its code was the code reviewed.
A skill no lockfile pins is now refused. A development run may be allowed explicitly;
it is recorded in the audit chain as unpinned and never attested, so never released.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from bioagent.governed import GovernedRunRefused, run_governed

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "skills" / "tcm" / "normalize-tcm-entities"


@pytest.fixture
def loose_tree(tmp_path):
    """The skill, copied where no registry/skills.lock.yaml lies above it."""
    tree = tmp_path / "elsewhere" / "skills" / "tcm"
    shutil.copytree(SKILL, tree / SKILL.name)
    return tree


def _events(state_dir: Path) -> list[tuple[str, dict]]:
    with sqlite3.connect(state_dir / "events.db") as db:
        rows = db.execute("SELECT event_type, detail FROM events ORDER BY seq").fetchall()
    return [(kind, json.loads(detail or "{}")) for kind, detail in rows]


def test_a_skill_no_lockfile_pins_is_refused(loose_tree, tmp_path):
    with pytest.raises(GovernedRunRefused, match="no lockfile pins skill"):
        run_governed("normalize-tcm-entities", {"names": ["黄芪"]}, skill_dir=loose_tree,
                     state_dir=tmp_path / "psh", output_dir=tmp_path / "out")


def test_a_named_lockfile_that_does_not_exist_is_refused(tmp_path):
    with pytest.raises(GovernedRunRefused, match="does not exist"):
        run_governed("normalize-tcm-entities", {"names": ["黄芪"]},
                     skill_dir=SKILL.parent, lockfile=tmp_path / "missing.lock.yaml",
                     state_dir=tmp_path / "psh")


def test_an_unpinned_development_run_is_recorded_and_never_released(loose_tree, tmp_path):
    run = run_governed("normalize-tcm-entities", {"names": ["黄芪"]}, skill_dir=loose_tree,
                       state_dir=tmp_path / "psh", output_dir=tmp_path / "out",
                       allow_unpinned=True)
    assert run.lockfile == "" and not run.released
    assert run.verdict.publishable and not run.verdict.execution_attested
    assert run.artifact.provenance["governed"]["pinned_by"] == ""
    events = dict(_events(tmp_path / "psh"))
    assert events["bioscience_skill_run_started"]["pinned"] is False
    assert "bioscience_artifact_not_attested" in events
    assert "bioscience_artifact_attested" not in events


def test_a_pinned_run_names_its_pin(tmp_path):
    run = run_governed("normalize-tcm-entities", {"names": ["黄芪"]}, skill_dir=SKILL.parent,
                       state_dir=tmp_path / "psh", output_dir=tmp_path / "out")
    assert run.released
    lock = str(REPO / "registry" / "skills.lock.yaml")
    assert run.lockfile == lock and run.artifact.provenance["governed"]["pinned_by"] == lock
    assert dict(_events(tmp_path / "psh"))["bioscience_skill_run_started"]["pinned"] is True


def test_the_cli_refuses_an_unpinned_skill_unless_told_it_is_a_development_run(
        loose_tree, tmp_path, capsys):
    from bioagent.cli import main
    base = ["skill", "normalize-tcm-entities", "--dir", str(loose_tree), "--arg", "names=黄芪",
            "--state-dir", str(tmp_path / "psh"), "--out-dir", str(tmp_path / "out")]
    assert main(base) == 2
    assert "no lockfile pins skill" in capsys.readouterr().err
    assert main(base + ["--allow-unpinned"]) == 1           # ran, not released
    assert "no lockfile (the skill was not checked against a pin)" in capsys.readouterr().out
