"""The network-pharmacology analysis as a component PSH can execute.

The research loop used to call :func:`run_network_pharmacology` in its own process, next
to the PSH program it had compiled. The audit (F06) asked for the compiled plan to be the
one that runs. This module is the analysis step in the form PSH's broker can execute. It
is a plain function of JSON arguments, so the kernel can run it in an isolated child
process with a clean environment, and it returns JSON.

The function trusts nothing it is handed. Snapshots are named by key, version and the id
recorded in the ledger, and it loads them itself through the ledger. The hashes are
re-checked in the process that computes, not only in the one that asked.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

__all__ = ["network_pharmacology_tool", "TOOL_ID", "TOOL_ENTRYPOINT"]

TOOL_ID = "research.tool.network_pharmacology"
TOOL_ENTRYPOINT = "bioagent.research.tools:network_pharmacology_tool"


def _tupled(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_tupled(v) for v in value)
    return value


def network_pharmacology_tool(snapshot_root: str, ledger_path: str,
                              snapshots: Sequence[Sequence[str]],
                              formula: Mapping[str, Any], parameters: Mapping[str, Any],
                              skill: Mapping[str, Any] | None = None,
                              accept_review: bool = False) -> dict[str, Any]:
    """Run the analysis on ledger-verified snapshots; return the result as JSON data.

    ``snapshots`` is a list of ``[key, version, snapshot_id]``. ``formula`` holds the
    fields of a ``FormulaVersion`` and ``parameters`` those of ``Parameters``. ``skill`` is
    the skill contract as data (``SkillContract.as_dict``), so the tool reads no file
    beyond the snapshots and the ledger it declares.
    """
    from ..analysis.network_pharmacology import Parameters, run_network_pharmacology
    from ..providers.skills import SkillContract
    from ..sources.herbs import FormulaVersion
    from ..sources.ledger import SnapshotLedger
    from ..sources.snapshot import load_snapshot

    ledger = SnapshotLedger(ledger_path)
    ledger.verify()
    loaded = [load_snapshot(snapshot_root, key, version, expected_id=sid, ledger=ledger,
                            accept_review=accept_review)
              for key, version, sid in snapshots]
    version = FormulaVersion(
        str(formula["id"]), str(formula["chinese"]), str(formula["source"]),
        tuple(tuple(c) for c in formula["components"]),
        license=str(formula.get("license") or "CC0-1.0"),
        primary_source=str(formula.get("primary_source") or ""),
        record_ids=tuple(str(r) for r in formula.get("record_ids") or ()),
        written=tuple(str(w) for w in formula.get("written") or ()))
    params = Parameters(**{k: _tupled(v) for k, v in parameters.items()})
    contract = SkillContract.from_mapping(skill) if skill else None
    result = run_network_pharmacology(loaded, formula=version, params=params,
                                      contract=contract)
    doc = json.loads(json.dumps(result.as_dict(), ensure_ascii=False, default=str))
    doc["digest"] = result.digest()
    return doc
