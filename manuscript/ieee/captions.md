# IEEE-style flowcharts: captions

Two architecture flowcharts in the style of the IEEE Transactions, for a submission there or
for a talk: the agent in full (two columns, 7.16 in) and the one idea it rests on (one
column, 3.5 in). Each is a PDF and an EPS (vector, fonts embedded) and a 600 dpi PNG.
`captions.tex` holds the same captions in IEEEtran `figure*` and `figure` environments.

Every component is named as the code names it, and every count is read from the code at the
commit the figures were built from (`data/extracted/inventory.json` and
`capabilities.json`), as for the Nature figures.

---

**Fig. 1.** The TCMScience agent architecture, with each component named as in the code.
(1) A request enters through the command line, the Python services or Studio and is labelled
at ingress. (2) It passes to the planner, which (3) obtains a proposal from the language
model only through `call_model` and the ModelGateway. (4) The scientific compiler checks the
proposed program in eight passes (structure, method, typecheck, effects, infoflow,
statistics, reproducibility, resources; 71 diagnostic codes) and lowers it to a typed plan; a
refused program returns to the planner with each diagnostic's code and remedy, for a bounded
number of repairs. (5) The PlanValidator checks the whole plan (graph, authority, dataflow,
budget, scientific) before any step runs. (6) The AgentLoopController acts only through the
ExecutionBroker's three calls (model, tool, delegate) and stops for one of 12 named reasons.
(7) Tool calls cross the BioScienceBridge into the capability plane and return as labelled
results. (8) The loop's candidate output passes evidence ingestion and claim checks and is
held in quarantine and checked by the OutputGate; (9) it leaves only through one release
gate, either as a result whose claim kind its evidence licenses or as a refusal with its code
and remedy. (10) Validated state is committed through the PersistenceGateway to four stores,
and every gate's decision is appended to the hash-chained event store. Governance constrains
the kernel and never executes. Solid lines, execution; dashed, an untrusted component or a
constraint.

**Fig. 2.** TCMScience in brief. The language model only proposes. The trusted kernel
compiles, authorizes, executes and verifies every step, calls the model and the tools
itself, and releases a claim only of a kind its evidence licenses; otherwise it refuses, with
a code and a remedy. Every decision is appended to a hash-chained audit. Dashed box,
untrusted; dashed arrow, a record.
