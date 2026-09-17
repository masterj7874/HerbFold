# English and Korean interface

The sidebar's **EN / KO** control changes the displayed interface language. Korean is the default when no valid preference is saved. Selecting EN uses the reviewed local English catalogs; selecting KO displays the original Korean messages. This is an interface preference, not a request to translate research data or rerun an analysis.

## Preference and rendering

The browser stores `en` or `ko` under the local-storage key `herbfold.language.v1`. The legacy view at `/legacy` carries the same control and reads the same key, so one preference drives both interfaces. The preference is restored on page load. A storage event synchronizes the preference between tabs on the same origin. If browser storage is unavailable, switching still works for the current page session. The code also sets the document's `lang` attribute and updates the buttons' pressed states and accessible labels.

[LanguageSwitcher.tsx](../frontend/src/components/LanguageSwitcher.tsx) calls `setLanguage()`. [useLanguage.ts](../frontend/src/lib/useLanguage.ts) subscribes through React's `useSyncExternalStore`, and the application rerenders its existing view. The language is not used as a component key, so switching languages does not intentionally reset the selected workspace, molecule, saved workflow or form state.

[i18n.ts](../frontend/src/lib/i18n.ts) translates at React display boundaries. It reads `en-research.json`, `en-design.json`, `en-core.json`, `en-backend.json` and `en-extra.json`; later catalogs override earlier entries with the same key. Keys are matched after whitespace normalization and supported HTML-entity decoding. Templates use numbered placeholders such as `{0}` and `{1}` so English can reorder displayed values without modifying their source records. Unmatched messages remain in their original language. There is no online translation service or LLM translation request.

Components using `localeCode()` format dates and numbers with `en-US` or `ko-KR`. This changes formatting, not the stored values, timestamps, assay units or scientific interpretation.

## Research data and inputs

The display translator is separate from form values, event handlers, API request bodies and file exports. SMILES inputs, constituent names entered into forms, CSV rows, assay conditions, source references, target accessions, job IDs and model seeds keep their original values. API objects and numeric values are not rewritten by `tr()`. Raw JSON views and downloads retain the underlying records.

Assay labels, explanations and interface headings can be shown in English while the corresponding source data remain unchanged. A valid value of `0` remains zero; an inactive label remains inactive; unavailable or withheld results do not become predictions. Translation does not convert AF3 confidence into affinity, a Tox21 activity score into human safety, or a generated candidate into an experimentally validated drug.

Changing the language does not itself submit AF3, LLM, QPU, design or assay calculations. Existing status polling may continue according to the selected workspace's normal behavior.

## Catalogs and coverage

| Catalog | Entries | Covers |
| --- | --- | --- |
| `frontend/src/i18n/en-core.json` | 894 | Shell, AlphaFold Studio, molecular viewer, compound library, AF3 calculation and workflow panels |
| `frontend/src/i18n/en-research.json` | 540 | Quantum Studio, walkthrough, agent analysis, measured data, research archive |
| `frontend/src/i18n/en-design.json` | 723 | Drug Design Pipeline, combination assay, large-scale discovery, performance and bioactivity |
| `frontend/src/i18n/en-backend.json` | 112 | Server-produced display strings: AF3 stage labels and activities, UniProt target errors, structure-selection notes, agent stage names |
| `frontend/src/i18n/en-extra.json` | 40 | Composed labels that override an earlier catalog entry |

`en-backend.json` covers text the API returns for display, such as the AF3 progress stage `단백질 서열 검색` shown as `Protein sequence search`, target-registration errors from UniProt, and the reasons a saved structure cannot be shown. The Python modules keep their original strings; only the display layer substitutes English, so stored job records and API responses are unchanged.

The legacy compatibility view at `/legacy` carries its own dictionary of 182 entries inside `src/herbfold/static/app.js`, since that page is plain HTML and JavaScript. It reads and writes the same `herbfold.language.v1` preference, so switching in either interface applies to both. Its compound cards lead with the recorded English name in English mode and the recorded Korean name in Korean mode; both recorded names stay on the card and neither record is modified.

## Research values that stay Korean

Interface text is translated; recorded values are not. In English mode the remaining Korean text on screen is research data, and the components that render it mark the boundary with `data-research-value`:

| Value | Example seen during verification | Marked as |
| --- | --- | --- |
| Saved design-run name entered by the researcher | `하이브리드_20260917`, `기능 확인 · 하이브리드 설계` | `run-name` |
| Generation-campaign name | `감초속 성분 × ChEMBL 약물 확장 탐색` | `campaign-name` |
| Text typed into a field | `황금` in a search box | `researcher-input` |
| Korean medicinal-herb and constituent names from the records | `황금`, `퀘르세틴` in source notes | catalog and alias data |

The verification script separates these from untranslated interface text and reports them under `research_values`, so a record written in Korean never counts as a translation gap and is never rewritten.

## Verification run — 2026-09-17

`scripts/verify_interface_language.py` drives the running local server with an isolated Chromium profile, blocks every non-GET API call except side-effect-free local molecular computations, and writes [docs/interface-language-verification.json](interface-language-verification.json).

| Check | Result |
| --- | --- |
| Views inspected in English (9 workspaces desktop, 9 at 390 px, shell, legacy) | 20 |
| Untranslated interface text | 0 |
| Research values kept in their original language | 20 |
| Views with horizontal overflow | 0 |
| Page errors | 0 |
| Requests caused by switching language | 0 |
| API calls blocked during the run | `/api/benchmark/evaluate`, `/api/data/audit`, `/api/data/structures`, `/api/models/train` |
| Local computations allowed | `/api/compare/structures`, `/api/molecular/resolve`, `/api/molecules/describe`, `/api/molecules/svg` |

State preserved across EN → KO → EN:

- **AlphaFold Studio** — calculation mode `No MSA`, the exploratory acknowledgement, seed `0`, the same WebGL canvas node, and an unchanged `herbfold.studio.selection.v1` entry.
- **Agent Analysis** — saved workflow `b86a39c482db4ad1b1c1c0172c77a76e` stayed selected with its linked AF3 job `6f07b8942cb04005afe1388aae7ddbc9`.
- **Drug Design Pipeline** — a researcher-entered name containing Korean text, a SMILES string and a zero (`연구 입력 · C[C@H](O)F · 0`) came back byte-identical, and the candidate list was unchanged.
- **Quantum Studio** — the selected run record kept its `data-record-id`.
- **Preference** — restored after a reload, synchronized to a second tab of the same origin, and still switchable when `localStorage` access throws.
- **Legacy view** — English title `HerbFold · AlphaFold 3`, Korean restored on KO, round trip back to English, and the AF3 experiment name and MSA mode preserved across the switch.

Four pytest cases (`tests/test_storage.py::test_api_token_and_cross_origin_writes`, `test_invalid_request_numbers_never_become_artifact_values`, `tests/test_af3_studio.py::test_api_reconnects_selected_identity_and_bounds_log`, `tests/test_molecular.py::test_real_import_api_scene_uses_registered_flat_output_and_exact_ligand_graph`) fail on this workstation because `load_dotenv()` injects the local `.env` values `HERBFOLD_API_TOKEN` and `HERBFOLD_ALLOWED_HOSTS=*` into the test process. They pass with the documented defaults and are unrelated to the interface language.

## English screenshots in the README

All README screenshots were captured from this English interface against saved local records, recorded in [docs/readme-capture-report.json](readme-capture-report.json). Reopening a saved record does not resubmit it.

| Image | View | Interpretation retained |
| --- | --- | --- |
| `readme-language-switch-en.png` | Header language control | An interface preference, not a research setting |
| `readme-alphafold-studio-en.png` | AlphaFold Studio | A completed prediction is not validated accuracy, affinity, efficacy or safety |
| `readme-agent-workflow-en.png`, `readme-agent-workflow-stages-en.png` | Agent Analysis → AlphaFold Workflow | Stage records are execution facts, separate from interpretation |
| `readme-design-pipeline-en.png`, `readme-design-pipeline-agents-en.png` | Drug Design Pipeline | Generated candidates are computational proposals |
| `readme-compound-comparison-en.png` | Compound Comparison | Structural similarity is not efficacy or interchangeability |
| `readme-quantum-studio-en.png`, `readme-quantum-walkthrough-en.png` | Quantum Studio | Measured observables without any claim of quantum advantage |
| `readme-discovery-en.png` | Large-scale Discovery | Collected structures are unvalidated research candidates |
| `readme-validation-scale-en.png`, `readme-validation-tox21-en.png` | Performance and Bioactivity | Attempts, retained structures and assay activity kept distinct from safety |
| `readme-mobile-alphafold-en.png`, `readme-mobile-agents-en.png` | 390 px viewport | The English labels fit without horizontal scrolling |
| `readme-legacy-en.png` | `/legacy` | The same preference drives the compatibility view |
