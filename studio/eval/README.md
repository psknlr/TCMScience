# The headless agent eval

`studio/eval` runs the Studio agent the way a user's question runs it, without a browser and without a paid model: the
real agent loop (`web/js/core/agent.js` `runTurn`), the real `ToolRouter` and `RunnerRuntime`, and a real
`tcmstudio serve` started in a temporary home (through `e2e/lib/servers.mjs`). Each case's turn is graded, and the run is
held to `baseline.json`.

```bash
cd studio
npm run test:eval                       # the eval's own tests (graders, mutants, cassettes, baseline rule, harness)
npm run eval:agent                      # every dev case; writes agent_eval_report.json (path printed at the end)
npm run eval:agent -- --case fault-length --suite fault
npm run eval:agent -- --update-baseline # after a fix: known failures that now pass move to passing
npm run eval:agent -- --record          # re-record the cassettes (cases tagged "cassette")
```

`PYTHON` names the Python that has `tcmstudio` (as for the e2e specs). The run takes about 20 seconds here; CI runs it
in the `test` job of `.github/workflows/studio.yml`, after `npm test`, and keeps the report as the `agent-eval-report`
artifact.

## What drives the model

| Driver | The model is | Measures |
|---|---|---|
| `scripted` | a policy written in the case, played in-process as the library the loop calls (`fixtures/fakes.mjs` `scriptedLLM`, `openaiStep`) | the loop, the router, the runner and its tools, the graders |
| `fault` | the same kind of policy served over HTTP by an OpenAI-compatible server on loopback, to the real client (`llm/openai.js`): HTTP errors with their bodies and headers (429 with Retry-After, a plan-quota 429, 413, 500), an error event in a 200 stream, a base_resp code, a stream that stalls, finish_reason length, a model that ignores tool_choice none, parallel calls with 16,000-character results | what the client and the loop do with a failing or unusual service |
| `relay` | the policy as the upstream of the real Tao-S1 relay (`edge/src/relay.js`, its handler run in-process with the limiter on node:sqlite and `wrangler.toml`'s `[vars]`) | upstream calls and limit-gate hits per turn, and what the visitor is told |
| `cassette` | recorded event streams, replayed | the same turns as recorded, while the prompt, the tool definitions and the tool results stay the same |
| `live` | any OpenAI-compatible service (`--live`, `EVAL_LIVE_BASE_URL`, `EVAL_LIVE_MODEL`, `EVAL_LIVE_API_KEY`) | a real model's choices; run by hand, or in CI only from `workflow_dispatch` |

Only a live run measures a model. The report says which kind of run it is (`model`: `scripted`, `cassette` or `live`),
so a scripted run is never read as a model's quality.

Every request the runner sends off the machine goes to a proxy on loopback that records its host and refuses it: the eval
is offline by construction, and a case can check that nothing tried to leave (with a project's web access off, no case
may; the identifier case checks it with web access on).

## Graders

In the PSH evaluator's order (`psh/runtime/evaluator.py`); all run on every turn and the first that failed is named.

1. **structural**: the turn ended by itself, with the status the case expects and an answer; a turn cut short (the step
   limit, finish_reason length) says so (a notice); every call has its result; an error says whether a retry can help
   (the Retry button).
2. **execution**: trajectory recall and precision against the case's gold calls, by catalog entry; no forbidden call; no
   call repeated after it failed in a way a retry cannot change.
3. **evidence**: every `[E#]` the answer cites was returned by a tool; none of the banned words (`prompt.js`), unless the
   answer denies them ("无记录不等于安全"); every claim code the kernel refused is named; "released" only when a tool
   authorized release. Inline until `web/js/core/answercheck.js` exists (plan item P1.2).
4. **goal**: entity sets the answer must name, statements it must not make (patterns, never string equality with a
   reference answer), the outcome class of the key result (data_unavailable, never a negative finding), a ceiling on a
   research_inquiry verdict, and nothing sent off the machine.
5. **cost**: model calls, upstream calls, gate hits, peak and total request bytes, tokens, retry waits, milliseconds; a
   case sets the ceilings that matter to it.

## Cases

`cases/dev/*.json`, one turn each: the question, the driver, the settings, a gold policy and at least one mutant, and
what each grader expects. The dev split has the twelve research scenarios of `tcmstudio/scenarios.py` (their gold calls
are the scenarios' own), the Guizhi benchmark's stage-11 trap, an honest gap, the proof of concept's answer with a
fabricated citation, an identifier in a connector's arguments, three research_inquiry adversaries and the remedy of one
(role 'artefact' given), and the fault, growth and relay probes. A policy step is `{calls: [...]}`, `{text}`, `{repeat: {...}}` or one of the HTTP-level faults
(`lib/policy.mjs` lists them); an argument may name an earlier call's result (`{"$from": "c1", "path": "result.x"}`) or be
a text repeated (`{"$repeat": "黄芪", "times": 350000}`), and an answer may name an earlier call's citation
(`{{cite:c1}}`). A reference that cannot be resolved ends the policy with its fallback: the gold policy's is the case's
`fallback`, a mutant's is its own (or its last plain answer), never the case's honest one.

A mutant is a bad behaviour of the model or of the service (a fabricated citation, a missing call, a negative finding
made of a missing store; a 500 where the case has a 413, a Retry-After of 1 s) written to fail the grader it names, and
when it names a `check`, that check of the grader: every fault and relay case has one aimed at the structural or cost
check it exists for (`retry_offered`, `error_kind`, `retry_waits`, `max_model_calls`, `max_upstream_calls`,
`max_peak_request_bytes`). A run fails when a mutant passes what it targets. A check the loop alone decides, such as
`states_how_it_ended`, has no mutant (no model behaviour can make a fixed loop leave out the notice); `graders.test.mjs`
tests it. Cases measure behaviour, not how the code does it: a fix may take any form that makes the turn end as the case
says.

## The baseline

`baseline.json` records, for every case of every suite, whether it passes or is known to fail, and for a known failure
its failing checks and what they said. It is written by `--update-baseline` only: its `digest` covers its cases, and a
run fails when they disagree. A run fails when a case that passed fails, when a known failure fails a check it did not
fail before, or when a failing case is not in the baseline; a known failure that passes is reported, and the update
script records it. `--update-baseline` refuses to write down a regression unless it is named with
`--accept-regression <suite>/<case>`; it writes nothing, and exits 1, over a baseline edited by hand (which could have
marked a regression as known) or when a mutant passed what it targets, and it exits 1 when a case crashed.

The known failures recorded at 530031f are the harms the plan names: the step limit with an empty answer, a cut-off
answer with no notice, a request past the relay's 2,000,000 bytes by request 15, a plan-quota 429 that offers Retry, an
unknown upstream code that costs three upstream calls, a fabricated citation that ends ok and unflagged, three
research_inquiry verdicts certified by the agent ('accepted', 'strong'), and an identifier that reached the network.
The three inquiry adversaries pass since plan item P1.6: outcomes the agent classified no longer decide acceptance, and
an explanation with neither a claim kind nor a role is refused at open (INQ103).

## Cassettes

`cassettes/dev/<case>.json` holds what `streamChat` yielded for each request of a case, keyed by the request's place in
the turn and the sha256 of the tool results given to the model by then (times, durations and generated ids masked), with
the digests of the system prompt and the tool definitions and the pinned date the prompt states. Anything else that
differs makes the replay `cassette_stale`: neither a pass nor a fail, reported so the cassette is re-recorded. The
committed cassettes are recorded through the fault server's echo of the gold policies (`source: "echo"`); the recorder
refuses a case tagged `hidden`.
