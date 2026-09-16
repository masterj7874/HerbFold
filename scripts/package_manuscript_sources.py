"""Package frozen manuscript evidence, without provider calls or original mutations.

The archive includes allowlisted source artifacts and a standalone verifier.
Original artifacts retain their bytes and repository-relative names. Generated
helpers relocate historical paths at read time; they do not rewrite evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import textwrap
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
JOBS = ("88692f7955d94c50814f6e3121f963b6", "3730181a45734f0dafa92a78917c48dd",
        "b868a97038e144f8a605a722d911338b", "d80fe6aa1e6945538da31833a471d6ed")
QUANTUM_JOB = "f1b0f1c6867b4d21b40193e65ab980bf"
SENSITIVE_KEY = re.compile(
    r"^(?:api_?key|access_?token|refresh_?token|id_?token|token|password|passwd|"
    r"client_?secret|private_?key|secret_?key|authorization|credentials?|instance_?crn|account_?id|crn)$", re.I)
SECRET_VALUE = re.compile(
    r"crn:v[0-9]:[^\s\"']{16,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?:^|\s)Bearer [A-Za-z0-9._-]{16,}|sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}")
# This one field is a 16-word user-request narrative, not an HTTP Authorization
# header or account credential. Any changed value is rejected and re-reviewed.
NARRATIVE_ALLOWLIST = {
    ("docs/quantum-projected-verification.json", "/authorization"):
        "a7936f1de8c1289a10dc46660f0d4fa511d4b9a29e51622e5e9df0a4d03d4421"
}
CHUNK = 4 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def safe_file(root, relative):
    key = PurePosixPath(relative)
    require(not key.is_absolute() and ".." not in key.parts and key.parts, "Unsafe source path")
    require(not any(p.startswith(".env") or p in {".git", ".venv", "__pycache__", "node_modules", "external",
                                                ".npmrc", ".netrc", ".pypirc"}
                    for p in key.parts), "Excluded source path")
    p = root.joinpath(*key.parts)
    require(p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(root.resolve()),
            f"Missing or unsafe source: {relative}")
    require(not any(parent.is_symlink() for parent in p.parents if parent != root and parent.is_relative_to(root)),
            "Symlinked source parent")
    require(p.suffix.lower() not in {".bin", ".zst", ".gz", ".zip", ".sqlite", ".db", ".docx", ".pem", ".key"},
            f"Excluded source type: {relative}")
    require(p.suffix.lower() != ".pdf" or key.parts[:3] == ("output", "manuscript", "figures"),
            "Only original generated figure PDFs may be included")
    require(p.stat().st_size <= 96 * 1024**2, f"Unexpected oversized individual artifact: {relative}")
    return p


def scan_document(value, name, pointer=""):
    stack = [(pointer, value)]
    scanned_keys = reviewed_narratives = 0
    while stack:
        path, item = stack.pop()
        if isinstance(item, dict):
            for key, val in item.items():
                field = path + "/" + str(key).replace("~", "~0").replace("/", "~1")
                if SENSITIVE_KEY.fullmatch(key) and isinstance(val, str) and val.strip():
                    scanned_keys += 1
                    digest = hashlib.sha256(val.encode()).hexdigest()
                    require(NARRATIVE_ALLOWLIST.get((name, field)) == digest,
                            f"Credential-shaped field requires review: {name} at {field}; value withheld")
                    reviewed_narratives += 1
                stack.append((field, val))
        elif isinstance(item, list):
            stack.extend((path + f"/{i}", val) for i, val in enumerate(item))
        elif isinstance(item, str):
            require(not SECRET_VALUE.search(item), f"Credential-shaped value found: {name}; value withheld")
    return scanned_keys, reviewed_narratives


def scan_file(path, name):
    counts = Counter()
    if path.suffix == ".json":
        values = [json.loads(path.read_text())]
        counts["json_files"] = 1
    elif path.suffix == ".jsonl":
        values = (json.loads(line) for line in path.open() if line.strip())
        counts["jsonl_files"] = 1
    else:
        # Text/code checks detect literal credential formats, not variable names.
        if path.suffix in {".py", ".md", ".txt", ".csv", ".toml", ".lock", ".bib", ".svg",
                           ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".scss", ".html"}:
            require(not SECRET_VALUE.search(path.read_text()),
                    f"Credential-shaped text found: {name}; value withheld")
            counts["text_files"] = 1
        return counts
    for value in values:
        fields, narratives = scan_document(value, name)
        counts["json_records"] += 1
        counts["credential_shaped_fields_reviewed"] += fields
        counts["exact_hash_allowlisted_narratives"] += narratives
    return counts


def gather(root):
    audit = json.loads((root / "research/manuscript/data_audit.json").read_text())
    figure = json.loads((root / "output/manuscript/figures/figure-manifest.json").read_text())
    expected = {}

    def remember(name, row):
        item = {"sha256": row["sha256"], "size_bytes": row.get("size_bytes", row.get("bytes"))}
        require(isinstance(item["size_bytes"], int), f"Missing source size receipt: {name}")
        require(name not in expected or expected[name] == item, f"Conflicting source receipts: {name}")
        expected[name] = item

    for row in audit["artifacts"]:
        remember(row["path"], row)
    for name, row in figure["input_sources"].items():
        remember(name, row)
    # The enlarged analyses add receipts; retain the original audit checks and
    # reject a conflicting hash rather than replacing an older source assertion.
    for name in ("research/manuscript/q1_extension/af3/contact_analysis.json",
                 "research/manuscript/q1_extension/chemistry/summary.json",
                 "research/manuscript/q1_extension/quantum/summary.json",
                 "research/manuscript/q1_extension/quantum/artifact_manifest.json",
                 "output/manuscript/figures/scientific-reports/figure-manifest.json"):
        receipt_path = safe_file(root, name)
        receipt = json.loads(receipt_path.read_text())
        for source, row in receipt.get("input_sources", {}).items():
            remember(source, row)
        for field in ("sources", "artifacts"):
            for row in receipt.get(field, []):
                if "path" in row and "sha256" in row:
                    remember(row["path"], row)
    selected = set(expected)
    for base, suffixes in (("research/manuscript", {".md", ".json", ".csv", ".bib", ".py"}),
                           ("output/manuscript/figures", {".svg", ".pdf", ".png", ".json", ".md", ".txt"}),
                           ("src/herbfold", {".py", ".json"}), ("data", {".json", ".csv"}),
                           ("frontend/src", {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
                                             ".css", ".scss", ".json", ".svg", ".png", ".jpg", ".webp"}),
                           ("frontend/tests", {".ts", ".tsx", ".js", ".mjs", ".json"})):
        for p in (root / base).rglob("*"):
            if (p.is_file() and p.suffix in suffixes and "__pycache__" not in p.parts
                    and not any(part == "qa" for part in p.parts) and not p.name.startswith("review-")):
                selected.add(p.relative_to(root).as_posix())
    for job in JOBS:
        for p in (root / "runtime" / job / "output").rglob("*"):
            if p.is_file() and p.suffix in {".json", ".cif", ".csv", ".md"}:
                selected.add(p.relative_to(root).as_posix())
    selected.add(f"runtime/{QUANTUM_JOB}/quantum.json")
    required = ["pyproject.toml", "uv.lock", "README.md", "scripts/package_manuscript_sources.py",
                "scripts/audit_projected_quantum.py", "scripts/verify_af3_msa_comparison.py",
                "scripts/manuscript_figures.py", "scripts/manuscript_document.py",
                "scripts/manuscript_chemical_space.py", "scripts/manuscript_af3_contacts.py",
                "scripts/manuscript_quantum_sensitivity.py", "scripts/manuscript_quantum_document_preview.py",
                "scripts/manuscript_submission_figures.py", "scripts/manuscript_q1_tables.py",
                "frontend/index.html", "frontend/package.json", "frontend/package-lock.json",
                "frontend/tsconfig.json", "frontend/vite.config.ts"]
    optional = ["scripts/validate_candidate_bioactivity.py", "scripts/validate_candidate_toxicity.py",
                "scripts/benchmark_discovery_scale.py", "docs/bio-validation-sources.md",
                "docs/discovery-storage.md", "docs/herb-aliases.md", "docs/molecular-selection-sources.md",
                "docs/af3-full-msa-setup.md", "docs/af3-msa-validation.md",
                "docs/quantum-projected-validation.md", "docs/af3-google-weights-acquisition.json",
                "docs/af3-google-weights-validation.json", "docs/af3-full-database-installation.json"]
    selected.update(required)
    selected.update(name for name in optional if (root / name).is_file())
    # Restrict root-level frontend configuration to known source/config patterns;
    # never recurse through frontend/node_modules, dist, caches, or account files.
    for pattern in ("tsconfig*.json", "*.config.ts", "*.config.js", "*.config.mjs", "*.config.cjs"):
        selected.update(p.relative_to(root).as_posix() for p in (root / "frontend").glob(pattern)
                        if p.is_file())
    for name in sorted(selected):
        safe_file(root, name)
    return sorted(selected), expected


VERIFIER = r'''
"""Verify all packaged original/generated files with stdlib SHA-256; no network."""
import hashlib
import json
from pathlib import Path, PurePosixPath

def verify(root):
    manifest = json.loads((root / "PACKAGE_MANIFEST.json").read_text())
    failures = []
    for row in manifest["files"]:
        rel = PurePosixPath(row["path"])
        p = root.joinpath(*rel.parts)
        if rel.is_absolute() or ".." in rel.parts or not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(root.resolve()):
            failures.append({"path": row["path"], "reason": "missing_or_unsafe"}); continue
        h = hashlib.sha256()
        with p.open("rb") as stream:
            while block := stream.read(4 * 1024**2): h.update(block)
        if p.stat().st_size != row["size_bytes"] or h.hexdigest() != row["sha256"]:
            failures.append({"path": row["path"], "reason": "size_or_sha256_mismatch"})
    result = {"passed": not failures, "checked_files": len(manifest["files"]), "failures": failures,
              "scope": "Integrity relative to the included manifest, not external authenticity or biological validation."}
    return result

if __name__ == "__main__":
    result = verify(Path(__file__).resolve().parent)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
'''


REPRODUCER = r'''
"""CPU-only read-time relocation of archived figure inputs. Original files stay unchanged."""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reproduced/figures")
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    from verify_package import verify
    if not verify(ROOT)["passed"]: raise ValueError("Package integrity check failed")
    manifest = json.loads((ROOT / "PACKAGE_MANIFEST.json").read_text())
    old_root = Path(manifest["original_repository_root"])
    spec = importlib.util.spec_from_file_location("packaged_figures", ROOT / "scripts/manuscript_figures.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    original_track = module.Figures.track
    def track(self, value):
        p = Path(value)
        if p.is_absolute():
            if p.is_relative_to(ROOT): return original_track(self, p)
            if p.is_relative_to(old_root): p = ROOT / p.relative_to(old_root)
            else: raise ValueError("External input path is not packaged")
        return original_track(self, p)
    module.Figures.track = track
    out = args.output.resolve()
    archived = ROOT / "output/manuscript/figures"
    if out == archived or out.is_relative_to(archived): raise ValueError("Choose a separate output directory")
    sys.argv = ["manuscript_figures.py", "--output", str(out)]
    if args.only: sys.argv += ["--only", *args.only]
    module.main()

if __name__ == "__main__": main()
'''


EXTENSION_REPRODUCER = r'''
"""Replay enlarged manuscript analyses on CPU into a separate output directory."""
import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", choices=("chemical-space", "af3-contacts", "quantum-sensitivity", "submission-figures"))
    parser.add_argument("--output", type=Path, default=ROOT / "reproduced/extensions")
    parser.add_argument("--audit-python", type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    from verify_package import verify
    if not verify(ROOT)["passed"]: raise ValueError("Package integrity check failed")
    inventory = json.loads((ROOT / "PACKAGE_MANIFEST.json").read_text())
    old_root = Path(inventory["original_repository_root"])
    out = args.output.resolve()
    # These builders record repository-relative output paths. Keep reproduction
    # below a dedicated untracked directory, never in any archived source tree.
    allowed = ROOT / "reproduced"
    if not out.is_relative_to(allowed): raise ValueError("Output must be below the extracted package's reproduced directory")
    out.mkdir(parents=True, exist_ok=True)

    def relocate(value):
        p = Path(value)
        if p.is_absolute():
            if p.is_relative_to(ROOT): return p
            if p.is_relative_to(old_root): return ROOT / p.relative_to(old_root)
            raise ValueError("External input path is not packaged")
        return p

    def patch_track(cls):
        original_track = cls.track
        def track(self, value): return original_track(self, relocate(value))
        cls.track = track

    names = {"chemical-space": "manuscript_chemical_space", "af3-contacts": "manuscript_af3_contacts",
             "quantum-sensitivity": "manuscript_quantum_sensitivity", "submission-figures": "manuscript_submission_figures"}
    spec = importlib.util.spec_from_file_location(names[args.analysis], ROOT / "scripts" / (names[args.analysis] + ".py"))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    if args.analysis == "chemical-space":
        module.OUT, module.FIG = out / "chemistry", out / "figures"
        module.OUT.mkdir(parents=True, exist_ok=True)
        snapshot = ROOT / "research/manuscript/q1_extension/chemistry/chembl_reference_snapshot.csv"
        if not snapshot.is_file(): raise ValueError("Packaged ChEMBL reference snapshot required; no database fallback")
        shutil.copyfile(snapshot, module.OUT / snapshot.name)
        module.main()
    elif args.analysis == "af3-contacts":
        patch_track(module.Audit)
        sys.argv = [names[args.analysis], "--output", str(out / "af3"), "--figure-output", str(out / "figures")]
        module.main()
    elif args.analysis == "quantum-sensitivity":
        sys.argv = [names[args.analysis], "--output", str(out / "quantum"), "--figures", str(out / "figures"),
                    "--audit-python", str(args.audit_python.expanduser().absolute())]
        module.main()
    else:
        patch_track(module.original.Figures)
        module.OUT = out / "scientific-reports"
        module.main()

if __name__ == "__main__": main()
'''


README = '''
# HerbFold manuscript source and selected raw-evidence package

This archive accompanies the enlarged Scientific Reports computational methods
manuscript: seven main figures, one main table, four supplementary figures and
thirteen supplementary tables. It retains
repository-relative paths and the exact bytes of every included original file.
It is a local deliverable, not a public data accession, clinical validation or a
claim that a novel drug or quantum advantage has been established.

## Included

- Main/supplement Markdown, verified bibliography, tables, numerical ledger,
  evidence audit and figure-generation/document-generation code.
- Original figures and the Scientific Reports arrangement (seven main and four
  supplementary figures), with SVG, vector PDF, 600-dpi publication PNG,
  separately exported 240-dpi Word PNG where supplied, captions and manifests.
- The numerical-audit, original figure and enlarged-analysis source inventories,
  including herbal chemical-space, AF3 ligand-contact and quantum-sensitivity
  tables, scripts and verification records.
- Four AF3 jobs: 20 diffusion samples plus four top-ranked copies, same-sample
  coordinates, confidence/summary arrays, ranking tables, actual output input
  JSON (including prepared MSA/templates where used), and native output terms.
- Projected quantum QPY circuits, raw counts, its manifest and independent audit;
  the archived global-overlap manifest; curated assay/holdout/candidate evidence.
- Python implementation, small configuration/reference data, pyproject.toml and
  uv.lock; React/TypeScript frontend source, tests, build configuration,
  package.json and package-lock.json. No node_modules, built frontend bundles,
  dependency binaries, account files or external AF3 source tree is bundled.

## Verify before use

Extract into a fresh directory and run from its root:

```bash
python verify_package.py
```

This uses only Python's standard library and checks every listed size/SHA-256.
The included manifest is an integrity inventory, not an independent signature.
The ZIP's separate build receipt records its complete SHA-256. Source bytes were
also checked against the audit/figure receipts during packaging. Credential-key
and literal-secret-format scans were applied without printing values; one exact
hash-allowlisted authorization narrative contains no credential. This is a
bounded screening procedure, not a guarantee of arbitrary secret detection.

## Reproduce selected analyses without GPU, IBM access or model weights

The core Python environment is recorded in `uv.lock`. Install dependencies only
when needed; dependency installation requires normal package-index access:

```bash
uv sync --locked
uv pip install matplotlib==3.10.6 python-docx==1.2.0
uv run --locked --no-sync python reproduce_figures.py --output reproduced/figures
uv run --locked --no-sync python scripts/audit_projected_quantum.py \\
  --manifest runtime/f1b0f1c6867b4d21b40193e65ab980bf/quantum.json \\
  --verification-report docs/quantum-projected-verification.json \\
  --output reproduced/quantum-independent-audit.json
uv run --locked --no-sync python reproduce_extensions.py chemical-space
uv run --locked --no-sync python reproduce_extensions.py af3-contacts
uv run --locked --no-sync python reproduce_extensions.py quantum-sensitivity
uv run --locked --no-sync python reproduce_extensions.py submission-figures
```

The figure wrapper maps the recorded original repository prefix to the extracted
root **in memory**. It neither changes archived JSON/CIFs nor substitutes data.
The original eight figure recipes remain available; `--only figure3` selects the
original structure figure for a shorter check. The extension wrapper adds the
chemical-space main figure and supplementary contact/sensitivity figures, and
replays the final Scientific Reports arrangement separately. It relocates AF3
historical paths in memory, without rewriting raw receipts, coordinates or counts.
The chemical-space replay copies the packaged 3,417-row ChEMBL reference snapshot
into its separate output directory, so the complete catalog SQLite is unnecessary
for this selected comparison. It still requires the included botanical provenance
CSV and does not repeat database acquisition or botanical authentication.

The extension wrapper only writes beneath `reproduced/`; the original raw
artifacts and numerical ledger remain unchanged. Quantum sensitivity uses the
same four measured inputs, prepared-state controls, raw counts and bound circuits;
gamma grids and post hoc qubit masks create no new hardware observations.
QPY decoding requires a compatible Qiskit version (the recorded format is 17).
`--audit-python` can select another compatible interpreter for that CPU audit.
Figure metadata timestamps and rendered bytes can
vary with the platform/fonts even when numerical values agree. Original figure
exports and hashes remain available for comparison. The quantum audit recomputes
the estimator and checks count/QPY artifacts without contacting IBM; it is not a
new hardware experiment. The direct analysis scripts are also included, but their
defaults can write derived files into archived locations; prefer the wrappers or
work in a second copy. The separate quantum document-preview script exports from
the original Matplotlib Figure at 240 dpi; it does not resize the publication PNG.

The frontend can be rebuilt separately using a compatible Node.js/npm environment:

```bash
cd frontend
npm ci
npm run build
```

This installs dependencies from the lockfile and requires package-index access.
Source availability does not recreate local API services, live credentials,
catalog databases, saved runtime state or external computation capacity.

The supplied `scripts/manuscript_document.py` builds DOCX from the included
Markdown/tables/figures with python-docx and Pillow. It writes manuscript-derived
files, so run it in a second copy of the extracted package. PDF conversion/visual
QA additionally needs the author's document-rendering environment and fonts;
the final main/supplement DOCX and PDF are separate deliverables and deliberately
excluded here. The package does not claim pixel-identical rendering everywhere.

## What cannot be independently rerun from this ZIP alone

The full natural-product catalog/SQLite generation pool, full downloaded assay
responses, complete nine-component MSA database distribution, AF3 model weights,
external AF3 runtime, signed service sessions, frontend dependency binaries and
the complete application database state are not included. Botanical queries and full enumeration therefore cannot be repeated
against the complete frozen databases using this ZIP alone; their selected
retrieval tables and provenance records can be inspected. Fresh AF3 inference,
training-source reconstruction and QPU acquisition require separately obtained
resources, versions, appropriate rights and explicit execution decisions.

`scripts/verify_af3_msa_comparison.py` is the original full provenance audit. It
checks external weight contents and runtime paths as well as the saved outputs,
so it intentionally cannot pass unmodified without those omitted external
resources. The selected raw coordinates/confidences and the existing audit are
provided for independent CPU inspection. Historical absolute paths, source URLs,
public job IDs and local artifact identifiers inside receipts are preserved for
traceability; a file hash alone cannot reconstruct an omitted source.

## Local archive and journal assets

The ZIP is a local convenience bundle, not an uploaded journal supplement or
public repository accession. Its added raw arrays and publication exports may
make it larger than 50 MB. The build receipt records the actual archive size;
individual journal asset/total-upload limits must be checked for the final
submission. Main figures, the supplementary document and source tables can be
provided as separate assets if required. No upload, deposition URL or new license
is created by this packaging script.

## Source terms and attribution

Existing attribution, source URLs, license fields and AF3 output terms are
retained. This compilation does not replace any original license or grant a new
blanket license over third-party data. Authors must finalize the project code
and manuscript license before public deposition. Dependencies retain their own
licenses and are obtained separately. Raw user-supplied reference-paper PDFs,
model parameters, large databases, credentials, `.env` files and final manuscript
DOCX/PDF files are excluded.

Foundational source/terms pointers: LOTUS (`https://lotus.naturalproducts.net/`),
COCONUT (`https://coconut.naturalproducts.net/`), ChEMBL
(`https://www.ebi.ac.uk/chembl/`), NIH Tox21
(`https://tripod.nih.gov/tox21/challenge/`), RCSB PDB
(`https://www.rcsb.org/pages/policies`), AlphaFold 3
(`https://github.com/google-deepmind/alphafold3/tree/v3.0.4`), IBM Quantum
(`https://quantum.cloud.ibm.com/docs/`). The recorded acquisition receipts and
reference registry, not these landing pages alone, identify the actual snapshots.

## 한국어 안내

코드·표·그림과 선택된 AF3 원시 구조/신뢰도, 양자 회로/측정값을 담았습니다.
먼저 `python verify_package.py`로 파일 무결성을 확인하세요. 그림 재생성과
기존 양자 측정의 독립 감사는 CPU로 실행할 수 있습니다. 새 AF3/QPU 실행이나
신약 효능·안전성 검증을 수행하는 패키지가 아닙니다. 전체 원천 DB와 생성
라이브러리, 모델 가중치, 자격증명, 최종 DOCX/PDF는 포함하지 않습니다.
외부 자료의 원래 출처·이용조건은 그대로 적용됩니다.
'''


def create_package(root, output):
    root = root.resolve()
    names, expected = gather(root)
    manifest = {"schema_version": 1, "created_at": datetime.now(UTC).isoformat(),
                "original_repository_root": str(root), "artifact_policy": "Original bytes and relative paths preserved",
                "audit_figure_union_files": len(expected), "files": [], "credential_scan": {},
                "source_receipt_scope": "Original numerical audit, original/journal figures and enlarged analysis receipts",
                "target_manuscript": globals().get("PACKAGE_TARGET", {"journal": "Scientific Reports", "main_figures": 7, "main_tables": 1,
                                      "supplementary_figures": 4, "supplementary_tables": 13}),
                "excluded": ["weights", "large databases", "credentials", ".env files", "reference paper PDFs",
                             "final manuscript DOCX/PDF", "review notes", "external runtime and dependency binaries"],
                "new_gpu_qpu_llm_submissions": 0}
    scan = Counter()
    source_bytes = 0
    for name in names:
        p = safe_file(root, name)
        scan.update(scan_file(p, name))
        row = {"path": name, "size_bytes": p.stat().st_size, "sha256": sha_file(p), "kind": "original"}
        if name in expected:
            require({k: row[k] for k in ("sha256", "size_bytes")} == expected[name], f"Source receipt mismatch: {name}")
            row["verified_against_audit_or_figure_receipt"] = True
        manifest["files"].append(row)
        source_bytes += row["size_bytes"]
    require(source_bytes < 1024**3, "Unexpectedly large package; review selection")
    manifest["credential_scan"] = {**scan, "status": "passed", "values_printed": False,
                                   "scope": "JSON/JSONL sensitive keys and text literal credential patterns; not exhaustive"}
    generated = {"PACKAGE_README.md": textwrap.dedent(README).strip() + "\n",
                 "verify_package.py": textwrap.dedent(VERIFIER).lstrip(),
                 "reproduce_figures.py": textwrap.dedent(REPRODUCER).lstrip(),
                 "reproduce_extensions.py": textwrap.dedent(EXTENSION_REPRODUCER).lstrip()}
    for name, content in generated.items():
        raw = content.encode()
        manifest["files"].append({"path": name, "size_bytes": len(raw),
                                  "sha256": hashlib.sha256(raw).hexdigest(), "kind": "package_helper"})
    manifest["original_files"] = len(names)
    manifest["total_listed_files"] = len(manifest["files"])
    manifest["uncompressed_listed_bytes"] = sum(r["size_bytes"] for r in manifest["files"])
    manifest_raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_suffix(output.suffix + ".part")
    try:
        with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=4, allowZip64=True) as archive:
            for row in manifest["files"]:
                info = zipfile.ZipInfo(row["path"], date_time=(2026, 9, 8, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                digest = hashlib.sha256()
                size = 0
                with archive.open(info, "w", force_zip64=True) as dest:
                    if row["kind"] == "original":
                        with safe_file(root, row["path"]).open("rb") as src:
                            while block := src.read(CHUNK):
                                dest.write(block); digest.update(block); size += len(block)
                    else:
                        block = generated[row["path"]].encode()
                        dest.write(block); digest.update(block); size += len(block)
                require(digest.hexdigest() == row["sha256"] and size == row["size_bytes"],
                        f"Source changed during packaging: {row['path']}")
            archive.writestr("PACKAGE_MANIFEST.json", manifest_raw)
        verify_zip(pending)
        pending.replace(output)
    finally:
        pending.unlink(missing_ok=True)
    receipt = {"schema_version": 1, "created_at": datetime.now(UTC).isoformat(), "status": "verified",
               "path": str(output.relative_to(root)) if output.is_relative_to(root) else str(output),
               "sha256": sha_file(output), "size_bytes": output.stat().st_size,
               "zip_entries": len(manifest["files"]) + 1, "original_files": len(names),
               "uncompressed_listed_bytes": manifest["uncompressed_listed_bytes"],
               "audit_figure_union_files": len(expected), "credential_scan": manifest["credential_scan"],
               "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(), "new_gpu_qpu_llm_submissions": 0,
               "distribution": {"local_convenience_archive": True,
                                "larger_than_50_mb": output.stat().st_size > 50_000_000,
                                "separate_journal_assets_available": True, "uploaded": False,
                                "public_deposition_url": None, "new_blanket_license_assigned": False}}
    receipt_path = output.with_suffix(".verification.json")
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    return receipt


def verify_zip(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "Duplicate ZIP members")
        for name in names:
            rel = PurePosixPath(name)
            require(not rel.is_absolute() and ".." not in rel.parts, "Unsafe ZIP member")
        manifest = json.loads(archive.read("PACKAGE_MANIFEST.json"))
        require(set(names) == {r["path"] for r in manifest["files"]} | {"PACKAGE_MANIFEST.json"}, "ZIP inventory mismatch")
        for row in manifest["files"]:
            h = hashlib.sha256(); size = 0
            with archive.open(row["path"]) as src:
                while block := src.read(CHUNK): h.update(block); size += len(block)
            require(h.hexdigest() == row["sha256"] and size == row["size_bytes"], f"ZIP integrity mismatch: {row['path']}")
    return {"passed": True, "checked_files": len(manifest["files"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / "output/manuscript/HerbFold_Source_Package.zip")
    parser.add_argument("--verify-only", type=Path)
    args = parser.parse_args()
    result = verify_zip(args.verify_only) if args.verify_only else create_package(args.root, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
