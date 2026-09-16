"""Source package for the revision; outer review packet is built separately."""
from pathlib import Path
import json,hashlib,zipfile,textwrap
import package_manuscript_sources as base

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/manuscript/round1'
original_gather=base.gather
original_safe=base.safe_file

def safe_file(root,name):
    if name=='runtime/validation/sources/tox21.csv.gz':
        p=root/name
        base.require(p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(root.resolve()),'Unsafe Tox21 snapshot')
        base.require(p.stat().st_size==122925 and base.sha_file(p)=='45d09792492ce049039dd24aa27b07fc79ce20c573187d4d90bcd178c0c0d360','Wrong exact Tox21 snapshot')
        return p
    return original_safe(root,name)

def gather(root):
    selected,expected=original_gather(root)
    selected={p for p in selected if '/frozen_round0/' not in p and '/related_work/sources/' not in p and p != 'scripts/manuscript_round1_text.py'}
    selected.add("research/manuscript/round1/review-map.csv")
    import csv
    with (root/"research/manuscript/round1/review-map.csv").open(newline="") as stream:
        for row in csv.DictReader(stream):
            selected.update(row["source_files"].split(" | "))
    # Backend contract tests and their fixtures are a scientific replay deliverable.
    for p in (root/'tests').rglob('*'):
        if p.is_file() and p.suffix in {'.py','.json','.cif','.csv','.txt'} and '__pycache__' not in p.parts:
            selected.add(p.relative_to(root).as_posix())
    for p in (root/'scripts').glob('*round1*.py'):
        if p.name != 'manuscript_round1_text.py':selected.add(p.relative_to(root).as_posix())
    for p in (root/'src/herbfold/static').rglob('*'):
        if p.is_file() and p.suffix.lower() in {'.js','.mjs','.css','.html','.svg','.png','.webp','.ico','.json','.woff','.woff2'}:
            selected.add(p.relative_to(root).as_posix())
    # Include every small immutable input consumed by the revision audits.
    for name in ('assays/source_hashes.csv',):
        import csv
        for row in csv.DictReader((root/'research/manuscript/round1'/name).open()):selected.add(row['path'])
    receipt=json.loads((root/'research/manuscript/round1/constraints/constraint-audit.json').read_text())
    for row in receipt['sources']:
        if row['path'].endswith(('.db','.sqlite','.sqlite3')):continue
        selected.add(row['path'])
    for p in (root/'research/manuscript/round1').rglob('*'):
        if p.is_file() and p.suffix in {'.log','.xml','.jsonl','.txt'} and 'frozen_round0' not in p.parts and 'sources' not in p.parts:
            selected.add(p.relative_to(root).as_posix())
    for p in selected:base.safe_file(root,p)
    return sorted(selected),expected

def main():
    base.safe_file=safe_file
    tables=json.loads((ROOT/'research/manuscript/round1/document_tables.json').read_text())
    base.PACKAGE_TARGET={'journal':'Scientific Reports','revision':'R1, 2026-09-09','main_figures':7,'main_tables':1,'supplementary_figures':5,'supplementary_tables':len(tables)-1}
    base.gather=gather
    base.README=base.README.replace('four supplementary figures and\nthirteen supplementary tables','five supplementary figures and\nexpanded comparison, reporting-rule and uncertainty tables')
    base.README+='''
\n## Round 1 additions and exact replay commands
The outer HerbFold_R1_Review_Packet.zip also contains the actual main manuscript,
Supplementary Information and point-by-point response as DOCX and PDF. Do not
send the manuscript alone: the source ZIP and Supplementary Information are
necessary to inspect the central evidence-preservation claims.

Round-1 code, CSV/JSON ledgers and source manifests are under
research/manuscript/round1. Original studies were frozen on 2026-09-08;
the 2026-09-09 additions are clearly labelled CPU analyses of those observations.
They neither replace raw measurements nor claim new hardware acquisitions.
Exact selected commands and completed replay outcomes are in
research/manuscript/round1/replay-report.json and review-map.csv.

Run the new analyses in separate output directories, after verifying integrity:

```bash
python verify_package.py
PYTHONPATH=src python scripts/manuscript_round1_assays.py --output tmp/round1-replay/assays
PYTHONPATH=src python scripts/replay_manuscript_round1.py constraints --output tmp/round1-replay/constraints
python scripts/replay_manuscript_round1.py figures --output tmp/round1-replay/figures
```

Consult each script's --help for analysis versus locally retained full-database
options. The selected raw AF3/QPU observations and test-set assay records are
included. A full-catalog or generation-campaign rerun is not implied by passing
these bounded CPU replays. Per-calibration hERG predictions absent from the
historical archive are explicitly identified in the uncertainty ledger.

No new blanket licence is imposed here. This is a user-requested local review
compilation. Existing source notices continue to apply; authors must confirm
study-specific declarations and public release/licence rights before submission
or unrestricted public redistribution. Review access is through the attached
local packet, not a claimed public DOI or repository.
'''
    receipt=base.create_package(ROOT,OUT/'HerbFold_R1_Source_Package.zip')
    print(json.dumps(receipt,indent=2))

if __name__=='__main__':main()
