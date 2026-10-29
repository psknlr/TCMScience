"""research_routes: the map from a research question to every source in this runtime.

Coverage: every connector source and operation of the catalog is in a route, and so is
every relation kind the data hub has rows of and every dataset (by one of its kinds, an
entry that reads it, or by name in tcmdb.query), except those NOT_ROUTED says why. Every
call a route names is checked against the schema it will be validated against, nested
objects included. Readiness is the one catalog_search and the capability audit give; a
source that cannot run names what covers it and what that changes, and a step with nothing
runnable says it is unanswered here. A route runs nothing. studio/docs/ROUTES.md is
generated from the module.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from tcmstudio import dispatch
from tcmstudio import routes as rt
from tcmstudio.catalog import entries, get_entry
from tcmstudio.core_tools import CORE_BY_NAME, INQUIRY_SCAFFOLDS, ROUTE_IDS, core_for, route
from tcmstudio.dispatch import call, validate

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def catalog():
    return {e["id"]: e for e in entries("runner")}


def _uses():
    return [(r, s, u) for r in rt.ROUTES for s in r.steps for u in s.uses]


def _options(schema):
    if "anyOf" in schema:
        return [o for x in schema["anyOf"] for o in _options(x)]
    if isinstance(schema.get("type"), list):
        return [{**schema, "type": t} for t in schema["type"]]
    return [schema]


def _shape(schema, value, path, out):
    """``value`` against ``schema`` where each '<…>' stands for a value still to fill in:
    keys known, required keys given, lists and objects where the schema has them, and every
    value given outright valid."""
    if not schema or (isinstance(value, str) and "<" in value):
        return
    if isinstance(value, dict):
        objects = [o for o in _options(schema) if o.get("type") == "object"]
        if not objects:
            out.append(f"{path}: an object where the schema has none")
            return
        props = objects[0].get("properties") or {}
        extra = objects[0].get("additionalProperties")
        for k, v in value.items():
            if k in props:
                _shape(props[k], v, f"{path}.{k}", out)
            elif isinstance(extra, dict):
                _shape(extra, v, f"{path}.{k}", out)
            elif extra is not True:
                out.append(f"{path}: unknown argument {k!r}")
        out += [f"{path}: missing {k!r}" for k in objects[0].get("required") or ()
                if k not in value]
    elif isinstance(value, (list, tuple)):
        arrays = [o for o in _options(schema) if o.get("type") == "array"]
        if not arrays:
            out.append(f"{path}: a list where the schema has none")
            return
        for i, v in enumerate(value):
            _shape(arrays[0].get("items") or {}, v, f"{path}[{i}]", out)
    else:
        res = validate(schema, value, path)
        out += res.problems


def _schema_of(call_, catalog):
    """The schema the dispatcher validates this call's arguments against, and the arguments."""
    tool, a = call_["tool"], call_["arguments"]
    if tool == "connector_call":
        return catalog[f"connector.{a['connector']}.{a['operation']}"]["parameters"], a["arguments"]
    if tool == "run_pipeline":
        return catalog[f"job.pipeline.{a['pipeline']}"]["parameters"], a["arguments"]
    if tool == "call_tool":
        return catalog[a["tool"]]["parameters"], a["arguments"]
    return CORE_BY_NAME[tool]["parameters"], a


def test_the_tool_reaches_its_entry_and_the_schema_lists_the_modules_routes():
    assert ROUTE_IDS == tuple(rt.ROUTE_BY_ID) and len(set(ROUTE_IDS)) == len(rt.ROUTES)
    assert CORE_BY_NAME["research_routes"]["maps_to"] == "system.routes"
    assert route("research_routes", {"route": "safety"}) == ("system.routes", {"route": "safety"})
    assert core_for("system.routes") == "research_routes"
    entry = get_entry("system.routes")
    assert entry["kind"] == "system" and entry["exec"] == ["browser", "runner"]
    assert not any(entry.get(k) for k in ("confirm", "job", "network"))
    assert entry["parameters"]["additionalProperties"] is False
    assert entry["parameters"]["properties"]["route"]["enum"] == list(ROUTE_IDS)


def test_every_connector_source_and_operation_is_in_a_route(catalog):
    used = {u.entry for _, _, u in _uses()}
    operations = {i for i in catalog if i.startswith("connector.")}
    assert operations, "the catalog lists connector operations"
    missing = sorted(operations - used)
    assert not missing, f"add these operations to a route in tcmstudio/routes.py: {missing}"
    sources = {i.split(".")[1] for i in operations}
    assert sources == {u.entry.split(".")[1] for _, _, u in _uses()
                       if u.entry.startswith("connector.")}


def test_every_relation_kind_with_rows_and_every_dataset_is_routed(catalog):
    from bioagent.tcmdb import DATASETS, RELATION_KINDS
    kinds = {u.args["kind"] for _, _, u in _uses()
             if u.entry in ("tcmdb.relations", "tcmdb.consensus", "tcmdb.compare")}
    assert kinds <= set(RELATION_KINDS)
    with_rows = {k for d in DATASETS for k in d.relations or ()}
    assert not with_rows - kinds, f"relation kinds no route uses: {sorted(with_rows - kinds)}"
    named = {u.args["dataset"] for _, _, u in _uses() if "dataset" in u.args}
    read = {k for _, _, u in _uses() for k in catalog[u.entry].get("data") or ()}
    keys = {d.key for d in DATASETS}
    assert named <= keys and set(rt.NOT_ROUTED) <= keys
    unrouted = sorted(d.key for d in DATASETS if not (set(d.relations or ()) & kinds)
                      and d.key not in named and d.key not in read)
    assert unrouted == sorted(rt.NOT_ROUTED), "a dataset is routed, or NOT_ROUTED says why not"
    # a dataset a route asks for by relation kind holds rows of that kind
    for _, _, u in _uses():
        for src in u.args.get("sources") or ():
            spec = next(d for d in DATASETS if d.key == src)
            assert u.args["kind"] in (spec.relations or ()), (src, u.args["kind"])


def test_every_call_a_route_names_fits_the_schema_it_is_validated_against(catalog):
    problems = []
    for r, s, u in _uses():
        where = f"{r.id}/{s.id}/{u.entry}"
        if u.entry not in catalog:
            problems.append(f"{where}: no such catalog entry")
            continue
        assert u.evidence in rt.EVIDENCE, where
        schema, args = _schema_of(rt.call_for(u.entry, u.args), catalog)
        _shape(schema, dict(args), where, problems)
    assert not problems, "\n".join(problems)


def test_every_route_is_whole():
    for r in rt.ROUTES:
        assert r.title[0] and r.title[1] and r.asks and r.reconcile and r.steps, r.id
        assert any(not k.isascii() for k in r.keywords) and any(k.isascii() for k in r.keywords)
        assert len({s.id for s in r.steps}) == len(r.steps), r.id
        assert all(s.uses and s.answers for s in r.steps), r.id
        assert r.scaffold is None or r.scaffold in INQUIRY_SCAFFOLDS, r.id
        assert all(t in rt.ROUTE_BY_ID and t != r.id for t in r.then), r.id
    # every scaffold of the inquiry engine is reachable from a route
    assert {r.scaffold for r in rt.ROUTES if r.scaffold} == set(INQUIRY_SCAFFOLDS)


def test_a_call_goes_through_the_simplest_tool_that_takes_its_arguments():
    assert rt.call_for("connector.uniprot.entry", {"accession": "P04637"}) == {
        "tool": "connector_call", "arguments": {"connector": "uniprot", "operation": "entry",
                                                "arguments": {"accession": "P04637"}}}
    assert rt.call_for("job.pipeline.dock", {"receptor": "PDB:1M17"}) == {
        "tool": "run_pipeline", "arguments": {"pipeline": "dock",
                                              "arguments": {"receptor": "PDB:1M17"}}}
    assert rt.call_for("native.tcm_herb", {"name": "黄芪"}) == {"tool": "tcm_herb",
                                                               "arguments": {"name": "黄芪"}}
    assert rt.call_for("tcmdb.relations", {"kind": "herb_ingredient"})["tool"] == "tcmdb_relations"
    # no core tool for the entry, or one that does not take these arguments: call_tool
    assert rt.call_for("tcmdb.query", {"dataset": "wfo"}) == {
        "tool": "call_tool", "arguments": {"tool": "tcmdb.query", "arguments": {"dataset": "wfo"}}}
    assert rt.call_for("native.tcm_herb", {"name": "黄芪", "other": 1})["tool"] == "call_tool"


def _judge(runnable):
    def judge(entry_id, fixed):
        if entry_id in runnable:
            return []
        return [{"need": "network", "detail": f"{entry_id} needs web access",
                 "remedy": "Turn web access on."}]
    return judge


def test_a_source_that_cannot_run_names_what_covers_it_and_what_that_changes():
    view = rt.view(rt.ROUTE_BY_ID["compound_targets"],
                   _judge({"connector.chembl.activities", "connector.dgidb.drug_interactions"}))
    measured = view["steps"][0]
    assert measured["step"] == "measured" and "unanswered_here" not in measured
    rows = {s["source"]: s for s in measured["sources"]}
    assert rows["connector.chembl.activities"]["runnable_now"] is True
    pharos = rows["connector.pharos.ligand"]
    assert pharos["runnable_now"] is False and pharos["needs"] == [
        "connector.pharos.ligand needs web access"]
    # the same kind of evidence covers it first, and the switch is named
    assert pharos["covered_by"] == "connector.chembl.activities"
    assert pharos["changes"].startswith("the same kind of evidence (measured values)")
    hub = rows["tcmdb.relations compound_target"]
    assert hub["covered_by"] == "connector.chembl.activities"
    assert hub["changes"] == ("measured values instead of the data hub's labelled rows: "
                              "report it as measured")
    curated = view["steps"][1]
    signor = next(s for s in curated["sources"]
                  if s["source"] == "connector.signor.entity_relations")
    assert signor["covered_by"] == "connector.dgidb.drug_interactions"
    assert signor["changes"].startswith("aggregated scores instead of relations curated")
    # nothing in the step can run: it says so, and what would answer it
    tcm = view["steps"][2]
    assert tcm["unanswered_here"]["needs"] and tcm["unanswered_here"]["to_answer_it"] == [
        "Turn web access on."]
    assert all("covered_by" not in s for s in tcm["sources"])
    assert view["counts"] == {"sources": 20, "runnable_now": 2}
    assert view["scaffold"]["id"] == "target_engagement"
    assert "research_inquiry" in view["scaffold"]["how"]
    assert view["rules"] == list(rt.RULES) and set(view["evidence"]) <= set(rt.EVIDENCE)


def test_a_relation_kind_needs_one_of_its_own_datasets_built():
    keys = rt.hub_datasets("compound_target")
    assert keys and len(set(keys)) == len(keys)
    every = _judge({u.entry for _, _, u in _uses()})
    route_ = rt.ROUTE_BY_ID["compound_targets"]

    def hub(view):
        return view["steps"][0]["sources"][0]
    view = rt.view(route_, every, built=["herb1"], where="runner")
    assert hub(view)["runnable_now"] is False and hub(view)["datasets"] == keys
    assert "compound_target rows" in hub(view)["needs"][0]
    assert rt.view(route_, every, built=[keys[-1]], where="runner")["steps"][0]["sources"][0][
        "runnable_now"] is True
    # not judged when the hub cannot be read
    assert hub(rt.view(route_, every, built=None))["runnable_now"] is True
    # the step that supplies it, where the agent is
    runner = rt._kind_requirement("compound_target", [], "runner")
    assert f"job.tcmdb.fetch {{dataset: {keys[0]}}}" in runner["remedy"]
    assert runner["names"] == keys and runner["need"] == "dataset"
    assert "tcmdb.import_store" in rt._kind_requirement("compound_target", [], "browser")["remedy"]
    assert rt._kind_requirement("compound_target", [keys[0]], "runner") is None


def _no_sockets(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("research_routes opened a connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)


def test_research_routes_lists_shows_and_matches(runner_ctx, jobs, monkeypatch):
    _no_sockets(monkeypatch)
    e = call("research_routes", {}, runner_ctx)
    assert e["status"] == "succeeded" and len(e["result"]["routes"]) == len(rt.ROUTES)
    assert e["summary_en"] == f"Research routes: {len(rt.ROUTES)}"
    limits = " ".join(e["governance"]["limitations"])
    assert "runs nothing" in limits and "approvals" in limits
    assert "not evidence of absence" in limits

    e = call("research_routes", {"route": "safety"}, {**runner_ctx, "network": True})
    assert e["status"] == "succeeded", e["error"]
    assert e["result"]["route"] == "safety" and e["result"]["counts"]["runnable_now"] > 0
    assert e["summary_en"].startswith("Research route “Safety")
    assert "此处可运行" in e["summary"]

    e = call("research_routes", {"question": "何首乌有肝毒性吗？"}, runner_ctx)
    assert e["result"]["shown"] == "safety" and e["result"]["matched"][0]["route"] == "safety"

    e = call("research_routes", {"question": "qqq zzz"}, runner_ctx)
    assert "routes" in e["result"] and "No route matched" in e["result"]["note"]

    e = call("call_tool", {"tool": "system.routes", "arguments": {"route": "nope"}}, runner_ctx)
    assert e["status"] == "failed" and e["error"]["type"] == "bad_arguments"
    assert not jobs.submitted, "a route runs nothing"


def test_readiness_is_the_capability_audits(browser_ctx, monkeypatch):
    # the same entries, judged by the two code paths, in the browser with web access on and off
    # and its job service off and on; a browser job's call also by the entry's own rules for
    # the inputs it names (capability.runner_only), which the audit of the entry as such does
    # not read
    from tcmstudio import capability
    catalog = {e["id"]: e for e in entries("browser")}
    for network, browser_jobs in ((True, False), (False, False), (True, True)):
        ctx = {**browser_ctx, "network": network, "browser_jobs": browser_jobs}
        audit = call("call_tool", {"tool": "system.capability_audit",
                                   "arguments": {"limit": 1000}}, ctx)
        ready = {r["id"]: r["readiness"] == "ready" for r in audit["result"]["entries"]}
        for r in rt.ROUTES:
            view = call("research_routes", {"route": r.id}, ctx)["result"]
            for step, s in zip(r.steps, view["steps"]):
                for u, row in zip(step.uses, s["sources"]):
                    runner_only = capability.runner_only(catalog[u.entry], dict(u.args))
                    assert row["runnable_now"] == (ready[u.entry] and not runner_only), (
                        network, browser_jobs, r.id, u.entry)


def _rows(view, step):
    return {s["source"]: s for s in next(x for x in view["steps"] if x["step"] == step)["sources"]}


def test_a_browser_job_is_judged_by_the_inputs_its_call_names(browser_ctx, runner_ctx):
    """A route's job call runs here only when the entry's own browser rules run the inputs it
    names: docking from a structure, RNA-seq from FASTQ, single-cell from an .h5ad and ADMET
    on an SDF need the runner, so the same job on prepared PDBQT files, a count table, count
    tables or SMILES covers each, and says so."""
    ctx = {**browser_ctx, "network": True, "browser_jobs": True}
    dock = _rows(call("research_routes", {"route": "structure_binding"}, ctx)["result"], "docking")
    by_id = dock["job.pipeline.dock (from a PDB or UniProt id)"]
    by_file = dock["job.pipeline.dock (from a PDB file)"]
    prepared = dock["job.pipeline.dock (prepared inputs)"]
    assert prepared["runnable_now"] is True
    assert prepared["call"]["arguments"]["arguments"]["receptor"].endswith(".pdbqt")
    # the receptor and the ligands each need the runner, which prepares them, and so does
    # the fetch of a structure (allow_remote)
    for raw, reasons in ((by_id, 3), (by_file, 2)):
        assert raw["runnable_now"] is False
        assert len([n for n in raw["needs"] if "runner" in n and ".pdbqt" in n]) == reasons
        assert raw["covered_by"] == "job.pipeline.dock (prepared inputs)"
        assert raw["changes"].startswith("the same job on prepared inputs")
    # a PDB:/UniProt: receptor is fetched only with allow_remote; a PDB file is fetched not
    assert by_id["call"]["arguments"]["arguments"]["allow_remote"] is True
    assert "allow_remote" not in by_file["call"]["arguments"]["arguments"]
    assert by_file["call"]["arguments"]["arguments"]["receptor"].endswith(".pdb")
    assert dock["skill.dock-ligands"]["covered_by"] == "job.pipeline.dock (prepared inputs)"
    # every call names the docking site, which either runtime requires
    for row in (by_id, by_file, prepared):
        assert {"center", "size"} <= set(row["call"]["arguments"]["arguments"])
    targets = call("research_routes", {"route": "compound_targets"}, ctx)["result"]
    binding = next(s for s in targets["steps"] if s["step"] == "binding")
    assert "unanswered_here" not in binding
    assert [s["runnable_now"] for s in binding["sources"]] == [False, False, True]

    # ADMET: SMILES and CSV, TSV or .smi tables run here; an SDF or .mol file needs the runner
    # (RDKit's SDMolSupplier), in both steps that name the job
    for route_, step in (("safety", "toxicity"), ("exposure_metabolism", "models")):
        rows = _rows(call("research_routes", {"route": route_}, ctx)["result"], step)
        smiles = rows["job.pipeline.admet (SMILES or CSV/TSV/.smi)"]
        sdf = rows["job.pipeline.admet (SDF or .mol)"]
        assert smiles["runnable_now"] is True and "needs" not in smiles, (route_, smiles)
        assert sdf["call"]["arguments"]["arguments"]["molecules"].endswith(".sdf")
        assert sdf["runnable_now"] is False and "SDMolSupplier" in sdf["needs"][0]
        assert sdf["covered_by"] == "job.pipeline.admet (SMILES or CSV/TSV/.smi)"
        assert sdf["changes"].startswith("the same job on SMILES or CSV/TSV/.smi")
        assert not [k for k in rows if k == "job.pipeline.admet"], "every ADMET call names its mode"

    omics = call("research_routes", {"route": "omics_datasets"}, ctx)["result"]
    bulk, single = _rows(omics, "analysis"), _rows(omics, "single_cell_analysis")
    assert bulk["job.pipeline.rnaseq (count matrix)"]["runnable_now"] is True
    fastq = bulk["job.pipeline.rnaseq (FASTQ)"]
    assert fastq["runnable_now"] is False and "without counts" in fastq["needs"][0]
    assert fastq["covered_by"] == "job.pipeline.rnaseq (count matrix)"
    assert single["job.pipeline.scrna (count tables)"]["runnable_now"] is True
    h5ad = single["job.pipeline.scrna (AnnData or 10x HDF5)"]
    assert h5ad["runnable_now"] is False and ".h5ad" in h5ad["needs"][0]
    # a single-cell atlas is covered by the single-cell job, never by a bulk RNA-seq one
    assert single["skill.scrna-cell-atlas"]["covered_by"] == "job.pipeline.scrna (count tables)"

    # the rules are the browser's: the runner judges the calls of a job alike, except that a
    # structure is fetched only with web access on
    for network in (False, True):
        on_runner = _rows(call("research_routes", {"route": "structure_binding"},
                               {**runner_ctx, "network": network})["result"], "docking")
        here = on_runner["job.pipeline.dock (prepared inputs)"]["runnable_now"]
        assert on_runner["job.pipeline.dock (from a PDB file)"]["runnable_now"] == here
        fetch = on_runner["job.pipeline.dock (from a PDB or UniProt id)"]
        assert ("web access for this project" in fetch.get("needs", [])) is not network
        if network:
            assert fetch["runnable_now"] == here


def test_a_job_template_is_judged_as_the_call_it_stands_for():
    """Each '<…>' of a job a route names stands for a value of one kind: with the '<…>'
    filled in, the entry's browser rules send the call where they send the template."""
    import re

    from tcmstudio import capability
    catalog = {e["id"]: e for e in entries("browser")}

    def fill(value):
        if isinstance(value, str):
            return re.sub(r"<[^>]*>", "x", value)
        if isinstance(value, list):
            return [fill(v) for v in value]
        return value
    judged = 0
    for r, s, u in _uses():
        rules = ((catalog[u.entry].get("execution") or {}).get("browser_job") or {}).get(
            "runner_only")
        if not rules:
            continue
        template = capability.runner_only(catalog[u.entry], dict(u.args))
        filled = capability.runner_only(catalog[u.entry], {k: fill(v) for k, v in u.args.items()})
        assert template == filled, (r.id, s.id, rt.label(u))
        judged += 1
    assert judged >= 7


def test_every_pipeline_a_route_names_is_taken_by_its_job_kind_once_filled_in(tmp_path):
    """Each pipeline job a route names, its '<…>' filled with a value of the kind each stands
    for (a '<…>.sdf' with a file of that suffix), is a call the runner's job kind takes as
    given: a PDB:/UniProt: receptor comes with the allow_remote that fetches it, a FASTQ run
    with the transcriptome it is quantified against, a fold by the default method with the
    allow_remote that sends the sequence out. The runner refuses a call that leaves one out
    before it is queued, so a route that left it out would send the agent back for it."""
    import re

    from tcmstudio.kinds import KINDS, BadParams, PrepareContext
    found: dict[str, tuple[Path, dict]] = {}

    def upload(name, text=""):
        path = tmp_path / "files" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
        found[name] = (path, {"upload": f"u_{len(found):016x}", "name": name})
        return name
    reads = upload("reads_1.fastq", "@r1\nACGTACGT\n+\nIIIIIIII\n")
    stands_for = {
        "<PDB:id or UniProt:accession>": "PDB:1M17",
        "<SMILES, or a CSV, SDF or .smi file>": "CCO",
        "<SMILES, or a CSV, TSV or .smi file>": "CCO",
        "<x>": 1.5, "<y>": -2.0, "<z>": 3.25, "<x, Å>": 20.0, "<y, Å>": 20.0, "<z, Å>": 20.0,
        "<sequence or FASTA>": "MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQ",
        "<gene × sample count table>": upload("counts.tsv", "gene\tS1\tS2\nG1\t1\t2\n"),
        "<sample table>": upload("samples.csv", "sample,condition\nS1,a\nS2,b\n"),
        "<sample sheet of FASTQ files>": upload("sheet.csv", f"sample,fastq_1\nS1,{reads}\n"),
        "<transcriptome FASTA>": upload("transcripts.fa", ">t1\nACGTACGT\n"),
        "<count tables (CSV/TSV), or a sample sheet of them>": upload("cells.csv",
                                                                      "gene,c1,c2\nG1,1,0\n"),
    }

    def fill(key, value):
        if isinstance(value, list):
            return [fill(key, v) for v in value]
        if not isinstance(value, str) or "<" not in value:
            return value
        suffix = re.fullmatch(r"<[^<>]*>(\.[\w.]+)", value)
        if suffix:
            return upload(f"{key}{suffix.group(1)}")
        if value not in stands_for:
            raise BadParams(f"{value!r}: say in this test what it stands for")
        return stands_for[value]
    ctx = PrepareContext(job_id="j1", job_dir=tmp_path / "job", project_id=None, paths={},
                         data_env={"BIOAGENT_DATA_LAKE": str(tmp_path / "lake")},
                         find=found.get, settings={})
    refused, taken = [], []
    for r, s, u in _uses():
        if not u.entry.startswith("job.pipeline."):
            continue
        try:
            KINDS[u.entry.split(".", 1)[1]].prepare(
                {k: fill(k, v) for k, v in u.args.items()}, ctx)
        except BadParams as exc:
            refused.append(f"{r.id}/{s.id}/{rt.label(u)}: {exc}")
            continue
        taken.append(rt.label(u))
    assert not refused, "the runner refuses these calls as the routes give them:\n" + "\n".join(
        refused)
    assert {"job.pipeline.dock (from a PDB or UniProt id)", "job.pipeline.dock (from a PDB file)",
            "job.pipeline.dock (prepared inputs)", "job.pipeline.rnaseq (FASTQ)",
            "job.pipeline.rnaseq (count matrix)", "job.pipeline.admet (SDF or .mol)",
            "job.pipeline.admet (SMILES or CSV/TSV/.smi)", "job.pipeline.fold",
            "job.pipeline.scrna (count tables)", "job.pipeline.scrna (AnnData or 10x HDF5)"
            } == set(taken)


def test_a_built_dataset_makes_its_relation_kind_runnable(runner_ctx, monkeypatch):
    key = rt.hub_datasets("herb_drug_interaction")[0]
    monkeypatch.setattr(dispatch, "_built", lambda ctx: [key])
    e = call("research_routes", {"route": "herb_drug_interaction"}, runner_ctx)
    first = e["result"]["steps"][0]["sources"][0]
    assert first["source"] == "tcmdb.relations herb_drug_interaction" and first["runnable_now"]
    monkeypatch.setattr(dispatch, "_built", lambda ctx: [])
    first = call("research_routes", {"route": "herb_drug_interaction"},
                 runner_ctx)["result"]["steps"][0]["sources"][0]
    assert not first["runnable_now"] and key in first["needs"][0]


@pytest.mark.parametrize("question,expected", [
    ("葛根芩连汤治疗2型糖尿病的作用机制是什么？", "formula_mechanism"),
    ("甘草和甘遂能一起用吗？", "safety"),
    ("人参皂苷Rg1口服后在血中的浓度有多高？", "exposure_metabolism"),
    ("Does berberine inhibit AMPK?", "compound_targets"),
    ("What is the evidence that Astragalus works for heart failure?", "efficacy_evidence"),
    ("Is Ginkgo safe to take with warfarin?", "herb_drug_interaction"),
    ("有没有肝脏的单细胞测序数据？", "omics_datasets"),
    ("Which pathways are enriched among the targets of Huangqi?", "pathways_networks"),
    ("黄芪的正品基原是什么，怎么和伪品区分？", "herb_identity"),
    ("黄芪含有哪些化学成分？", "herb_composition"),
    ("桂枝汤的组成和出处", "formula_composition"),
    ("四君子汤主治什么证候？", "traditional_use"),
    ("What is the structure and InChIKey of baicalein?", "compound_identity"),
    ("Which transcription factors regulate NFE2L2?", "gene_regulation"),
    ("Which diseases is IL6 associated with?", "disease_links"),
    ("何首乌有肝毒性吗？", "safety"),
    ("Is there a crystal structure of PTGS2 with a ligand bound?", "structure_binding"),
    ("rs1057910 对华法林剂量的影响", "variants_pharmacogenomics"),
    ("Identify this compound from its MS/MS spectrum", "spectra_identification"),
    ("小檗碱对哪些肿瘤细胞系敏感？", "cell_models_cancer"),
    ("Which epitopes of the peanut allergen Ara h 2 are known?", "immunology"),
    ("黄连对肠道菌群的影响", "microbiome"),
    ("Are there pathology images of liver cancer?", "imaging_data"),
    ("Find papers on Danshen and coronary heart disease", "literature"),
    ("Map the MeSH term for diabetes to ICD-10", "ontology_mapping"),
    ("黄芪和当归配伍有协同作用吗？", "combination_effects"),
    ("葛根芩连汤是否比单用小檗碱更有效？", "formula_composition"),
    ("What did NCT01234567 find?", "efficacy_evidence"),
    ("Re-analyse GSE12345 for Huangqi treatment", "omics_datasets"),
    # the safety_signal scaffold's own example event, and how it is asked
    ("Does Polygonum multiflorum cause drug-induced liver injury?", "safety"),
    ("何首乌会引起肝损伤吗？", "safety"),
    ("何首乌导致药物性肝损伤的证据", "safety"),
    ("Is Heshouwu hepatotoxic?", "safety"),
    ("What are the protein targets of quercetin?", "compound_targets"),
    ("Which proteins does quercetin bind?", "compound_targets"),
    ("What is the effect of BRAF p.Val600Glu on vemurafenib response?",
     "variants_pharmacogenomics"),
    ("Is c.1799T>A pathogenic?", "variants_pharmacogenomics"),
    ("What does GO:0008150 map to?", "ontology_mapping"),
    ("What is ChEMBL25?", "compound_identity"),
    # a 伤寒论 syndrome named without its closing 证, and what a syndrome is treated with
    ("桂枝汤治疗太阳中风的证据", "traditional_use"),
    ("太阳中风用什么方？", "traditional_use"),
    ("少阴病四逆汤的条文", "traditional_use"),
    ("Dock quercetin into EGFR", "structure_binding"),
])
def test_a_question_finds_its_route(question, expected):
    assert rt.match(question)[0][0].id == expected


@pytest.mark.parametrize("question,not_expected", [
    # a p-value is not an HGVS protein change, and 'go' is not the GO prefix: identifiers
    # are matched as they are written
    ("Is the effect significant at p.05?", "variants_pharmacogenomics"),
    ("Is the effect significant at P.05?", "variants_pharmacogenomics"),
    ("go: 1", "ontology_mapping"),
    ("hp: 2", "ontology_mapping"),
])
def test_text_that_only_looks_like_an_identifier_finds_no_route_by_it(question, not_expected):
    assert not_expected not in [r.id for r, _ in rt.match(question)]


def test_the_routes_document_is_generated_from_the_module():
    doc = REPO / "studio" / "docs" / "ROUTES.md"
    assert doc.read_text(encoding="utf-8") == rt.markdown(), (
        "studio/docs/ROUTES.md is out of date: run python3 studio/scripts/routes_doc.py")
