# Offline replay: what a third party can reproduce without AF3, a QPU or network

This page exists because AlphaFold 3 inference, IBM Quantum acquisition and the
672 GB MSA/template installation cannot be redistributed (see `THIRD_PARTY.md`).
Everything listed under "Tier 1" runs from this repository alone, on CPU, with
no network access, no GPU, no API key and no provider account.

Commands were executed on 2026-09-16 on Linux 6.14 with Python 3.12.3 and
`uv 0.8.11`.

## Prerequisite: do not inherit a local `.env`

`src/herbfold/config.py` calls `load_dotenv()` at import time, so a workstation
`.env` (for example `HERBFOLD_ALLOWED_HOSTS=*` or populated `AF3_*` paths)
changes test expectations and server behaviour. Replay in a clean environment:

```bash
git clone https://github.com/masterj7874/HerbFold.git
cd HerbFold
uv sync --locked --extra dev
export HERBFOLD_DATA_DIR="$(mktemp -d)"   # keep runtime state out of the repo
# do NOT create .env for replay
```

With a workstation `.env` present, four host-policy and artifact-path tests fail
for environment reasons rather than code reasons. Without it, the suite is
clean. This is the single most common replay error.

## Tier 1 — reproducible from this repository only

| Check | Command | Observed on 2026-09-16 |
| --- | --- | --- |
| Full test suite | `uv run pytest -q` | 769 passed, 2 warnings, 48.04 s |
| Lint, application and tests | `uv run ruff check src tests` | All checks passed |
| Offline scientific demo | `uv run herbfold demo --output "$HERBFOLD_DATA_DIR/demo.json"` | `8 candidate structures`; writes real RDKit Morgan/Tanimoto comparison, BRICS candidates and a 4-qubit statevector kernel |

The demo performs actual computation: a Morgan radius-2, 2048-bit,
chirality-aware Tanimoto comparison, BRICS fragment recombination with parent
lineage, and an exact 4-qubit statevector kernel. It submits no GPU inference
and no QPU job.

`uv run ruff check scripts` reports 577 findings, all of them E701/E702
multiple-statements-per-line style in the manuscript figure and document
generators. They are style findings under the configured rule set, not
application defects; the application package and the tests are clean.

## Tier 2 — reproducible from this repository plus recorded artifacts

The evidence records that the manuscript cites are committed under `docs/`
(for example `docs/af3-msa-validation.md`,
`docs/af3-msa-structure-comparison.json`,
`docs/quantum-projected-verification.json`,
`docs/validation-results.json`, `docs/discovery-verification.json`). These are
the stored outputs of the historical runs, with seeds, versions and file
hashes. They allow an inspector to re-derive the reported aggregates and to
re-run the identity, evidence-state and accounting logic against the same
inputs. They do not re-execute AF3 or the QPU.

## Tier 3 — requires the restricted external components

| Capability | What the third party must supply |
| --- | --- |
| Fresh AF3 inference | AF3 source and parameters under Google DeepMind's terms, a CUDA GPU, and the installed MSA/template databases |
| Full MSA and template search | Roughly 672 GB installed databases plus HMMER 3.4 |
| IBM Quantum acquisition | Personal IBM Quantum account and token |
| `gpt-6-astra` agent analysis | The user's own OpenAI API key with access to that model |
| `uv run herbfold doctor` device listing | Network access and stored IBM credentials (read-only query) |

Absence of these does not block Tier 1 or Tier 2. The linkage, evidence-state
and reporting behaviour that this software claims as its contribution is
verifiable in Tier 1 and Tier 2.

## Scope

This page reports commands that were run and their observed output. It does not
claim that a fresh AF3 prediction, a new QPU acquisition or any assay outcome
can be reproduced from this repository, and it does not establish predictive
accuracy, binding affinity, efficacy or safety for any compound.
