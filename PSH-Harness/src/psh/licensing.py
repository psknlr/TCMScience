"""Licence and integration provenance: may this code be used *this way*?

Adopted from BioScience-Harness, whose genuinely distinct contribution to policy is not its
permission model — PSH's is stronger — but this: a capability's licence and the *manner* in
which it is integrated are a governable dimension, and for research software they are one
of the dimensions that actually bites. A lab that vendors GPL code into a product it then
distributes has a real problem, and no amount of data-label enforcement notices it.

The question is not "is this licence acceptable" but "is this licence acceptable **for this
integration mode**", because the same licence gives different answers:

    vendor      copy the upstream implementation into this tree   -> redistribution
    native      call an independent equivalent, not upstream code  -> no upstream code at all
    federated   invoke upstream in its own process                 -> use, not redistribution

So an unlicensed component may be *invoked* and may not be *copied*, which a single
allow/deny per licence cannot express. A copyleft licence answers by mode for the opposite
reason: it grants running the code for any purpose and attaches its obligations to passing
it on, so vendoring — whose copy is distributed with this tree — is permitted with a
native equivalent preferred, federated and native use are allowed, and a ruling states the
obligation (``COPYLEFT_OBLIGATIONS``). The table below is the rule; ``PolicySnapshot``
narrows which classes and modes a profile permits at all; and ``ToolGateway`` enforces it
at the same gate as every other egress decision, so it cannot be bypassed by reaching the
component another way.

The shape deliberately mirrors ``labels.DEFAULT_CEILINGS``: a fixed table stating what is
permissible in principle, plus a per-policy narrowing of what this profile allows. That is
the existing pattern for destinations, and reusing it means the two dimensions behave the
same way under ``restrict``, ``meet`` and delegation without anyone having to remember to
make them.

A commercial purpose asks a narrower question, and has its own table. ``federated`` use
of unlicensed code is allowed above because invoking it is not redistributing it — which
says nothing about whether its owner lets anyone use it in a product, and the answer was
being read as that permission. With ``commercial=True`` running upstream code needs a
grant: an unlicensed component is refused federated as well as vendored, and a native
equivalent, which runs none of its code, is still allowed. The commercial table is never
more permissive than the research one, cell for cell, and the research answers are
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

__all__ = ["LicenseClass", "LicenseDecision", "LicenseRuling", "INTEGRATION_MODES",
           "LICENSE_CLASSES", "COMMERCIAL_LICENSE_TABLE", "COPYLEFT_OBLIGATIONS",
           "classify_license", "license_ruling", "normalise_mode"]


class LicenseClass(str, Enum):
    """What a licence permits, coarsely. Finer distinctions need a lawyer, not a table."""

    PERMISSIVE = "permissive"
    COPYLEFT = "copyleft"
    NONE = "none"                  # no grant: all rights reserved by default


class LicenseDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    #: Permitted, but a native equivalent should be preferred. Distinct from ALLOW because
    #: a policy may choose to treat it as a refusal, and distinct from DENY because
    #: treating every copyleft vendoring as forbidden is wrong.
    PREFER_ALTERNATIVE = "prefer_alternative"


#: SPDX ids that permit reusing (vendoring) upstream implementation code.
PERMISSIVE_SPDX = frozenset({
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC",
    "NLM-Public-Domain", "CC0-1.0", "Unlicense", "PostgreSQL", "Zlib",
    # Data licences: attribution-only or public domain. A data source's licence governs
    # reuse of what it returns, and these grant it. ``US-Gov-Public-Domain`` is this
    # package's convention for works of the US government (NCBI, NLM, FDA, NCI), which
    # SPDX has no id for; ``NLM-Public-Domain`` is the older spelling of the same.
    "CC-BY-4.0", "CC-BY-3.0", "ODC-BY-1.0", "CC-PDDC", "US-Gov-Public-Domain",
})

_GPL = "a work built on it is distributed only with its source, under the same licence"
_LGPL = ("the library and changes to it are distributed only with their source, under the "
         "same licence, and a work linking it must stay relinkable")
_AGPL = ("a work built on it is distributed, or a modified version offered to users over "
         "a network, only with its source, under the same licence")
_FILE_LEVEL = "a modified file is distributed only with its source, under the same licence"
_SHARE_ALIKE = "an adaptation is shared only under the same terms"

#: Copyleft licences, and what each asks in return, as a ruling states it. Each obligation
#: attaches to passing the code (or data) on — the AGPL's also to offering a modified
#: version over a network — and none to running it, which every one of these licences
#: grants for any purpose. That is why the integration mode decides: vendoring copies the
#: code into a tree that is then distributed, while federated use runs it in its own
#: process and native use runs none of it.
#:
#: The bare GNU ids are SPDX's deprecated spellings of the "-only" licences; the current
#: ids are what upstream metadata now states. "-only" and "-or-later" differ in which later
#: versions a recipient may choose, not in what the licence obliges. Without them
#: python-igraph (GPL-2.0-or-later) read as unlicensed, and was refused a commercial run
#: its licence grants.
COPYLEFT_OBLIGATIONS: Mapping[str, str] = {
    **dict.fromkeys(("GPL-2.0", "GPL-2.0-only", "GPL-2.0-or-later",
                     "GPL-3.0", "GPL-3.0-only", "GPL-3.0-or-later"), _GPL),
    **dict.fromkeys(("LGPL-2.1", "LGPL-2.1-only", "LGPL-2.1-or-later",
                     "LGPL-3.0", "LGPL-3.0-only", "LGPL-3.0-or-later"), _LGPL),
    **dict.fromkeys(("AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later"), _AGPL),
    **dict.fromkeys(("MPL-2.0", "EPL-2.0"), _FILE_LEVEL),
    # Share-alike data licences: reuse is granted on the condition that derived data is
    # shared under the same terms — the copyleft shape, so the same rulings apply.
    **dict.fromkeys(("CC-BY-SA-4.0", "CC-BY-SA-3.0", "ODbL-1.0"), _SHARE_ALIKE),
}
COPYLEFT_SPDX = frozenset(COPYLEFT_OBLIGATIONS)

#: Treated as "no licence granted".
NO_LICENSE_SPDX = frozenset({"NONE", "NOASSERTION", "", "UNKNOWN", "PROPRIETARY"})

#: The three ways a capability can be integrated, most to least entangling.
INTEGRATION_MODES: tuple[str, ...] = ("vendor", "native", "federated")

LICENSE_CLASSES: tuple[str, ...] = tuple(c.value for c in LicenseClass)

#: The rule. class -> mode -> decision.
LICENSE_TABLE: Mapping[str, Mapping[str, LicenseDecision]] = {
    LicenseClass.PERMISSIVE.value: {
        "vendor": LicenseDecision.ALLOW,
        "native": LicenseDecision.ALLOW,
        "federated": LicenseDecision.ALLOW,
    },
    LicenseClass.COPYLEFT.value: {
        "vendor": LicenseDecision.PREFER_ALTERNATIVE,
        "native": LicenseDecision.ALLOW,
        "federated": LicenseDecision.ALLOW,
    },
    LicenseClass.NONE.value: {
        # Copying from a project that grants no licence is not permitted.
        "vendor": LicenseDecision.DENY,
        # A native equivalent avoids the upstream code entirely.
        "native": LicenseDecision.ALLOW,
        # Invoking upstream in its own process is use, not redistribution.
        "federated": LicenseDecision.ALLOW,
    },
}

#: The rule for a commercial purpose. The same as ``LICENSE_TABLE`` except where silence
#: was standing in for a grant: running unlicensed code in a product needs its owner's
#: permission whichever process it runs in. Permissive and copyleft licences grant use for
#: any purpose, so their rows do not change.
COMMERCIAL_LICENSE_TABLE: Mapping[str, Mapping[str, LicenseDecision]] = {
    **LICENSE_TABLE,
    LicenseClass.NONE.value: {
        "vendor": LicenseDecision.DENY,
        # No upstream code runs, so the upstream's silence does not bind it.
        "native": LicenseDecision.ALLOW,
        # Invoking is use, and commercial use of code nobody licensed is not granted.
        "federated": LicenseDecision.DENY,
    },
}


@dataclass(frozen=True, slots=True)
class LicenseRuling:
    """A decision plus the rule that produced it, so a refusal can be argued with."""

    decision: LicenseDecision
    license_class: str
    mode: str
    reason: str
    rule: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision is not LicenseDecision.DENY


def classify_license(spdx: str | None) -> str:
    """Map an SPDX id onto a class. **Unrecognised means unlicensed**, not permissive.

    Failing closed here is the whole point: a typo, a new SPDX id or a vendor's own string
    must not be read as a grant. The cost of being wrong in the safe direction is that
    someone adds an id to the table.
    """
    value = (spdx or "").strip()
    if value in PERMISSIVE_SPDX:
        return LicenseClass.PERMISSIVE.value
    if value in COPYLEFT_SPDX:
        return LicenseClass.COPYLEFT.value
    return LicenseClass.NONE.value


def normalise_mode(mode: str | None) -> str:
    """Accept BioScience's vocabulary, including its ``adapter-only`` alias."""
    value = (mode or "").strip().lower().replace("_", "-")
    if value in ("adapter-only", "adapter"):
        return "federated"
    return value.replace("-", "_") if value not in INTEGRATION_MODES else value


def license_ruling(spdx: str | None, mode: str | None, *,
                   commercial: bool = False) -> LicenseRuling:
    """Rule on one (licence, integration mode) pair. Unknown combinations are refused.

    ``commercial=True`` rules for a commercial purpose (``COMMERCIAL_LICENSE_TABLE``), and
    its rule names end in ``.commercial`` so an audit record says which table decided.
    """
    license_class = classify_license(spdx)
    normalised = normalise_mode(mode)
    table = COMMERCIAL_LICENSE_TABLE if commercial else LICENSE_TABLE
    decision = table.get(license_class, {}).get(normalised)
    named = spdx or "no licence"
    rule = f"license.{license_class}.{normalised}" + (".commercial" if commercial else "")

    if decision is None:
        return LicenseRuling(
            LicenseDecision.DENY, license_class, normalised,
            f"no rule covers licence class {license_class!r} integrated as {normalised!r}; "
            f"integration mode must be one of {list(INTEGRATION_MODES)}",
            rule="license.default_deny")
    if decision is LicenseDecision.DENY and commercial:
        # Not the research advice to invoke it federated instead: that is refused too.
        return LicenseRuling(
            decision, license_class, normalised,
            f"{named} grants no licence, and running code nobody licensed, copied in or "
            "invoked in its own process, is no permission to use it commercially; a "
            "commercial run needs a licence that grants the use, or a native equivalent",
            rule=rule)
    if decision is LicenseDecision.DENY:
        return LicenseRuling(
            decision, license_class, normalised,
            f"{named} does not permit {normalised} use; invoke the upstream in its own "
            "process (federated) or use a native equivalent",
            rule=rule)
    obligation = COPYLEFT_OBLIGATIONS.get((spdx or "").strip(), "")
    if decision is LicenseDecision.PREFER_ALTERNATIVE:
        return LicenseRuling(
            decision, license_class, normalised,
            f"{named} is copyleft: vendored code keeps its terms, and {obligation}; prefer "
            "a native equivalent before vendoring",
            rule=rule)
    permits = (f"{named} permits {normalised} use"
               + (" for a commercial purpose" if commercial else ""))
    if obligation and normalised == "federated":
        # Running upstream is what the licence grants unconditionally; say what is not.
        permits += (f"; running it in its own process does not incur its copyleft terms "
                    f"({obligation})")
    return LicenseRuling(decision, license_class, normalised, permits, rule=rule)
