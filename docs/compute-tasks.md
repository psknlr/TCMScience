# Compute tasks: complex prediction, sequence design, dynamics, and jobs that take hours

`structure.predict.Prediction` is one sequence, one PDB file, pLDDT and PAE: the shape of an
ESMFold or AlphaFold2 monomer. The tools that matter next do not fit it. **Boltz** and
**Chai-1** predict complexes, with copies of chains, ligands, modified residues and
constraints, and what they are used for is the interface confidence. **ProteinMPNN** goes
the other way, from a backbone to sequences. **OpenMM** asks whether a model holds together
under a force field. Each is a different scientific task, with its own inputs, settings
and outputs, and each runs for minutes to days on a GPU. A tool call that blocks until a
timeout reports TIMEOUT for a job that is still running. A tool call that returns the job
id reports the *submission* as the result. That is the v1 false-success defect in a new
form: **an accepted request is not executed work.**

This page covers six pieces that fix this:

| Piece | Where | What it guarantees |
| --- | --- | --- |
| Task contracts | `bioagent.structure.{complex,design,dynamics,tasks}` | A task names everything that changes the answer (the model's version and weights included) and is refused, with every reason, before anything runs |
| Engine adapters | `bioagent.structure.engines` | Boltz, Chai-1, ProteinMPNN and OpenMM are detected in their own environments and refused with the reason when absent. No engine stands in for another |
| Long jobs | `bioagent.backends.jobs` | `submit` / `status` / `collect` / `cancel`. A step is SUCCEEDED only after its artefacts are collected and validated. A cancellation needs a grant naming the job |
| Governed long jobs | `bioagent.backends.jobtool`, `bioagent.psh.jobs`, PSH's pending result kind | A job is a tool call through PSH's broker, in process or in the isolated child. Work that has not finished is *pending*: recorded in the audit chain with its job reference, never a result, never released. The loop waits, resumes and collects; a restart finds the open jobs; a cancellation needs a grant recorded beforehand |
| Reviewed environments | `bioagent.backends.environments` | A provider runs in its own interpreter. A container gets a GPU, read-only data and one writable directory only when a reviewed, digest-bound configuration says so |
| ToolsAgent adapter | `bioagent.backends.toolsagent` | SciToolAgent's tool service, one fixed function per component, typed arguments, honest statuses, and long calls run as jobs |

## What ran, and what did not

The engines were first run through their adapters on 2026-10-07, on a CPU: four vCPUs of
an Intel Xeon at 2.8 GHz (AVX-512, no AVX512-BF16), 15.7 GB of RAM, no GPU, no container
runtime, shared with five other agents (load average 6 to 14 during the runs, so each time
below is an upper bound for this machine idle). Each tool ran in its own virtualenv
(Python 3.12.3) named by a reviewed environments file, through `Engine.run`: probe,
prepare, a supervised local job, collection with validation, reading. Nothing was
downloaded during a run. A job carries no proxy settings, and the weights were fetched
beforehand and checked against their publisher's digests.

| Engine | Environment | Weights (SHA-256) |
| --- | --- | --- |
| OpenMM | openmm 8.6.1 (PyPI wheel: Reference and CPU platforms), numpy 2.5.3 | the force fields OpenMM ships |
| ProteinMPNN | checkout `8907e6671bfbfc92303b5f79c4b5e6ce47cdef57`, torch 2.6.0+cpu, numpy 2.3.4 | `vanilla_model_weights/v_48_020.pt` `c9cb4a671d79…` |
| Boltz-2 | boltz 2.2.1, torch 2.6.0+cpu, numpy 1.26.4, pytorch-lightning 2.5.0, rdkit 2026.3.6 | `boltz2_conf.ckpt` `090e82ac8c92…` (2.29 GB), `boltz2_aff.ckpt` `dcc5cd3722b1…` (2.06 GB), `mols.tar` `39e076d96dbe…` (1.86 GB; 1.8 GB extracted): each the SHA-256 Hugging Face publishes for `boltz-community/boltz-2` |
| Chai-1 | chai_lab 0.6.1, torch 2.6.0+cpu, numpy 1.26.4, rdkit 2024.9.6 | the six `models_v2/*.pt` from chaiassets.com (1.18 GB; each MD5 equal to the server's ETag; `trunk.pt` `b7a08e104455…`), `conformers_v1.apkl` `f2161256b565…` |

| Run | Input and settings | Status | Wall time | Peak memory | Outputs (SHA-256) |
| --- | --- | --- | --- | --- | --- |
| OpenMM | 1UBQ with its 58 crystal waters; amber14 and TIP3P-FB, 1 nm padding, 0.15 M NaCl, pH 7: 17,189 atoms; minimisation, then 10 ps (5,000 steps of 2 fs) at 300 K, CPU platform | SUCCEEDED | 1,198 s: set-up and minimisation 575 s, the 10 ps 621 s | 0.15 GB | relaxed `b08ea8391775…`, DCD `e546ba451826…`, energies `771e6dac8a86…` |
| OpenMM | the same task for 2 ps (a report every 0.2 ps) with the script as it is now, one thread: 17,303 atoms | SUCCEEDED | 710 s: set-up and minimisation 601 s, the 2 ps 109 s | 0.14 GB | energies `e9271ea74ccb…`, run record `8ef600ec2b3e…` |
| OpenMM | the same structure with amber14 and GBn2 implicit solvent | FAILED in `createSystem`: "No template found for residue 76 (HOH)" | 2 s | — | now refused before it runs (below) |
| OpenMM | 1UBQ without its waters (1,231 atoms), GBn2; minimisation, then 2 ps | SUCCEEDED | 1,377 s: set-up and minimisation 1,023 s, the 2 ps 353 s (GBn2 is a `CustomGBForce`, slow on the CPU platform) | 0.07 GB | relaxed `4e1ff92165d5…`, energies `fc275b5ce0dc…` |
| OpenMM | 1UBQ without its waters, vacuum (amber14 alone); minimisation, then 1 ps; one thread (`OPENMM_CPU_THREADS=1`, which the job now inherits) | SUCCEEDED | 75 s | 0.07 GB | energies `94265e311189…`, run record `388f2e7fb538…` |
| ProteinMPNN | 1UBQ chain A, positions 1, 2, 3, 44 and 68 fixed, 8 designs in batches of 2, T = 0.1, seed 37, `v_48_020` | SUCCEEDED | 414 s, of which sampling 393 s, with torch's default of four threads; the same design run directly took 10.5 s in all with one thread (16 s on a repeat) and 47 s with two | 0.39 GB | `seqs/1ubq.fa` `1e3b06941a56…` |
| Boltz-2 | ubiquitin (76 residues, `msa: empty`) and aspirin by SMILES; 1 recycle (2 trunk passes), 50 sampling steps, 1 sample, seed 42, `--accelerator cpu`, 2 threads | SUCCEEDED | 378 s, of which prediction 289 s | 4.7 GB (the process's peak RSS; its two data-loader workers share most of it) | model `d6768a18df4d…`, PAE `1f652d6c3ee3…` |
| Boltz-2 | three chains: A = ubiquitin 1–20, B = a 19-residue peptide, C = A's sequence; the same settings | SUCCEEDED | 380 s | 4.6 GB (PSS, all its processes) | model `bc279ab01038…` |
| Chai-1 | ubiquitin and aspirin by SMILES, no MSA, no ESM embeddings; one trunk pass (`--num-trunk-recycles 1`), 50 diffusion steps, 1 sample, seed 42, `--device cpu`, 2 threads | TIMEOUT at the run's 45-minute limit, the job left running; it finished and was collected with `Engine.resume`: SUCCEEDED | 2,777 s: the trunk pass 1,946 s, 49 diffusion steps 659 s | 11.3 GB (PSS); the host's free memory fell to 1.3 GB | model `ef5bab2f0548…`, scores `cf114c55d772…` |

What the numbers are: Boltz-2 gave ubiquitin a mean pLDDT of 93.4 and the aspirin 32.1,
pTM 0.88 and ipTM 0.53, from a single sequence, one recycle and 50 sampling steps on a CPU;
Chai-1, with one trunk pass and no embeddings, 57.8 and 35.8, pTM 0.51, ipTM 0.10 and an
aggregate score of 0.18. Those are runs of the plumbing on real models, not poses anyone
should use. The designs recover 49 to 59 % of the native residues at the designed
positions; ProteinMPNN's own scores for them (0.83 to 0.88) are lower than the native's
(1.33).

The two explicit-solvent runs of 1UBQ built different systems: 17,189 atoms, then 17,303
(a cubic box of 5.685 nm, then 5.697 nm, and 5,252 added waters, then 5,290; the protein,
its 58 crystal waters and the 14 Na⁺ and 14 Cl⁻ were the same). OpenMM 8.6.1's
`addHydrogens` ends with a 50-step minimisation on the platform, and `addSolvent` sizes the
box from the result; the two runs used four threads and one. So the task alone does not
fix the system: the run record carries the atom count and, now, the thread count.

**What the first runs changed.** Each change is pinned by a test, most of them on the
runs' own outputs, trimmed and committed as fixtures (`tests/fixtures/compute`):

- **Boltz downloads 1.86 GB at start-up when `mols.tar` is missing**, even when the
  extracted `mols/` is there: `download_boltz2` (boltz 2.2.1) tests for the tar first. The
  cache must hold `mols.tar` as well; one without it is refused before anything runs.
- **Boltz numbers the chains by entity, not in input order.** Chains A = X, B = Y, C = X
  ran as A, C, B: its record (`processed/records/<name>.json`), the per-chain scores in the
  confidence file and the atoms in the mmCIF all follow that order. Mapping the scores by
  input order would have swapped B's and C's without any check noticing. The order of the
  chains in the mmCIF did match the numbering, as the adapter assumed, but the adapter now
  reads the numbering from Boltz's record, a required artefact checked to name exactly the
  task's chains, rather than infer it.
- **The same task ran one trunk pass fewer in Chai-1 than in Boltz.** Boltz runs
  `recycling_steps + 1` passes; chai_lab counts every pass (`--num-trunk-recycles 1`
  gave one, "Trunk recycles: 0/1"), and with `recycling_steps = 0`, which the task
  allows, it would skip the trunk and diffuse from the input embeddings. The adapter now
  passes `recycling_steps + 1`.
- **`resume` recorded the command the task would run now, not the one that ran.** The
  Chai-1 job outlived its run's limit and finished after the recycle mapping above had
  changed. Collected as the same task, `resume` would have rendered it again, recorded
  `--num-trunk-recycles 2` for a job that ran with `1`, and read its outputs with the new
  task's checks. An engine job's spec now binds the task's digest, and `resume` collects
  only a job whose spec is the one the task renders (otherwise FAILED, nothing read). The
  Chai-1 job was collected as `recycling_steps = 0`, which renders exactly the spec it was
  submitted with.
- **A structure with crystal waters in an implicit-solvent or vacuum task** reached
  OpenMM and failed there. `DynamicsTask` now refuses it before anything runs when no
  water model is among the force-field files; an explicit-solvent task keeps the waters
  and says so in a warning.
- **OpenMM reports a box volume for a system without a box**: 8.0 nm³, its default 2 nm
  cube, in every row of the implicit-solvent run. The script now asks for the volume only
  for a periodic system, records `periodic` and the CPU platform's thread count, and records
  a platform without a precision property as `null` rather than `""`.
- **A job could not be told how many CPU threads to use.** The local executor passed no
  thread variable, so torch, BLAS and OpenMM started one thread per core whatever else ran,
  and on this shared machine ProteinMPNN sampled for 393 s where a one-thread run took
  10.5 s. A job now inherits `OMP_NUM_THREADS`, `MKL_NUM_THREADS`,
  `OPENBLAS_NUM_THREADS` and `OPENMM_CPU_THREADS`, as it inherits `CUDA_VISIBLE_DEVICES`;
  the vacuum run's record shows OpenMM using the one thread it was given.

**Confirmed, unchanged.** The Boltz flags, YAML (`msa: empty` runs single-sequence),
output paths and confidence keys; pLDDT × 100 in `B_iso_or_equiv` (per residue for a
polymer, per atom for a ligand: the chain means reproduce Boltz's own `complex_plddt`);
the PAE file; the ligand as HETATM `LIG1`. ProteinMPNN's command line, the `seqs/<name>.fa`
layout, the header fields (seed, `model_name`, `git_hash`), 1-based fixed positions, and
`backbone_chains`, which gave the same sequences as ProteinMPNN's own `parse_PDB` on a
PDB with renumbering, a gap, an insertion code, an MSE as HETATM, an alternate location
of another residue and a second chain. ProteinMPNN's checkpoints load under torch 2.6's
`weights_only` default, and a fixed seed gave byte-identical designs with one thread,
with two, and on a repeat. OpenMM's StateDataReporter columns and units and the DCD
header.

**What did not run, and why.**

- **Chai-1 within its budget, and anything past one sample.** The run above took 46
  minutes, 77 s over the 45-minute budget: without an MSA, chai_lab still gives its trunk
  a masked MSA of 16,384 rows (`MAX_MSA_DEPTH`) and pads the complex to 256 tokens, which
  on this CPU cost 32 minutes for one pass and up to 11.3 GB. It is exported for a CUDA
  device; on a CPU it runs, slowly. `test_structure_complex_real.py` has a Chai-1 test for
  the same task, not run here as a test. Not exercised: restraints (the file's columns were
  checked against chai_lab's schema in its source), ESM embeddings (a 5.7 GB fp16 ESM-2
  3B), MSAs and templates, more than one sample, so the best-sample choice is tested only
  on synthetic scores.
- **ESMFold, local and governed** (`bioagent fold --method esmfold`, the
  `predict-protein-structure` skill). *Governed:* in a harness interpreter with torch
  2.6.0+cpu and transformers 4.57.1, a governed run of the skill with method `esmfold` was
  refused by the operation broker before any weight was fetched
  (`structure.esmfold.weights` reaches huggingface.co, which the skill's manifest does not
  declare: DENIED, recorded; the Hugging Face cache stayed empty), as
  [operation-governance.md](operation-governance.md) says it should. *Local:* the fold did
  not run. `facebook/esmfold_v1` has one fp32 `pytorch_model.bin` (8.44 GB; the file
  fetched hashed to the Hub's `2ee07356b125…`), and transformers 4.57.1 also fetched, on
  its own, an 8.44 GB `model.safetensors` from the Hub's unmerged pull requests
  (`refs/pr/4`, `refs/pr/6`; `9a865162cdca…`). Still loading, the process passed 11.7 GB,
  and the guard stopped it at the 11.5 GB this work was allowed on a shared 15.7 GB
  machine; the 16 GB of downloads were deleted. It needs a machine with more free memory,
  or a GPU. Found, not changed: `load_esmfold` pins no revision and records none, so the
  weights a local fold uses can come from an unmerged pull request.
- **No GPU, no container.** Nothing here ran on CUDA: Boltz's cuequivariance kernels
  (switched off on a CPU by Boltz itself), OpenMM's CUDA and OpenCL platforms, Chai-1 on
  the device it was exported for. `ContainerBackend` and its GPU profiles are still tested
  only through the argv they build.
- **Not exercised:** MSAs (files or a server) for any model, Boltz-1, Boltz's affinity
  head and potentials, more than one sample, constraints, ProteinMPNN's soluble weights,
  OpenMM on a structure with ligands. Boltz-2 and Chai-1 are minutes to an hour per complex
  on this CPU, so their real-run tests (`test_structure_complex_real.py`) are for a
  machine with the weights, not for CI.

The ToolsAgent adapter was checked in three ways. A local stub reproduces the endpoint's
behaviour. A FastAPI app with ToolsAgent's exact `run_func` signature, served by uvicorn,
accepts the adapter's request and refuses the request shape the README shows. And the
service itself, deployed from SciToolAgent's repository with its Chemical category,
answered every call the way the adapter reads it ([below](#deployed)).

## The three tasks

| Task | Inputs | Result | Refused before running |
| --- | --- | --- | --- |
| `ComplexPredictionTask` | chains (`ids` per copy, so `("A","B")` is a dimer; sequence; kind protein/dna/rna; modifications by 1-based position and CCD code; an MSA file), ligands (SMILES *or* CCD code), constraints (pocket, contact, covalent bond), `ModelSpec`, seed, samples, recycling and sampling steps | `ComplexPredictionResult`: the mmCIF model with its SHA-256, pLDDT per chain, pTM, ipTM, ipTM per pair of chains, PAE when written. Numbers only one model reports stay in `model_metrics` under their own names | duplicate chain ids, an entity with no copy, residues outside the alphabet, positions outside their chain, a ligand with both or neither of SMILES and CCD, a SMILES RDKit cannot parse (recorded as *unchecked* when RDKit is absent), a constraint naming a missing chain, an MSA server without `allow_remote` |
| `SequenceDesignTask` | backbone (PDB), chains to design, fixed positions per designed chain, temperature, number of sequences, batch size, seed | `SequenceDesignResult`: the native sequence and score, and each design's sequences per chain, score, global score and recovery | fixed positions outside the chain *as ProteinMPNN reads it* (residue numbers first to last, a gap for each missing number), fixed positions on a chain that is not designed, every position fixed, seed 0 (ProteinMPNN's "pick a random seed"), a number of sequences that is not a multiple of the batch size (ProteinMPNN would quietly make fewer), a multi-model PDB |
| `DynamicsTask` | structure, force-field files, explicit/implicit/no solvent, padding, ionic strength and ions, protonation pH, minimisation, duration, time step, temperature, friction, report interval, seed, platform | `DynamicsResult`: initial and minimised energies, the relaxed and final structures, the DCD trajectory, the StateDataReporter energies | seed 0 (OpenMM's "choose a seed"), explicit solvent without a water model, implicit solvent without an implicit-solvent file, waters in the structure with no water model among the force-field files (a crystal structure in an implicit-solvent or vacuum run), ions without explicit solvent, a time step above 2 fs (needs hydrogen-mass repartitioning, not set up here), a duration or report interval that is not a whole number of steps |

Positions are always 1-based indices into the chain's sequence as given. That is the
convention of Boltz, Chai-1 and ProteinMPNN, and PDB residue numbers do not follow it.

`ModelSpec(name, version, weights_sha256, environment, options)` makes the model part of the
task. Leave `version` and `weights_sha256` empty to record whatever is installed. Set them
to make them requirements. An installation that does not match is a refusal, not a warning.

## The engines

| Engine | Models | Runs as | Must already be on disk | Refuses |
| --- | --- | --- | --- | --- |
| `BoltzEngine` | `boltz-1`, `boltz-2` | `python -I -m boltz.main predict <name>.yaml --out_dir … --cache … --seed … --write_full_pae` | the cache (`model.options["cache"]`): `boltz2_conf.ckpt`, `boltz2_aff.ckpt`, `mols.tar` and `mols/` (Boltz-1: `boltz1_conf.ckpt`, `ccd.pkl`), because Boltz otherwise downloads them when it starts | a ligand contact without an atom name, `max_distance` outside 4–20 Å, contact constraints on Boltz-1 |
| `ChaiEngine` | `chai-1` | `python -I -m chai_lab.main fold <name>.fasta … --seed … --num-trunk-recycles <recycling_steps + 1> --no-use-esm-embeddings [--constraint-path …]` | `CHAI_DOWNLOADS_DIR` (`model.options["downloads"]`): the six `models_v2/*.pt` and `conformers_v1.apkl` | a ligand given by CCD code (Chai-1 takes SMILES; converting would choose protonation and stereochemistry), MSA files, covalent bonds, a contact within one chain, more than 26 chains |
| `ProteinMPNNEngine` | `proteinmpnn` | `python -I -c <runpy shim> <checkout> --pdb_path … --pdb_path_chains … --seed … --path_to_model_weights …` | the checkout (`interpreters["ProteinMPNN"].root`) with `vanilla_model_weights/` or `soluble_model_weights/` | model names the weights folder does not have |
| `OpenMMEngine` | `openmm` | `python -I openmm_run.py openmm_config.json <output>`, a script written from the task | — | (the task's own refusals) |

Detection asks the interpreter the reviewed configuration names for the project (the
harness's own interpreter when none is named) whether the tool's modules can be found
(`importlib.util.find_spec`, so torch is never imported just to ask), and which version is
installed. Not installed is `UNAVAILABLE`, the reason names the interpreter, the result
ends "no model was run", and `provenance.ran` is `False`.

Each engine also refuses what it would otherwise do silently:

- **Weights are never downloaded mid-run.** The weights that ran are the weights recorded
  (`provenance.weights`, file → SHA-256). Chai-1 records one digest per component and a
  combined digest, `sha256` over `name:digest` lines in name order, which is what
  `ModelSpec.weights_sha256` pins.
- **Chain ids are mapped, not assumed.** Chai-1 letters chains A, B, C in input order
  whatever they are called. The adapter maps in both directions. Boltz indexes per-chain
  scores by asym id and numbers the chains grouped by entity, not in input order (chains
  A = X, B = Y, C = X ran as A, C, B). The adapter reads the numbering from Boltz's record
  of the input (`processed/records/<name>.json`, a required artefact that must name
  exactly the task's chains).
- **The best model is the one reported.** Boltz's `model_0` is its top sample by
  `confidence_score`. Chai-1 writes samples in sampling order, so the adapter reports the
  sample with the highest aggregate score and records which one it was.
- **The model must be of what was asked.** Before a result exists, collection checks that
  the mmCIF holds exactly the task's chains, each with the task's residue count, and for
  proteins the task's sequence (modified positions aside). A model of another sequence is
  FAILED with no numbers in it.
- **ProteinMPNN's output is checked against the request**: the designed chains, the seed and
  the weights it reports, the number of designs, and that every fixed position kept its
  native residue.

## Long jobs

```
submit  ── JobRequested (written first) ── executor accepts ── JobSubmitted(ref)  step: RUNNING
status  ── JobObserved when the state changes          "completed" is the executor's claim
collect ── each artefact: fetch ─ SHA-256 ─ = declared digest? ─ validator ─ JobCollected
                                                       step: SUCCEEDED only if all pass
cancel  ── CancelGrant(job_id, granted_by, reason)? ─ executor ─ JobCancelled
                                                       CANCELLED only when confirmed
```

The job reference is recorded in the run's event log (`runtime.events.EventLog`), where the
run's other records live. `JobRequested` is written *before* the executor is asked, and
`JobSubmitted` with the reference after it answers. With `trace_path`, the log is
rewritten after every job event, atomically (`EventLog.save` now replaces the file in one
step). A crash at any point therefore leaves something to reconcile:
`open_jobs(trace).jobs` lists jobs submitted and not finished. `open_jobs(trace).unconfirmed`
lists requests with no reference. Submit those again with the same `submission_id`, and
the executor answers with the job that request started, if it started one, instead of a
second job. An `idempotency_key` (the PSH loop's `"<run id>:<task id>"`) returns the job
already submitted under it.

| The executor says | `collect` gives | Why |
| --- | --- | --- |
| queued / running | RUNNING, nothing collected | a submission is not a result |
| completed, every required artefact present, non-empty, matching the declared digest, validated | **SUCCEEDED** | the only way to SUCCEEDED |
| completed, an artefact missing, empty, mismatched or invalid | FAILED, each problem listed | the process exiting 0 is not the step succeeding |
| completed, an artefact unreachable for now | UNAVAILABLE | collect again later |
| failed / killed at its wall-clock limit | FAILED / TIMEOUT | |
| cancelled (confirmed) | CANCELLED | |
| lost (no record, or a supervisor that died without an exit status) | FAILED, "whether it did its work is unknown" | |
| a state the client does not know, or no answer | UNAVAILABLE | never read as completed |

`run(spec, timeout_s=…)` submits, waits and collects. A job still running at the deadline
is TIMEOUT. It is **not** cancelled, and it can be collected later from its reference
(`Engine.resume` for the engines, only as the task it was submitted for: an engine job's
spec binds the task's digest, and a task that renders another spec is refused). Build the
collecting controller without the run's `trace_path`, or its new log replaces the saved
one; append its events instead. `cancel` without a grant, or with a grant naming
another job, is DENIED and never reaches the executor. A cancellation the executor does
not confirm is reported as RUNNING ("requested, not confirmed"), not as done.

**Executors.** `LocalSubprocessJobs(root)` runs each job under a supervisor in its own
session. The job's directory holds `job.json`, `out/` (substituted for `{output}` in the
argv), the logs and `exit.json`. The supervisor holds a file lock for its whole life, so
"running" is a lock held, not a PID that may since belong to another process. A job
inherits only `PATH`, `HOME`, `LANG`, `LC_ALL`, `TMPDIR`, `CUDA_VISIBLE_DEVICES`,
`LD_LIBRARY_PATH` and the thread limits `OMP_NUM_THREADS`, `MKL_NUM_THREADS`,
`OPENBLAS_NUM_THREADS` and `OPENMM_CPU_THREADS`, plus what its spec declares, so the
harness's API keys stay behind. On a shared CPU host, set the thread limits: torch's
default of one thread per core made a ProteinMPNN run about 40 times slower here. It needs
POSIX. `HTTPJobService(base_url)` speaks this protocol:

| Call | Request | Answer |
| --- | --- | --- |
| submit | `POST /jobs` `{"submission_id", "component_id", "spec_digest", "task", "artefacts": [names]}` | `{"job_id", "state"}`; a repeated `submission_id` should return the existing job |
| status | `GET /jobs/<id>` | `{"state": "queued\|running\|completed\|failed\|cancelled", "detail", "artefacts": [{"name", "sha256", "size"}]}`; 404 is a lost job |
| artefact | `GET /jobs/<id>/artefacts/<name>` | the bytes; 404 is a missing artefact |
| cancel | `DELETE /jobs/<id>` `{"reason"}` | `{"state": "cancelled"}` confirms; any other state is a request; 409 means the job had already finished |

Its JSON calls go through `HTTPBackend.request`, so redirects stay on the service's host,
an HTML page is not taken as data, and sizes are capped. Each call is made once. A retried
POST is a second submission unless the service deduplicates on `submission_id`, and a
status poll is repeated by its caller anyway. Artefacts are streamed to `collect_dir`,
capped, and digested.

## Long jobs under PSH

A governed run reaches a component one tool call at a time, through PSH's broker, and the
bridge used to turn a RUNNING answer into a `ContractViolation`. That kept a submission
from counting as success, and it also meant a governed run could not use a job that
outlives one call. A job is now a *job tool*: a BioScience component admitted through the
bridge with a `JobTool`, whose calls submit, collect or cancel a job, and whose unfinished
work PSH records as pending.

```
submit   call_tool ─ gates ─ job tool ─ JobRequested, JobSubmitted ─ PendingResult(job ref)
              broker: tool_call_pending {reference, reference_sha256} ─ raises ResultPending
              loop:   task WAITING, ledger PENDING(digest) ─ AWAITING when nothing else can run
collect  call_tool + _psh_pending=digest ─ job tool collects that job, never submits
              still running ─ pending again (same digest)
              done ─ artefacts fetched, SHA-256, declared digest, validators ─ the call's value
cancel   grant_cancellation ─ job_cancel_granted (chain, read back) ─ JobCancelGranted (trace)
              call_tool {operation: cancel, job, grant} ─ the recorded grant, or DENIED
restart  reconcile ─ open_jobs(trace) ─ in-doubt ledger keys → PENDING ─ resume ─ collect
```

**PSH's pending result kind.** PSH gained a third outcome beside a value and a failure.
A component that started work which has not finished returns `PendingResult(reference,
reason)`: the reference names the work and never holds its output (keys that would carry
content are refused, and it must be plain JSON so its digest is the same in every
process). The broker builds no `ExecutionResult` for it. It records `tool_call_pending` in
the audit chain (the reference, its SHA-256 `reference_sha256`, the reason, which execution
ran it) and raises `ResultPending`, whose `pending` is a `PendingOutcome`: the reference,
labelled as the join of the call's inputs and the reference's own classification, with no
`value` attribute. `ResultPending` is not a `ContractViolation` and no retry policy names
it. A caller that does not know it fails rather than reading a submission as the work. The
isolated child says the same thing with `{"$psh": {"status": "pending", "reference": ...,
"reason": ...}}`; a pending envelope without a usable reference is a `ContractViolation`,
because passed through as a value it would be a successful result holding a submission.
The module holding the result kinds (`psh.kernel.results`) is now under the architecture
ratchet (`scripts/check_scientific_architecture.py`): it may import the contracts and the
labels and nothing that executes.

**The loop.** A tool task whose call is pending becomes `WAITING`: not terminal (the work
may still succeed), not ready, never succeeded, so nothing downstream reads it and the goal
cannot be satisfied while it waits. The operation ledger records the key as `PENDING` with
the reference digest: not in doubt (the work is named), not finished
(`OperationLedger.pending(run_id)` lists them for whoever resumes). When every task that
could still run waits, the loop stops with `Termination.AWAITING`, checkpoints, and the
result lists the outstanding work in `LoopResult.pending`. Resuming (`checkpoint.resume`,
`ResearchRunService.resume`) first *collects*: the same call again, with `_psh_pending`
naming the digest. A collection is not another attempt (no retry delay, no attempt count,
no repeat-safety refusal, since it repeats no side effect), and a collection that changes
nothing ends the call with AWAITING again without spending an iteration. Polling is bounded
by the deadline, cancellation and the budget (each poll is a tool call), and by whoever
decides when to resume, never by a loop spinning on a job. The ledger is the durable
record: a task whose graph state was lost but whose key is PENDING is collected, not
submitted again. A component that answers a collection with other work fails the task
(a collection never starts the work again). A collection the component cannot make now
(its executor out of reach) leaves the work pending. A loop that ends for another reason
(budget, deadline, cancellation) cancels its unstarted tasks but never calls a waiting task
cancelled, because its job goes on, and lists it as pending. The `Finalizer` releases
nothing from a result that lists pending work, whatever its termination says. The
checkpoint keeps a waiting task's digest always, and its reference under the rule for a
result (`withholding_reason`). The workflow journal records `node_waiting`, and an amended
program treats a waiting node like one in doubt: a non-replayable node is UNSAFE, not
recomputed.

**A job tool** (`bioagent.backends.jobtool`). `JobDefinition` says how a call's arguments
become a `JobSpec`: a local executor's `argv` with `{output}` and `{name}` placeholders, or
the task a service receives; the artefacts; validators named `module:function`. Arguments
are exactly the declared names, each a string or a number. `JobTool(executor, definition,
trace, collect_dir)` binds it to an executor and to the trace that records its jobs. Every
call takes an exclusive `flock` on the trace and reads it back first, so the in-process
component, isolated children and a restarted process append to one record; a trace that
cannot be read is refused, never started afresh over the record of a running job. A call is
`submit` (the default), `collect` (`job`: a job id this tool recorded) or `cancel` (`job`
and `grant`). A key (the loop's `"<run id>:<task id>"`) names one job: a submission under a
key that already has a job reports on that job, and one whose request was never confirmed
is submitted again with the same submission id, so the executor answers with the job that
request started. A continuation collects the job whose reference has the digest it names,
and refuses a digest it does not hold or a job submitted for another key. What it answers:

| The tool answers | Bridge (PSH) | Why |
| --- | --- | --- |
| RUNNING, naming its job | `PendingResult` → `ResultPending` | submitted, or still running |
| UNAVAILABLE, naming its job | `PendingResult` (the reason says why) | the executor could not be asked, or an artefact could not be fetched, for now |
| SUCCEEDED | the value: the job, `state: completed`, each artefact's path, SHA-256 and size | only after every required artefact validated |
| FAILED / TIMEOUT / CANCELLED | `ContractViolation` / `ToolTimeout` / `ContractViolation` | the job ended without its artefacts |
| DENIED | `PolicyDenied` | a cancellation without a recorded grant, or the BioScience policy kernel |
| cancel attempted | the value `{job_id, grant, cancelled, state, detail}` | `cancelled` only when the executor confirmed it |

The bridge admits a job tool with `BioScienceBridge.admit(manifest, jobs=tool)`. The
manifest must say what the tool really does (`JobTool.check`, a `BridgeRefused`
otherwise): a local executor is a `subprocess` component with `subprocess: true`, a service
an `http` component declaring the service's host, and `filesystem_write` covers the job
root, the trace and the collect directory. The PSH manifest is derived as for any component
and then marked: it mutates, is never idempotent, is at least `R2` and needs at least
`act_with_approval` autonomy, because starting a job is a side effect. The tool runs in a
runtime of its own (`JobTool.runtime`) where its declared mechanism is served by the job
backend, so resolution, the BioScience policy kernel and the event log rule on every call,
and the BioScience event log records RUNNING for a submission, never SUCCEEDED.

**In the isolated child.** The bridge writes the tool's configuration (executor, definition,
trace) beside the manifest, owner-only, and passes `--jobs <file> --jobs-digest <sha256>`;
the child refuses a file whose digest differs. The child submits or collects and exits; the
job's supervisor runs in its own session and outlives it. The child rules under the
bridge's permission profile, which the bridge now passes with `--profile`: every isolated
component used to be ruled on under the child's default, `biomedical-research`, whatever
the bridge was given. A profile only the parent defines is ruled on in the child as
`offline-analysis`: no network, no subprocess. The child evaluates the policy
against its own roots: its temporary directory is its sandbox directory, so an isolated job
tool's job root, trace and collect directory must lie under the workspace or data lake the
bridge hands it (`roots=`).

**Cancellation needs the recorded grant.** `bioagent.psh.jobs.grant_cancellation(kernel,
component, job_id, granted_by=, reason=)` checks that the tool recorded the job, writes
`job_cancel_granted` to PSH's audit chain, reads it back, and only then records
`JobCancelGranted` in the trace; it returns the grant id. A cancel call names that id, and
the tool builds the `CancelGrant` from its own record, not from the payload. Without a
recorded grant, with an id nobody recorded, or with a grant for another job, the call is
DENIED (`PolicyDenied` in process) and never reaches the executor. A grant the chain does
not hold is never written to the trace.

**A restart.** After a crash, `bioagent.psh.jobs.reconcile(ledger, component, run_id=)`
reads `open_jobs(trace)` (which now also gives each job's idempotency key) and moves every
ledger key of the run that is in doubt (RUNNING or UNKNOWN: the call died between the
executor accepting the job and the loop recording it as pending) to PENDING with the job's
reference digest, recording `job_reconciled`. The resumed loop then collects that job
rather than refusing the task as in doubt, and never submits it again. A ledger record that
disagrees with the trace is reported, never overwritten. A request the trace never saw
confirmed stays in doubt and is reported (`unconfirmed`): the trace does not hold the job's
arguments, so only the call that made the request can submit it again with its submission
id. A process that died after the tool collected a job and before the loop recorded the
value leaves the ledger PENDING: `open_jobs` no longer lists the job, and the resumed loop
goes by the ledger, collecting and validating the same job again.

## Reviewed environments

```json
{"interpreters": {"ProteinMPNN": {"prefix": "/opt/conda/envs/mlfold", "root": "/opt/ProteinMPNN"},
                  "boltz": {"python": "/opt/venvs/boltz/bin/python"}},
 "containers": {"structure.tool.boltz": {"gpus": {"devices": ["0"]},
                                         "data": {"msa": "/srv/msa"},
                                         "output": "/scratch/boltz"}},
 "review": {"by": "…", "on": "2026-10-07", "sha256": "<SHA-256 of the two keys above>"}}
```

`$BIOAGENT_ENVIRONMENTS` names the file. `default_runtime` loads it, and a file that fails
its review stops the assembly rather than being skipped. The review binds to the content:
the file carries the SHA-256 of its body (`review_digest`; `reviewed(body, by=, on=)`
writes the block). A body edited after review, such as a mount added or an interpreter
re-pointed, is refused as a whole. The digest proves the configuration is the one
reviewed. It cannot prove who reviewed it. Unknown keys, relative paths, a writable
directory overlapping a data root, commas in mount paths, and any network but `none` or
`bridge` are refused.

- **`SubprocessBackend`** runs a project the configuration names in that project's
  interpreter (`python`, or `prefix` for a virtualenv or conda environment, whose
  `bin/python` is used), from its `root`, still with `-I`. A missing or broken interpreter
  is UNAVAILABLE with the reason. A broken one is detected by starting it, because a
  virtualenv whose base Python was removed still has an executable `bin/python`. It never
  falls back to the harness's interpreter. A project the configuration does not name runs
  in the harness's interpreter, as before. Every result records which interpreter ran and
  its Python version.
- **`ContainerBackend`** keeps `--rm --network none` with no GPU and no host path for every
  component the configuration does not name. A named one gets exactly its profile:
  `--gpus '"device=0,1"'` or `--gpus count=N` (docker, nerdctl), `--device
  nvidia.com/gpu=N` (podman, which has no count form, so a count is refused there), each
  data root `--mount type=bind,source=…,target=/data/<name>,readonly=true`, the one output
  directory at `/out` (`BIOAGENT_OUTPUT_DIR=/out`), and `bridge` only if the profile says
  so. `command()` returns the exact argv without a runtime. A profile naming a host path
  this machine does not have is UNAVAILABLE before anything runs. A GPU the runtime cannot
  provide ("could not select device driver") is UNAVAILABLE, not FAILED.

## ToolsAgent (SciToolAgent)

ToolsAgent serves 500+ tools behind one FastAPI endpoint. As `main.py` and `tool_runner.py`
read on 2026-10-07, the endpoint is

```
POST /run-func?func_name=<name>&func_args=<one string>      body {"file_path_list": [...]}
200 {"status_code": 200, "result": …}   400 ValueError/ImportError   422 wrong shape   500 anything else
```

The README shows all three fields in one JSON body. The code reads `func_name` and
`func_args` as query parameters, and a FastAPI app with that signature refuses the README's
shape with 422. The adapter follows the code.

- **One function per component.** Whoever chooses `func_name` can run any of the 500
  tools. So `ToolsAgentTool` fixes it, and the component declares the hosts the data
  reaches, including hosts the service contacts on the tool's behalf (NovoPro for the
  alignment). A profile can then refuse it. Calls can go through a governed `Runtime`,
  where the policy kernel rules and the event log records the rendered `func_args`.
- **Arguments are typed and rendered by each tool's own rule.** Every function takes one
  string, and multi-argument tools split it on their own separator. `MolSimilarity`
  splits on `.`, which is also the SMILES disconnection operator, so a salt is refused
  before it is sent.
- **A 200 is a result only in the tool's result shape.** ToolsAgent tools catch their own
  exceptions and answer `None`, "Invalid SMILES string" or "### Error" with HTTP 200. Each
  tool declares `accept`, the shape of a real answer. Anything else is FAILED, with the
  text it returned.
- **Files are references on the service's host, never uploads.** The service resolves every
  name against the *last* path's directory and keeps only the last path's extension. A list
  that mixes them is refused. A file a tool writes is a `ServerFile`: digested and
  validated where `shared_dirs` makes it readable, DEGRADED otherwise.
- **Calls are not repeated.** A 500 is the tool raising, and repeating it runs it again.
  `main.py` stores each request's file list in a process-wide `Config()` that the tool reads
  while it runs, so the client never has a file-carrying call in flight alongside another
  of its own calls. Other clients of the same service can still collide.

| ToolsAgent answered | Status |
| --- | --- |
| 200 with the tool's result shape | SUCCEEDED (DEGRADED for an output file not readable here) |
| 200 with `None`, an error string, or anything else | FAILED, with the text |
| 400 "… not found …" (the tool's module is absent) | UNAVAILABLE |
| 400 other, 422 (request shape), 500 (the tool raised, or an unknown `func_name`) | FAILED |
| no connection | UNAVAILABLE; timeout: TIMEOUT |

Four functions are defined, each read in the source: `SMILESToWeight` (the number is
RDKit's `CalcExactMolWt`, the monoisotopic mass, which the service labels "Molecular
Weight … g/mol"), `MolSimilarity` (Morgan radius 2, 2048 bits; the service answers
identical molecules with an error, so 1.0 never comes back), `SMILESToInChI` (RDKit's
`MolToInchi` on the service; the ChemSpider call beside it is commented out), and
`DoubleSequenceGlobalAlignment` (computed by NovoPro's web service). Two neighbours were
read and left out: `SmilesToPdb` passes NovoPro's reply to `eval()` on the service host,
and `ConvertSdfToCsv` writes into a directory hard-coded for its author's machine. Long
calls run through the job protocol (`ToolsAgentJobs`). ToolsAgent has no job API and no
cancellation, so `cancel` stops nothing and says so, and a call from a process that has
ended is LOST.

<a id="deployed"></a>
### The deployed service

`scripts/deploy_toolsagent.sh DIR [PORT]` fetches SciToolAgent at commit
`ac1cf19fcef84e69f149db6da424afe4e9b2f11f`, installs the Chemical category's dependencies
in a virtual environment of its own, and serves ToolsAgent on `127.0.0.1`. Upstream's
`requirements.txt` cannot be installed as written: it pins numpy 2.1.3 beside
langchain 0.3.8, which requires numpy<2, and rdkit 2023.9.3, which predates numpy 2. The
script installs langchain 0.3.8, rdkit 2024.9.6 and numpy 1.26.4, and adds IPython, which
rdkit's `IPythonConsole` needs and the Chemical tools import. The Biology and Material
categories need PyTorch, transformers and model weights, and are not installed.

On 2026-10-07 the service deployed this way answered:

| Call through the adapter | The service answered | Status |
| --- | --- | --- |
| `SMILESToWeight` CCO | 200, `**Weight**: 46.04 g/mol` | SUCCEEDED, 46.04 |
| `MolSimilarity` CCO, CCN | 200, `**Tanimoto Similarity**: 0.3333` | SUCCEEDED, 0.3333 |
| `SMILESToInChI` CCO | 200, `InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3` | SUCCEEDED |
| `MolSimilarity` CCO, CCO | 200, "Error: Input Molecules Are Identical" | FAILED |
| `SMILESToWeight` C1CC | 200, "Invalid SMILES string" | FAILED |
| `SMILESToInChI` C1CC | 200, `null` (the tool caught its own exception) | FAILED |
| `DoubleSequenceGlobalAlignment` | 400, "Module for Biology not found …" | UNAVAILABLE |
| an unknown `func_name` | 500 | FAILED |
| `SMILESToWeight` through a governed `Runtime` | allowed by a profile naming the host, recorded; refused by the default profile | SUCCEEDED / DENIED |

For phenol, aspirin and caffeine, the weight and the InChI the service computed (RDKit
2024.9.6) equal those RDKit 2026.3.6 computes here, and the phenol–aniline similarity
equals the Morgan-generator value to four places. A test does not call the alignment on a
deployment that has Biology installed, because the service would send both sequences on to
NovoPro.

```bash
scripts/deploy_toolsagent.sh "$HOME/toolsagent"      # prints TOOLSAGENT_URL, TOOLSAGENT_CATEGORIES
TOOLSAGENT_URL=http://127.0.0.1:60002 TOOLSAGENT_CATEGORIES=Chemical \
  python -m pytest -q tests/test_toolsagent.py
```

## Use

```python
from bioagent.backends.environments import ExecutionEnvironments
from bioagent.structure.complex import Chain, ComplexPredictionTask, Ligand, PocketConstraint, Token
from bioagent.structure.engines import BoltzEngine
from bioagent.structure.tasks import ModelSpec

ubq = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
task = ComplexPredictionTask(
    "ubq_dimer", chains=(Chain(("A", "B"), ubq),), ligands=(Ligand("L", ccd="HEM"),),
    constraints=(PocketConstraint("L", (Token("A", 8), Token("A", 44))),),
    model=ModelSpec("boltz-2", version="2.2.1", options={"cache": "/srv/boltz-cache"}))
result = BoltzEngine(ExecutionEnvironments.from_env()).run(task, "runs/", timeout_s=4 * 3600)
result.status, result.reason, result.provenance.ran       # UNAVAILABLE, "... no model was run", False
```

```python
from bioagent.backends.jobs import CancelGrant, HTTPJobService, JobController, JobSpec, ArtefactSpec, open_jobs

jobs = JobController(HTTPJobService("https://gpu.example.org/api"), trace_path="run/trace.json",
                     collect_dir="run/artefacts")
ref = jobs.submit(JobSpec("structure.tool.fold", payload={...},
                          artefacts=(ArtefactSpec("model", "model.cif"),)))
outcome = jobs.collect(ref)                     # RUNNING until it has finished and validated
open_jobs("run/trace.json")                     # after a restart: what is still out there
jobs.cancel(ref, CancelGrant(ref.job_id, "j.doe", "superseded by run 12"))
```

```python
from psh.contracts import ResultPending
from bioagent.backends.jobs import ArtefactSpec, LocalSubprocessJobs
from bioagent.backends.jobtool import JobDefinition, JobTool
from bioagent.psh import BioScienceBridge, grant_cancellation, reconcile

tool = JobTool(LocalSubprocessJobs("ws/jobs"),
               JobDefinition(arguments=("sequence",), argv=("fold", "{sequence}", "{output}"),
                             artefacts=(ArtefactSpec("model", "model.cif"),)),
               trace="ws/trace/fold.json")
component = bridge.component(bridge.admit(fold_manifest, jobs=tool).id)
try:
    kernel.broker.call_tool(component, {"sequence": "MQIF..."}, envelope)
except ResultPending as pending:              # recorded as tool_call_pending, not a result
    digest = pending.pending.reference_digest
kernel.broker.call_tool(component, {"sequence": "MQIF...", "_psh_pending": digest}, envelope)
grant = grant_cancellation(kernel, component, job_id, granted_by="j.doe", reason="superseded")
kernel.broker.call_tool(component, {"operation": "cancel", "job": job_id, "grant": grant},
                        envelope)
reconcile(ledger, component, run_id=run_id)   # after a restart, before resuming the loop
```

In a governed loop the plan names the tool like any other; the loop adds `_psh_pending` on
its own when it collects, and `ResearchRunService.resume(checkpoint)` collects what an
`awaiting` run left outstanding.

```python
from bioagent.backends.toolsagent import ToolsAgentClient, MOL_SIMILARITY
ToolsAgentClient("http://127.0.0.1:60002").call(MOL_SIMILARITY, smiles1="CCO", smiles2="CCN")
```

## What this is not

- **Not a validation of the models.** OpenMM, ProteinMPNN, Boltz-2 and Chai-1 ran here,
  on a CPU, with the settings above; what that shows is that the adapters read what these
  versions write. A version that changes its formats fails validation; it does not produce
  a wrong result.
- **A governed long job is collected when someone resumes the loop.** An `awaiting` run
  holds its state in its checkpoint and ledger; nothing in this repository schedules the
  resume. Within one call the loop never sleeps on a job.
- **The isolated child's protocol has no refusal code.** In the child, a DENIED call (a
  cancellation without its grant, the BioScience policy) exits non-zero and PSH records a
  `ContractViolation` naming DENIED, where the in-process path raises `PolicyDenied`. Both
  refuse; only the exception class differs.
- **A local job is not inside the child's boundary.** A job a child starts inherits the
  child's resource limits (its address space is capped at the manifest's `memory_mb`, 2048
  MB by default), but not the egress proxy: a local job's environment holds only the
  variables `INHERITED_ENV` names (no proxy settings) and those its definition sets, so its
  network reach is the machine's unless an OS sandbox confines it (with `NoSandbox`,
  nothing does). A job started in process has the same reach and no limit from PSH.
- **The isolated HTTP path is not verified here.** From a child, a service job is ruled on
  under the bridge's profile, and its requests go through the kernel's egress proxy, which
  refuses loopback and private addresses. The shipped profiles do not allow `127.0.0.1`, so
  the loopback fixture is reached in process only; the isolated test checks only that the
  child's refusal starts no job.
- **One call at a time per job tool.** A call holds the trace's lock for its duration,
  including an artefact download, so calls on one tool queue behind each other.
- **A reconcile cannot resubmit an unconfirmed request.** The trace does not hold the job's
  arguments; the call that made the request can submit it again with its key, and the
  executor then answers with the job that request started.
- **Not exposed:** Boltz's affinity head and templates, Chai-1's MSA files and covalent
  bonds, ProteinMPNN's tied positions, biases and CA-only models, OpenMM's
  hydrogen-mass repartitioning, barostat and restraints. Each is refused or absent. None
  is approximated.
- **OpenMM protonation** uses its residue templates at the given pH. No pKa model is
  applied, and the result says so.
- **The local executor is POSIX-only.** A supervisor killed together with its job leaves
  the job LOST, not FAILED.
- **ToolsAgent's own state is out of reach.** It keeps no job records, cannot cancel, and
  shares one `Config()` between requests.

Tests: `BioScience-Harness/tests/test_compute_contracts.py` (the three tasks, the mmCIF
reader on a hand-made file and on Boltz's), `test_compute_engines.py` (refusals,
renderers, readers on the real runs' outputs, the stand-in end to end),
`test_compute_jobs.py` (local executor and the HTTP job-service fixture: success, failure,
lying digests, missing artefacts, unknown states, cancellation with and without grants,
crash reconciliation, the inherited environment), `test_backend_environments.py` (the
review, container argv, interpreters, the default runtime), `test_toolsagent.py` (the stub,
the governed path, long calls, the file-call guard, FastAPI with ToolsAgent's signature
when FastAPI and uvicorn are installed, and the deployed service when `TOOLSAGENT_URL`
names one), `test_psh_jobs.py` (governed long jobs: in process and in the isolated child
on the local executor, in process on the HTTP fixture; pending submissions and collections
in the chain, validated artefacts and their digests, continuations, a key per job, grants
recorded in the chain and the trace, the loop waiting and resuming, a restart reconciled
from `open_jobs`, a restart after a collection the loop never recorded, an unconfirmed
request, concurrent calls, an unreadable trace, a changed child configuration). All of
them run in the unit tier, where the ToolsAgent deployment tests skip; the `toolsagent`
CI job deploys the service and runs them. The SMILES check runs where RDKit is installed.
PSH's side is `PSH-Harness/tests/test_pending_results.py` (the contract, the broker's
record and label, the child's envelope, the loop's WAITING and AWAITING, the ledger,
checkpoints, the journal, and properties over random job schedules: pending work is never
a result, never released, never submitted twice, and the chain stays intact).

Two files run the tools themselves, each in the interpreter `$BIOAGENT_ENVIRONMENTS` names
for it, and skip when it names none (fail under `BIOAGENT_REQUIRE_TOOLS=1`):
`test_structure_engines_real.py` (OpenMM: ubiquitin in vacuum, minimisation and 1 ps;
ProteinMPNN: four designs for ubiquitin with fixed positions) is cheap enough for CI;
`test_structure_complex_real.py` (Boltz-2, and Chai-1 where it can run, on ubiquitin and
aspirin) also needs `$BIOAGENT_BOLTZ_CACHE` or `$BIOAGENT_CHAI_DOWNLOADS` and is not.
Here the first took 50 to 84 s with one thread per tool, depending on the machine's
load. A CI job for it (Python 3.12, as the modelling job sets up), as verified here:

```bash
python -m venv /tmp/openmm && /tmp/openmm/bin/pip install openmm==8.6.1
python -m venv /tmp/mpnn
/tmp/mpnn/bin/pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
/tmp/mpnn/bin/pip install numpy==2.3.4
git clone https://github.com/dauparas/ProteinMPNN /tmp/ProteinMPNN
git -C /tmp/ProteinMPNN checkout 8907e6671bfbfc92303b5f79c4b5e6ce47cdef57
pip install -e "BioScience-Harness[dev]"
python - <<'EOF'
import json
from bioagent.backends.environments import reviewed
body = {"interpreters": {"openmm": {"prefix": "/tmp/openmm"},
                         "ProteinMPNN": {"prefix": "/tmp/mpnn", "root": "/tmp/ProteinMPNN"}}}
json.dump(reviewed(body, by="ci", on="2026-10-07"), open("/tmp/environments.json", "w"))
EOF
cd BioScience-Harness
BIOAGENT_ENVIRONMENTS=/tmp/environments.json BIOAGENT_REQUIRE_TOOLS=1 \
    python -m pytest -q tests/test_structure_engines_real.py
```

## 中文摘要

**问题：** `structure.predict.Prediction` 只容纳单条序列、一个 PDB、pLDDT 与 PAE，装不下复合物预测（Boltz、Chai-1）、序列设计（ProteinMPNN）和分子动力学（OpenMM）这三类不同的科学任务。这些任务在 GPU 上一跑数小时；把"作业被接受"当作"成功"，是 v1 虚假成功缺陷的新形式。

**做法：**
- **任务契约** 把影响结果的一切写进任务，包括模型名称、版本、权重 SHA-256 与运行环境。复合物任务含链及拷贝数、配体（SMILES 或 CCD 二选一）、修饰残基与约束。设计任务的固定位置按 ProteinMPNN 的读法计（链内序号，不是 PDB 残基编号），种子须 ≥1。动力学任务拒绝种子 0、缺水模型的显式溶剂、超过 2 fs 的步长和非整数步数。所有问题在运行前一次性列出。
- **引擎适配器** 在评审过的环境中探测工具是否安装；未安装即 UNAVAILABLE，理由写明解释器，并注明"没有运行任何模型"。权重必须事先在本地（不在运行中下载），版本与权重摘要可被钉住。Chai-1 的链名映射、最佳样本选择、ProteinMPNN 的种子与固定位置都会对照请求核验；模型不是所请求的序列即判 FAILED。
- **长作业协议** 提交、状态、收集、取消四步分开。作业引用先写入运行事件日志（先记请求，再记引用），崩溃后可用 `open_jobs` 对账，并用同一提交号重新提交而不重复运行。只有收集并校验产物（存在、非空、摘要一致、校验器通过）后才算 SUCCEEDED。取消须有点名该作业的授权，且以执行方确认为准。实现有本地受监督进程与通用 HTTP 作业服务两种。
- **评审过的环境配置** 绑定内容摘要，评审后被改动即整体拒用。子进程后端按项目使用各自的解释器（仍为 `-I`），缺失或损坏时拒绝，不回退。容器后端只给配置点名的组件 GPU、只读数据挂载和唯一可写输出目录，默认仍 `--network none`；可直接查看将执行的完整命令。
- **ToolsAgent 适配器** 每个函数一个组件，固定 `func_name` 并声明数据去向（含服务端再转发的主机）。参数按各工具自己的分隔规则渲染，含分隔符的值发送前即拒绝。HTTP 200 只有符合工具结果形态才算成功。文件只是服务端路径引用。长调用走作业协议，但 ToolsAgent 无法取消，也不保留作业记录，这两点如实说明。
- **ToolsAgent 真实部署** `scripts/deploy_toolsagent.sh` 按审阅过的提交（ac1cf19）部署化学类工具，只监听 127.0.0.1。上游依赖清单本身无法安装（numpy 2.1.3 与 langchain 0.3.8 冲突），脚本给出可共存的版本。2026-10-07 实测：分子量、相似度、InChI 均按适配器的读法返回 SUCCEEDED，数值与本地 RDKit 一致；服务以 HTTP 200 返回的错误文本判为 FAILED；未安装的生物类模块判为 UNAVAILABLE；未知函数（HTTP 500）判为 FAILED；经受治理的 Runtime 调用被记录，默认权限下被拒。CI 的 `toolsagent` 任务每次部署并重跑这些测试。

**实际运行（2026-10-07，4 核 CPU、15.7 GB 内存、无 GPU，与另外五个代理共用）：** 四个引擎各装在独立虚拟环境中，由评审过的环境配置指定解释器，经适配器与长作业协议运行；运行中不下载任何东西，权重事先下载并与发布方摘要核对。OpenMM 8.6.1：1UBQ 显式溶剂（17,189 原子）最小化加 10 ps，成功，1,198 秒。ProteinMPNN（提交 8907e66）：泛素 8 条设计，成功，固定位置保持原残基。Boltz-2（boltz 2.2.1）：泛素加阿司匹林（SMILES，无 MSA，1 次循环，50 步采样），成功，378 秒，约 4.7 GB；三条链的换序实验表明 Boltz 按实体给链编号（输入 A、B、C，运行顺序为 A、C、B）。Chai-1（chai_lab 0.6.1）：同一复合物（主干一遍、50 步扩散、不用 ESM 嵌入）用时 46 分钟（主干一遍 32 分钟）、内存峰值 11.3 GB，超出 45 分钟预算；适配器按设计报告 TIMEOUT 并让作业继续运行，作业结束后用 `Engine.resume` 收集，结果 SUCCEEDED。同一显式溶剂任务的两次运行构建出不同的体系（17,189 与 17,303 个原子）：OpenMM 加氢后会在计算平台上做 50 步能量最小化，盒子大小随之而定，两次运行分别用了 4 个与 1 个线程；运行记录现同时记下原子数与线程数。

**真实运行发现并已修复的问题（均有测试，测试样例取自这些运行的真实输出）：** Boltz 缓存缺 `mols.tar` 时会在启动时重新下载 1.86 GB，现要求其存在；Boltz 的逐链分数改按其记录文件中的编号映射，不再推断；Chai-1 的 `--num-trunk-recycles` 计的是主干总遍数而 Boltz 计的是首遍之后的循环次数，适配器现给 Chai-1 传 `recycling_steps + 1`（否则 0 次循环会让 Chai-1 完全跳过主干）；`resume` 原先会按当前适配器重新渲染任务，记录一条并未运行的命令，现在作业规格绑定任务摘要，规格不一致即拒绝收集；含晶体水的结构用于隐式溶剂或真空任务时会在 OpenMM 内部失败，现在运行前即拒绝；非周期体系不再报告 OpenMM 默认盒子的 8 nm³ 体积；作业现可继承线程数变量（`OMP_NUM_THREADS` 等），在共享 CPU 上 ProteinMPNN 采样由 393 秒降到 10.5 秒。

**未做与未验证：** 本地 ESMFold 折叠没有完成：`facebook/esmfold_v1` 只有一个 8.44 GB 的 fp32 `pytorch_model.bin`，transformers 4.57.1 还自行从 Hub 未合并的拉取请求下载了 8.44 GB 的 `model.safetensors`，加载过程中内存超过 11.7 GB，被为共享机器设置的 11.5 GB 守护进程终止，下载的 16 GB 已删除；另发现 `load_esmfold` 不固定也不记录权重版本，尚未修改。受治理的 ESMFold 运行在下载任何权重之前即被操作代理拒绝（清单未声明 huggingface.co），与设计一致。没有 GPU 与容器运行时；MSA、Boltz-1、Boltz 亲和力、多样本、约束、ProteinMPNN 可溶性权重、含配体的 OpenMM 体系均未运行。长作业已可作为受 PSH 治理的工具调用（进程内与隔离子进程均可）：未完成的作业记为待定（pending），不作为结果、不发布，循环暂停后恢复时收集，但恢复须由调用方发起；Boltz 亲和力、Chai-1 的 MSA 文件与共价键、ProteinMPNN 的绑定位置、OpenMM 的氢质量重分配等均未提供，也没有近似替代。OpenMM 与 ProteinMPNN 的真实运行测试足够便宜，可放入 CI；Boltz-2 与 Chai-1 的不行。
