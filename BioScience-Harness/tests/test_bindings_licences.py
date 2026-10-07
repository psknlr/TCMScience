"""Implementation bindings, licence records, and the gate a commercial purpose passes.

Three defects, each of which let a fact about a catalogue *row* stand in for a fact about
the code that would run:

* the catalogue's source path for a Biomni tool is its description file, and the python
  entrypoint derived from it named a module with no function in it;
* the catalogue's licence for a row listed by an aggregator is the aggregator's licence —
  GSEApy carried Biomni's Apache-2.0 and is BSD-3-Clause;
* PSH's code matrix allows ``NONE + federated`` (invoking is not redistributing), and that
  was the only answer to whether a commercial run could use unlicensed upstream code.

The fixture tree below has Biomni's layout, and both of its modules raise when imported,
so every test that verifies against it also shows nothing was imported.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from bioagent.licences import (Asset, Checked, CodeLicence, DataLicence, LicenceRecordError,
                               LicenceRecords, ModelLicence, ServiceTerms, assets_for,
                               usage_decision)
from bioagent.policy import Authorization, PolicyDecision, Ruling
from bioagent.providers.biomni import BiomniProvider
from bioagent.providers.bindings import (BindingError, ImplementationBinding, LicenceClaim,
                                         Param, load_bindings, parse_bindings,
                                         render_signature, tree_commit)
from bioagent.providers.catalogue import CatalogueProvider
from bioagent.runtime.component import (ComponentManifest, LicenseSpec, Provider,
                                        RuntimeSpec)
from bioagent.runtime.registry import ComponentRegistry, Resolver
from bioagent.sources.cards import PURPOSES, SOURCE_CARDS, effective_sources
from bioagent.status import LifecycleState

REPO = Path(__file__).resolve().parents[1]
FIXTURE_COMMIT = "0123456789abcdef0123456789abcdef01234567"

IMPLEMENTATION = '''\
"""Fixture: a Biomni tool module. Importing it raises, so a verifier that imports fails."""
import functools
import os

import scanpy as sc
from biomni.llm import get_llm

raise RuntimeError("biomni.tool.genomics was imported; verification must stay static")


def annotate_celltype_scRNA(adata_filename, data_dir, data_info, data_lake_path,
                            cluster="leiden", llm="model", composition=None):
    import langchain_core
    return "steps"


def create_harmony_embeddings_scRNA(adata_filename, batch_key, data_dir):
    from harmony import harmonize
    return "steps"


if os.environ.get("NEVER_SET"):
    def only_sometimes(x):
        return x


class Holder:
    def inside_a_class(self, x):
        return x


def rebound_later(x):
    return x


rebound_later = None


@functools.lru_cache
def decorated_tool(x):
    return x
'''

DESCRIPTION = '''\
description = [
    {"name": "annotate_celltype_scRNA", "description": "Annotate cell types by markers.",
     "required_parameters": [{"name": "adata_filename"}, {"name": "data_dir"},
                             {"name": "data_info"}, {"name": "data_lake_path"}],
     "optional_parameters": [{"name": "cluster"}, {"name": "llm"},
                             {"name": "composition"}]},
    {"name": "create_harmony_embeddings_scRNA", "description": "Harmony batch integration.",
     "required_parameters": [{"name": "adata_filename"}, {"name": "batch_key"},
                             {"name": "data_dir"}],
     "optional_parameters": []},
]
raise RuntimeError("the description module was imported; it is only ever parsed")
'''

SIGNATURES = {
    "annotate_celltype_scRNA": (
        *(Param(n) for n in ("adata_filename", "data_dir", "data_info", "data_lake_path")),
        *(Param(n, default=True) for n in ("cluster", "llm", "composition"))),
    "create_harmony_embeddings_scRNA": tuple(Param(n) for n in ("adata_filename",
                                                                "batch_key", "data_dir")),
}


def make_tree(root: Path, *, commit: str = FIXTURE_COMMIT,
              implementation: str = IMPLEMENTATION, description: str = DESCRIPTION,
              git: bool = True) -> Path:
    """A tree with Biomni's layout: ``tool/<area>.py`` beside ``tool/tool_description/``."""
    for rel, text in {"biomni/__init__.py": "", "biomni/version.py": '__version__ = "0.0.8"\n',
                      "biomni/tool/__init__.py": "",
                      "biomni/tool/genomics.py": implementation,
                      "biomni/tool/tool_description/genomics.py": description}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if git:
        (root / ".git").mkdir()
        (root / ".git" / "HEAD").write_text(commit + "\n", encoding="utf-8")
    return root


def bind(function: str = "annotate_celltype_scRNA", **override) -> ImplementationBinding:
    fields = dict(component=f"biomni.tool.{function.lower()}", project="Biomni",
                  entrypoint=f"biomni.tool.genomics:{function}",
                  repo="https://github.com/snap-stanford/Biomni", commit=FIXTURE_COMMIT,
                  implementation_path="biomni/tool/genomics.py",
                  description_path="biomni/tool/tool_description/genomics.py",
                  signature=SIGNATURES.get(function, (Param("x"),)),
                  licence=LicenceClaim("Apache-2.0", "fixture LICENSE"),
                  checked_on="2026-10-07")
    fields.update(override)
    return ImplementationBinding(**fields)


def no_biomni_imported() -> bool:
    return not any(k == "biomni" or k.startswith("biomni.") for k in sys.modules)


def catalogue_rows() -> list[dict[str, str]]:
    from bioagent.psh.assembly import load_catalogue_rows
    return load_catalogue_rows()


@pytest.fixture(scope="module")
def catalogue() -> dict[str, ComponentManifest]:
    return {m.id: m for m in CatalogueProvider(catalogue_rows()).discover()}


# ================================================================ the bindings file

def test_the_shipped_bindings_name_implementations_not_descriptions(catalogue):
    shipped = load_bindings()
    assert 3 <= len(shipped) <= 5
    for cid, b in shipped.items():
        assert cid in catalogue, f"{cid} is not a catalogue component"
        assert catalogue[cid].runtime.backend == "python"
        assert "tool_description" not in b.implementation_path
        assert "tool_description" in b.description_path
        assert b.module == "biomni.tool.genomics"
        assert b.licence.spdx == "Apache-2.0" and b.commit.startswith("400c1f36")


def test_a_binding_must_pin_a_commit_and_name_the_module_its_path_defines():
    with pytest.raises(BindingError, match="40-character SHA"):
        bind(commit="main")
    with pytest.raises(BindingError, match="is not the module"):
        bind(entrypoint="biomni.tool.tool_description.genomics:annotate_celltype_scRNA")
    with pytest.raises(BindingError, match="same file"):
        bind(description_path="biomni/tool/genomics.py")
    with pytest.raises(BindingError, match="inside the tree"):
        bind(implementation_path="../elsewhere/genomics.py")
    with pytest.raises(BindingError, match="kind"):
        Param("x", kind="sometimes")


def test_the_bindings_file_refuses_what_it_does_not_know():
    good = ("api_version: '1'\nbindings:\n"
            "  - component: biomni.tool.x\n    project: Biomni\n"
            "    entrypoint: biomni.tool.genomics:x\n"
            "    repo: https://github.com/snap-stanford/Biomni\n"
            f"    commit: {FIXTURE_COMMIT}\n"
            "    implementation_path: biomni/tool/genomics.py\n"
            "    description_path: ''\n    signature: [{name: x}]\n"
            "    licence: {spdx: Apache-2.0, source: LICENSE}\n    checked_on: '2026-10-07'\n")
    assert list(parse_bindings(good)) == ["biomni.tool.x"]
    with pytest.raises(BindingError, match="unknown key"):
        parse_bindings(good + "    reviewer_mood: fine\n")
    with pytest.raises(BindingError, match="bound twice"):
        parse_bindings(good + good.split("bindings:\n", 1)[1])
    with pytest.raises(BindingError, match="api_version"):
        parse_bindings(good.replace("api_version: '1'", "api_version: '2'"))


def test_a_signature_renders_as_python_spells_it():
    params = (Param("a", "positional_only"), Param("b", default=True),
              Param("args", "var_positional"), Param("c", "keyword_only"),
              Param("kw", "var_keyword"))
    assert render_signature(params) == "a, /, b=…, *args, c, **kw"
    assert render_signature((Param("a"), Param("k", "keyword_only", True))) == "a, *, k=…"


# ===================================================== static verification (Biomni)

def test_a_bound_function_is_verified_without_importing_biomni(tmp_path):
    root = make_tree(tmp_path)
    provider = BiomniProvider(root, bindings=[bind(), bind("create_harmony_embeddings_scRNA")])
    manifests = {m.id: m for m in provider.discover()}

    assert provider.refusals == ()
    annotate = manifests["biomni.tool.annotate_celltype_scrna"]
    assert annotate.runtime.entrypoint == "biomni.tool.genomics:annotate_celltype_scRNA"
    assert annotate.runtime.entrypoint_basis == "verified"
    assert annotate.provider.source_path == "biomni/tool/genomics.py"
    assert annotate.provider.description_path == "biomni/tool/tool_description/genomics.py"
    assert annotate.provider.commit == FIXTURE_COMMIT and annotate.version == "0.0.8"
    assert annotate.description == "Annotate cell types by markers."
    assert annotate.license.spdx == "Apache-2.0"
    assert annotate.license.record == "binding:biomni.tool.annotate_celltype_scrna"
    # its module's imports and its own, not every function's in the file
    assert annotate.requires.python == ("biomni", "langchain_core", "scanpy")
    harmony = manifests["biomni.tool.create_harmony_embeddings_scrna"]
    assert harmony.requires.python == ("biomni", "harmony", "scanpy")
    assert annotate.validate() == []
    assert no_biomni_imported()


@pytest.mark.parametrize("override,reason", [
    (dict(entrypoint="biomni.tool.tool_description.genomics:annotate_celltype_scRNA",
          implementation_path="biomni/tool/tool_description/genomics.py",
          description_path=""), "is a tool description"),
    (dict(component="biomni.tool.missing_tool", entrypoint="biomni.tool.genomics:missing_tool",
          signature=(Param("x"),)), "not defined at module level"),
    (dict(component="biomni.tool.only_sometimes",
          entrypoint="biomni.tool.genomics:only_sometimes", signature=(Param("x"),)),
     "inside another block"),
    (dict(component="biomni.tool.inside_a_class",
          entrypoint="biomni.tool.genomics:inside_a_class", signature=(Param("x"),)),
     "inside another block"),
    (dict(component="biomni.tool.rebound_later",
          entrypoint="biomni.tool.genomics:rebound_later", signature=(Param("x"),),
          description_path=""), "is rebound at line"),
    (dict(component="biomni.tool.decorated_tool",
          entrypoint="biomni.tool.genomics:decorated_tool", signature=(Param("x"),),
          description_path=""), "is decorated"),
    (dict(signature=SIGNATURES["annotate_celltype_scRNA"][:-1]), "the binding expects"),
    (dict(signature=(*SIGNATURES["annotate_celltype_scRNA"][:4],
                     Param("cluster"), *SIGNATURES["annotate_celltype_scRNA"][5:])),
     "the binding expects"),
    (dict(commit="f" * 40), "the tree is at"),
    (dict(description_path="biomni/tool/descriptions.py"), "not under"),
    (dict(component="clawbio.tool.annotate"), "not a Biomni component id"),
])
def test_a_binding_that_does_not_hold_is_refused_with_its_reason(tmp_path, override, reason):
    root = make_tree(tmp_path)
    provider = BiomniProvider(root, bindings=[bind(**override)])
    assert list(provider.discover()) == []
    (refusal,) = provider.refusals
    assert reason in refusal.reason, refusal.reason
    assert no_biomni_imported()


def test_a_description_that_disagrees_with_the_implementation_is_refused(tmp_path):
    """A caller that learnt the tool from its description would pass what it lists."""
    root = make_tree(tmp_path, description=DESCRIPTION.replace(
        '{"name": "data_dir"}],\n     "optional_parameters": []',
        '{"name": "data_dir"}, {"name": "n_pcs"}],\n     "optional_parameters": []'))
    provider = BiomniProvider(root, bindings=[bind("create_harmony_embeddings_scRNA")])
    assert list(provider.discover()) == []
    assert "n_pcs" in provider.refusals[0].reason

    silent = make_tree(tmp_path / "silent", description="description = []\n")
    provider = BiomniProvider(silent, bindings=[bind()])
    assert list(provider.discover()) == []
    assert "does not describe annotate_celltype_scRNA" in provider.refusals[0].reason


def test_a_tree_whose_commit_cannot_be_established_admits_nothing(tmp_path):
    bare = make_tree(tmp_path / "bare", git=False)
    provider = BiomniProvider(bare, bindings=[bind()])
    assert list(provider.discover()) == []
    assert "no git metadata" in provider.refusals[0].reason

    declared = BiomniProvider(bare, bindings=[bind()], commit=FIXTURE_COMMIT).verify()
    assert declared.as_dict() == {"commit": FIXTURE_COMMIT, "commit_source": "declared",
                                  "verified": ["biomni.tool.annotate_celltype_scrna"],
                                  "refused": {}}

    checkout = make_tree(tmp_path / "checkout")
    conflict = BiomniProvider(checkout, bindings=[bind()], commit="e" * 40).verify()
    assert conflict.manifests == () and "one of them is wrong" in conflict.refusals[0].reason


def test_the_commit_is_read_through_symbolic_and_packed_refs(tmp_path):
    root = make_tree(tmp_path, git=False)
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (root / ".git" / "packed-refs").write_text(
        f"# pack-refs with: peeled\n{FIXTURE_COMMIT} refs/heads/main\n", encoding="utf-8")
    assert tree_commit(root) == FIXTURE_COMMIT
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").write_text(f"gitdir: {root / '.git'}\n", encoding="utf-8")
    assert tree_commit(linked) == FIXTURE_COMMIT
    assert tree_commit(tmp_path / "nowhere") == ""


# ============================================================ the catalogue provider

def test_a_bound_catalogue_row_runs_the_implementation(catalogue):
    m = catalogue["biomni.tool.annotate_celltype_scrna"]
    assert m.runtime.entrypoint == "biomni.tool.genomics:annotate_celltype_scRNA"
    assert m.runtime.entrypoint_basis == "bound"
    assert m.provider.source_path == "Biomni/biomni/tool/genomics.py"
    assert m.provider.description_path == "Biomni/biomni/tool/tool_description/genomics.py"
    assert m.provider.commit.startswith("400c1f36")
    assert m.license.spdx == "Apache-2.0" and m.license.catalogue == "Apache-2.0"
    assert m.license.record == "binding:biomni.tool.annotate_celltype_scrna"


def test_an_unbound_catalogue_entrypoint_stays_discoverable_and_does_not_run(catalogue):
    """Not deleted, not quarantined: indexed, searchable, UNAVAILABLE with the reason."""
    m = catalogue["biomni.tool.get_rna_seq_archs4"]
    assert m.runtime.entrypoint == "biomni.tool.tool_description.genomics:get_rna_seq_archs4"
    assert m.runtime.entrypoint_basis == "derived"

    registry = ComponentRegistry(catalogue.values())
    assert m.id in {x.id for x in registry.search("archs4 rna seq", limit=50)}
    resolver = Resolver(registry, backend_probe=lambda backend: (True, ""))
    resolution = resolver.resolve(m.id)
    assert resolution.state is LifecycleState.UNAVAILABLE
    assert "registry/implementation_bindings.yaml" in resolution.reason
    ok, why = resolver.resolve_manifest(m)
    assert not ok and "derived from the catalogue path" in why
    bound = resolver.resolve("biomni.tool.annotate_celltype_scrna")
    assert "implementation_bindings" not in bound.reason


def test_without_bindings_every_python_row_is_a_derived_candidate():
    manifests = [m for m in CatalogueProvider(catalogue_rows(), bindings={}).discover()
                 if m.runtime.backend == "python"]
    assert len(manifests) > 500
    assert {m.runtime.entrypoint_basis for m in manifests} == {"derived"}


def test_the_catalogue_licence_is_kept_as_provenance_and_the_record_governs(catalogue):
    gseapy = catalogue["biomni.software.gseapy"]
    assert gseapy.license.spdx == "BSD-3-Clause"
    assert gseapy.license.catalogue == "Apache-2.0"
    assert gseapy.license.record == "code.gseapy"
    igraph = catalogue["biomni.software.igraph"]
    assert igraph.license.spdx == "GPL-2.0-or-later"
    assert igraph.license.catalogue == "Apache-2.0"
    # a row nobody has reviewed keeps the catalogue's value, and says it is unreviewed
    r_harmony = catalogue["biomni.software.harmony"]
    assert r_harmony.license.spdx == "Apache-2.0" and r_harmony.license.record == ""

    bare = {m.id: m for m in CatalogueProvider(catalogue_rows(),
                                               licences=LicenceRecords()).discover()}
    assert bare["biomni.software.gseapy"].license.spdx == "Apache-2.0"
    assert bare["biomni.software.gseapy"].license.record == ""


def test_the_new_provenance_fields_round_trip_and_default_for_old_manifests(catalogue):
    m = catalogue["biomni.tool.annotate_celltype_scrna"]
    for again in (ComponentManifest.from_dict(m.to_dict()),
                  ComponentManifest.from_yaml(m.to_yaml())):
        assert again.provider.description_path == m.provider.description_path
        assert again.runtime.entrypoint_basis == "bound"
        assert (again.license.catalogue, again.license.record) == (
            m.license.catalogue, m.license.record)
    old = {k: v for k, v in m.to_dict().items()}
    old["provider"] = {k: v for k, v in old["provider"].items() if k != "description_path"}
    old["runtime"] = {k: v for k, v in old["runtime"].items() if k != "entrypoint_basis"}
    old["license"] = {k: v for k, v in old["license"].items()
                      if k not in ("catalogue", "record")}
    legacy = ComponentManifest.from_dict(old)
    assert legacy.provider.description_path == "" and legacy.runtime.entrypoint_basis == ""
    assert legacy.license.record == ""


def test_an_unknown_entrypoint_basis_quarantines_the_manifest():
    m = ComponentManifest(id="x.tool.y", kind="tool",
                          runtime=RuntimeSpec(backend="python", entrypoint="json:dumps",
                                              entrypoint_basis="trust-me"))
    assert any("entrypoint_basis" in e for e in m.validate())
    registry = ComponentRegistry([m])
    assert registry.get("x.tool.y").state is LifecycleState.QUARANTINED


def test_the_census_counts_what_each_entrypoint_rests_on():
    sys.path.insert(0, str(REPO / "scripts"))
    from entrypoint_census import census

    data = census(check_imports=False)
    assert data["bound"] == len(load_bindings())
    assert data["derived"] == data["python_components"] - data["bound"]
    assert data["derived_from_description"] > 200
    assert data["inconsistent"] == 0


# =================================================================== licence records

def test_the_shipped_records_are_complete_and_cover_real_catalogue_rows(catalogue):
    records = LicenceRecords.load()
    code = [r for r in records.records if r.kind == "code"]
    assert {r.id for r in code} >= {"code.bioagent", "code.biomni", "code.gseapy"}
    for r in code:
        assert len(r.licence_sha256) == 64
        assert r.first_party or (len(r.commit) == 40 and r.repo.startswith("https://"))
        for cid in r.components:
            row = catalogue[cid]
            # every corrected row is one an aggregator listed: Biomni's env_desc.py,
            # carrying Biomni's own licence
            assert row.provider.project == "Biomni"
            assert row.provider.source_path == "Biomni/biomni/env_desc.py"
            assert row.license.catalogue == "Apache-2.0"
            assert row.license.spdx == r.spdx and row.license.record == r.id
    for b in load_bindings().values():
        assert records.record_for(Asset("code", b.component, "implementation",
                                        module=b.module.split(".")[0])).spdx == b.licence.spdx
    assert records.get("code.gseapy").licence_url == (
        "https://github.com/zqfang/GSEApy/blob/"
        "08cb2b689def365f7dee1f5da50c49d3a149cf5a/LICENSE")


def test_the_first_party_record_matches_the_licence_file_on_disk():
    record = LicenceRecords.load().get("code.bioagent")
    text = (REPO / record.licence_path).read_bytes()
    assert hashlib.sha256(text).hexdigest() == record.licence_sha256
    assert text.startswith(b"MIT License")


def _code(**override) -> CodeLicence:
    fields = dict(id="code.lab-tool", name="a lab tool", spdx="MIT", licence_path="LICENSE",
                  licence_sha256="a" * 64, integration_mode="federated",
                  checked=Checked("fixture", "2026-10-07"), repo="https://example.org/lab",
                  commit="ab" * 20, components=("lab.tool.licensed",))
    fields.update(override)
    return CodeLicence(**fields)


def test_a_record_without_the_facts_is_refused():
    with pytest.raises(LicenceRecordError, match="full"):
        _code(commit="main")
    with pytest.raises(LicenceRecordError, match="sha256"):
        _code(licence_sha256="")
    with pytest.raises(LicenceRecordError, match="only honest SPDX value is NONE"):
        _code(licence_path="", licence_sha256="")
    with pytest.raises(LicenceRecordError, match="code.<name>"):
        _code(id="lab-tool")
    with pytest.raises(LicenceRecordError, match="never a credential"):
        ServiceTerms(id="service.api", name="api", terms_url="https://example.org/terms",
                     terms_version="2026-01", account="sk-live-123",
                     permitted_use=("academic",), rate_limit="1/s",
                     checked=Checked("fixture", "2026-10-07"))
    with pytest.raises(LicenceRecordError, match="output_terms"):
        ModelLicence(id="model.m", name="m", weights_version="1", weights_sha256="b" * 64,
                     licence="custom", terms_url="https://example.org/m",
                     permitted_use=("commercial",), output_terms="whatever",
                     checked=Checked("fixture", "2026-10-07"))


def test_the_records_file_refuses_what_it_does_not_know():
    head = "api_version: '1'\ncode:\n  - id: code.x\n    name: x\n    spdx: MIT\n"
    tail = ("    licence_path: LICENSE\n    licence_sha256: " + "c" * 64 + "\n"
            "    integration_mode: vendor\n    repo: https://example.org/x\n"
            "    commit: " + "d" * 40 + "\n    modules: [x]\n")
    good = head + tail + "    checked: {source: fixture, date: '2026-10-07'}\n"
    assert LicenceRecords.parse(good).get("code.x").spdx == "MIT"
    with pytest.raises(LicenceRecordError, match="source and date"):
        # PyYAML reads a bare ``on`` key as True, which is why the key is ``date``
        LicenceRecords.parse(head + tail + "    checked: {source: x, on: '2026-10-07'}\n")
    with pytest.raises(LicenceRecordError, match="unknown key"):
        LicenceRecords.parse(good + "    trust: high\n")
    with pytest.raises(LicenceRecordError, match="first_party is true or false"):
        LicenceRecords.parse(good + "    first_party: 'false'\n")
    with pytest.raises(LicenceRecordError, match="module 'x' has two records"):
        LicenceRecords.parse(good + good.split("code:\n", 1)[1].replace("code.x", "code.y"))


# ============================================================================ the gate

def test_an_asset_without_a_record_is_refused_for_commercial_use_only():
    asset = Asset("code", "lab.tool.unreviewed", "implementation", declared="Apache-2.0",
                  mode="vendor")
    commercial = usage_decision([asset], purpose="commercial", records=LicenceRecords())
    assert not commercial.allowed
    assert "no reviewed licence record" in commercial.reason
    assert "'Apache-2.0', which is a statement, not a reviewed grant" in commercial.reason
    academic = usage_decision([asset], purpose="academic", records=LicenceRecords())
    assert academic.allowed and academic.entries[0].ruling.rule == "usage.academic"
    with pytest.raises(ValueError, match="not one of"):
        usage_decision([asset], purpose="internal", records=LicenceRecords())


def test_none_plus_federated_is_not_permission_for_a_commercial_run():
    """The code matrix passes it; the gate does not, and says which rule decided."""
    from psh.licensing import license_ruling

    assert license_ruling("NONE", "federated").allowed
    records = LicenceRecords([_code(spdx="NONE", licence_path="", licence_sha256="",
                                    components=("lab.tool.unlicensed",))])
    asset = Asset("code", "lab.tool.unlicensed", "implementation", mode="federated")
    decision = usage_decision([asset], purpose="commercial", records=records)
    assert not decision.allowed
    assert decision.entries[0].ruling.rule == "usage.code.license.none.federated.commercial"
    assert usage_decision([asset], purpose="academic", records=records).allowed


def test_a_permissive_record_permits_and_an_unclassified_one_fails_closed():
    records = LicenceRecords([_code(),
                              _code(id="code.gpl", spdx="GPL-2.0-or-later",
                                    components=("lab.tool.gpl",))])
    allowed = usage_decision([Asset("code", "lab.tool.licensed", "implementation",
                                    mode="federated")], purpose="commercial", records=records)
    assert allowed.allowed and allowed.entries[0].record == "code.lab-tool"
    refused = usage_decision([Asset("code", "lab.tool.gpl", "implementation",
                                    mode="federated")], purpose="commercial", records=records)
    assert not refused.allowed, "an SPDX id the table does not know is not a grant"


@pytest.mark.parametrize("purpose", PURPOSES)
def test_a_source_card_is_ruled_by_effective_sources_itself(purpose):
    for card in SOURCE_CARDS:
        decision = usage_decision([Asset("data", f"source:{card.key}", "source")],
                                  purpose=purpose, records=LicenceRecords())
        granted, refused = effective_sources([card.key], purpose=purpose)
        assert decision.allowed == bool(granted), card.key
        if refused:
            assert refused[card.key] in decision.reason
    npass = usage_decision([Asset("data", "source:npass@2.0", "source")],
                           purpose="commercial", records=LicenceRecords())
    assert not npass.allowed and "Free for academic use" in npass.reason


def test_data_model_and_service_records_name_what_they_permit():
    checked = Checked("fixture", "2026-10-07")
    data = DataLicence(id="data.atlas", name="an atlas", version="2026.1", licence="CC-BY-4.0",
                       commercial_use="allowed", retention="cache 30 days",
                       redistribution="with attribution", checked=checked,
                       components=("lab.dataset.atlas",))
    closed = DataLicence(id="data.closed", name="a closed set", version="1",
                         licence="Free for academic use", commercial_use="unknown",
                         retention="unknown", redistribution="unknown", checked=checked,
                         components=("lab.dataset.closed",))
    weights = ModelLicence(id="model.folder", name="a folding model", weights_version="v1",
                           weights_sha256="e" * 64, licence="MIT",
                           terms_url="https://example.org/model", permitted_use=PURPOSES,
                           output_terms="unrestricted", checked=checked,
                           components=("lab.tool.fold",))
    research_weights = ModelLicence(id="model.research", name="a research model",
                                    weights_version="v2", weights_sha256="f" * 64,
                                    licence="custom research licence",
                                    terms_url="https://example.org/research",
                                    permitted_use=("academic",), output_terms="non_commercial",
                                    checked=checked, components=("lab.tool.research",))
    api = ServiceTerms(id="service.api", name="an API", terms_url="https://example.org/terms",
                       terms_version="2026-01", account="env:LAB_API_KEY",
                       permitted_use=PURPOSES, rate_limit="3 req/s", checked=checked,
                       components=("lab.connector.api",))
    records = LicenceRecords(models=[weights, research_weights], data=[data, closed],
                             services=[api])

    def verdict(kind: str, ref: str) -> bool:
        return usage_decision([Asset(kind, ref, "implementation")], purpose="commercial",
                              records=records).allowed

    assert verdict("data", "lab.dataset.atlas")
    assert not verdict("data", "lab.dataset.closed")
    assert verdict("model", "lab.tool.fold")
    assert not verdict("model", "lab.tool.research")
    assert verdict("service", "lab.connector.api")
    entry = usage_decision([Asset("service", "lab.connector.api", "implementation")],
                           purpose="commercial", records=records).as_dict()["assets"][0]
    assert entry["terms"]["account"] == "env:LAB_API_KEY"
    assert entry["terms"]["rate_limit"] == "3 req/s"


def test_assets_cover_what_runs_what_it_imports_and_what_a_record_attaches(tmp_path):
    root = make_tree(tmp_path)
    (verified,) = BiomniProvider(root, bindings=[bind()]).discover()
    weights = ModelLicence(id="model.annotator", name="annotator weights",
                           weights_version="v1", weights_sha256="0" * 64, licence="MIT",
                           terms_url="https://example.org/w", permitted_use=PURPOSES,
                           output_terms="unrestricted",
                           checked=Checked("fixture", "2026-10-07"),
                           components=(verified.id,))
    shipped = LicenceRecords.load()
    records = LicenceRecords([r for r in shipped.records if r.kind == "code"],
                             models=[weights])
    assets = assets_for(verified, records=records)
    assert [(a.kind, a.ref, a.role) for a in assets] == [
        ("code", verified.id, "implementation"), ("code", "langchain_core", "requirement"),
        ("code", "scanpy", "requirement"), ("model", verified.id, "attached")]
    decision = usage_decision(assets, purpose="commercial", records=records)
    entry = {(e.asset.kind, e.asset.ref): e for e in decision.entries}
    assert entry["code", verified.id].record == "code.biomni"
    assert entry["code", verified.id].ruling.allowed
    assert entry["model", verified.id].record == "model.annotator"
    assert entry["code", "scanpy"].record == "code.scanpy"
    assert entry["code", "scanpy"].ruling.allowed
    assert not entry["code", "langchain_core"].ruling.allowed
    assert not decision.allowed and "langchain_core" in decision.reason
    assert decision.as_dict()["verdict"] == "deny"


def test_the_decision_folds_into_the_authorization_without_losing_the_kernels():
    kernel = Authorization("lab.tool.licensed", True,
                           (Ruling(PolicyDecision.ALLOW, "MIT permits federated use",
                                   "license.permissive.federated"),))
    refused = usage_decision([Asset("code", "lab.tool.unreviewed", "implementation")],
                             purpose="commercial", records=LicenceRecords())
    folded = refused.authorization(kernel)
    assert not folded.allowed and folded.rulings[0] == kernel.rulings[0]
    assert folded.denied_rules == ("usage.code.unknown",)


def test_without_psh_a_commercial_use_of_code_is_refused(monkeypatch):
    monkeypatch.setitem(sys.modules, "psh.licensing", None)
    decision = usage_decision([Asset("code", "lab.tool.licensed", "implementation")],
                              purpose="commercial", records=LicenceRecords([_code()]))
    assert not decision.allowed
    assert decision.entries[0].ruling.rule == "usage.code.no_psh"


# ===================================================================== the runtime

def _runtime(*manifests: ComponentManifest, records: LicenceRecords | None = None):
    from bioagent.backends.base import BackendRegistry
    from bioagent.backends.concrete import PythonBackend
    from bioagent.runtime.agentspec import Runtime
    from bioagent.runtime.registry import Loader

    registry = ComponentRegistry(manifests)
    resolver = Resolver(registry)
    loader = Loader(registry, resolver)
    return Runtime(registry, BackendRegistry([PythonBackend(loader)]), resolver=resolver,
                   loader=loader, licences=records)


def _tool(cid: str, spdx: str, mode: str) -> ComponentManifest:
    return ComponentManifest(id=cid, kind="tool", name=cid,
                             provider=Provider(project="lab"),
                             runtime=RuntimeSpec(backend="python", entrypoint="json:dumps"),
                             license=LicenseSpec(spdx=spdx, integration_mode=mode))


def test_a_commercial_run_is_denied_what_a_research_run_may_do():
    from bioagent.runtime.agentspec import AgentSpec
    from bioagent.runtime.events import EventLog, EventType
    from bioagent.status import ExecutionStatus

    records = LicenceRecords([_code(spdx="NONE", licence_path="", licence_sha256="",
                                    components=("lab.tool.unlicensed",))])
    rt = _runtime(_tool("lab.tool.unlicensed", "NONE", "federated"), records=records)

    research = rt.invoke("lab.tool.unlicensed", spec=AgentSpec(name="r"), obj={"a": 1})
    assert research.status is ExecutionStatus.SUCCEEDED and research.value == '{"a": 1}'
    assert "usage" not in (research.metadata or {})

    events = EventLog()
    commercial = rt.invoke("lab.tool.unlicensed", events=events, obj={"a": 1},
                           spec=AgentSpec(name="c", purpose="commercial"))
    assert commercial.status is ExecutionStatus.DENIED
    assert "no permission to use it commercially" in commercial.error
    assert commercial.metadata["usage"]["verdict"] == "deny"
    assert commercial.authorization.denied_rules == (
        "usage.code.license.none.federated.commercial",)
    (checked,) = [e for e in events if e.event_type == EventType.POLICY_CHECKED]
    assert checked.status == "DENY"
    assert checked.detail["usage"]["assets"][0]["record"] == "code.lab-tool"


def test_a_commercial_run_proceeds_on_reviewed_terms_and_records_them():
    from bioagent.runtime.agentspec import AgentSpec
    from bioagent.status import ExecutionStatus

    rt = _runtime(_tool("lab.tool.licensed", "MIT", "federated"),
                  records=LicenceRecords([_code()]))
    res = rt.invoke("lab.tool.licensed", spec=AgentSpec(name="c", purpose="commercial"),
                    obj=[1])
    assert res.status is ExecutionStatus.SUCCEEDED
    usage = res.metadata["usage"]
    assert usage["verdict"] == "allow" and usage["purpose"] == "commercial"
    assert usage["assets"][0]["terms"]["spdx"] == "MIT"

    refused = rt.invoke("lab.tool.licensed", spec=AgentSpec(name="x", purpose="internal"),
                        obj=[1])
    assert refused.status is ExecutionStatus.DENIED and "not one of" in refused.error


def test_a_commercial_run_does_not_download_what_it_may_not_use(tmp_path):
    """Auto-fetch runs before invoke's gate, so the gate is asked before the download —
    otherwise the bytes are on disk by the time the use is refused."""
    from bioagent.acquisition.downloader import Downloader
    from bioagent.acquisition.sources import AcquisitionSpec
    from bioagent.backends.base import BackendRegistry
    from bioagent.backends.concrete import DatasetBackend
    from bioagent.runtime.agentspec import AgentSpec, Runtime
    from bioagent.runtime.component import Permissions, Requirements
    from bioagent.runtime.registry import Loader

    body = "a\tb\n1\t2\n"
    remote = tmp_path / "remote" / "toy.tsv"
    remote.parent.mkdir()
    remote.write_text(body, encoding="utf-8")
    digest = "sha256:" + hashlib.sha256(body.encode()).hexdigest()
    acquisition = AcquisitionSpec(remote.as_uri(), "toy.tsv", "ftp.ebi.ac.uk", "CC0-1.0",
                                  "toy dataset", "general", expected_bytes=len(body),
                                  checksum=digest)
    m = ComponentManifest(id="toy.dataset.toy_tsv", kind="dataset", name="toy.tsv",
                          runtime=RuntimeSpec(backend="dataset"),
                          requires=Requirements(datasets=("toy.tsv",)),
                          permissions=Permissions(network=("ftp.ebi.ac.uk",)),
                          license=LicenseSpec(spdx="CC0-1.0", integration_mode="native"),
                          inputs={"acquisition": acquisition.__dict__})
    lake = tmp_path / "lake"
    lake.mkdir()
    registry = ComponentRegistry([m])
    resolver = Resolver(registry, dataset_probe=lambda name: (lake / name).exists())
    rt = Runtime(registry, BackendRegistry([DatasetBackend(lake)]), resolver=resolver,
                 loader=Loader(registry, resolver), downloader=Downloader(lake),
                 licences=LicenceRecords())

    commercial = rt.invoke(m.id, nrows=1, spec=AgentSpec(name="c", purpose="commercial",
                                                         auto_fetch=True))
    assert not (lake / "toy.tsv").exists(), "a commercial run downloaded what it may not use"
    assert not commercial.ok and "no reviewed licence record" in commercial.error
    research = rt.invoke(m.id, nrows=1, spec=AgentSpec(name="r", auto_fetch=True))
    assert (lake / "toy.tsv").exists() and research.ok


def test_a_commercial_component_set_leaves_out_what_the_gate_refuses():
    from bioagent.runtime.agentspec import AgentSpec

    records = LicenceRecords([_code(), _code(id="code.unlicensed", spdx="NONE",
                                             licence_path="", licence_sha256="",
                                             components=("lab.tool.unlicensed",))])
    rt = _runtime(_tool("lab.tool.licensed", "MIT", "federated"),
                  _tool("lab.tool.unlicensed", "NONE", "federated"), records=records)
    commercial = rt.lazy_set(AgentSpec(name="c", purpose="commercial")).acquire("lab tool")
    research = rt.lazy_set(AgentSpec(name="r")).acquire("lab tool")
    assert [m.id for m in commercial] == ["lab.tool.licensed"]
    assert sorted(m.id for m in research) == ["lab.tool.licensed", "lab.tool.unlicensed"]


# ===================================================================== integration

@pytest.mark.integration
def test_the_shipped_bindings_verify_against_a_fresh_clone_of_biomni(tmp_path):
    """Shallow-clone Biomni at the pinned commit and verify every shipped binding."""
    from omics_world import need_tools

    need_tools("git")
    bindings = load_bindings()
    (commit,) = {b.commit for b in bindings.values() if b.project == "Biomni"}
    root = tmp_path / "Biomni"
    root.mkdir()
    for args in (["init", "-q"],
                 ["remote", "add", "origin", "https://github.com/snap-stanford/Biomni.git"],
                 ["fetch", "-q", "--depth", "1", "origin", commit],
                 ["checkout", "-q", "FETCH_HEAD"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                       timeout=600)

    result = BiomniProvider(root, bindings=bindings).verify()
    assert result.commit == commit and result.commit_source == "git"
    assert result.refusals == (), result.as_dict()["refused"]
    assert {m.id for m in result.manifests} == set(bindings)
    assert all(m.runtime.entrypoint_basis == "verified" for m in result.manifests)
    assert no_biomni_imported()
