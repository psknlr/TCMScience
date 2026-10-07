"""Isolated entrypoint: run one BioScience component inside a PSH child process.

PSH's ``IsolatedExecutor`` runs a component's entrypoint with a clean environment (no
``PYTHONPATH``, no inherited secrets, the kernel's egress proxy as the only route out),
writes ``{"tool", "run_id", "payload"}`` as one JSON object on stdin and expects one JSON
value on stdout. This script is that entrypoint for bridged components::

    python exec.py --manifest /path/to/component.yaml     # the admitted manifest
    python exec.py --component public.connector.hgnc      # by id, from the default runtime

It bootstraps ``bioagent`` from its own location because the environment carries no
path, builds the smallest runtime that can serve the component, and reports failures on
stderr with a non-zero exit so the kernel records a contract violation rather than a
result.

An MCP component's servers arrive the same way its manifest does: ``--mcp-config`` names
the registry file the admitting process wrote (only the server the component calls), and
``--mcp-config-digest`` the digest it admitted. A file whose digest differs is refused
before anything starts. The child never reads the shipped registry, so it reaches exactly
what was admitted, and it is given no credential source: a server that needs a credential
is UNAVAILABLE here (docs/mcp-transport.md says why).

A long-job tool's configuration arrives the same way (``--jobs`` and ``--jobs-digest``):
the executor, the job definition and the trace its jobs are recorded in. The child submits,
collects or cancels through that tool and exits; the job runs on. When its job has not
finished the child writes ``{"$psh": {"status": "pending", "reference": ..., "reason":
...}}`` — the job's reference, never a value — and the kernel records the call as pending.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):                            # run as a script by the kernel
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--manifest", help="path to the admitted BioScience manifest (YAML)")
    group.add_argument("--component", help="component id in the packaged catalogue/sources")
    parser.add_argument("--profile", default="biomedical-research")
    parser.add_argument("--workspace", help="the workspace root the admitting process resolved")
    parser.add_argument("--data-lake", help="the data-lake root the admitting process resolved")
    parser.add_argument("--tcmdb", help="the TCM data hub root the admitting process resolved")
    parser.add_argument("--mcp-config",
                        help="the MCP registry file the admitting process wrote")
    parser.add_argument("--mcp-config-digest",
                        help="the digest of that registry, as admitted")
    parser.add_argument("--jobs",
                        help="the job tool configuration the admitting process wrote")
    parser.add_argument("--jobs-digest",
                        help="the digest of that configuration, as admitted")
    args = parser.parse_args(argv)

    # The environment is cleared; the profile's ${workspace}, ${data_lake} and ${tcmdb} are
    # the admitting process's, passed on the command line fixed at admission.
    import os
    from bioagent.config import ENV_DATA_LAKE, ENV_TCMDB, ENV_WORKSPACE
    if args.workspace:
        os.environ[ENV_WORKSPACE] = args.workspace
    if args.data_lake:
        os.environ[ENV_DATA_LAKE] = args.data_lake
    if args.tcmdb:
        os.environ[ENV_TCMDB] = args.tcmdb

    from bioagent.backends.jobtool import JobTool
    from bioagent.mcp import MCPConfigError, MCPServerRegistry, load_registry
    from bioagent.psh.arguments import ArgumentError, arguments_for, job_arguments
    from bioagent.psh.assembly import default_runtime
    from bioagent.runtime.agentspec import AgentSpec
    from bioagent.runtime.component import ComponentManifest
    from bioagent.status import ExecutionStatus

    try:
        request = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        print(f"invalid request on stdin: {exc}", file=sys.stderr)
        return 2
    payload = request.get("payload") if isinstance(request, dict) else None
    payload = payload if isinstance(payload, dict) else {}

    # Exactly the MCP servers the admitting process handed over, verified; never the
    # shipped registry, which may have changed since admission or name more than this
    # component may reach.
    mcp_servers = MCPServerRegistry(source="none given to this child")
    if args.mcp_config or args.mcp_config_digest:
        if not (args.mcp_config and args.mcp_config_digest):
            print("ContractViolation: --mcp-config and --mcp-config-digest come together; "
                  "an MCP registry without the digest it was admitted under is refused",
                  file=sys.stderr)
            return 2
        try:
            mcp_servers = load_registry(args.mcp_config)
        except MCPConfigError as exc:
            print(f"ContractViolation: {str(exc)[:300]}", file=sys.stderr)
            return 1
        if mcp_servers.digest != args.mcp_config_digest:
            print(f"ContractViolation: the MCP registry {args.mcp_config} has digest "
                  f"{mcp_servers.digest[:16]}, not the {args.mcp_config_digest[:16]} it was "
                  "admitted under; refusing to reach servers nobody admitted",
                  file=sys.stderr)
            return 1

    # A job tool runs exactly the configuration admitted with it, checked like the MCP
    # registry: a file whose digest differs would run another executor or command.
    tool = None
    if args.jobs or args.jobs_digest:
        if not (args.jobs and args.jobs_digest and args.manifest):
            print("ContractViolation: --jobs and --jobs-digest come together, with the "
                  "admitted --manifest", file=sys.stderr)
            return 2
        try:
            tool = JobTool.load(args.jobs)
        except (OSError, ValueError, KeyError) as exc:
            print(f"ContractViolation: the job tool configuration {args.jobs} cannot be "
                  f"read: {str(exc)[:200]}", file=sys.stderr)
            return 1
        if tool.digest != args.jobs_digest:
            print(f"ContractViolation: the job tool configuration {args.jobs} has digest "
                  f"{tool.digest[:16]}, not the {args.jobs_digest[:16]} it was admitted under",
                  file=sys.stderr)
            return 1

    if tool is not None:
        bio = ComponentManifest.load(args.manifest)
        runtime = tool.runtime(bio)
    elif args.manifest:
        bio = ComponentManifest.load(args.manifest)
        connector = bio.id.startswith("public.connector.")
        runtime = default_runtime(catalogue=False, public_apis=connector,
                                  extra_manifests=() if connector else (bio,),
                                  mcp_servers=mcp_servers, mcp_credentials=None)
    else:
        runtime = default_runtime(mcp_servers=mcp_servers, mcp_credentials=None)
        bio = runtime.registry.get(args.component)
        if bio is None:
            print(f"no component {args.component!r}", file=sys.stderr)
            return 2

    source = None
    if bio.id.startswith("public.connector."):
        from bioagent.providers.public_apis import BY_KEY
        source = BY_KEY.get(bio.id.rsplit(".", 1)[-1])

    try:
        kwargs = (job_arguments(payload, component_id=bio.id) if tool is not None
                  else arguments_for(payload, source=source, component_id=bio.id))
    except ArgumentError as exc:
        print(f"ContractViolation: {exc}", file=sys.stderr)
        return 1
    spec = AgentSpec(name="psh-isolated", permission_profile=args.profile)
    key = payload.get("_psh_idempotency_key")
    bookkeeping = {"idempotency_key": str(key)} if key else {}
    try:
        result = runtime.invoke(bio.id, spec=spec, **bookkeeping, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the exit code is the contract
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        # Stop any MCP server this call started before the child exits, while there is
        # time to do it cleanly; the kernel kills only this child's process group, and
        # the SDK starts a server in a session of its own.
        close = getattr(runtime.backends.get("mcp"), "close", None)
        if callable(close):
            close()
    if result.status is ExecutionStatus.TIMEOUT:
        # 124 is what ``timeout(1)`` exits with; the kernel reads it as a ToolTimeout.
        print(f"TIMEOUT: {(result.error or 'no detail')[:300]}", file=sys.stderr)
        return 124
    job = (result.metadata or {}).get("job") if result.status in (
        ExecutionStatus.RUNNING, ExecutionStatus.UNAVAILABLE) else None
    if isinstance(job, dict) and job:
        # The protocol's second extension: work that outlives this process, named by its
        # job's reference — not finished, or not collectable now (the in-process bridge's
        # rule, ``BridgedComponent.pending_of``). The kernel records it as pending; it is
        # never a value.
        sys.stdout.write(json.dumps({"$psh": {"status": "pending",
                                              "reason": (result.error or "")[:300],
                                              "reference": job}}, default=str))
        return 0
    if result.status not in (ExecutionStatus.SUCCEEDED, ExecutionStatus.DEGRADED):
        # The kernel records a contract violation with this text; keep it bounded.
        print(f"{result.status.value}: {(result.error or 'no detail')[:300]}", file=sys.stderr)
        return 1
    if result.status is ExecutionStatus.DEGRADED:
        # The one extension of the one-JSON-value protocol: the value, wrapped with the
        # shortfall it ran under, so the kernel records a degraded result with a caveat.
        sys.stdout.write(json.dumps({"$psh": {"status": "degraded",
                                              "reason": (result.error or "")[:300],
                                              "value": result.value}}, default=str))
        return 0
    sys.stdout.write(json.dumps(result.value, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
