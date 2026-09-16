# AlphaFold 3 runtime readiness audit

Updated 2026-09-08 KST. **The official Google weights, complete sequence/template database installation, one full-human PTGS2 feature search, and separate aspirin/quercetin MSA-enabled inference jobs are complete and verified.** The selected outputs have pTM/ipTM **0.90/0.88** and **0.89/0.85**, respectively. Original inputs, executed feature inputs, cache/database provenance, all sampled outputs and the selected top copies were audited. These confidence and structural-agreement measurements do not establish ligand-pose accuracy, affinity, efficacy or safety. [Final Korean validation report](af3-msa-validation.md), [real MSA jobs](af3-msa-selected-predictions.json), [raw comparison](af3-msa-structure-comparison.json).

The initial audit read local source, parameter records, existing coordinates and primary documentation without launching inference. Subsequent official weight acquisition, configuration and actual job submission are documented below. The rejected archive and prior ibuprofen output remain preserved and excluded from prediction use. The independent arithmetic diagnostic is also identified below.

## Installed software and resources

| Item | Observed state |
| --- | --- |
| AF3 source | Tag **v3.0.4**, commit `85c4d20505fd5cef05eac22b534d4e793971ae69`; no tracked source modifications at audit |
| Installed AF3 package | 3.0.4 in the dedicated `external/alphafold3/.venv` |
| Numerical dependencies | JAX/jaxlib 0.10.2, Tokamax 0.0.12, Haiku 0.0.16, NumPy 2.4.1 |
| CUDA Python packages | Runtime 12.9.79, NVCC 12.9.86, cuBLAS 12.9.1.4, cuDNN 9.17.1.4 |
| GPU hardware | Two NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition cards, each 97,887 MiB total; driver 575.57.08 |
| Available VRAM snapshot | GPU 0: 10,324 MiB; GPU 1: 13,448 MiB. Existing vLLM workers use about 83,150 MiB on each card |
| Host memory snapshot | 251 GiB total, 139 GiB available; swap nearly full |
| Workspace storage snapshot | About 1.419 TB available; `/tmp` volume about 0.761 TB available |
| AF3 configuration | Native v3.0.4; `AF3_MODEL_DIR=/home/jerisuh/models/af3-google-20260604`; API restarted after configuration; GPU 1 selected; preallocation disabled |
| Full sequence/template databases | Installed and configured at `/media/jerisuh/8ddd80eb-6ff2-439e-bc5f-fa97b55fa26c/alphafold3_databases/v3.0`; all 9 components verified and readiness passed |
| Verified target-feature cache | Full 604-residue PTGS2; 11,229 unpaired and 17,801 paired rows including query, 4 templates; reused for separate quercetin inference |

The available-VRAM, host-memory and storage rows are historical pre-installation resource snapshots, not current free-capacity measurements or a memory reservation. Two GPUs do not automatically combine their free VRAM for one AF3 inference. The pinned release updates the numerical stack and adds CPU support; official performance measurements primarily validate A100/H100 configurations. [Official v3.0.4 release](https://github.com/google-deepmind/alphafold3/releases/tag/v3.0.4), [performance and supported hardware](https://github.com/google-deepmind/alphafold3/blob/v3.0.4/docs/performance.md)

The 9-item miniature database directory under upstream test data is present, as are HMMER tools. Those fixtures are for software tests and do not constitute a production MSA search database.

## Completed full-database and MSA validation

The official eight FASTAs plus the mmCIF tree were installed and independently checked at **2026-09-08 01:40:18 UTC**. Expanded content totals **672,435,030,468 bytes**, including **195,858 CIFs**; original compressed objects total 238,784,443,666 bytes. The active `AF3_DB_DIR` contains a completed installation manifest and passes backend readiness. Full installer and subsequent metadata/inventory verification scopes are distinguished in the [installation record](af3-full-msa-setup.md) and [machine-readable receipt](af3-full-database-installation.json).

The first real protein feature search took **452.12 seconds**. It produced 11,229 unpaired MSA rows and 17,801 paired rows, each including the full query. The corresponding non-query counts are 11,228 and 17,800; these are alignment rows, not Neff or counts of independent evolutionary observations. The four accepted template entry IDs are **1PXX, 3QH0, 3LN0 and 4RRX**. Quercetin reused the same verified feature content and source job; its execution steps contain only `inference`, with no repeated CPU search.

| Ligand | Completed MSA job | Inference stage | Top pTM / ipTM | Mean full-protein Cα pLDDT | Mean ligand heavy-atom pLDDT | 5IKR matched-Cα RMSD |
| --- | --- | ---: | --- | ---: | ---: | ---: |
| Aspirin | `b868a97038e144f8a605a722d911338b` | 167.84 s | 0.90 / 0.88 | 92.81 | 72.94 | 0.355 Å |
| Quercetin | `d80fe6aa1e6945538da31833a471d6ed` | 165.34 s | 0.89 / 0.85 | 92.84 | 65.84 | 0.339 Å |

Both use full 604-residue human P35354, the exact selected ligand (13 aspirin or 22 quercetin heavy atoms), the same Google model content, seed 1, five diffusion samples, ten recycles, cutoff 2021-09-30 and the same numerical settings as the earlier no-MSA baseline. All controlled comparison settings matched. Each sixth CIF is the top sample's copy: the MSA runs add **ten sampled structures**, and the preserved baseline adds ten more. Diffusion samples from one seed are not independent experiments.

The baseline top pTM/ipTM was 0.20/0.37 for aspirin and 0.21/0.32 for quercetin. Baseline Cα RMSD was **28.720 Å** and **30.306 Å**, respectively. Both conditions use the same **551 observed Cα positions** mapped by SIFTS from 5IKR chain A to human residues 19–569. Rigid fits exclude no outliers and apply no confidence filter. All five MSA sample RMSDs range from **0.327–0.371 Å** for aspirin and **0.330–0.374 Å** for quercetin. These are descriptive agreement values. Although 5IKR itself is absent from the four recorded templates, homologous templates and model training prevent treating this as an independent holdout test. 5IKR is also mefenamic-acid-bound, not a ligand-pose reference for either selected compound.

Both new selected outputs have `has_clash=false`, zero named short/long covalent-bond flags and `identity_verified_quality_unassessed` with `quality_pass=null`. The raw audit passed input-to-executed-`*_data.json` checks, feature/template content hashes and mappings, exact ligand/target identity, all five sample inventories, raw ranking selection and the molecularly identical top copy. Confidence gains and close protein agreement do not validate the aspirin/quercetin binding poses or drug activity. [Raw comparison and complete measurements](af3-msa-structure-comparison.json).

Software verification recorded **483 passing tests, zero failures/skips**, passing Ruff, wheel and frontend builds. These software checks and actual scientific-data audits have separate receipts; neither certifies therapeutic performance. [Software verification](af3-msa-software-verification.json).

## Official parameters now configured

The local search did not find another usable trained archive. The pinned official README provides a [direct Google download](https://storage.googleapis.com/alphafold3/af3.bin.zst). It was acquired over certificate-verified HTTPS and saved separately as `/home/jerisuh/models/af3-google-20260604/af3.bin.zst`. The response identifies a 2026-06-04 object version. The **1,020,545,840-byte** file matches Google's supplied CRC32C, and its complete SHA-256 is:

```text
74d0258616917cd122f5eab6d076afe4a8930e96823851e65e4f777dfb1f33ff
```

An independent CPU-only full decode checked all **405 records** against the published schema, including 368,384,538 parameter values. No nonfinite tensors or all-zero tensors were found. The identifier is nonzero and the aggregate distribution differs from the rejected uniform-random archive. Publisher provenance comes from the official-source acquisition receipt and transport/checksum checks, not from the identifier or distribution alone. [Acquisition and applied configuration](af3-google-weights-acquisition.json), [full-container validation](af3-google-weights-validation.json), [official acquisition instructions](https://github.com/google-deepmind/alphafold3/tree/v3.0.4#obtaining-model-parameters)

The regular bounded preflight still uses the label `unverified_parameters`: its prefix check does not itself perform publisher authentication or full-container validation. The separate receipts above supply the additional evidence for this specific file. Its fingerprint is recorded in preparation and rechecked before execution.

The API was restarted after configuring the official model and full database directories; both modes pass their respective readiness checks. The earlier blocked search job is retained as history. **Preserved no-MSA baseline:** aspirin job `88692f7955d94c50814f6e3121f963b6` and quercetin job `3730181a45734f0dafa92a78917c48dd` completed separately with the full 604-residue P35354 target, seed 1, five diffusion samples and ten recycles. Both explicitly omitted MSAs and templates. The stored sixth model file in each job is a copy of its top-ranked sample, so the total is **ten sampled structures**, not twelve independent predictions.

| No-MSA baseline ligand | Recorded execution time | Selected output pTM / ipTM | Exact target/ligand identity | Baseline output check |
| --- | --- | --- | --- | --- |
| Aspirin | 164.59 s | 0.20 / 0.37 | Passed; 13 ligand heavy atoms | 0 long / 0 short bonds; overall quality unassessed |
| Quercetin | 162.59 s | 0.21 / 0.32 | Passed; 22 ligand heavy atoms | 0 long / 0 short bonds; overall quality unassessed |

Both baseline processes exited zero, and their selected-output summaries report `has_clash=false`. The initial 34 long-bond warnings per structure came from a **viewer/checker defect**, not from stretched bonds: RDKit's isoleucine template connected `CG2–CD1`, whereas the official ILE atom names require `CG1–CD1`. The fallback named graph now follows wwPDB. No original atom name or coordinate was changed. Recomputed maximum covalent distances are 1.827065 Å for aspirin and 1.830096 Å for quercetin, with no short/long flags. The old validation receipts remain archived, all model file hashes are unchanged, and inference was not rerun. [Official ILE chemical component](https://files.rcsb.org/ligands/download/ILE.cif), [correction and unchanged-file evidence](af3-ile-template-correction.json)

An independent comparison against official CCD records for all 20 standard residues found ILE to be the only named heavy-atom connectivity mismatch. ARG has a separate single/double bond-order difference between RDKit's neutral representation and CCD's protonated guanidinium; its connectivity agrees, and that representation was not changed by this repair. [Standard-residue comparison](af3-standard-residue-template-audit.json)

Correcting that detector error does not validate the global protein fold or ligand pose. The low baseline pTM/ipTM values remain preserved, and overall quality is unassessed (`quality_pass=null`, rather than a quality-pass claim). Neither result establishes affinity, efficacy or safety. [Actual execution and output validation](af3-trained-selected-predictions.json)

## Rejected previous parameter container

The configured local file at the time of inspection was `/home/jerisuh/models/af3.bin.zst`, size **1,007,928,004 bytes**, modification timestamp 2026-02-09 11:00:56 UTC. Its SHA-256 is:

```text
cec2df7fb03948b277109b0ae0faf4ddd007501a896d2a6c5444a56221242dd6
```

CPU-only streaming inspection found:

- 405 well-formed records: one metadata record and 404 parameter tensors.
- 368,384,538 numeric parameter values; supported float32/bfloat16 layouts, no nonfinite values.
- The 64-byte `__meta__:__identifier__` is entirely zero.
- Every nonmetadata tensor stays within [-1, 1]. Across 1,034,458 sampled values, all ten equal-width histogram bins contain about 103,000 values.
- Median tensor sample standard deviation is 0.577081, consistent with uniform [-1, 1] sampling; layer-normalization scales have roughly half negative values.

The official parameter-schema documentation explicitly describes generating random performance-test parameters with a zero identifier and uniformly sampled tensors. The local aggregate pattern closely matches that recipe. This is strong evidence of test parameters and explains why a valid container and a successful process exit are insufficient. It does not identify who created the file, prove its complete acquisition history, or provide an authenticated publisher checksum. [Official parameter schema and random-parameter example](https://github.com/google-deepmind/alphafold3/blob/v3.0.4/docs/model_parameters.md)

No tensor values or identifier bytes were emitted. Aggregate receipts are `tmp/af3-parameter-container-audit.json` and `tmp/af3-parameter-distribution-audit.json`. Genuine trained parameters must come through the publisher's authorized distribution path; a filename such as `af3.bin.zst` does not authenticate them. [Official model-parameter acquisition instructions](https://github.com/google-deepmind/alphafold3/tree/v3.0.4#obtaining-model-parameters)

The [parameter inspection helper](../src/herbfold/af3_parameters.py) returns `status=test_parameters`, `runnable=false` for this previous file. It checks a bounded prefix using an isolated CPU-only interpreter, enforces a 15-second subprocess timeout, a 32 MiB decoded/input bound, and a 32 MiB decompression window. It recognizes upstream model-selection precedence and split segments. Because pinned upstream has a filename-regex defect for `.bin.N`, that layout is explicitly rejected with guidance to use `.N.bin`.

The verdict is cached by selected paths, device/inode, sizes, nanosecond modification/change timestamps, and interpreter identity. A canonical stat fingerprint is available to bind preparation to later execution. Initial inspection of the previous file took about 0.042 seconds; an unchanged cached check took about 0.00025 seconds. Seventeen targeted tests passed, including compressed split-frame decoding, replacement invalidation, CPU isolation, timeout, truncated records and oversized-window rejection. A nonzero identifier is reported as **unverified**, never as authenticated Google origin or a fully validated parameter archive.

A library documentation discrepancy was checked experimentally: installed `zstandard 0.25.0` cext interprets `max_window_size` in **bytes**, despite the Python documentation calling it KiB. Setting 32,768 rejects a 64 KiB frame; 33,554,432 permits it. A real streaming frame advertising a 64 MiB window is rejected by the helper's 32 MiB limit. The installed cffi implementation likewise passes the value directly to `ZSTD_DCtx_setMaxWindowSize`. [Python-zstandard documentation](https://python-zstandard.readthedocs.io/en/latest/decompressor.html)

## Existing ibuprofen output and the independent GPU defect

The previous full-human PTGS2 smoke job used 604 residues plus ibuprofen, 619 tokens padded to bucket 768, seed 1, one diffusion sample, three recycles, empty MSAs/templates, and Triton attention. The process exited zero after 166.16 seconds, but its existing report records pTM/ipTM 0.39, clash=true and ranking score -99.11.

Direct inspection of the original mmCIF confirms an underlying coordinate failure:

| Covalent backbone pair | Pairs inspected | Median distance | Outside 0.9–2.1 Å |
| --- | ---: | ---: | ---: |
| N–CA | 604 | 31.69 Å | 604 |
| CA–C | 604 | 29.32 Å | 603 |
| C–O | 604 | 29.19 Å | 604 |

The mmCIF contains all 604 residues and 4,880 finite-coordinate atoms. These measurements use raw atom names and coordinates, independently of the viewer or its bond construction. This output is unusable; it must not be described merely as a plausible low-confidence pose. Receipt: `tmp/af3-prior-smoke-geometry-audit.json`; original artifact hash `6fc95b98048cb1ad2e62bb1ea20783a0cdd00b0cc21c0eea663e42b7a2f06a4a`.

Separately, the root agent reproduced a **JAX GPU arithmetic defect** on this workstation: a small compiled linear-layer scaling calculation disagreed with its eager counterpart by up to 0.11914. With `--xla_gpu_autotune_level=3`, all four checks passed and the largest discrepancy was below 6e-7. That is a measured numerical workaround, not proof of a successful AF3 fold. [Local arithmetic verification](af3-numerics-verification.json), [upstream JAX issue 39336 and reproducer](https://github.com/jax-ml/jax/issues/39336)

The upstream AF3 known-issues warning about severely clashing V100 output concerns capability 7.x GPUs, not these capability 12.0 Blackwell cards; its workaround must not be assumed to diagnose this machine. Both parameter provenance and numerical correctness need their own checks. The precise contribution of each problem to the previous malformed pose has not been isolated. [AF3 known issues](https://github.com/google-deepmind/alphafold3/blob/v3.0.4/docs/known_issues.md)

## Practical route to separate genuine predictions

1. **Official parameter acquisition and full-container validation are complete.** The active directory is `/home/jerisuh/models/af3-google-20260604`. Keep the rejected file and old output labeled as test/unusable evidence; they must not be reused for inference.
2. **Retain the measured numerical workaround in the AF3 child process.** The project setting is `AF3_XLA_FLAGS=--xla_gpu_autotune_level=3`, mapped to child `XLA_FLAGS` and recorded in execution provenance. Keep `AF3_PREALLOCATE=false` and the existing child-only CUDA library isolation. A new environment/cache should requalify the arithmetic diagnostic and an upstream known-structure inference test before claims of scientific readiness.
3. **Use distinct jobs for distinct molecules.** Aspirin and quercetin have now completed separate jobs with full P35354 chain A and one exact canonical ligand in chain B. Their identities match the requested inputs; global fold and pose quality remain unvalidated. Preserve the SMILES, the full 604-residue sequence hash, parameter fingerprint, seed and output file hashes. Neither job is an ibuprofen rerun or a relabeled experimental complex.
4. **Keep the standard and exploratory conditions explicit.** Current validated standard execution is native v3.0.4 with the installed full DBs, `modelSeeds: [1]`, five samples, ten recycles and the fixed numerical environment. The preserved exploratory baseline explicitly used empty MSAs, `templates: []` and `--run_data_pipeline=false`. The new standard jobs first searched or reused validated features; skipping the pipeline at their inference stage does not make them MSA-free.
5. **Recheck capacity and validate every result.** Current free VRAM is not enough evidence that five samples fit. Run jobs sequentially; record any OOM as failure. If a separately declared lower-memory configuration is needed, several sequential single-sample seeds are an option, with their changed sampling settings recorded. Do not truncate the human target or substitute another protein. Require exact observed ligand/target identity, plausible covalent geometry and confidence/clash review before releasing a pose as usable. Confidence alone is not affinity or efficacy.
6. **The studio implements one verified target-feature search followed by ligand-specific inference.** A single-protein `search` job runs `--run_data_pipeline=true --run_inference=false` on CPU, validates its completed `*_data.json`, then constructs an internal input with the selected ligand and runs `--run_data_pipeline=false --run_inference=true`. The completed quercetin job reused the aspirin search data under identical target/search provenance. Original request inputs remain immutable. Shared cache reuse applies to the studio/AF3Queue API; the analysis orchestrator's synchronous runner and multi-protein legacy inputs retain the combined full-DB pipeline. The public adapter still rejects arbitrary custom MSA/file paths; only the internal worker may enrich its inference input. [Official staged pipeline and feature reuse](https://github.com/google-deepmind/alphafold3/blob/v3.0.4/docs/performance.md), [input format](https://github.com/google-deepmind/alphafold3/blob/v3.0.4/docs/input.md)

The full database installation and actual MSA search now have separate completed download, installation and execution receipts linked above. The rules below describe how subsequent jobs preserve that verification. The upstream launcher constructs all configured database paths before running its data pipeline, including RNA database paths even for a protein-only input; a partial download must therefore be planned against the actual launcher rather than advertised as ready. The existing miniature fixtures must not be substituted. [Official installation guide](https://github.com/google-deepmind/alphafold3/blob/v3.0.4/docs/installation.md), [database fetch script](https://github.com/google-deepmind/alphafold3/blob/v3.0.4/fetch_databases.sh)

## Verified DB and protein-feature contract

`AF3_DB_DIR/official_databases_manifest.json` must be schema 1, `status=complete`, and bound to the pinned v3.0.4 commit. All eight expanded FASTAs, the mmCIF tree and its inventory must be present. Each component requires a positive verified record count, expanded content SHA-256/size, Google download CRC32C and MD5 verification, and final size/device/inode/mtime/ctime metadata. The installer performs full content checks; request-time inspection validates the receipt and recorded metadata, not a repeated full 672.435 GB expanded-content scan. Nested mmCIF files are covered by the installer's inventory receipt rather than rescanned at each preparation. Keep the installation immutable and revalidate it after changes. [Completed installation receipt](af3-full-database-installation.json), [readiness implementation](../src/herbfold/af3_databases.py).

The durable protein-feature identity includes the full sequence SHA-256, verified DB manifest/file-state fingerprint, AF3 version/commit and entrypoint or immutable Docker image, HMMER binary metadata, template cutoff, and feature schema. It excludes ligand identity and sampling seeds because each ligand/seed remains in its own inference job. Cache reuse requires a completed atomic manifest, matching feature SHA-256, exact query identity, valid alignment lengths and observed template coordinates/mappings. Empty or partial MSA fields are rejected. A real completed query-only search or zero accepted templates is recorded with explicit warnings, never relabeled as successful homolog evidence.

The native data child is isolated with `CUDA_VISIBLE_DEVICES=` and `JAX_PLATFORMS=cpu`. Docker data execution omits `--gpus` and receives the same CPU environment inside the container; its inference child receives only the documented GPU/XLA settings. The default five samples, ten recycles and 2021-09-30 template cutoff remain explicit. The shared AF3 execution lock covers both stages and is inherited by native children. A server restart preserves interrupted work without automatic repetition. An explicit retry uses already completed verified protein features; partial feature output never counts as a reusable cache. A final parameter/settings check after CPU search prevents an unnoticed model replacement before inference.

`stage` and `msa_features` are persisted in prediction responses. Feature metadata records measured query-inclusive unpaired/paired counts, unpaired and paired non-query counts, accepted templates, cache source job, source execution time, and hashes. Each template records its observed entry ID/data block, mmCIF SHA and actual query/template residue mappings. These support checking whether a later experimental reference appeared among search templates. Missing measurements remain null.

Search-vs-none comparisons must hold target, selected ligand, model files, seeds, sample/recycle settings and cutoff constant, and inspect both observed identity and coordinates. A confidence increase is not measured pose accuracy, binding affinity or efficacy. Reference comparisons against a structure included in template search are not an independent holdout assessment. The earlier exploratory aspirin/quercetin jobs remain explicitly labeled no-MSA baseline records. The completed standard jobs and the raw comparison above provide measured confidence/agreement observations, not independent accuracy certification.

Synthetic CPU subprocess tests exercise separate data/inference stages, ligand preservation, one-search reuse, restart recovery, corrupt-cache replacement, invalid query rejection and database/model changes during search. A CPU-only serialization check using the actual pinned upstream parser and the existing 5IKT chain A verified that its 551-residue mmCIF and mapping pass the new template validator. It used existing experimental coordinates and performed no MSA search or AF3 inference. [Feature implementation](../src/herbfold/af3_features.py), [execution worker](../src/herbfold/af3_execution.py), [operating workflow](af3-calculation-workflow.md).
