# HerbFold 2: durable Astra research agents

The new `/api/analyses` workflow runs a real **`gpt-6-astra`** Responses API orchestration by default. It has no alternate-model fallback. Set `OPENAI_API_KEY` on the server. The key is never sent to the browser, included in model context or written to analysis artifacts. Requests use the fixed official `https://api.openai.com/v1` endpoint; this module deliberately does not forward credentials to an arbitrary base URL.

Official implementation references, checked 2026-09-07:

- [GPT-6 Astra model and capabilities](https://developers.openai.com/api/docs/models/gpt-6-astra)
- [Responses Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Function calling and application-owned tool execution](https://developers.openai.com/api/docs/guides/function-calling)

`GET /api/llm/status` checks the exact model's availability via a read-only model request, cached for 60 seconds. This does not consume a generation or prove a future generation will succeed. For this installation an actual strict-schema smoke request succeeded on 2026-09-07: response `resp_0ef860f5f8aabf45016a9e78d5d6fc87d0ad15e9de27886885`, model `gpt-6-astra`, 390 input / 49 output tokens. This was a connectivity/schema test, not a scientific validation.

## Agent graph and actual decisions

The coordinator chooses a subset of the user's verified catalog parents, retaining both herbal and drug categories, and selects a candidate policy (`balanced`, `qed`, `low_alerts`, `diversity`). It routes the optional source refresh, AF3 preparation and quantum branches. Its choices are validated against the supplied IDs and the allowed tool list; the LLM cannot add new providers, models, execution commands or budgets.

The evidence and chemistry specialists execute concurrently after planning. Evidence reads bounded PubChem and, for a supplied database accession, UniProt or ChEMBL records using the existing fixed-host connectors. Every record identifies its source and whether it is a live fetch or catalog snapshot. Conflicting PubChem identity stops candidate design. Failed requests remain explicit gaps. A supplied protein construct may differ from the canonical UniProt sequence; the input remains the supplied construct and the difference is recorded.

The chemistry specialist interprets actual RDKit descriptors, interference alerts, stereochemistry and Morgan Tanimoto comparisons. The design specialist receives only valid generated candidates, ranks/selects their IDs for the stated objective, and passes that ordered shortlist to the execution stages. Candidate structures themselves come from bounded cross-category BRICS enumeration, preserving verified herbal and drug fragment ancestry. The model never supplies an unchecked SMILES string for execution.

AF3 preparation and quantum execution use a deterministic dispatcher, followed by a separately prompted critical reviewer and report author. Six model calls normally occur: coordinator, evidence, chemistry, candidate design, review, report. The structure and quantum agents are deterministic scientific-engine workers; the interface does not claim that those workers independently called a model. Each role sees an explicit stage contract: tools already run by the application, its own assigned assessment, and later workers that will execute only after it returns. Empty tool lists for audit roles and missing future artifacts are expected. Neither specialist sees the other's unfinished output. Versioned role prompts preserve completed stages; an explicitly resumed stopped stage with an older prompt is re-evaluated through the actual model, retaining its old decision in `agent_history`, without silently overriding a scientific stop.

A `stop` decision in planning, evidence, chemistry or design blocks downstream dispatch. A review stop becomes `verdict: needs_revision`, retained in the final report. Missing measured affinity is always represented as missing; it is never derived from an LLM statement, QED, AF3 confidence or a quantum kernel.

## API

```json
{
  "goal": "퀘르세틴과 아스피린을 비교하고 물성 및 간섭 경고를 고려한 후보를 설계하세요.",
  "compound_ids": ["quercetin", "aspirin"],
  "target_id": "P35354",
  "protein_sequence": "",
  "max_candidates": 6,
  "mode": "astra",
  "run_af3": false,
  "msa_mode": "search",
  "quantum_mode": "local",
  "budgets": {
    "max_llm_calls": 8,
    "max_output_tokens": 1800,
    "max_candidates": 12,
    "max_af3_candidates": 1,
    "max_source_requests": 8,
    "max_qpu_jobs": 1,
    "max_qpu_shots": 4096,
    "max_qpu_seconds": 30,
    "af3_timeout_seconds": 3600
  }
}
```

| Method and path | Behavior |
| --- | --- |
| `POST /api/analyses` | Validate, create durable run, return HTTP 202 immediately |
| `GET /api/analyses` | `{analyses: [...]}` for the latest 100 runs |
| `GET /api/analyses/{id}` | Full state, stage checkpoints, events, usage and results |
| `GET /api/analyses/{id}/events?after=0` | Events after the cursor plus `next_cursor` |
| `POST /api/analyses/{id}/cancel` | Persist a stop request and stop further dispatch |
| `POST /api/analyses/{id}/resume` | Resume a stopped run from checkpoints; HTTP 409 if active or exhausted |
| `POST /api/analyses/{id}/quantum/refresh` | Retrieve existing IBM job IDs only; update artifacts, flag prior review/report stale if execution changed |
| `GET /api/llm/status` | Read-only exact-model readiness; `refresh=true` bypasses cache |

Run states are `queued`, `running`, `completed`, `blocked`, `failed`, `cancelled`, `interrupted`. Stages expose their ID, dependency IDs, status, timestamps, machine result and artifact link/hash. Events expose the stage, summary, response ID and token usage; they do not contain private model reasoning. Final results contain `comparisons`, `candidates`, `af3_jobs`, `quantum`, `evidence`, `review`, `report`, `affinity`, `model` and `llm_used`.

`compounds` may replace `compound_ids` (never provide both). It accepts 2–8 objects with `id`, `smiles`, and an explicit `category: herbal | drug`; bounded name, PubChem CID (`pubchem_cid` or `cid`) and source fields are optional. The server validates the chemical structure and recomputes descriptors. Catalog-identical ID/structure/category triples retain catalog provenance; all other supplied identities/categories are `user_provided_unverified` until a recorded fixed-host source check establishes chemical identity. Supplied descriptors or UI metadata are never treated as scientific results. Missing source URLs remain missing, and no botanical occurrence is inferred from an imported label.

`mode: local` is a deliberately selected offline deterministic workflow: it does not call an LLM or refresh public sources, identifies `llm_used: false`, and does not silently replace an unavailable Astra analysis. Selecting `quantum_mode: ibm` still explicitly requests IBM hardware even in local orchestration mode; use `quantum_mode: local` or `off` for a completely offline run. Native AF3 inference similarly requires `run_af3: true` and configured local parameters/runtime.

## Budgets, execution and recovery

Model calls are reserved atomically before dispatch and are never automatically retried. `max_output_tokens` applies to each response, including reasoning tokens; the maximum response-token reservation is `max_llm_calls * max_output_tokens`. Actual input/output usage and response IDs are recorded when available. A network disconnect can make actual provider usage unknown; there is no invented usage or billing figure. An incomplete response blocks transparently, with the actual returned usage retained. New attempts require explicit resume and consume the remaining call budget.

The run stores its state in a separate SQLite table in the workstation's existing `jobs.sqlite3`. SQLite transactions protect parallel event/usage updates. Advisory filesystem locks prevent two workers from running the same analysis; a second process leaves an actively locked run alone. Startup marks abandoned runs interrupted. Completed scientific tool checkpoints and completed model decisions are reused on resume, including when cancellation arrived during a model call.

AF3 jobs are persisted before execution and limited to the ordered shortlist budget. `run_af3: false` prepares reproducible input/manifest only; it does not produce a predicted complex. Default `msa_mode: search` requires the official databases. Explicit `none` records MSA-free/template-free inputs and their accuracy limitation. A shared AF3 execution lock serializes analyses. Native commands come only from the configured, validated AF3 runner, with `shell=False`; model text is never executed. Timeout/cancellation terminates the launched process group. Any job already marked running, interrupted, failed or cancelled is **not** automatically run a second time, even if the worker crashed before saving its PID. Such output must be inspected or a new run created.

The local quantum branch computes an exact 4-qubit statevector fidelity kernel on explicit normalized descriptor features. This is a molecular-descriptor kernel, not a quantum simulation of electron structure or a binding-affinity estimator. IBM mode selects `qubits: max`, meaning the maximum accessible operational device's usable physical width, with at most four samples, 16 circuits and the request's QPU budget. It records a durable intent before submitting and relies on the existing exclusive manifest/job-ID journal. A partial or uncertain submission is never submitted again on resume. Existing manifests are retrieved only. Pending IBM jobs remain pending in the report; a submitted job is not reported as a measurement. `POST /api/analyses/{id}/quantum/refresh` retrieves saved job IDs without submitting or calling an LLM. If hardware state changes, `review.stale` and `report.stale` plus their `stale_reason` identify the original interpretation as outdated; the refreshed quantum artifact is the current observation. Both local and IBM artifacts preserve the descriptor columns, scaling divisors and exact vectors. Cancellation does not erase or claim to cancel previously submitted IBM jobs.

Source snapshots and computational outputs establish neither target-specific efficacy nor chemical patent novelty. Existing benchmark/model APIs support explicitly curated measured Kd/Ki evaluation; this discovery DAG does not automatically pool heterogeneous assays or manufacture affinity labels. The exported candidate shortlist remains a set of unvalidated computational hypotheses requiring chemical, biochemical and independent experimental assessment.

## Verification

`pytest tests/test_orchestration.py` exercises the offline end-to-end DAG, genuine specialist concurrency with a test barrier, planner parent selection, design shortlist control, exact-model failure, token-call budget races, cancellation/resume, restart recovery, uncertain QPU submission prevention, native AF3 no-repeat behavior, preparation-only mode, source conflicts, exact Responses schema and forbidden-tool/model rejection, the polling API, cancellation during IBM preflight, retrieval-only hardware refresh, and explicit re-evaluation of old stopped prompts with preserved history. Test doubles are isolated in the test module and cannot be selected through the application API.
