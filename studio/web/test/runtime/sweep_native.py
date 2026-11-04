#!/usr/bin/env python3
"""The browser sweep's native side (sweep.mjs): each call as ``python3 -m tcmstudio call TOOL --args -
--where browser --no-network --state-root ROOT --project ID --compact`` makes it, all in one process.

    python sweep_native.py --calls calls.json --state-root DIR --project ID --out native.jsonl [--timeout S]

calls.json is {calls: [{key, tool, args}]}, written by Node: the arguments as JavaScript serialises
them, which is what the page hands the runtime (4.0 is the number 4 on both sides). The context is
the one tcmstudio.cli builds for ``call`` (where, network, purpose, project_id, state_root,
tcmdb_root), the envelope goes through the same ``json.dumps`` the CLI prints, and Node reads it
back with JSON.parse as run.mjs does. One JSON line per call: {key, envelope_json | error, ms}. A
call that raises (the CLI would print a traceback and nothing on stdout) is recorded with its
exception; one that outlives --timeout with ``timeout: true``.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
import traceback
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--calls", required=True)
    p.add_argument("--state-root", required=True)
    p.add_argument("--project", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--timeout", type=float, default=0, help="seconds a call may take (0: no limit)")
    a = p.parse_args()
    from tcmstudio.dispatch import call

    doc = json.loads(Path(a.calls).read_text(encoding="utf-8"))
    context = {"where": "browser", "network": False, "purpose": "academic", "project_id": a.project,
               "state_root": a.state_root, "tcmdb_root": None}
    fired = []

    def expired(signum, frame):  # noqa: ARG001
        # the dispatcher turns most exceptions into a failed envelope, so the record also notes that the alarm fired
        fired.append(True)
        raise TimeoutError(f"the call took longer than {a.timeout:g} s")

    if a.timeout > 0:
        signal.signal(signal.SIGALRM, expired)
    with open(a.out, "w", encoding="utf-8") as out:
        for c in doc["calls"]:
            t0 = time.perf_counter()
            rec = {"key": c["key"]}
            fired.clear()
            if a.timeout > 0:
                signal.setitimer(signal.ITIMER_REAL, a.timeout)
            try:
                # a fresh copy each time, as the CLI parses its own: the dispatcher must not see another call's objects
                env = call(c["tool"], json.loads(json.dumps(c["args"])), dict(context))
                rec["envelope_json"] = json.dumps(env, ensure_ascii=False)
            except TimeoutError as exc:
                rec["timeout"] = True
                rec["error"] = str(exc)
            except Exception as exc:  # noqa: BLE001 - the record says what was raised
                rec["error"] = f"{type(exc).__name__}: {exc}"
                rec["traceback"] = traceback.format_exc()[-3000:]
            finally:
                if a.timeout > 0:
                    signal.setitimer(signal.ITIMER_REAL, 0)
            if fired:
                rec["timeout"] = True
            rec["ms"] = round((time.perf_counter() - t0) * 1000)
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
    print(f"native: {len(doc['calls'])} calls", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
