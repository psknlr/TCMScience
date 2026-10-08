"""The browser-capable path starts no thread, process or socket.

The Pyodide worker imports the same modules: there a thread or a subprocess raises, so the
check runs in a fresh interpreter with those primitives made to fail loudly.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

PROBE = textwrap.dedent(r"""
    import json, socket, subprocess, sys, threading
    calls = []
    def forbid(name):
        def fail(*a, **k):
            calls.append(name)
            raise RuntimeError(f"{name} is not allowed on the browser path")
        return fail
    threading.Thread.start = forbid("thread")
    subprocess.Popen.__init__ = forbid("subprocess")
    socket.socket.connect = forbid("socket")
    import os
    os.system = forbid("os.system")

    import tcmstudio, tcmstudio.envelope, tcmstudio.governance, tcmstudio.core_tools
    import tcmstudio.catalog, tcmstudio.dispatch, tcmstudio.cli
    from tcmstudio.catalog import build_catalog
    from tcmstudio.dispatch import call
    doc = build_catalog("browser")
    ctx = {"where": "browser", "state_root": sys.argv[1], "project_id": "iso"}
    results = {}
    for tool, args in [("tcm_compatibility", {"herbs": ["甘草", "甘遂"]}),
                       ("tcm_safety_report", {"subject": "甘草", "co_administered": ["甘遂"]}),
                       ("clinic_followup", {"baseline": {"date": "2026-10-01",
                                                         "scores": {"神疲乏力": "重"}},
                                            "current": {"date": "2026-10-08",
                                                        "scores": {"神疲乏力": "轻"}}}),
                       ("catalog_search", {"query": "十八反"}),
                       ("capabilities_status", {}),
                       ("connector_call", {"connector": "uniprot", "operation": "entry",
                                           "arguments": {"accession": "P04637"}})]:
        e = call(tool, args, ctx)
        results[tool] = [e["status"], (e["error"] or {}).get("type"),
                         e["governance"].get("released")]
    print(json.dumps({"calls": calls, "threads": threading.active_count(),
                      "entries": len(doc["entries"]), "results": results}))
""")


def test_browser_path_starts_no_thread_process_or_socket(tmp_path):
    proc = subprocess.run([sys.executable, "-I", "-c", PROBE, str(tmp_path)],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-4000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["calls"] == [], out
    assert out["threads"] == 1
    assert out["entries"] > 600
    r = out["results"]
    assert r["tcm_compatibility"][0] == "succeeded"
    assert r["tcm_safety_report"] == ["succeeded", None, True]
    assert r["clinic_followup"][0] == "succeeded"
    assert r["catalog_search"][0] == "succeeded" and r["capabilities_status"][0] == "succeeded"
    assert r["connector_call"][:2] == ["failed", "unavailable"]


def test_importing_the_package_is_cheap():
    code = ("import sys, tcmstudio; mods = [m for m in sys.modules if m.startswith(('bioagent', "
            "'psh', 'yaml', 'numpy'))]; print(len(mods))")
    proc = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "0"
