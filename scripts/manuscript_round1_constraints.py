#!/usr/bin/env python3
"""Read-only R1 constraint audit, with explicitly constructed join controls.

No scientific jobs or provider requests are submitted. Actual archived coordinates
are copied to an isolated temporary Store. Its input hints are omitted deliberately
so that the production resolver must inspect the observed output, not merely reject
a conflicting input payload. An ID-only comparator is a deliberately limited
diagnostic ablation, not a representation of another software system.
"""
from __future__ import annotations

import argparse
import ast
import copy
import csv
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "research/manuscript/round1/constraints"
SOURCE_ID = "b3d261566fa14891925d3b1fd730f2d1"
BASELINE = "docs/af3-trained-selected-predictions.json"
MSA = "docs/af3-msa-selected-predictions.json"
TESTS = [
    "tests/test_molecular_selection.py", "tests/test_af3_diagnostics.py",
    "tests/test_af3_parameters.py",
    "tests/test_af3_studio.py::test_blocked_preflight_has_no_fallback_or_launch",
    "tests/test_af3_studio.py::test_execution_rechecks_changed_readiness_before_process",
    "tests/test_bio_validation.py::test_endpoint_assay_and_conflicting_replicates_are_not_pooled",
    "tests/test_bio_validation.py::test_censored_flags_mutants_and_low_confidence_are_excluded",
    "tests/test_bio_validation.py::test_out_of_domain_prediction_abstains_even_for_qualified_model",
    "tests/test_bio_validation.py::test_exact_measurement_and_alerts_are_separate_from_clinical_claims",
    "tests/test_quantum_api.py",
    "tests/test_quantum_projected.py::test_decode_bit_order_and_wilson_not_global_all_zero",
    "tests/test_quantum_projected.py::test_invalid_counts_do_not_create_measured_kernel",
    "tests/test_quantum_projected.py::test_partial_submission_keeps_ids_and_never_fills_missing_observables",
    "tests/test_quantum_projected.py::test_manifest_input_tampering_rejected_without_provider",
    "tests/test_quantum_projected.py::test_qiskit_measurements_roundtrip_controls_bootstrap_and_raw_artifact",
    "tests/test_scale_validation.py::test_real_unique_candidates_have_compact_fragment_ancestry_and_finite_ceiling",
    "tests/test_scale_validation.py::test_two_source_roles_or_different_salt_ids_do_not_fake_distinct_parent_structures",
    "tests/test_scale_validation.py::test_read_only_audit_does_not_replace_live_checkpoint_or_report",
]


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class Audit:
    def __init__(self):
        self.sources, self.checks = {}, []

    def source(self, relative, pointer=""):
        path = ROOT / relative
        key = path.relative_to(ROOT).as_posix()
        if key not in self.sources:
            self.sources[key] = {"path": key, "sha256": digest(path), "bytes": path.stat().st_size}
        return {**self.sources[key], "json_pointer": pointer}

    def read(self, relative):
        self.source(relative)
        return json.loads((ROOT / relative).read_text())

    def check(self, name, passed, detail=None):
        self.checks.append({"name": name, "passed": bool(passed), "detail": detail})
        if not passed:
            raise AssertionError(name + ": " + str(detail))

    def function(self, path, name):
        source = self.source(path)
        module = ast.parse((ROOT / path).read_text())
        parts = name.split(".")
        def locate(nodes, remaining):
            for node in nodes:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name == remaining[0]:
                        if len(remaining) == 1:
                            return node
                        hit = locate(node.body, remaining[1:])
                        if hit:
                            return hit
                    hit = locate(node.body, remaining)
                    if hit:
                        return hit
            return None
        node = locate(module.body, parts)
        self.check(f"function exists: {path}:{name}", node is not None)
        return {**source, "symbol": name, "line": node.lineno, "end_line": node.end_lineno}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def write_csv(path, rows):
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                             for key, value in row.items()})


def run_tests(out):
    work = tempfile.mkdtemp(prefix="herbfold-round1-constraints-")
    # Do not resolve the venv symlink: invoking /usr/bin/python loses its site-packages.
    env = os.environ.copy()
    env.update(CUDA_VISIBLE_DEVICES="", HERBFOLD_DATA_DIR=work + "/runtime", PYTHONDONTWRITEBYTECODE="1",
               RAYON_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1",
               QISKIT_PARALLEL="FALSE")
    for key in list(env):
        if any(part in key.upper() for part in ("API_KEY", "API_TOKEN", "QUANTUM_TOKEN", "IBM_TOKEN")):
            env.pop(key)
    started = time.monotonic()
    runs, logs = [], []
    merged = ET.Element("testsuites")
    for index, selector in enumerate(TESTS):
        xml_path = out / f"cpu-tests-group-{index:02}.xml"
        command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", selector,
                   "-k", "not actual_zstandard_container and not zstandard_backend",
                   "--basetemp", f"{work}/pytest-{index}", "--junitxml", str(xml_path)]
        result = subprocess.run(command, cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=180)
        runs.append({"selector": selector, "command": command, "exit_code": result.returncode})
        logs.append(f"GROUP {index}: {selector}\n" + result.stdout)
        if xml_path.exists():
            merged.extend(list(ET.parse(xml_path).getroot()))
    ET.ElementTree(merged).write(out / "cpu-tests.xml", encoding="utf-8", xml_declaration=True)
    (out / "cpu-tests.log").write_text("\n".join(logs))
    exit_code = 0 if all(run["exit_code"] == 0 for run in runs) else 1
    write_json(out / "cpu-tests-run.json", {
        "commands": runs, "exit_code": exit_code, "selection": TESTS,
        "elapsed_seconds": time.monotonic() - started, "fresh_temporary_root": work,
        "new_scientific_jobs": 0, "cuda_visible_devices": "",
        "network_providers": "Provider paths are explicit test doubles; local Qiskit fixtures use CPU only.",
        "optional_local_AF3_interpreter_tests": "Three optional zstandard/external-AF3-interpreter cases deselected; clean extraction needs no AF3 checkout.",
        "thread_environment": {key: env[key] for key in ("RAYON_NUM_THREADS", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "QISKIT_PARALLEL")},
        "process_isolation": "Each selected module or individual selector runs in a fresh process and distinct basetemp. Earlier combined-process native Qiskit segmentation faults are preserved in separate failed receipts; this is not a passing monolithic-suite claim.",
    })
    if exit_code:
        raise RuntimeError("Selected CPU tests failed; see cpu-tests.log")


def test_inventory(audit, out):
    receipt = audit.read((out / "cpu-tests-run.json").relative_to(ROOT))
    audit.source((out / "cpu-tests.xml").relative_to(ROOT))
    cases = []
    for item in ET.parse(out / "cpu-tests.xml").iter("testcase"):
        status = "passed"
        for flag in ("failure", "error", "skipped"):
            if item.find(flag) is not None:
                status = flag
        cases.append({"test_id": item.get("classname") + "::" + item.get("name"),
                      "status": status, "seconds": float(item.get("time", 0)),
                      "case_origin": "constructed test fixture; no scientific measurements"})
    audit.check("bounded existing test run passed", receipt["exit_code"] == 0 and
                all(row["status"] == "passed" for row in cases))
    for filename in {selector.split("::")[0] for selector in receipt["selection"]}:
        audit.source(filename)
    write_csv(out / "existing-cpu-test-cases.csv", cases)
    return {"cases": len(cases), "passed": sum(row["status"] == "passed" for row in cases),
            "denominator": "Collected pytest cases, including parameterized instances; not independent scientific observations",
            "by_module": dict(Counter(row["test_id"].split("::")[0] for row in cases)),
            "receipt": receipt, "cases_file": "existing-cpu-test-cases.csv"}


def export_reconstruction(audit, out):
    """Export exactly the archived audit SQL sample, without opening a writer."""
    database = ROOT / "runtime/validation/scale/scale.sqlite3"
    report = audit.read("docs/scale-validation-results.json")
    before = digest(database)
    samples, fragments, parents = [], {}, {}
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        rows = connection.execute(
            "SELECT digest,smiles,left_fragment,right_fragment,attempt_index,mw,logp,tpsa,qed "
            "FROM candidates ORDER BY digest LIMIT 500"
        ).fetchall()
        for stored_digest, smiles, left, right, attempt, mw, logp, tpsa, qed in rows:
            samples.append({"digest_hex": stored_digest.hex(), "smiles": smiles, "left_fragment": left,
                "right_fragment": right, "attempt_index": attempt,
                "stored_descriptors": {"mw": mw, "logp": logp, "tpsa": tpsa, "qed": qed}})
            for fragment_id in (left, right):
                if str(fragment_id) in fragments:
                    continue
                row = connection.execute("SELECT smiles,label,role FROM fragments WHERE id=?", (fragment_id,)).fetchone()
                parent = connection.execute(
                    "SELECT p.catalog_id,p.original_smiles,p.role,p.smiles FROM ancestry a JOIN parents p "
                    "ON p.catalog_id=a.catalog_id AND p.role=a.role WHERE a.fragment_id=? LIMIT 1", (fragment_id,)
                ).fetchone()
                if parent is None:
                    raise AssertionError("Sampled fragment has no recorded original parent")
                parent_key = f"{parent[0]}:{parent[2]}"
                parents[parent_key] = {"catalog_id": parent[0], "original_smiles": parent[1], "role": parent[2],
                                       "stored_standardized_smiles": parent[3]}
                fragments[str(fragment_id)] = {"id": fragment_id, "smiles": row[0], "label": row[1],
                                               "role": row[2], "replayed_parent_key": parent_key}
        count = connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        full_digest = hashlib.sha256()
        for (value,) in connection.execute("SELECT digest FROM candidates ORDER BY digest"):
            full_digest.update(value)
        boundary = connection.execute("SELECT digest FROM candidates ORDER BY digest LIMIT 1 OFFSET 500").fetchone()[0]
        through_last = connection.execute("SELECT COUNT(*) FROM candidates WHERE digest<=?", (rows[-1][0],)).fetchone()[0]
        connection.rollback()
    after = digest(database)
    audit.check("read-only reconstruction export preserves full database bytes", before == after)
    audit.check("actual database full digest agrees with archived full-table receipt",
                full_digest.hexdigest() == report["audit"]["sorted_structure_digest_sha256"])
    audit.check("actual database count agrees with archived full-table receipt", count == report["retained_unique"])
    audit.check("exact 500 lowest-digest sample selected", len(samples) == through_last == 500 and
                rows[-1][0] < boundary)
    audit.check("same sampled distinct-fragment denominator", len(fragments) == report["audit"]["sample_unique_fragments_checked"])
    bundle = {"schema_version": 1, "rdkit_version": report["rdkit_version"],
        "selection": "SELECT ... FROM candidates ORDER BY digest LIMIT 500",
        "sample_count": len(samples), "fragment_count": len(fragments), "parent_record_count": len(parents),
        "samples": samples, "fragments": fragments, "parents": parents,
        "scope": "Exact 500 archived hash-prefix reconstructions plus the single recorded parent used per distinct sampled fragment. No newly generated candidates; no complete-database inclusion or full ancestry proof is inferred from this subset."}
    path = out / "reconstruction-500-input.json"
    write_json(path, bundle)
    receipt = {"source_database": database.relative_to(ROOT).as_posix(), "source_database_sha256": before,
        "database_bytes_unchanged_after_read": before == after, "sqlite_open_mode": "mode=ro, query_only=ON, read transaction",
        "full_row_count": count, "full_sorted_digest_sha256": full_digest.hexdigest(),
        "sample_count": len(samples), "first_digest": rows[0][0].hex(), "last_digest": rows[-1][0].hex(),
        "next_excluded_digest": boundary.hex(), "full_database_count_at_or_below_last_sample_digest": through_last,
        "input_sha256": digest(path), "original_full_table_audit": report["audit"],
        "limitation": "This records the full-database selection query and hash at export. An offline subset replay verifies its contents and chemistry, but cannot independently prove that the omitted database contains no earlier digest; that requires the full archived database/selection receipt. The original all-row lineage/filter claim is separate."}
    write_json(out / "reconstruction-selection-receipt.json", receipt)
    return receipt


def replay_reconstruction(path, out):
    from rdkit import rdBase
    from herbfold.scale_validation import _generate_chunk, _prepare_chunk
    bundle = json.loads(path.read_text())
    if rdBase.rdkitVersion != bundle["rdkit_version"]:
        raise ValueError(f"Exact replay requires archived RDKit {bundle['rdkit_version']}; current {rdBase.rdkitVersion}")
    fragment_replays, rows = {}, []
    for key, fragment in bundle["fragments"].items():
        parent = bundle["parents"][fragment["replayed_parent_key"]]
        parsed = _prepare_chunk([(parent["catalog_id"], parent["original_smiles"], parent["role"])])[0]
        fragment_replays[key] = (parsed[3] == parent["stored_standardized_smiles"] and
                                (fragment["label"], fragment["smiles"]) in parsed[4])
    for sample in bundle["samples"]:
        left = bundle["fragments"][str(sample["left_fragment"])]
        right = bundle["fragments"][str(sample["right_fragment"])]
        operation = (sample["attempt_index"], left["id"], left["smiles"], left["label"],
                     right["id"], right["smiles"], right["label"])
        counters, products, _ = _generate_chunk([operation])
        product = products[0] if products else None
        descriptor_differences = ({name: product[index] - sample["stored_descriptors"][name]
                                  for name, index in (("mw", 4), ("logp", 5), ("tpsa", 6), ("qed", 7))}
                                 if product is not None else None)
        rows.append({"digest_hex": sample["digest_hex"], "attempt_index": sample["attempt_index"],
            "fragment_parent_replay": fragment_replays[str(left["id"])] and fragment_replays[str(right["id"])],
            "source_roles": left["role"] == "natural_product" and right["role"] == "drug",
            "canonical_isomeric_smiles_equal": product is not None and product[1] == sample["smiles"],
            "sha256_equal": product is not None and product[0].hex() == sample["digest_hex"],
            "stored_descriptor_absolute_differences": {k: abs(v) for k, v in (descriptor_differences or {}).items()},
            "stored_descriptors_reproduced": descriptor_differences is not None and all(abs(value) <= 1e-10 for value in descriptor_differences.values()),
            "accepted_replay_operation": counters["attempted"] == counters["sanitized"] == counters["passed_filters"] == 1})
    flags = ("fragment_parent_replay", "source_roles", "canonical_isomeric_smiles_equal", "sha256_equal",
             "stored_descriptors_reproduced", "accepted_replay_operation")
    result = {"schema_version": 1, "passed": all(all(row[key] for key in flags) for row in rows) and all(fragment_replays.values()),
        "sample_count": len(rows), "distinct_fragments": len(fragment_replays), "rdkit_version": rdBase.rdkitVersion,
        "input_sha256": digest(path), "checks_by_sample": {key: sum(row[key] for row in rows) for key in flags},
        "fragment_parent_passed": sum(fragment_replays.values()), "rows": rows,
        "scope": "CPU reconstruction of the exact exported 500 structures, not a rerun of five million attempts. Same pinned implementation consistency; no efficacy/synthesis/novelty claim. Full-database prefix and all-row checks require their separate receipt."}
    write_json(out / "reconstruction-500-replay.json", result)
    write_csv(out / "reconstruction-500-replay.csv", rows)
    if not result["passed"]:
        raise AssertionError("Exported reconstruction replay failed")
    return {key: value for key, value in result.items() if key != "rows"}


def controlled_joins(audit):
    from Bio.PDB.MMCIF2Dict import MMCIF2Dict
    from Bio.SeqUtils import seq1
    from rdkit import Chem
    from herbfold import molecular_selection as selection
    from herbfold.molecular_api import _job_scene
    from herbfold.storage import Store
    records = [(mode, compound, envelope) for mode, path in (("none", BASELINE), ("search", MSA))
               for compound, envelope in audit.read(path)["jobs"].items()]
    target = audit.read("data/ptgs2.json")
    sequence = target["sequence"]
    altered = ("A" if sequence[0] != "A" else "G") + sequence[1:]
    rows, actual_sources = [], []
    with tempfile.TemporaryDirectory(prefix="herbfold-round1-joins-") as temporary:
        store = Store(Path(temporary) / "runtime")
        for mode, compound, envelope in records:
            original = envelope["job"]
            top = next(model for model in original["result"]["models"] if model["is_top_ranked_copy"])
            relative = "output/" + top["structure_path"]
            path = ROOT / "runtime" / original["id"] / relative
            source = audit.source(path.relative_to(ROOT))
            audit.check(f"{mode}/{compound} registered actual top SHA", source["sha256"] == top["structure_sha256"])
            audit.check(f"{mode}/{compound} actual completed execution", original["status"] == "completed" and
                        original["result"].get("execution_verified") is True)
            # Independent raw residue-sequence extraction for the expected identity label.
            cif = MMCIF2Dict(str(path))
            residues = {}
            ligand_elements = []
            for chain, position, residue, element in zip(cif["_atom_site.label_asym_id"],
                    cif["_atom_site.label_seq_id"], cif["_atom_site.label_comp_id"], cif["_atom_site.type_symbol"], strict=True):
                if chain == "A":
                    residues[int(position)] = residue
                if chain == "B" and element not in ("H", "D"):
                    ligand_elements.append(element)
            observed_sequence = "".join(seq1(residues[position]) for position in sorted(residues))
            audit.check(f"{mode}/{compound} independently observed 604 residues", observed_sequence == sequence
                        and sorted(residues) == list(range(1, len(sequence) + 1)))
            native_smiles = next(row["ligand"]["smiles"] for row in original["payload"]["sequences"] if "ligand" in row)
            other = next(envelope2 for _, compound2, envelope2 in records if compound2 != compound)
            other_smiles = next(row["ligand"]["smiles"] for row in other["job"]["payload"]["sequences"] if "ligand" in row)
            expected_elements = Counter(atom.GetSymbol() for atom in Chem.MolFromSmiles(native_smiles).GetAtoms()
                                        if atom.GetAtomicNum() > 1)
            audit.check(f"{mode}/{compound} raw selected atom composition", Counter(ligand_elements) == expected_elements)
            job = copy.deepcopy(original)
            job["payload"] = {}  # Equal metadata-hint ablation in all 12 controlled joins.
            job["result"]["models"] = [copy.deepcopy(top)]
            destination = store.directory(job["id"]) / relative
            destination.parent.mkdir(parents=True)
            shutil.copyfile(path, destination)
            with store.connect() as connection:
                connection.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", (
                    job["id"], job["kind"], job["status"], job["created"], job["updated"],
                    json.dumps(job["payload"]), json.dumps(job["result"]), job.get("error")))
            # ID-only comparator invokes the production registered-artifact loader,
            # hence has the same actual file/hash validity, but no query identity guard.
            loaded = _job_scene(store, job["id"], relative)
            id_only_accepts = loaded["metadata"]["sha256"] == source["sha256"]
            scenarios = [("native_join", native_smiles, sequence, True),
                         ("swapped_ligand_query", other_smiles, sequence, False),
                         ("one_residue_target_query_control", native_smiles, altered, False)]
            for kind, requested_smiles, requested_sequence, expected in scenarios:
                scene, matches, scanned, checked, truncated = selection._resolve_saved(
                    store, selection.exact_identity(requested_smiles), target["accession"],
                    requested_sequence, jobs=[job])
                accepted = scene is not None
                audit.check(f"{mode}/{compound}/{kind} production guard", accepted is expected)
                audit.check(f"{mode}/{compound}/{kind} actual file was inspected", checked == 1 and not truncated)
                rows.append({"case_id": f"{mode}/{compound}/{kind}", "origin": "controlled join using archived real coordinates",
                    "compound": compound, "msa_mode": mode, "job_id": job["id"], "case": kind,
                    "structure_sha256": source["sha256"], "requested_smiles": requested_smiles,
                    "requested_sequence_sha256": hashlib.sha256(requested_sequence.encode()).hexdigest(),
                    "expected_accept": expected, "id_only_accept": id_only_accepts,
                    "production_validator_accept": accepted, "observed_artifacts_checked": checked,
                    "matched_artifact": scene["metadata"]["artifact"] if scene else None})
            actual_sources.append({"job_id": job["id"], "compound": compound, "mode": mode,
                "source": source, "protein_residues": len(residues), "ligand_heavy_atoms": len(ligand_elements),
                "native_smiles": native_smiles, "raw_input_hints_omitted_in_temporary_copy": True})
    positive = [row for row in rows if row["expected_accept"]]
    negative = [row for row in rows if not row["expected_accept"]]
    return {"cases": rows, "actual_source_artifacts": actual_sources, "denominator": len(rows),
        "accepted_join_controls": len(positive), "deliberately_invalid_join_controls": len(negative),
        "production": {"native_accepted": sum(row["production_validator_accept"] for row in positive),
                       "invalid_rejected": sum(not row["production_validator_accept"] for row in negative)},
        "id_only": {"native_accepted": sum(row["id_only_accept"] for row in positive),
                    "invalid_rejected": sum(not row["id_only_accept"] for row in negative)},
        "design": "Four real top artifacts (two ligands × two MSA conditions), each with one native query, one other-ligand query and one deliberately one-residue-altered target query. Payload hints are omitted equally in temporary copies so the observed full sequence and ligand graph, not input metadata alone, determine acceptance.",
        "limitations": ["The ID-only comparator intentionally lacks query identity checks. It is a task-specific diagnostic ablation, not an implementation or benchmark of a prior system.",
            "The eight invalid pairings are deliberately constructed, not eight naturally observed failures or an estimate of user error prevalence.",
            "Four artifacts share two ligands and one target; conditions and repeated controls are not independent biological replicates.",
            "The altered target is an explicit synthetic single-residue query control, not a real mutation experiment or another biological target.",
            "This shows the tested join decision difference, not comparative utility, prediction accuracy, affinity or clinical benefit."]}


def archived_cases(audit):
    weights = audit.read("docs/af3-studio-ui-verification.json")["production"]
    molecule = audit.read("docs/molecular-selection-verification.json")["checks"]
    quantum = audit.read("docs/quantum-zero-kernel-latest-analysis-audit.json")
    race = audit.read("docs/quantum-selection-race-verification.json")
    cases = [
        {"case": "actual_test_parameter_blocker", "origin": "naturally encountered configuration, archived production check",
         "denominator": 1, "denominator_unit": "configuration episode, not number of correlated blocked jobs",
         "result": weights["standard"]["status"], "evidence": weights,
         "source": audit.source("docs/af3-studio-ui-verification.json", "/production")},
        {"case": "quarantined_prior_ibuprofen_output", "origin": "natural prior execution using the same test-parameter configuration",
         "denominator": 1, "denominator_unit": "prior artifact; correlated with the parameter episode, not an independent failure",
         "result": molecule["quarantined_ibuprofen_is_not_a_prediction"],
         "source": audit.source("docs/molecular-selection-verification.json", "/checks/quarantined_ibuprofen_is_not_a_prediction")},
        {"case": "actual_completed_all_zero_fidelity", "origin": "natural completed IBM measurement",
         "denominator": 10, "denominator_unit": "unique upper-triangle measurement circuits; 4 samples, 128 shots each, one provider job",
         "result": {key: quantum[key] for key in ("status", "provider_status", "pairs", "conclusion")},
         "source": audit.source("docs/quantum-zero-kernel-latest-analysis-audit.json")},
        {"case": "delayed_real_molecular_response", "origin": "deliberately delayed real archived/API response; timing injected, data real",
         "denominator": 1, "denominator_unit": "archived browser timing scenario; not a natural race prevalence",
         "result": molecule["late_previous_real_response_does_not_replace_current_selection"],
         "source": audit.source("docs/molecular-selection-verification.json", "/checks/delayed_result")},
        {"case": "delayed_real_quantum_parent_response", "origin": "deliberately delayed real response; timing injected, data real",
         "denominator": 1, "denominator_unit": "archived browser timing scenario",
         "result": {"delayed": race["delayed"], "selection_preserved": race["selection_preserved"]},
         "source": audit.source("docs/quantum-selection-race-verification.json")},
    ]
    bundle_path = "runtime/validation/bio-validation/source-bundle.json"
    bundle = audit.read(bundle_path)
    from herbfold.bio_validation import curate_assay_records
    assay_id = "CHEMBL5732036"
    records = [row for row in bundle["activities"] if row["assay_chembl_id"] == assay_id]
    scoped = {**bundle, "activities": records}
    curated, report = curate_assay_records(scoped)
    model_report = audit.read("runtime/validation/bio-validation/summary.json")
    model = next(row for row in model_report["models"] if row["assay_id"] == assay_id)
    audit.check("known semantic discrepancy remains explicit", "PTGES" in bundle["assays"][assay_id]["description"])
    audit.check("semantic discrepancy is not falsely credited to automation", len(curated) > 0 and
                model["quality_status"] == "insufficient_data")
    cases.append({"case": "target_id_and_assay_description_semantic_discrepancy",
        "origin": "natural source annotation inconsistency; found by manual manuscript semantic audit",
        "denominator": 1, "denominator_unit": "identified assay, not a systematic semantic-audit cohort",
        "assay_id": assay_id, "source_activity_rows": len(records), "metadata_curation_retained": len(curated),
        "curation": report, "description_excerpt": bundle["assays"][assay_id]["description"][:160],
        "model_quality_status": model["quality_status"], "model_data_counts": model.get("data_counts"),
        "automated_semantic_rejection": False,
        "interpretation": "Matching target IDs/confidence score do not authenticate assay meaning. This group was ineligible on sample/scaffold grounds independently of the later manual semantic review; the software has no general natural-language assay identity validator.",
        "source": audit.source(bundle_path, "/assays/" + assay_id)})
    return cases


def quantum_state_controls(audit):
    """Replay real legacy counts; inject provider status only in memory."""
    from herbfold import quantum
    original = audit.read(f"runtime/{SOURCE_ID}/quantum.json")
    raw_path = "runtime/quantum-audits/dafn10dnj4cs73agj6fg/raw-bitcounts.json"
    raw = audit.read(raw_path)
    audit.check("real legacy raw records match stored measurement count", len(raw["pubs"]) == len(original["jobs"][0]["pairs"]))
    rows = []
    for status, expected in (("DONE", "completed"), ("QUEUED", "running"), ("ERROR", "failed"),
                             ("CANCELLED", "failed"), ("NO_JOB", "credentials_required"),
                             ("WRONG_SHOT_TOTAL", "rejected")):
        manifest = copy.deepcopy(original)
        manifest.pop("kernel", None)
        manifest.pop("kernel_diagnostics", None)
        manifest["hardware_executed"] = None
        manifest["status"] = "submitted"
        for job in manifest["jobs"]:
            job.pop("observations", None)
        if status == "NO_JOB":
            manifest["jobs"] = []
            manifest["status"] = "credentials_required"
        counts = [dict(pub["counts"]) for pub in raw["pubs"]]
        if status == "WRONG_SHOT_TOTAL":
            counts[0][next(iter(counts[0]))] += 1
        pubs = [SimpleNamespace(data=SimpleNamespace(meas=SimpleNamespace(get_counts=lambda value=value: value)))
                for value in counts]
        fake_job = SimpleNamespace(status=lambda: "DONE" if status == "WRONG_SHOT_TOTAL" else status,
                                   result=lambda **kwargs: pubs, metrics=lambda: {})
        service = SimpleNamespace(job=lambda job_id: fake_job)
        error = None
        try:
            result = quantum.retrieve_kernel(manifest, service)
            actual = result["status"]
            kernel = result.get("kernel")
        except quantum.QuantumExecutionError as problem:
            actual, kernel, error = "rejected", None, str(problem)
        audit.check(f"legacy quantum state {status}", actual == expected)
        if status == "DONE":
            audit.check("actual zero counts remain an observed zero matrix", kernel == original["kernel"] and
                        all(value == 0 for row in kernel for value in row))
        else:
            audit.check(f"legacy quantum {status} never zero-filled", kernel is None)
        rows.append({"input_state": status, "origin": "raw-count replay with mocked transport" if status == "DONE" else
                     "explicit injected status/count control, not a naturally failed provider job",
                     "expected_state": expected, "actual_state": actual, "kernel_present": kernel is not None,
                     "measured_zero_matrix": kernel is not None and all(value == 0 for row in kernel for value in row),
                     "error": error})
    return {"cases": rows, "denominator": len(rows), "real_count_replays": 1, "injected_state_or_count_cases": 5,
            "source": audit.source(raw_path), "scientific_job_submissions": 0,
            "scope": "Calls the existing legacy retrieval function with a supplied in-memory provider double. Only DONE uses the actual observed state/counts; the other five are deliberately constructed controls. No provider contact or new measurement occurs."}


def selection_rules(audit):
    scale = audit.read("docs/scale-validation-results.json")
    cohort = audit.read("research/manuscript/data_audit.json")["sections"]["biochemical"]["cohort"]
    source_path = f"runtime/{SOURCE_ID}/analysis.json"
    original = audit.read(source_path)
    design_path = f"runtime/{SOURCE_ID}/candidate_design.json"
    design = audit.read(design_path)
    chemistry_path = f"runtime/{SOURCE_ID}/chemistry.json"
    chemistry = audit.read(chemistry_path)
    quantum = original["quantum"]
    concatenated = design["candidates"] + chemistry["compounds"]
    audit.check("quantum actual first-four order", [row["id"] for row in concatenated[:4]] == quantum["sample_ids"])
    features = [[row["descriptors"][key] / scale for key, scale in
                 (("molecular_weight", 650), ("logp", 6), ("tpsa", 180), ("qed", 1))]
                for row in concatenated[:4]]
    audit.check("quantum original feature values and order", features == quantum["feature_definition"]["features"])
    projected = audit.read("docs/quantum-projected-verification.json")
    # Receipt schema exposes the context directly; it preserves the original values.
    context = projected.get("context") or projected.get("source_context")
    if context is not None:
        audit.check("projected remeasurement exact source input", context["sample_ids"] == quantum["sample_ids"] and
                    context["feature_definition"] == quantum["feature_definition"])
    return {
        "scale_generation": {"source": audit.source("docs/scale-validation-results.json"),
            "catalog_max_id": scale["catalog_max_id"], "catalog_max_provenance_id": scale["catalog_max_provenance_id"],
            "requested_attempts": scale["requested_attempts"], "retained_unique": scale["retained_unique"],
            "natural_parent_sources": ["lotus-2026-04", "coconut-2026-09"], "drug_parent_sources": ["chembl-approved"],
            "selection_rule": "Source-role snapshot bounded by catalog and provenance maximum IDs; parents ordered by catalog ID; terminal fragments grouped by sorted BRICS joining labels then sorted by fragment-SMILES SHA-256. A row-major prefix of these compatible pair slots is generated.",
            "rng_seed": None, "rng_reason": "No random sampling in this enumerator; deterministic prefix is chemically ordered and is not a random population sample.",
            "functions": [audit.function("src/herbfold/scale_validation.py", name) for name in
                          ("ScaleValidation._parent_chunks", "_prepare_chunk", "ScaleValidation._groups", "ScaleValidation._operation_chunks")]},
        "reconstruction_500": {"source": audit.source("docs/scale-validation-results.json", "/audit"),
            "recorded_audit": {key: value for key, value in scale["audit"].items() if key != "top_qed"},
            "sample_query": "SELECT digest,smiles,left_fragment,right_fragment,attempt_index FROM candidates ORDER BY digest LIMIT 500",
            "rng_seed": None, "selection_rule": "Lowest 500 stored structure SHA-256 values; deterministic hash-ordered sample, not an RNG draw or exhaustive chemistry reconstruction.",
            "reconstruction_identity": "Replay the saved terminal-fragment joining operation; exact canonical isomeric SMILES and binary SHA-256 digest must match. Recreate each sampled distinct fragment from one recorded original parent using the same pinned RDKit/standardization implementation.",
            "pass_criterion": "Actual row count equals retained accounting; every stored filter column meets thresholds; all-row distinct-parent criterion has zero failures; sampled chemistry and parent-fragment replay have zero failures.",
            "limitations": "This is deterministic consistency/replay within the same implementation, not an independent chemical algorithm or a biological validation; only sampled parent-to-fragment reconstruction is performed. Full-row SQL checks use saved descriptors, not fresh descriptor recalculation for every row.",
            "function": audit.function("src/herbfold/scale_validation.py", "ScaleValidation.audit")},
        "bioassay_cohort": {**cohort, "function": audit.function("scripts/prepare_validation_cohort.py", "main")},
        "quantum_case": {"source_analysis_id": SOURCE_ID, "source": audit.source(source_path, "/quantum"),
            "case_selection": "A previously user-triggered quercetin/aspirin analysis whose legacy fidelity matrix collapsed to observed zero was chosen for the projected-method diagnostic. This is a convenience engineering case, not a random chemical panel or an efficacy-selected benchmark.",
            "generated_candidate_ids": [row["id"] for row in design["generated_candidates"]],
            "candidate_policy": design["candidate_policy"], "selected_candidate_ids": design["decision"]["selected_ids"],
            "archived_selection_decision": design["decision"], "sample_ids": quantum["sample_ids"],
            "feature_definition": quantum["feature_definition"],
            "input_order_rule": "Archived Astra-selected candidate IDs in decision order, followed by original chemistry inputs; first four rows for IBM, first eight for local. Remeasurement reuses the exact original ordered values.",
            "BRICS_rng_seed": None, "BRICS_rule": "Sorted parent pairs and fragment pool, maxDepth=2, scrambleReagents=False, uniquify=True, bounded enumeration. Candidate policy sorting occurs before the archived Astra selection.",
            "LLM_selection_seed": None, "LLM_seed_scope": "No deterministic LLM seed is established by the archived selection; the actual selected IDs/decision are preserved and no LLM is replayed.",
            "AF3_seed_per_condition": [1], "AF3_samples_per_condition": 5,
            "quantum_sampling_seed": None, "quantum_seed_scope": "Actual hardware shots are not assigned a reproducible classical RNG seed. Simulator/bootstrap seeds describe different secondary procedures.",
            "projected_transpiler_seed": 42, "projected_bootstrap_seed": 42,
            "functions": [audit.function("src/herbfold/orchestration.py", "Orchestrator._candidate_design"),
                          audit.function("src/herbfold/orchestration.py", "Orchestrator._quantum"),
                          audit.function("src/herbfold/chemistry.py", "generate_candidates"),
                          audit.function("scripts/verify_projected_quantum.py", "source_context")]},
    }


def campaign_snapshot(audit, out):
    path = out / "prior-campaign-selection-snapshot.json"
    if path.exists():
        return audit.read(path.relative_to(ROOT))
    database = ROOT / "runtime/discovery/discovery-campaigns.sqlite3"
    before = digest(database)
    records = []
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        for cid in ("f31db5de956c486daadc1d5c78ce0c2b", "5dc6a27485934703b902f65fff32aa98"):
            row = dict(connection.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone())
            request = json.loads(row.pop("request_json"))
            seeds = []
            for seed in connection.execute("SELECT * FROM campaign_seeds WHERE campaign_id=? ORDER BY role,position", (cid,)):
                data = json.loads(seed["data_json"])
                seeds.append({"role": seed["role"], "position": seed["position"], "smiles": seed["smiles"],
                    "id": data["id"], "name": data["name"], "source_records": [{key: original.get(key) for key in
                        ("id", "selected_source_id", "selected_source_url", "source_scope", "seed_query_resolution", "reference_status")}
                        for original in data["source_records"]]})
            for role in ("plant", "drug"):
                ordered = [seed["smiles"] for seed in seeds if seed["role"] == role]
                expected = sorted(ordered, key=lambda value: hashlib.sha256(f"{request['seed']}:{value}".encode()).hexdigest())
                audit.check(f"prior campaign {cid}/{role} actual seeded hash order", ordered == expected)
            records.append({"record": row, "request": request, "seed_snapshots": seeds})
        connection.rollback()
    audit.check("prior campaign database preserved", digest(database) == before)
    result = {"source_database": database.relative_to(ROOT).as_posix(), "source_database_sha256": before,
        "selected_campaigns": records, "seed_role_order_rule": "Within each source role, SHA-256 of '<seed>:<canonical SMILES>'; seed 42. This is deterministic seeded hash ordering, not a random sample drawn from the complete corpus.",
        "selection_scope": "These are the two older campaigns used in the prior 5,741-unique candidate cohort, not the separate 5-million-attempt scale run. Later campaigns are not included.",
        "retrieval_limit": "Archived source snapshots retain actual parent IDs and query hints. A campaign name or retrieval query does not authenticate a species–compound association or current drug approval. Missing query hints in the earlier campaign are left null, not inferred."}
    write_json(path, result)
    audit.source(path.relative_to(ROOT))
    return result


def portability_manifest(audit, out):
    packages = ("pytest", "rdkit", "numpy", "scipy", "scikit-learn", "biopython", "fastapi", "httpx",
                "pydantic", "qiskit", "qiskit-ibm-runtime", "python-dotenv", "uvicorn", "zstandard")
    versions = {name: importlib.metadata.version(name) for name in packages}
    result = {"python": sys.version, "versions": versions,
        "test_files": sorted({selector.split("::")[0] for selector in TESTS}),
        "fixtures": "Fixtures are generated inside the listed test modules using pytest tmp_path/monkeypatch. No tests/conftest.py or external recorded fixtures are required.",
        "source_tree_required": "src/herbfold/**/*.py (the API test imports the application modules), pyproject.toml for pytest pythonpath, data/compounds.json, data/ptgs2.json, data/molecular_references.json, and src/herbfold/static/. Static directory is mandatory because create_app mounts it during API fixtures; include the existing static files in clean source packages.",
        "excluded_optional_cases": ["test_actual_zstandard_container_and_split_compressed_frame[False]", "test_actual_zstandard_container_and_split_compressed_frame[True]", "test_zstandard_backend_enforces_window_in_bytes"],
        "external_dependencies_not_needed": ["AF3 checkout", "trained weights", "MSA databases", "GPU", "IBM credentials", "LLM API", "production SQLite"],
        "reconstruction_only": "scripts/manuscript_round1_constraints.py + src/herbfold Python modules + reconstruction-500-input.json; requires exact archived RDKit 2026.03.6 for exact canonical/descriptor replay; full 1.5GB SQLite is not required.",
        "full_audit_sources": [record for record in audit.sources.values() if not record["path"].startswith("research/manuscript/round1/constraints/")],
        "commands": ["python scripts/manuscript_round1_constraints.py --tests-only --output-dir reproduced/constraints", "python scripts/manuscript_round1_constraints.py --replay-reconstruction research/manuscript/round1/constraints/reconstruction-500-input.json --output-dir reproduced/constraints"],
        "native_library_caveat": "The current environment segfaulted in native Qiskit when all modules shared one pytest process. Per-module/per-selector isolation passed. This is a scoped execution workaround, not a fix or a passing claim for the combined invocation."}
    write_json(out / "portability-requirements.json", result)
    (out / "requirements-exact-observed.txt").write_text("\n".join(f"{name}=={version}" for name, version in versions.items()) + "\n")
    return result


def ledger(audit):
    definitions = [
      ("I01", "Requested ligand + full observed protein", "automated",
       [("src/herbfold/molecular_selection.py", "_resolve_saved"), ("src/herbfold/molecular_selection.py", "_graph_matches"), ("src/herbfold/molecular_selection.py", "_chain_matches")],
       "Exact stereo/charge-preserving canonical ligand, observed named atom/bond graph, complete pinned target sequence",
       "Another ligand, isomer/charge, missing ligand atoms, misleading job/payload name, incorrect observed target",
       ["test_selected_ligand_matches_real_chain_graph_and_selected_atom_ids", "test_payload_or_job_name_cannot_override_missing_or_mismatched_output_identity", "test_same_formula_different_declared_output_graph_is_not_a_match", "test_covalent_adduct_is_not_returned_as_intact_selected_free_ligand"],
       "12 designed joins separately; existing synthetic unit fixtures are a different denominator", "Not affinity or pose accuracy; canonical identity is not tautomer/protonation equivalence."),
      ("I02", "Job + registered file + content hash", "automated",
       [("src/herbfold/molecular_api.py", "_job_scene"), ("src/herbfold/storage.py", "Store.artifact")],
       "Registered confined artifact belonging to the selected job with matching saved SHA-256",
       "Changed bytes, unregistered filename, path traversal, another job's model",
       ["test_changed_artifact_is_not_returned", "test_file_from_other_job_cannot_be_selected_even_with_matching_chemistry"],
       "Parameterized/unit fixture cases; four actual intact top artifacts", "Hash consistency is not independent authentication of scientific provenance."),
      ("I03", "Selected diffusion sample + confidence/PAE", "automated with stated provenance limit",
       [("src/herbfold/af3_diagnostics.py", "selected_diagnostics"), ("src/herbfold/af3_diagnostics.py", "_pae")],
       "Exact selected file, same-sample confidence sibling, atom pLDDT/chain/token consistency",
       "Confidence from another sample, wrong selection, malformed/absent PAE, confidence symlink to a different sample",
       ["test_exact_selected_sample_stats_and_directional_pae", "test_wrong_selected_sample_identity_does_not_fall_back_to_good_top", "test_malformed_or_cross_sample_confidence_is_unavailable_without_invented_zero", "test_confidence_symlink_into_different_sample_is_rejected"],
       "Existing constructed sample/confidence fixtures; absence remains unavailable", "Older confidence files lack a previously signed hash; current SHA and consistency checks are not independent origin authentication."),
      ("I04", "Current selection + delayed response", "automated UI request guard",
       [], "Current molecule/target/mode/request generation only; prior scene cleared during loading",
       "A late response for the previously selected molecule or quantum parent",
       ["scripts/verify_molecular_selection.py", "scripts/verify_quantum_selection_race.py"],
       "Two archived injected timing scenarios with real response bodies, reported separately", "Browser timing was deliberately injected. No estimate of naturally occurring race frequency."),
      ("I05", "AF3 parameter/readiness + execution request", "automated bounded preflight",
       [("src/herbfold/af3_parameters.py", "inspect_parameters"), ("src/herbfold/af3_studio.py", "StudioPredictions.prepare")],
       "Runnable current parameter/configuration conditions, explicit search/none choice and required acknowledgement",
       "Known all-zero test identifier, missing/malformed parameters, blocked or changed readiness",
       ["test_zero_identifier_is_rejected_without_exposing_values", "test_nonzero_identifier_does_not_claim_authentication_or_full_validation", "test_blocked_preflight_has_no_fallback_or_launch", "test_execution_rechecks_changed_readiness_before_process"],
       "One natural parameter episode plus separately counted synthetic preflight fixtures", "A nonzero identifier alone does not authenticate trained weights; completed process alone is not a usable prediction."),
      ("I06", "Assay metadata + endpoint + measurement", "automated structured filters; manual source semantics",
       [("src/herbfold/bio_validation.py", "curate_assay_records")],
       "Source-annotated human exact target IDs, assay ID, endpoint, exact valid nM values; keep assay groups separate",
       "Wrong structured target ID, censored/invalid/variant measurements, contradictory same-assay duplicates",
       ["test_endpoint_assay_and_conflicting_replicates_are_not_pooled", "test_censored_flags_mutants_and_low_confidence_are_excluded"],
       "Existing unit fixtures; one naturally identified PTGES-description discrepancy is a separate manual case",
       "No automated natural-language assay identity validator: CHEMBL5732036 passes structured metadata curation and was ineligible for modeling for a different reason."),
      ("I07", "Measured observation vs model applicability", "automated abstention",
       [("src/herbfold/bio_validation.py", "_prediction_evidence")],
       "Exact archived assay observation retains assay context; eligible in-domain model predictions retain model context",
       "Unqualified model or out-of-domain query cannot become a numerical model prediction or clinical claim",
       ["test_out_of_domain_prediction_abstains_even_for_qualified_model", "test_exact_measurement_and_alerts_are_separate_from_clinical_claims"],
       "Existing fixture cases; actual 15,740 candidate analysis is a separate, previously measured cohort",
       "Abstained predictive models and exact observations can coexist on a candidate; not mutually exclusive patient outcomes."),
      ("I08", "Quantum zero vs missing/failed measurement", "automated state distinctions",
       [("src/herbfold/quantum.py", "retrieve_kernel"), ("src/herbfold/quantum_projected.py", "_decode"), ("src/herbfold/quantum_projected.py", "retrieve_projected")],
       "Complete provider/count records may yield an observed exact zero; it remains measured zero",
       "Invalid counts, queued/failed/partial jobs cannot be filled with fabricated zero kernels",
       ["test_decode_bit_order_and_wilson_not_global_all_zero", "test_invalid_counts_do_not_create_measured_kernel", "test_partial_submission_keeps_ids_and_never_fills_missing_observables", "test_qiskit_measurements_roundtrip_controls_bootstrap_and_raw_artifact"],
       "One actual IBM legacy run / ten measured circuits versus separately enumerated mocked state fixtures",
       "Observed zero at finite shots does not prove true probability zero. Projected diagonal one is defined, not measured self-fidelity."),
      ("I09", "Original quantum parent + ordered features/IDs", "automated",
       [("src/herbfold/api.py", "quantum_request_parts")],
       "New result is separately linked to exact original feature values and row IDs in their original order",
       "Changed values, reordered feature rows or changed sample-ID ordering",
       ["test_reanalysis_rejects_false_original_association_before_submission", "test_refresh_preserves_source_mapping_and_never_submits"],
       "Three deliberate association mutations tested against plan/run; actual four-row case separately preserved",
       "Exact association does not demonstrate quantum advantage or task utility."),
      ("I10", "Candidate + two distinct standardized parent ancestries", "automated replay/audit",
       [("src/herbfold/scale_validation.py", "ScaleValidation._single_parent_fragments"), ("src/herbfold/scale_validation.py", "ScaleValidation.audit")],
       "Nonempty source-role ancestry sets with at least one pair of distinct standardized parent structures",
       "Different source IDs/roles or salt IDs that reduce to the same sole standardized parent",
       ["test_two_source_roles_or_different_salt_ids_do_not_fake_distinct_parent_structures", "test_read_only_audit_does_not_replace_live_checkpoint_or_report"],
       "All-row lineage check versus 500 hash-selected chemistry reconstructions; distinct denominators",
       "Original three post-generation exclusions and archived time remain visible; no new generation or retrospective timing claim."),
    ]
    rows = []
    for key, title, level, funcs, accepted, rejected, tests, denominator, limitation in definitions:
        rows.append({"invariant_id": key, "contribution": title, "enforcement": level,
            "implementation": [audit.function(path, name) for path, name in funcs],
            "accepted_join": accepted, "rejected_or_unavailable_join": rejected,
            "existing_test_or_browser_script": tests, "denominator_scope": denominator, "limitations": limitation})
    ui = rows[3]
    ui["implementation"] = [{**audit.source(path), "anchor": anchor} for path, anchor in (
        ("frontend/src/App.tsx", "sceneRequest.current/requestId, AbortController, current() guard in structure useEffect"),
        ("frontend/src/components/AF3CalculationPanel.tsx", "latestSelection + latestId + abort guard before job completion callback"),
        ("frontend/src/components/QuantumPanel.tsx", "latest.current and AbortController preserve selected linked job"))]
    for path in ("scripts/verify_molecular_selection.py", "scripts/verify_quantum_selection_race.py"):
        audit.source(path)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--run-tests", action="store_true", help="Run the bounded existing CPU fixture subset in fresh temporary storage")
    parser.add_argument("--tests-only", action="store_true", help="Run only isolated CPU fixtures; no archived raw reports/structures needed")
    parser.add_argument("--export-reconstruction", action="store_true", help="Read the archived SQLite once and export its exact lowest-digest 500 sample")
    parser.add_argument("--replay-reconstruction", type=Path, help="Replay only an exported 500 sample; no full SQLite or other archived reports required")
    args = parser.parse_args()
    out = args.output_dir.resolve()
    allowed = (OUT.resolve(), (ROOT / "tmp").resolve(), (ROOT / "reproduced").resolve())
    if not any(out.is_relative_to(directory) for directory in allowed):
        raise ValueError("Outputs must stay within round1/constraints, ROOT/tmp or ROOT/reproduced")
    out.mkdir(parents=True, exist_ok=True)
    if args.replay_reconstruction:
        print(json.dumps(replay_reconstruction(args.replay_reconstruction, out)))
        return
    if args.run_tests or args.tests_only:
        run_tests(out)
    audit = Audit()
    if args.tests_only:
        result = test_inventory(audit, out)
        write_json(out / "tests-only-verification.json", result)
        print(json.dumps({"passed": True, "tests": result["cases"], "scope": "isolated CPU fixture subset"}))
        return
    if args.export_reconstruction:
        export_reconstruction(audit, out)
    reconstruction = None
    if (out / "reconstruction-500-input.json").exists():
        audit.source((out / "reconstruction-500-input.json").relative_to(ROOT))
        reconstruction = replay_reconstruction(out / "reconstruction-500-input.json", out)
        receipt_path = out / "reconstruction-selection-receipt.json"
        if receipt_path.exists():
            selection_receipt = audit.read(receipt_path.relative_to(ROOT))
            audit.check("reconstruction subset matches original export receipt",
                        selection_receipt["input_sha256"] == reconstruction["input_sha256"])
            reconstruction["full_database_selection_receipt"] = audit.source(receipt_path.relative_to(ROOT))
    tests = test_inventory(audit, out)
    joins = controlled_joins(audit)
    natural = archived_cases(audit)
    quantum_states = quantum_state_controls(audit)
    rules = selection_rules(audit)
    rules["prior_campaigns"] = campaign_snapshot(audit, out)
    contributions = ledger(audit)
    with (out / "existing-cpu-test-cases.csv").open() as table:
        executed = list(csv.DictReader(table))
    for contribution in contributions:
        names = contribution["existing_test_or_browser_script"]
        matched = [row for row in executed if row["test_id"].split("::", 1)[1].split("[", 1)[0] in names]
        contribution["executed_cpu_cases"] = [{"test_id": row["test_id"], "status": row["status"]} for row in matched]
        contribution["executed_cpu_case_count"] = len(matched)
    portability = portability_manifest(audit, out)
    prior_failures = []
    for path in sorted(out.glob("*-failed-run.json")):
        previous = audit.read(path.relative_to(ROOT))
        prior_failures.append({"receipt": path.name, "exit_code": previous["exit_code"],
            "description": "Environment resolution failure" if "environment" in path.name else
            "Native Qiskit segmentation fault in combined-process selected suite; unresolved for that invocation"})
    audit.source(Path(__file__).absolute().relative_to(ROOT))
    for relative, record in audit.sources.items():
        audit.check("source unchanged: " + relative, digest(ROOT / relative) == record["sha256"])
    result = {"schema_version": 1, "generated_at": datetime.now(UTC).isoformat(), "passed": True,
        "scope": "R1-1/R1-2 implementation-to-artifact accounting and controlled join diagnostic, not a clinical or competitor benchmark",
        "new_af3_qpu_llm_jobs": 0, "existing_cpu_tests": tests, "controlled_join_diagnostic": joins,
        "exported_reconstruction_replay": reconstruction,
        "prior_unsuccessful_test_invocations": prior_failures, "portability": portability,
        "quantum_state_diagnostic": quantum_states,
        "archived_case_evidence": natural, "selection_and_reconstruction_rules": rules,
        "contribution_to_artifact_ledger": contributions, "checks": audit.checks,
        "sources": list(audit.sources.values()), "denominator_policy": "Never pool real configuration episodes, real provider circuits, injected UI timings, 12 designed joins and parametrized unit-test cases into an accuracy or reliability rate."}
    write_json(out / "constraint-audit.json", result)
    write_csv(out / "controlled-joins.csv", joins["cases"])
    write_csv(out / "contribution-artifact-ledger.csv", contributions)
    write_json(out / "selection-and-reconstruction-rules.json", rules)
    lines = ["# R1 constraint audit", "", "The contribution is the explicit, tested binding of requests to their own evidence artifacts. The diagnostic does not establish comparative utility over another system.", "",
        f"- Existing CPU tests: {tests['passed']}/{tests['cases']} collected fixture cases passed.",
        f"- Controlled joins: {joins['production']['native_accepted']}/4 native joins accepted and {joins['production']['invalid_rejected']}/8 deliberately invalid joins rejected. ID-only accepted all 12 by design.",
        "- Real source coordinates were not changed. Input hints were omitted equally in isolated copies to exercise observed-sequence/graph verification.",
        "- Two earlier combined-process runs segfaulted in native Qiskit. The isolated-process result does not resolve or conceal that limitation; failed receipts/logs remain archived.",
        "- The PTGES-description discrepancy is a manual source-semantic finding, not an automated semantic rejection.",
        "- The 500 reconstruction entries are the lowest structure SHA-256 values, not an RNG draw. All-row SQL checks and sampled same-implementation reconstruction are distinct.",
        "- The four quantum inputs are archived Astra selections plus two parent inputs, not a random molecular panel. No LLM, AF3 or QPU run was made for this audit.", "",
        "Run: `.venv/bin/python scripts/manuscript_round1_constraints.py --run-tests`", "",
        "The JSON and CSV files retain per-case outcomes, functions/lines, artifact hashes, existing test references, scope limitations and separate denominators."]
    (out / "README.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"passed": True, "checks": len(audit.checks), "tests": tests["cases"], "controlled_joins": len(joins["cases"]), "report": str(out / "constraint-audit.json")}))


if __name__ == "__main__":
    main()
