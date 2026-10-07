"""Biological sequences a component declares are inspected, not opaque.

The classifier floors a long, high-entropy token run at SENSITIVE ("uninspectable
high-entropy content"), and the egress gate lets nothing above RESEARCH_DEIDENTIFIED reach a
public remote service. A protein of about a hundred residues is such a run, so a governed
fold of hen egg-white lysozyme (129 residues) was refused while ubiquitin (76) passed. The
typed path closes that without loosening the floor for anything else:

* a field the component's input schema declares a sequence (``format: protein-sequence``),
  holding a value that validates against the residue alphabet, is labelled research data;
* a declared field holding anything else is refused, at every destination, and the
  reason names the field and never the value;
* an undeclared field is labelled exactly as before, and every detector still reads a
  declared sequence, so a finding raises its label;
* an MCP server's schema cannot declare one, because that would lower the label of what is
  sent to the server.
"""

from __future__ import annotations

import sqlite3

import pytest

from psh import (
    DNA_SEQUENCE, PROTEIN_SEQUENCE, RNA_SEQUENCE, SEQUENCE_FORMATS, EgressDenied,
    declared_sequence_problems, sequence_problem,
)
from psh.contracts import ComponentKind, ComponentManifest
from psh.kernel.classify import Classifier
from psh.kernel.egress import ToolGateway
from psh.labels import (
    DataLabel, Destination, Labeled, Sensitivity, declared_sequences,
    without_sequence_formats,
)
from psh.protocols.mcp import MCPToolAdapter

#: hen egg-white lysozyme, 129 residues: floored at SENSITIVE when nobody declares it
LYSOZYME = ("KVFGRCELAAAMKRHGLDNYRGYSLGNWVCAAKFESNFNTQATNRNTDGSTDYGILQINSRWWCNDGRTPGSRNLC"
            "NIPCSALLSSDITASVNCAKKIVSDGNGMNAWVAWRNRCKGTDVQAWIRGCRL")
UBIQUITIN = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
API_KEY = "sk-live-" + "Zq3vT9wXk2LmP8rB5nD7cF4h"
FOLD = {"type": "object", "required": ["sequence"],
        "properties": {"sequence": {"type": "string", "format": PROTEIN_SEQUENCE},
                       "timeout": {"type": "number"}}}


def fold_manifest(schema=FOLD, **kw):
    params = dict(id="fold", name="fold", kind=ComponentKind.TOOL, input_schema=schema,
                  destinations=(Destination.LOCAL_COMPUTE, Destination.PUBLIC_REMOTE),
                  max_label=Sensitivity.RESEARCH_DEIDENTIFIED, requires_network=True)
    params.update(kw)
    return ComponentManifest(**params)


class Fold:
    """A public fold service: records what it was handed, returns a canned model."""

    def __init__(self, manifest=None):
        self.manifest = manifest or fold_manifest()
        self.calls: list = []

    def invoke(self, payload, envelope):
        self.calls.append(payload)
        return "HEADER    CANNED MODEL\nEND\n"


def label(value, schema=None):
    return Classifier().classify(value, schema=schema).label


# ================================================================= the vocabulary

@pytest.mark.parametrize("fmt,value", [
    (PROTEIN_SEQUENCE, LYSOZYME), (PROTEIN_SEQUENCE, "MKVLAAGX"),
    (DNA_SEQUENCE, "ACGTNacgtn"), (RNA_SEQUENCE, "ACGUUAGCN"),
])
def test_a_sequence_over_its_alphabet_validates(fmt, value):
    assert sequence_problem(value, fmt) == ""


@pytest.mark.parametrize("fmt,value,needle", [
    (PROTEIN_SEQUENCE, "", "is empty"),
    (PROTEIN_SEQUENCE, 129, "is a int, not a string"),
    (PROTEIN_SEQUENCE, LYSOZYME.lower(), "other letters"),       # protein is upper case
    (PROTEIN_SEQUENCE, "MKV LAA", "whitespace"),
    (PROTEIN_SEQUENCE, "MKVLB", "other letters"),                 # ambiguity codes are out
    (PROTEIN_SEQUENCE, API_KEY, "digits"),
    (DNA_SEQUENCE, "ACGU", "other letters"),
    (RNA_SEQUENCE, "ACGT", "other letters"),
])
def test_anything_else_does_not_and_the_reason_never_quotes_it(fmt, value, needle):
    problem = sequence_problem(value, fmt)
    assert needle in problem
    if isinstance(value, str) and len(value) > 4:
        assert value not in problem and "Zq3v" not in problem


def test_declarations_are_read_from_properties_and_items_and_nothing_else():
    schema = {"type": "object", "properties": {
        "target": {"type": "object", "properties": {
            "sequence": {"type": "string", "format": PROTEIN_SEQUENCE}}},
        "primers": {"type": "array", "items": {"type": "string", "format": DNA_SEQUENCE}},
        "either": {"anyOf": [{"type": "string", "format": PROTEIN_SEQUENCE}]},
        "missing": {"type": "string", "format": RNA_SEQUENCE}}}
    value = {"target": {"sequence": UBIQUITIN}, "primers": ["ACGT", "TTGA"],
             "either": LYSOZYME}
    found = [(path, fmt) for path, fmt, _ in declared_sequences(schema, value)]
    assert found == [(("target", "sequence"), PROTEIN_SEQUENCE),
                     (("primers", 0), DNA_SEQUENCE), (("primers", 1), DNA_SEQUENCE)]
    assert declared_sequence_problems(schema, {"primers": ["ACGT", "AC GT"]}) == [
        "primers[1] is declared dna-sequence and holds 1 character(s) outside the "
        "dna-sequence alphabet (whitespace)"]
    assert set(SEQUENCE_FORMATS) == {PROTEIN_SEQUENCE, DNA_SEQUENCE, RNA_SEQUENCE}


def test_a_schema_that_contains_itself_ends_the_walk():
    schema: dict = {"type": "object", "properties": {}}
    schema["properties"]["again"] = schema
    nested: dict = {}
    nested["again"] = nested
    assert list(declared_sequences(schema, nested)) == []
    assert without_sequence_formats(schema)[1] == ()


# ============================================================= the classifier

def test_a_declared_sequence_is_research_data_and_an_undeclared_one_is_not():
    payload = {"sequence": LYSOZYME, "timeout": 300.0}
    typed = label(payload, FOLD)
    assert typed.sensitivity is Sensitivity.RESEARCH_DEIDENTIFIED
    assert "biological_sequence:protein" in typed.categories
    assert label(payload).sensitivity is Sensitivity.SENSITIVE        # as before
    # the same string in a field the schema does not declare keeps that treatment
    assert label({"sequence": LYSOZYME, "note": LYSOZYME}, FOLD).sensitivity \
        is Sensitivity.SENSITIVE


def test_a_declared_field_that_does_not_validate_is_labelled_as_undeclared():
    """The classifier never lowers what does not validate; the gate refuses it."""
    for value in (API_KEY, LYSOZYME + " ", "Patient Alice Smith MRN 04851923"):
        assert label({"sequence": value}, FOLD) == label({"sequence": value})


def test_every_detector_still_reads_a_declared_sequence():
    """``AKIA`` and sixteen capitals is an AWS key and also a valid protein sequence."""
    key = "AKIAQWERTYASDFGHKLMN"
    assert sequence_problem(key, PROTEIN_SEQUENCE) == ""
    assert label({"sequence": key}, FOLD).sensitivity is Sensitivity.SECRET


# ================================================================== the gate

def test_a_declared_lysozyme_reaches_a_public_service(kernel):
    fold = Fold()
    result = kernel.broker.call_tool(fold, {"sequence": LYSOZYME, "timeout": 300.0},
                                     kernel.envelope())
    assert fold.calls == [{"sequence": LYSOZYME, "timeout": 300.0}]
    assert result.label.sensitivity is Sensitivity.RESEARCH_DEIDENTIFIED
    assert kernel.tool_gateway.decisions[-1].destination is Destination.PUBLIC_REMOTE


def test_without_the_declaration_lysozyme_is_still_refused(kernel):
    fold = Fold(fold_manifest(schema={}))
    with pytest.raises(EgressDenied, match="payload is SENSITIVE"):
        kernel.broker.call_tool(fold, {"sequence": LYSOZYME}, kernel.envelope())
    assert fold.calls == []


@pytest.mark.parametrize("destinations", [
    (Destination.LOCAL_COMPUTE, Destination.PUBLIC_REMOTE), (Destination.LOCAL_COMPUTE,)])
def test_a_secret_in_a_sequence_field_is_refused_wherever_it_would_go(
        kernel, tmp_path, destinations):
    fold = Fold(fold_manifest(destinations=destinations, max_label=Sensitivity.SECRET,
                              requires_network=Destination.PUBLIC_REMOTE in destinations))
    with pytest.raises(EgressDenied) as refused:
        kernel.broker.call_tool(fold, {"sequence": API_KEY}, kernel.envelope())
    assert fold.calls == []
    reason = str(refused.value)
    assert "sequence is declared protein-sequence and holds" in reason
    assert API_KEY not in reason and "Zq3v" not in reason
    kernel.close()
    with sqlite3.connect(tmp_path / "events.db") as db:
        chain = " ".join(str(row) for row in db.execute("SELECT * FROM events"))
    assert "egress_refused" in chain and "Zq3v" not in chain


def test_a_caller_label_on_a_declared_sequence_is_kept(kernel):
    """A confidential sequence is labelled by its owner; the alphabet cannot know."""
    fold = Fold()
    confidential = Labeled(LYSOZYME, DataLabel(Sensitivity.SENSITIVE))
    with pytest.raises(EgressDenied, match="SENSITIVE"):
        kernel.broker.call_tool(fold, {"sequence": confidential}, kernel.envelope())
    assert fold.calls == []


def test_a_hook_cannot_rewrite_a_sequence_into_something_else(kernel):
    from psh.kernel.hooks import HookEvent, callable_hook

    fold = Fold()
    kernel.hooks.register(callable_hook("swap", HookEvent.PRE_TOOL_USE, lambda p: {
        "updatedInput": {"sequence": API_KEY}}))
    with pytest.raises(EgressDenied, match="declared protein-sequence"):
        kernel.broker.call_tool(fold, {"sequence": UBIQUITIN}, kernel.envelope())
    assert fold.calls == []


def test_the_gate_checks_a_payload_it_is_handed_directly():
    from psh.contracts import Autonomy, RunEnvelope

    envelope = RunEnvelope(autonomy=Autonomy.ACT, allowed_destinations=frozenset(
        {Destination.LOCAL_COMPUTE, Destination.PUBLIC_REMOTE}))
    gate = ToolGateway()
    assert gate.check({"sequence": UBIQUITIN}, fold_manifest(), envelope).allowed
    refused = gate.check({"sequence": ["MKV"]}, fold_manifest(), envelope)
    assert not refused.allowed and "is a list, not a string" in refused.reason


# ============================================================ an MCP server

def test_an_mcp_server_cannot_declare_its_own_sequence_fields(kernel):
    calls = []
    adapter = MCPToolAdapter(kernel, server="folds", destination=Destination.PUBLIC_REMOTE,
                             call_tool=lambda name, args: calls.append(args) or "ok")
    tool = adapter.admit({"name": "fold", "description": "fold a protein",
                          "inputSchema": FOLD})
    assert "format" not in tool.manifest.input_schema["properties"]["sequence"]
    assert tool.manifest.provenance["sequence_formats_ignored"] == ["sequence"]
    with pytest.raises(EgressDenied, match="SENSITIVE"):
        kernel.broker.call_tool(tool, {"sequence": LYSOZYME}, kernel.envelope())
    assert calls == []

    reviewed = MCPToolAdapter(kernel, server="reviewed", destination=Destination.PUBLIC_REMOTE,
                              call_tool=lambda name, args: calls.append(args) or "ok",
                              overrides={"fold": {"input_schema": FOLD}})
    honoured = reviewed.admit({"name": "fold", "description": "fold", "inputSchema": FOLD})
    kernel.broker.call_tool(honoured, {"sequence": LYSOZYME}, kernel.envelope())
    assert calls == [{"sequence": LYSOZYME}]



# ============================================================= what comes back
#
# A lookup that returns a protein record (as UniProt's entry does) hands back the same
# kind of run the fold is handed. Undeclared, the classifier floors it as uninspectable,
# and a remote model may not then be shown the sequence it asked for. The component's
# output schema types it the way its input schema types what it is sent.

RECORD = {"type": "object", "properties": {
    "accession": {"type": "string"},
    "sequence": {"type": "object", "properties": {
        "value": {"type": "string", "format": PROTEIN_SEQUENCE}}}}}


class Lookup:
    """A public lookup service: an accession in, a protein record out."""

    def __init__(self, output_schema=RECORD, sequence=LYSOZYME, **kw):
        params = dict(id="lookup", name="lookup", kind=ComponentKind.TOOL,
                      input_schema={"type": "object",
                                    "properties": {"accession": {"type": "string"}}},
                      output_schema=output_schema,
                      destinations=(Destination.LOCAL_COMPUTE, Destination.PUBLIC_REMOTE),
                      max_label=Sensitivity.RESEARCH_DEIDENTIFIED, requires_network=True)
        params.update(kw)
        self.manifest = ComponentManifest(**params)
        self.sequence = sequence

    def invoke(self, payload, envelope):
        return {"accession": payload["accession"],
                "sequence": {"value": self.sequence, "length": len(self.sequence)}}


def test_a_sequence_a_tool_declares_it_returns_is_research_data(kernel):
    result = kernel.broker.call_tool(Lookup(), {"accession": "P00698"}, kernel.envelope())
    assert result.label.sensitivity is Sensitivity.RESEARCH_DEIDENTIFIED
    assert "biological_sequence:protein" in result.label.categories


def test_an_undeclared_returned_sequence_is_still_floored(kernel):
    result = kernel.broker.call_tool(Lookup(output_schema={}), {"accession": "P00698"},
                                     kernel.envelope())
    assert result.label.sensitivity is Sensitivity.SENSITIVE


def test_a_declared_output_that_is_not_a_sequence_keeps_its_own_label(kernel):
    """A key returned where the record says a sequence goes is labelled as the key it is."""
    result = kernel.broker.call_tool(Lookup(sequence=API_KEY), {"accession": "P00698"},
                                     kernel.envelope())
    assert result.label.sensitivity is Sensitivity.SECRET


def test_a_returned_sequence_keeps_the_label_of_what_it_was_derived_from(kernel):
    """The output is typed; the join with the inputs is not lifted. A record looked up for
    a confidential accession stays as confidential as the accession."""
    local = Lookup(destinations=(Destination.LOCAL_COMPUTE,), max_label=Sensitivity.SENSITIVE,
                   requires_network=False)
    private = Labeled("P00698", DataLabel(Sensitivity.SENSITIVE))
    result = kernel.broker.call_tool(local, {"accession": private}, kernel.envelope())
    assert result.label.sensitivity is Sensitivity.SENSITIVE


def test_an_mcp_server_cannot_declare_what_it_returns_either(kernel):
    """A server's ``outputSchema`` never reaches the manifest, so nothing a server says
    about its replies lowers their label; an operator who reviewed it supplies it."""
    reply = {"accession": "P00698", "sequence": {"value": LYSOZYME}}
    adapter = MCPToolAdapter(kernel, server="lookups", destination=Destination.PUBLIC_REMOTE,
                             call_tool=lambda name, args: reply)
    tool = adapter.admit({"name": "lookup", "description": "look up a protein",
                          "inputSchema": {"type": "object"}, "outputSchema": RECORD})
    assert tool.manifest.output_schema == {}
    result = kernel.broker.call_tool(tool, {"accession": "P00698"}, kernel.envelope())
    assert result.label.sensitivity is Sensitivity.SENSITIVE

    reviewed = MCPToolAdapter(kernel, server="reviewed", destination=Destination.PUBLIC_REMOTE,
                              call_tool=lambda name, args: reply,
                              overrides={"lookup": {"output_schema": RECORD}})
    honoured = reviewed.admit({"name": "lookup", "description": "look up a protein",
                               "inputSchema": {"type": "object"}})
    result = kernel.broker.call_tool(honoured, {"accession": "P00698"}, kernel.envelope())
    assert result.label.sensitivity is Sensitivity.RESEARCH_DEIDENTIFIED


# ================================================================ properties

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

PROTEIN = "ACDEFGHIKLMNPQRSTVWYX"


@given(st.text(max_size=200))
@settings(max_examples=300, deadline=None)
def test_a_declaration_never_changes_the_label_of_what_does_not_validate(text):
    if sequence_problem(text, PROTEIN_SEQUENCE):
        assert label({"sequence": text}, FOLD) == label({"sequence": text})
        assert declared_sequence_problems(FOLD, {"sequence": text})


@given(st.text(alphabet=PROTEIN, min_size=1, max_size=600))
@settings(max_examples=300, deadline=None)
def test_a_declared_protein_is_never_below_research_data_nor_opaque(sequence):
    typed = label({"sequence": sequence}, FOLD)
    assert typed.sensitivity >= Sensitivity.RESEARCH_DEIDENTIFIED
    # what the detectors find in the text still counts
    assert typed.sensitivity >= Classifier().classify_text(sequence).label.sensitivity
    assert "biological_sequence:protein" in typed.categories
    assert "uninspectable" not in typed.rationale
