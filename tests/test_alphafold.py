import json
import sys
from pathlib import Path

import pytest

from herbfold import alphafold as af3


def valid_input(**kwargs):
    return af3.build_input(
        "herb_ligand",
        [{"id": "A", "sequence": "ACDEFGHIKLMNPQRSTVWY"}],
        [{"id": "B", "smiles": "C[C@H](O)C(=O)O"}],
        **kwargs,
    )


def test_export_schema_seed_stereochemistry_and_msa_contract():
    data = valid_input(seeds=[0, 2**32 - 1], msa_mode="none")
    assert data["version"] == 4
    assert data["dialect"] == "alphafold3"
    assert data["modelSeeds"] == [0, 2**32 - 1]
    assert "@" in data["sequences"][1]["ligand"]["smiles"]
    assert data["sequences"][0]["protein"]["templates"] == []
    assert data["sequences"][0]["protein"]["unpairedMsa"] == ""
    assert af3.validate_input(data) == (data, "none")
    assert "unpairedMsa" not in valid_input()["sequences"][0]["protein"]


@pytest.mark.parametrize("seeds", [[], [True], [-1], [2**32], [1.0], [1, 1]])
def test_invalid_seeds(seeds):
    with pytest.raises(af3.AF3ValidationError):
        valid_input(seeds=seeds)


@pytest.mark.parametrize("smiles", ["garbage", "C1CC", "C C", "*CC", "CCO.[Na+]", ""])
def test_invalid_or_disconnected_ligands(smiles):
    with pytest.raises(af3.AF3ValidationError):
        af3.build_input("job", [{"sequence": "ACDE"}], [{"smiles": smiles}])


@pytest.mark.parametrize("name", ["../../etc/passwd", "$(touch hacked)", "two words", "한약", "", "-option"])
def test_names_cannot_control_paths_or_commands(name):
    with pytest.raises(af3.AF3ValidationError):
        af3.build_input(name, [{"sequence": "ACDE"}], [{"smiles": "CCO"}])


def test_multichain_validation_and_descriptions():
    data = af3.build_input(
        "homomer",
        [{"id": ["A", "C"], "sequence": "ac de", "description": "target"}],
        [{"smiles": "CCO", "description": "reference ligand"}],
    )
    assert data["sequences"][0]["protein"]["sequence"] == "ACDE"
    assert data["sequences"][1]["ligand"]["id"] == "B"
    assert data["sequences"][1]["ligand"]["description"] == "reference ligand"
    with pytest.raises(af3.AF3ValidationError, match="Duplicate"):
        af3.build_input("job", [{"id": ["A", "B"], "sequence": "ACDE"}], [{"id": "B", "smiles": "CCO"}])
    with pytest.raises(af3.AF3ValidationError, match="Protein sequence"):
        af3.build_input("job", [{"sequence": ">FASTA\nACDE"}], [{"smiles": "CCO"}])


def test_reject_arbitrary_file_and_mixed_msa_inputs():
    data = valid_input()
    data["sequences"][0]["protein"]["unpairedMsaPath"] = "/etc/passwd"
    with pytest.raises(af3.AF3ValidationError, match="Unsupported"):
        af3.validate_input(data)
    data = valid_input()
    data["userCCDPath"] = "../../private"
    with pytest.raises(af3.AF3ValidationError):
        af3.validate_input(data)
    data = valid_input(msa_mode="none")
    data["sequences"].append({"protein": {"id": "C", "sequence": "ACDE"}})
    with pytest.raises(af3.AF3ValidationError, match="same MSA"):
        af3.validate_input(data)


def test_native_cpu_command_is_shell_free_and_database_free(tmp_path):
    config = af3.AF3Config(
        repo_dir=tmp_path / "source with spaces", model_dir=tmp_path / "models", device="cpu"
    )
    command, cwd = af3.build_command(config, tmp_path / "input.json", tmp_path / "out", msa_mode="none")
    assert command[0] == sys.executable
    assert command[1] == str(tmp_path / "source with spaces/run_alphafold.py")
    assert cwd == str(config.repo_dir)
    assert "--run_data_pipeline=false" in command
    assert "--jax_backend=cpu" in command
    assert "--flash_attention_implementation=xla" in command
    assert "--num_diffusion_samples=5" in command
    assert not any(arg.startswith("--db_dir") for arg in command)


def test_docker_gpu_command_mounts_data_read_only(tmp_path):
    config = af3.AF3Config(runner="docker", model_dir=tmp_path / "models", database_dir=tmp_path / "db")
    command, cwd = af3.build_command(config, tmp_path / "input/in.json", tmp_path / "output")
    assert command[:4] == ["docker", "run", "--rm", "--network=none"]
    assert cwd is None
    assert "--gpus" in command
    assert f"{tmp_path}/models:/models:ro" in command
    assert f"{tmp_path}/db:/databases:ro" in command
    assert f"{tmp_path}/output:/af_output:rw" in command
    assert "--db_dir=/databases" in command
    assert "--run_data_pipeline=true" in command


def test_docker_rejects_mount_syntax_injection(tmp_path):
    config = af3.AF3Config(runner="docker", model_dir=tmp_path / "models:ro", device="cpu")
    with pytest.raises(af3.AF3ValidationError, match="colons"):
        af3.build_command(config, tmp_path / "input.json", tmp_path / "output", msa_mode="none")


def test_unconfigured_preparation_produces_input_without_structure(tmp_path):
    config = af3.AF3Config(repo_dir=tmp_path / "missing", model_dir=tmp_path / "no_weights")
    result = af3.prepare_job(tmp_path / "job", valid_input(msa_mode="none"), config)
    assert not result["ready"]
    assert any("AF3_MODEL_DIR" in error for error in result["blockers"])
    assert Path(result["input_path"]).exists()
    assert (tmp_path / "job/af3_manifest.json").exists()
    assert not list(Path(result["output_dir"]).rglob("*.cif"))
    assert af3.parse_outputs(result["output_dir"])["models"] == []
    with pytest.raises(af3.AF3ValidationError, match="overwritten"):
        af3.prepare_job(tmp_path / "job", valid_input(seeds=[2], msa_mode="none"), config)


def write_result(root, prefix="job", *, metrics=None, sample=None):
    directory = root / "job"
    if sample:
        directory = directory / sample
    directory.mkdir(parents=True, exist_ok=True)
    summary = {
        "ptm": 0.7,
        "iptm": 0.8,
        "ranking_score": 0.78,
        "has_clash": False,
        "fraction_disordered": 0,
        "chain_ids": ["A", "B"],
        "chain_pair_iptm": [[0.7, 0.8], [0.8, 0.6]],
        "chain_ptm": [0.7, 0.6],
        "chain_pair_pae_min": [[0.5, 2.0], [3.0, 0.6]],
    }
    if metrics:
        summary.update(metrics)
    (directory / f"{prefix}_summary_confidences.json").write_text(json.dumps(summary))
    (directory / f"{prefix}_model.cif").write_text(
        "data_test\nloop_\n_atom_site.id\n_atom_site.Cartn_x\n_atom_site.Cartn_y\n_atom_site.Cartn_z\n1 0.1 0.2 0.3\n"
    )
    return directory


def test_real_output_contract_preserves_chain_confidence_and_null_affinity(tmp_path):
    write_result(tmp_path)
    write_result(tmp_path, "job_seed-7_sample-2", sample="seed-7_sample-2", metrics={"ranking_score": 0.5})
    imported = af3.parse_outputs(tmp_path)
    assert imported["affinity_prediction"] is None
    assert imported["execution_verified"] is False
    assert len(imported["models"]) == 2
    top, sample = imported["models"]
    assert top["metrics"]["chain_pair_iptm"][0][1] == 0.8
    assert top["chain_ids"] == ["A", "B"]
    assert top["structure_path"] == "job/job_model.cif"
    assert top["is_top_ranked_copy"]
    assert sample["seed"] == 7 and sample["sample"] == 2


def test_missing_nan_and_clashing_confidences_are_not_invented(tmp_path):
    write_result(tmp_path, metrics={"iptm": None, "ranking_score": None, "has_clash": 1.0})
    imported = af3.parse_outputs(tmp_path)
    assert imported["models"][0]["metrics"]["iptm"] is None
    assert len(imported["warnings"]) == 2


@pytest.mark.parametrize(
    "metrics",
    [
        {"ptm": 1.1},
        {"iptm": float("nan")},
        {"has_clash": "false"},
        {"chain_pair_iptm": [[1, 0.5]]},
        {"chain_ids": ["A", "A"]},
    ],
)
def test_invalid_confidence_files_rejected(tmp_path, metrics):
    write_result(tmp_path, metrics=metrics)
    with pytest.raises(af3.AF3ValidationError):
        af3.parse_outputs(tmp_path)


def test_missing_structure_rejected(tmp_path):
    directory = write_result(tmp_path)
    (directory / "job_model.cif").unlink()
    with pytest.raises(af3.AF3ValidationError, match="missing"):
        af3.parse_outputs(tmp_path)


def test_output_traversal_and_symlinks_rejected(tmp_path):
    root = tmp_path / "outputs"
    directory = write_result(root)
    outside = tmp_path / "private.cif"
    outside.write_text("private")
    for path in ("../private.cif", str(outside), "..\\private.cif"):
        with pytest.raises(af3.AF3ValidationError):
            af3.safe_output_path(root, path)
    (directory / "job_model.cif").unlink()
    (directory / "job_model.cif").symlink_to(outside)
    with pytest.raises(af3.AF3ValidationError, match="outside"):
        af3.parse_outputs(root)


def test_preparation_cannot_follow_external_symlink(tmp_path):
    directory = tmp_path / "job"
    directory.mkdir()
    private = tmp_path / "private"
    private.write_text("unchanged")
    (directory / "fold_input.json").symlink_to(private)
    with pytest.raises(af3.AF3ValidationError, match="within"):
        af3.prepare_job(directory, valid_input(), af3.AF3Config())
    assert private.read_text() == "unchanged"


def test_af3_cuda_environment_avoids_global_toolkit_conflict(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/usr/local/cuda-12.8/lib64:")
    monkeypatch.delenv("AF3_LD_LIBRARY_PATH", raising=False)
    monkeypatch.setenv("AF3_CUDA_VISIBLE_DEVICES", "1")
    monkeypatch.delenv("AF3_PREALLOCATE", raising=False)
    env = af3.execution_environment()
    assert "LD_LIBRARY_PATH" not in env
    assert env["CUDA_VISIBLE_DEVICES"] == "1"
    assert env["XLA_PYTHON_CLIENT_PREALLOCATE"] == "false"
    monkeypatch.setenv("AF3_LD_LIBRARY_PATH", "/trusted/custom/cuda")
    assert af3.execution_environment()["LD_LIBRARY_PATH"] == "/trusted/custom/cuda"


def test_manifest_never_persists_full_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("EXAMPLE_SECRET_TOKEN", "DO_NOT_PERSIST_THIS")
    plan = af3.prepare_job(tmp_path / "job", valid_input(msa_mode="none"), af3.AF3Config())
    assert "DO_NOT_PERSIST_THIS" not in json.dumps(plan)
    assert "EXAMPLE_SECRET_TOKEN" not in (tmp_path / "job/af3_manifest.json").read_text()


def test_af3_xla_flags_are_child_only_and_archived(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("XLA_FLAGS", "--unrelated-global-option")
    monkeypatch.setenv("AF3_XLA_FLAGS", "--xla_gpu_autotune_level=3")
    env = af3.execution_environment()
    assert env["XLA_FLAGS"] == "--xla_gpu_autotune_level=3"
    assert os.environ["XLA_FLAGS"] == "--unrelated-global-option"
    plan = af3.prepare_job(tmp_path / "job", valid_input(msa_mode="none"), af3.AF3Config())
    assert plan["provenance"]["cuda_environment"]["XLA_FLAGS"] == "--xla_gpu_autotune_level=3"


def test_parameter_audit_blocks_execution_before_any_jax_probe(tmp_path, monkeypatch):
    from herbfold import af3_parameters

    (tmp_path / "run_alphafold.py").write_text("# synthetic fixture")
    monkeypatch.setattr(
        af3_parameters,
        "inspect_parameters",
        lambda config: {
            "status": "test_parameters",
            "runnable": False,
            "blockers": ["Random performance-test parameters are not prediction weights"],
            "provenance": {"trained_parameters_authenticated": False, "stat_fingerprint_sha256": "synthetic"},
        },
    )
    commands = []

    def probe(command, *args, **kwargs):
        commands.append(command)
        return {"ok": True, "stdout": af3.AF3_COMMIT if "rev-parse" in command else ""}

    monkeypatch.setattr(af3, "_probe", probe)
    result = af3.capabilities(
        af3.AF3Config(repo_dir=tmp_path, model_dir=tmp_path), msa_mode="none", probe_runtime=True
    )
    assert result["runnable"] is False and result["parameter_status"] == "test_parameters"
    assert result["provenance"]["parameters"]["stat_fingerprint_sha256"] == "synthetic"
    assert all(command[0] == "git" for command in commands)


def test_af3_304_actual_token_level_summary_chain_ids(tmp_path):
    # Regression from a real pinned 3.0.4 inference: summary chain_ids repeats
    # IDs for every token while per-chain confidence arrays remain chain-sized.
    write_result(tmp_path, metrics={"chain_ids": ["B"] * 604 + ["A"] * 15})
    result = af3.parse_outputs(tmp_path)
    assert result["models"][0]["chain_ids"] == ["B", "A"]
    assert result["models"][0]["metrics"]["chain_pair_iptm"][0][1] == 0.8
    assert any("token-level" in warning for warning in result["warnings"])


def test_af3_token_level_chain_ids_reject_noncontiguous_groups(tmp_path):
    write_result(tmp_path, metrics={"chain_ids": ["A", "B", "A"]})
    with pytest.raises(af3.AF3ValidationError, match="contiguous"):
        af3.parse_outputs(tmp_path)
