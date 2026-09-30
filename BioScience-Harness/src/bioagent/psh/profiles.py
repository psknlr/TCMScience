"""Deployment profiles: one name for a coherent set of bridge settings (audit F09).

The bridge has independent knobs: process isolation, whether an audit failure refuses
admission, the highest data label a local component may receive, and which destinations
a component may reach. Chosen one at a time, they can be combined into a configuration
nobody intended, such as sensitive data with in-process plugins, or public connectors
behind a de-identified ceiling. A profile names the combination, and the bridge refuses
an explicit setting that contradicts it rather than quietly letting it win.

``trusted_local``
    A developer's machine running reviewed code. Isolation is used when the kernel
    can provide it (a state directory), and in-process execution is allowed. The audit
    is strict. PHI may reach local components. Remote sources are allowed.
``restricted_research``
    De-identified research data. Isolation is **required**. The audit is strict.
    Nothing above ``RESEARCH_DEIDENTIFIED`` reaches a component. Remote sources are
    allowed, under the usual egress rules.
``sensitive_data``
    Identifiable data. Isolation is **required**. The audit is strict. PHI may reach
    local components, but no component that reaches a remote destination is
    admitted at all: a connector that could carry data off the machine is refused at
    admission, not merely at call time.

These are software settings. Whether "isolated" means an OS sandbox, a container or a
VM is the kernel's ``IsolatedExecutor`` configuration, and the audit's warning stands: no
real isolation-escape test has been run here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from psh.labels import Destination, Sensitivity

__all__ = ["DeploymentProfile", "PROFILES", "profile_named"]


@dataclass(frozen=True)
class DeploymentProfile:
    name: str
    require_isolation: bool
    strict_audit: bool
    local_ceiling: Sensitivity
    allow_remote: bool
    description: str = ""

    def admits(self, destinations) -> str:
        """Why a component reaching ``destinations`` is refused, or '' when admitted."""
        if not self.allow_remote:
            remote = [d.name for d in destinations
                      if d in (Destination.PUBLIC_REMOTE, Destination.TRUSTED_REMOTE)]
            if remote:
                return (f"profile {self.name} admits no component that reaches a remote "
                        f"destination ({', '.join(remote)})")
        return ""


PROFILES: Mapping[str, DeploymentProfile] = {p.name: p for p in (
    DeploymentProfile("trusted_local", require_isolation=False, strict_audit=True,
                      local_ceiling=Sensitivity.PHI, allow_remote=True,
                      description="reviewed code on a developer machine"),
    DeploymentProfile("restricted_research", require_isolation=True, strict_audit=True,
                      local_ceiling=Sensitivity.RESEARCH_DEIDENTIFIED, allow_remote=True,
                      description="de-identified research data"),
    DeploymentProfile("sensitive_data", require_isolation=True, strict_audit=True,
                      local_ceiling=Sensitivity.PHI, allow_remote=False,
                      description="identifiable data; nothing leaves the machine"),
)}


def profile_named(profile: "str | DeploymentProfile") -> DeploymentProfile:
    if isinstance(profile, DeploymentProfile):
        return profile
    try:
        return PROFILES[profile]
    except KeyError:
        raise ValueError(f"unknown deployment profile {profile!r}; "
                         f"known: {sorted(PROFILES)}") from None
