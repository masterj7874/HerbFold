"""Small, explicitly synthetic contracts; these fixtures are not scientific MSAs."""

import copy
import hashlib
import json
from dataclasses import replace

import pytest

from herbfold import af3_features as features
from herbfold import alphafold

TEMPLATE = """data_SYNTHETIC_TEMPLATE
loop_
_entity_poly_seq.entity_id
_entity_poly_seq.num
_entity_poly_seq.mon_id
1 1 ALA
1 2 CYS
1 3 ASP
loop_
_atom_site.label_asym_id
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
A 0 0 0
A 4 0 0
A 8 0 0
"""


def data():
    return {
        "sequence": "ACD",
        "unpairedMsa": ">SYNTHETIC_QUERY\nACD\n>SYNTHETIC_HIT\nAcC-\n",
        "pairedMsa": ">SYNTHETIC_QUERY\nACD\n",
        "templates": [{"mmcif": TEMPLATE, "queryIndices": [0, 1, 2], "templateIndices": [0, 1, 2]}],
    }


def payload(smiles="CCO", sequence="ACD"):
    return alphafold.build_input(
        "SYNTHETIC_input", [{"id": "A", "sequence": sequence}], [{"id": "B", "smiles": smiles}]
    )


def expected(tmp_path, value=None):
    config = alphafold.AF3Config(repo_dir=tmp_path, model_dir=tmp_path, database_dir=tmp_path)
    return features.identity(value or payload(), config, {"databases": {"fingerprint_sha256": "f" * 64}})


def publish(tmp_path):
    source = tmp_path / "data_pipeline"
    source.mkdir()
    processed = payload()
    processed["sequences"][0]["protein"].update(data())
    (source / "synthetic_data.json").write_text(json.dumps(processed))
    cache = features.FeatureCache(tmp_path)
    loaded = cache.publish(
        expected(tmp_path),
        payload(),
        source,
        "SYNTHETIC_SOURCE_JOB",
        {"return_code": 0, "finished_at": "2026-09-08", "name": "data_pipeline"},
    )
    return cache, loaded


def test_actual_alignment_counts_inline_template_and_no_binding_claims():
    report = features.validate_features(data(), "ACD")
    assert report["unpaired_msa_sequences"] == 2
    assert report["non_query_sequences"] == 1
    assert report["paired_msa_sequences"] == 1
    assert report["paired_non_query_sequences"] == 0
    assert report["template_count"] == 1
    assert report["templates"][0]["mapped_residues"] == 3
    assert "affinity" not in report


@pytest.mark.parametrize(
    "field,value",
    [
        ("unpairedMsa", ""),
        ("pairedMsa", None),
        ("unpairedMsa", ">wrong\nCCD\n"),
        ("unpairedMsa", ">query\nACD\n>short\nAC\n"),
        ("templates", None),
    ],
)
def test_partial_empty_or_wrong_protein_features_are_rejected(field, value):
    malformed = data()
    malformed[field] = value
    with pytest.raises(alphafold.AF3ValidationError):
        features.validate_features(malformed, "ACD")


@pytest.mark.parametrize("change", ["path", "wrong_index", "nonfinite", "missing_coordinates"])
def test_templates_need_observed_coordinates_and_valid_residue_mappings(change):
    malformed = data()
    item = malformed["templates"][0]
    if change == "path":
        item["mmcifPath"] = "/etc/passwd"
    elif change == "wrong_index":
        item["templateIndices"] = [0, 1, 3]
    elif change == "nonfinite":
        item["mmcif"] = TEMPLATE.replace("A 4 0 0", "A nan 0 0")
    else:
        item["mmcif"] = TEMPLATE.split("_atom_site")[0]
    with pytest.raises(alphafold.AF3ValidationError):
        features.validate_features(malformed, "ACD")


def test_completed_no_hit_template_search_is_explicit_not_missing():
    value = data()
    value["templates"] = []
    value["unpairedMsa"] = ">query\nACD\n"
    report = features.validate_features(value, "ACD")
    assert report["template_count"] == report["non_query_sequences"] == 0
    assert any("no usable templates" in item for item in report["warnings"])
    assert any("only the query" in item for item in report["warnings"])


def test_cache_survives_restart_and_reuses_only_protein_preserving_new_ligand(tmp_path):
    _, first = publish(tmp_path)
    assert first[1]["cache_hit"] is False
    cache = features.FeatureCache(tmp_path)
    loaded = cache.load(expected(tmp_path), "ACD")
    assert loaded[1]["cache_hit"] is True
    assert loaded[1]["source_job_id"] == "SYNTHETIC_SOURCE_JOB"
    new_input = payload("CCN")
    original = copy.deepcopy(new_input)
    path = tmp_path / "inference_input.json"
    receipt = features.write_inference_input(path, new_input, loaded[0])
    assert new_input == original
    output = json.loads(path.read_text())
    assert output["sequences"][1]["ligand"]["smiles"] == "CCN"
    assert output["sequences"][0]["protein"]["unpairedMsa"] == data()["unpairedMsa"]
    assert receipt["inference_input_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(alphafold.AF3ValidationError, match="custom MSA"):
        alphafold.validate_input(output)  # internal enrichments never open public file/MSA input


def test_cache_key_ignores_ligand_seed_but_requires_full_sequence_db_version_cutoff(tmp_path):
    value = expected(tmp_path)
    another = payload("CCN")
    another["modelSeeds"] = [21]
    assert expected(tmp_path, another) == value
    assert expected(tmp_path, payload(sequence="ACDE")) != value
    for field in ("database_fingerprint", "af3_commit", "max_template_date"):
        changed = {**value, field: "changed"}
        assert features.cache_key(changed) != features.cache_key(value)
    config = alphafold.AF3Config(database_dir=tmp_path)
    newer = replace(config, max_template_date="2025-01-01")
    assert features.identity(payload(), newer, {"databases": {"fingerprint_sha256": "f" * 64}}) != value


def test_corrupt_partial_or_wrong_identity_cache_is_never_loaded(tmp_path):
    cache, loaded = publish(tmp_path)
    directory = cache.root / loaded[1]["cache_key"]
    manifest = directory / "manifest.json"
    original = manifest.read_bytes()
    manifest.unlink()
    assert cache.load(expected(tmp_path), "ACD") is None
    manifest.write_bytes(original)
    assert cache.load(expected(tmp_path), "CCC") is None
    file = directory / "protein_features.json"
    file.write_bytes(file.read_bytes().replace(b"AcC-", b"AaC-"))
    assert cache.load(expected(tmp_path), "ACD") is None


def test_no_publication_on_failed_or_ambiguous_upstream_output(tmp_path):
    cache = features.FeatureCache(tmp_path)
    with pytest.raises(alphafold.AF3ValidationError, match="successfully completed"):
        cache.publish(expected(tmp_path), payload(), tmp_path, "failed", {"return_code": 7})
    with pytest.raises(alphafold.AF3ValidationError, match="exactly one"):
        cache.publish(
            expected(tmp_path), payload(), tmp_path, "empty", {"return_code": 0, "finished_at": "now"}
        )
    assert not list(cache.root.glob("*/manifest.json"))


def test_data_pipeline_command_has_no_gpu_and_inference_does_not_search_again(tmp_path, monkeypatch):
    monkeypatch.setenv("AF3_CUDA_VISIBLE_DEVICES", "1")
    monkeypatch.setenv("AF3_XLA_FLAGS", "--xla_gpu_autotune_level=3")
    config = alphafold.AF3Config(runner="docker", model_dir=tmp_path, database_dir=tmp_path)
    command, _ = alphafold.build_command(
        config, tmp_path / "in.json", tmp_path / "out", stage="data_pipeline"
    )
    assert "--gpus" not in command
    assert "--run_inference=false" in command and "--run_data_pipeline=true" in command
    assert "--jax_backend=cpu" in command
    assert "CUDA_VISIBLE_DEVICES=" in command and "JAX_PLATFORMS=cpu" in command
    assert "XLA_FLAGS=--xla_gpu_autotune_level=3" in command
    command, _ = alphafold.build_command(config, tmp_path / "in.json", tmp_path / "out", stage="inference")
    assert "--gpus" in command
    assert "CUDA_VISIBLE_DEVICES=1" in command
    assert "--run_data_pipeline=false" in command and "--run_inference=true" in command
    assert "--num_diffusion_samples=5" in command and "--num_recycles=10" in command
