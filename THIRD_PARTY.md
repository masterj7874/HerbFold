# Third-party software and data conditions

HerbFold's own source code is released under the Apache License 2.0 (`LICENSE`).
This file records what is **not** covered by that licence, because HerbFold
links to external engines and to public chemical resources that keep their own
terms. Nothing in this repository re-licences those materials.

Per the Journal of Cheminformatics software requirements, the HerbFold
application code, the evaluation scripts and the recorded evidence artifacts
under `docs/` are the openly licensed part. External model parameters, search
databases and provider credentials must be obtained by each user under the
provider's own conditions.

## Not included in this repository

| Component | Why it is absent | Where to obtain it |
| --- | --- | --- |
| AlphaFold 3 source and model parameters | Google DeepMind distributes AF3 code and weights under its own terms; the standard parameters are restricted to non-commercial use by non-commercial organisations and cannot be redistributed here | https://github.com/google-deepmind/alphafold3 |
| AF3 MSA and template databases (approx. 672 GB installed) | Size and per-database licences (BFD, UniRef, MGnify, UniProt, PDB seqres, PDB mmCIF, RFam, RNACentral, NT) | Installed with `scripts/install_af3_databases.py` from the official sources |
| HMMER 3.4 binaries | Built locally by `scripts/setup_hmmer.sh` | http://hmmer.org |
| IBM Quantum access | Requires a personal IBM Quantum account and token | https://quantum.ibm.com |
| OpenAI API access (`gpt-6-astra`) | Requires the user's own API key and model access | https://platform.openai.com |
| Downloaded public collections in `runtime/` | Bulk third-party records; HerbFold stores fetch URLs, licence labels and checksums instead of redistributing the corpora | COCONUT, LOTUS, ChEMBL, PubChem, Tox21, UniProt, PDB |

`runtime/`, `external/`, `tmp/`, `output/` and `research/` are excluded by
`.gitignore` for this reason.

## Public resources used, with the licence label HerbFold records

| Resource | Role in HerbFold | Licence / terms as recorded |
| --- | --- | --- |
| COCONUT | Natural-product structure collection | Open collection; per-record provenance and licence label stored with each import |
| LOTUS | Natural product to organism occurrence links | Open data (CC0 for the LOTUS-curated pairs); source URLs retained |
| ChEMBL | Approved-drug comparator set and Kd/Ki assay evidence | CC BY-SA 3.0 (EMBL-EBI) |
| PubChem | User-supplied compound resolution | Public domain records via NCBI terms |
| Tox21 | Toxicology assay labels used for model evaluation | US public data |
| UniProt | Protein target sequences (for example P35354) | CC BY 4.0 |
| RCSB PDB | Experimental reference structures such as 5IKR | Public domain (CC0) |
| NIKOM Korean medicinal-material index | Korean herb name to source-reported taxon bridges only | Content licence unspecified; HerbFold stores only name and taxon facts plus the source URL, and records `unspecified; factual reference index only` |

The NIKOM bridge deliberately imports no efficacy text, no monograph body and
no batch-composition assertion. See `docs/herb-aliases.md` and
`src/herbfold/herb_aliases.py`.

## Bundled third-party code

| File | Component | Licence |
| --- | --- | --- |
| `src/herbfold/static/vendor/3Dmol-min.js` | 3Dmol.js molecular viewer | BSD 3-Clause (`src/herbfold/static/vendor/3Dmol-LICENSE.txt`) |
| `src/herbfold/web/assets/*` | Compiled React 19 / Three.js / GSAP / Motion bundle | Build output of `frontend/`; upstream packages keep their own MIT/BSD/Apache terms, listed in `frontend/package.json` |

## Python dependencies

Declared in `pyproject.toml`. The scientific stack is RDKit (BSD 3-Clause),
NumPy and SciPy (BSD 3-Clause), scikit-learn (BSD 3-Clause), Biopython
(Biopython License / BSD-like), Qiskit and qiskit-ibm-runtime (Apache 2.0),
FastAPI, Uvicorn, httpx, Pydantic and python-dotenv (MIT). AiiDA, used only for
the portability control, is MIT.

## What this means for reuse

You may use, modify and redistribute HerbFold's own code under Apache 2.0. You
may not treat this repository as a licence for AlphaFold 3 weights, for the MSA
databases, or for the third-party corpora listed above. Offline replay of the
recorded evidence does not require any of the restricted components; see
`docs/offline-replay.md`.
