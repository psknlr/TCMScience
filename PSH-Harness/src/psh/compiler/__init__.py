"""The scientific compiler: refuse a research program before it spends anything.

``psh.runtime.plan_validator`` refuses a plan whose authority does not fit the run. This
package refuses a *program* whose science does not fit its evidence, whose statistics will
not support its inference, whose data flow would carry identifiable values somewhere they
may not go, or whose fan-out would exhaust a budget nobody bounded.

Everything it produces is a ``psh.runtime.plan.Plan``, which the existing validator
validates and the existing kernel executes. The compiler adds refusals and no execution
path.

``bridge.to_scientific_program`` carries a compilation over to ``psh.workflow``, the
back-half compiler that checks a plan against the registry and the protocol ledger, so a
programme can be ruled on by both. See that module for what crosses and what does not.
"""

from .bridge import (
    CLAIM_KINDS, DESIGN_NAMES, EFFECT_NAMES, SIDE_EFFECTS, BridgeRefused,
    to_scientific_program,
)
from .diagnostics import (
    REGISTRY, Diagnostic, DiagnosticSpec, Diagnostics, Severity, severity_of,
)
from .effects import CONSEQUENTIAL, check_effects
from .infoflow import LICENCE_FORBIDS, FlowResult, check_flow
from .lowering import LoweringError, executable_dependencies, lower
from .method import check_method
from .pipeline import (
    PASS_ORDER, CompiledProgram, CompileOptions, CompileRejected, compile_or_raise,
    compile_program,
)
from .reproducibility import check_reproducibility
from .resources import UNBOUNDED, check_resources, multipliers
from .statistics import (
    CONFIRMATORY_FAMILIES, DISCOVERY_FAMILIES, PREDICTIVE_FAMILIES, check_statistics,
)
from .structure import MODEL_EFFECTS, check_structure
from .typecheck import check_types, upstream_evidence

__all__ = [
    "compile_program", "compile_or_raise", "CompiledProgram", "CompileOptions",
    "CompileRejected", "PASS_ORDER",
    "Diagnostic", "Diagnostics", "DiagnosticSpec", "Severity", "REGISTRY", "severity_of",
    "check_structure", "check_method", "check_types", "check_effects", "check_flow",
    "check_statistics", "check_reproducibility", "check_resources",
    "lower", "LoweringError", "executable_dependencies",
    "FlowResult", "LICENCE_FORBIDS", "MODEL_EFFECTS", "CONSEQUENTIAL",
    "DISCOVERY_FAMILIES", "CONFIRMATORY_FAMILIES", "PREDICTIVE_FAMILIES",
    "upstream_evidence", "multipliers", "UNBOUNDED",
    "to_scientific_program", "BridgeRefused", "DESIGN_NAMES", "CLAIM_KINDS",
    "EFFECT_NAMES", "SIDE_EFFECTS",
]
