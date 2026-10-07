"""The network-pharmacology skill run goes to the usage gate, as ``Runtime.invoke`` does.

Before, ``run_skill`` applied the source cards and the studied composition's licence to a
commercial run and asked nothing about the code that ran: the analysis, the library that
reads the snapshots, PSH's compiler. The provider-bindings note said why: calling the gate
would refuse every commercial skill run, because PSH-Harness shipped no licence file. It
ships one now, with a first-party record, and the run is gated per asset:

* a research run is unchanged — the gate is not consulted and the provenance gains nothing;
* a commercial run records every asset it draws on, the record relied on and the verdict,
  and is refused, before the analysis runs, when one asset has no record permitting it.
"""

from __future__ import annotations

import pytest

from bioagent.analysis.skill_runner import SkillRunRefused, run_skill
from bioagent.licences import LicenceRecords
from bioagent.sources.herbs import GEGEN_QINLIAN
from test_hkbu_formulas import _world_with
from test_network_pharmacology import FAST, SKILL_DIR

pytestmark = pytest.mark.unit

ALLOWED = {"lotus", "string", "reactome"}


def _run(tmp_path, ledger, out, **kw):
    return run_skill(skill_dir=SKILL_DIR, snapshot_root=tmp_path / "snap",
                     ledger_path=ledger.path, out_dir=tmp_path / out, params=FAST,
                     allowed=ALLOWED, formula=GEGEN_QINLIAN, **kw)


def test_a_research_run_does_not_consult_the_gate(tmp_path, monkeypatch):
    import bioagent.licences as licences

    def refuse(*args, **kwargs):
        raise AssertionError("an academic skill run consulted the usage gate")

    monkeypatch.setattr(licences, "usage_decision", refuse)
    monkeypatch.setattr(licences.LicenceRecords, "load", refuse)
    ledger = _world_with(tmp_path, [])
    provenance = _run(tmp_path, ledger, "academic")
    assert provenance["purpose"] == "academic" and "usage" not in provenance
    assert provenance["claims"]["released"] == 1


def test_a_commercial_run_records_every_asset_it_draws_on(tmp_path):
    ledger = _world_with(tmp_path, [])
    provenance = _run(tmp_path, ledger, "commercial", purpose="commercial")
    usage = provenance["usage"]
    assert usage["purpose"] == "commercial" and usage["verdict"] == "allow"
    assert [(a["kind"], a["ref"], a["role"], a["record"], a["verdict"])
            for a in usage["assets"]] == [
        ("code", "analysis.network_pharmacology", "implementation", "code.bioagent", "ALLOW"),
        ("code", "pyarrow", "requirement", "code.pyarrow", "ALLOW"),
        ("code", "yaml", "requirement", "code.pyyaml", "ALLOW"),
        ("code", "psh", "requirement", "code.psh", "ALLOW"),
        ("data", "source:lotus@2026-04-13", "source", "source:lotus", "ALLOW"),
        ("data", "source:reactome@current", "source", "source:reactome", "ALLOW"),
        ("data", "source:string@12.0+fx", "source", "source:string", "ALLOW")]
    psh = next(a for a in usage["assets"] if a["ref"] == "psh")
    assert psh["terms"]["spdx"] == "MIT" and psh["terms"]["licence_path"].endswith(
        "PSH-Harness/LICENSE")
    assert provenance["claims"]["released"] == 1


def test_a_commercial_run_is_refused_before_the_analysis_for_one_unreviewed_asset(tmp_path):
    shipped = LicenceRecords.load()
    without_psh = LicenceRecords([r for r in shipped.records if r.id != "code.psh"])
    ledger = _world_with(tmp_path, [])
    with pytest.raises(SkillRunRefused, match="code psh .requirement. has no reviewed "
                                              "licence record"):
        _run(tmp_path, ledger, "refused", purpose="commercial", licences=without_psh)
    assert not (tmp_path / "refused" / "claims.json").exists(), "the analysis ran"
    # the same records serve a research run, which the gate does not rule on
    assert _run(tmp_path, ledger, "research", licences=without_psh)["claims"]["released"] == 1


def test_a_run_without_psh_does_not_count_it(tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "psh.workflow", None)
    ledger = _world_with(tmp_path, [])
    provenance = _run(tmp_path, ledger, "ungoverned", purpose="commercial",
                      require_psh=False)
    assert provenance["governed"] is False
    assert "psh" not in {a["ref"] for a in provenance["usage"]["assets"]}
