"""Shared fixtures: a fake job service, a project state root, and real clinic inputs.

Every test runs offline. Network entries are exercised with web access off, which the
policy kernel refuses before any socket is opened.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

INTAKE: dict[str, Any] = {
    "schema": "bioagent.clinic.intake/1",
    "patient": {"id": "demo-001", "age": 40, "sex": "female", "pregnant": False,
                "breastfeeding": False, "weight_kg": 58},
    "chief_complaint": "乏力纳差两月", "duration": "2个月",
    "inspection": {"present": ["面色萎黄"], "absent": [], "tongue": ["舌淡，苔白"]},
    "listening_smelling": {"present": ["少气懒言"], "absent": []},
    "inquiry": {"present": ["神疲乏力", "胃口差", "大便稀", "腹胀"], "absent": ["发热"]},
    "palpation": {"pulse": ["脉缓弱"], "present": [], "absent": []},
    "vitals": {"temperature_c": 36.6, "heart_rate": 72, "resp_rate": 16, "systolic": 118,
               "diastolic": 76, "spo2": 98},
    "medications": [], "allergies": [], "conditions": [], "red_flags_cleared": [],
    "collected_by": "demo", "collected_at": "2026-10-07T09:00:00Z",
}

RED_FLAG_INTAKE: dict[str, Any] = {
    **copy.deepcopy(INTAKE),
    "chief_complaint": "胸痛半小时",
    "inquiry": {"present": ["胸痛", "气短"], "absent": []},
    "vitals": {"temperature_c": 36.8, "heart_rate": 110, "resp_rate": 24, "systolic": 100,
               "diastolic": 60, "spo2": 88},
}

VISITS: dict[str, Any] = {
    "syndrome": "脾胃气虚证",
    "baseline": {"date": "2026-10-01", "scores": {"神疲乏力": "重", "食少": "中", "便溏": "中",
                                                  "腹胀": "轻"}},
    "current": {"date": "2026-10-08", "scores": {"神疲乏力": "轻", "食少": "轻", "便溏": "无",
                                                 "腹胀": "无"}, "adherence": "full",
                "adverse_events": [{"event": "口干", "severity": "mild", "relation": "possible"}]},
}


class FakeJobs:
    """The runner's job service, as the dispatcher sees it: submit and get."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.submitted: list[tuple[str, dict[str, Any], Any]] = []

    def submit(self, kind: str, params: dict[str, Any], project_id: Any) -> dict[str, Any]:
        if kind == "bad.kind":
            raise ValueError("unknown kind")
        job = {"id": f"j_{len(self.jobs) + 1:04d}", "kind": kind, "state": "queued",
               "params": params, "project_id": project_id, "progress": None, "outcome": None,
               "artefacts": []}
        self.jobs[job["id"]] = job
        self.submitted.append((kind, params, project_id))
        return dict(job)

    def get(self, job_id: str, wait_s: int = 0) -> dict[str, Any]:
        return dict(self.jobs[job_id])


@pytest.fixture
def jobs() -> FakeJobs:
    return FakeJobs()


@pytest.fixture
def runner_ctx(tmp_path, jobs) -> dict[str, Any]:
    return {"where": "runner", "state_root": str(tmp_path / "projects"),
            "project_id": "p-test", "jobs": jobs, "tcmdb_root": str(tmp_path / "tcmdb")}


@pytest.fixture
def browser_ctx(tmp_path) -> dict[str, Any]:
    return {"where": "browser", "state_root": str(tmp_path / "persist"), "project_id": "p-web",
            "durable": False}


@pytest.fixture
def intake() -> dict[str, Any]:
    return copy.deepcopy(INTAKE)


@pytest.fixture
def red_flag_intake() -> dict[str, Any]:
    return copy.deepcopy(RED_FLAG_INTAKE)


@pytest.fixture
def visits() -> dict[str, Any]:
    return copy.deepcopy(VISITS)
