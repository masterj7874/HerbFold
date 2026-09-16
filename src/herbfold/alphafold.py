"""Pinned AlphaFold 3 input, execution planning, and real output import.

No model weights or synthetic predictions are included. Model confidence is
structural confidence, never a binding affinity or evidence of efficacy.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

AF3_VERSION = "3.0.4"
AF3_COMMIT = "85c4d20505fd5cef05eac22b534d4e793971ae69"
AF3_SCHEMA_VERSION = 4
AF3_RELEASE_URL = "https://github.com/google-deepmind/alphafold3/releases/tag/v3.0.4"
AF3_VERIFIED_DATE = "2026-09-07"
CONFIDENCE_NOTE = (
    "AlphaFold 3 confidence describes predicted structure quality. It is not "
    "binding affinity (Kd/Ki/IC50), a validated binding probability, or efficacy."
)


class AF3ValidationError(ValueError):
    """An input or imported result fails the supported AF3 contract."""


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise AF3ValidationError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _chain_name(index: int) -> str:
    result = ""
    while index >= 0:
        result = chr(65 + index % 26) + result
        index = index // 26 - 1
    return result


def _ids(value: Any, used: set[str], default: str) -> str | list[str]:
    value = default if value is None else value
    ids = value if isinstance(value, list) else [value]
    if not ids or len(ids) > 128:
        raise AF3ValidationError("Each entity needs between 1 and 128 chain IDs")
    for chain in ids:
        if not isinstance(chain, str) or not re.fullmatch(r"[A-Z]{1,8}", chain):
            raise AF3ValidationError("Chain IDs must contain 1–8 uppercase ASCII letters")
        if chain in used:
            raise AF3ValidationError(f"Duplicate chain ID: {chain}")
        used.add(chain)
    return value


def _description(entity: Mapping[str, Any], output: dict[str, Any]) -> None:
    if "description" in entity:
        value = entity["description"]
        if not isinstance(value, str) or len(value) > 2000:
            raise AF3ValidationError("description must be a string of at most 2000 characters")
        output["description"] = value


def build_input(
    name: str,
    proteins: Sequence[Mapping[str, Any]],
    ligands: Sequence[Mapping[str, Any]],
    seeds: Sequence[int] | None = None,
    msa_mode: str = "search",
) -> dict[str, Any]:
    """Export the validated protein/SMILES subset of AF3 dialect v4.

    ``search`` leaves MSA/template fields absent so AF3 searches databases.
    ``none`` sets both MSAs to empty strings and templates to an empty list;
    it explicitly requests an MSA-free, template-free run. Protein sequences
    accept the 20 canonical residues and X. Arbitrary filesystem fields,
    custom CCD, covalent links, RNA, and DNA require the upstream expert API.
    """
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", name):
        raise AF3ValidationError(
            "name must be 1–100 ASCII letters/digits/dots/dashes/underscores, starting with a letter or digit"
        )
    if msa_mode not in {"search", "none"}:
        raise AF3ValidationError("msa_mode must be 'search' or 'none'")
    if not isinstance(proteins, (list, tuple)) or not 1 <= len(proteins) <= 128:
        raise AF3ValidationError("Provide 1–128 protein entities")
    if not isinstance(ligands, (list, tuple)) or not 1 <= len(ligands) <= 128:
        raise AF3ValidationError("Provide 1–128 ligand entities")
    seeds = [1] if seeds is None else list(seeds)
    if not 1 <= len(seeds) <= 100:
        raise AF3ValidationError("Provide 1–100 model seeds")
    for seed in seeds:
        _integer(seed, "model seed", 0, 2**32 - 1)
    if len(set(seeds)) != len(seeds):
        raise AF3ValidationError("Model seeds must be unique")
    used: set[str] = set()
    sequences: list[dict[str, Any]] = []
    total_residues = 0
    for kind, items in (("protein", proteins), ("ligand", ligands)):
        for entity in items:
            if not isinstance(entity, Mapping):
                raise AF3ValidationError("Each entity must be an object")
            allowed = {"id", "description", "sequence" if kind == "protein" else "smiles"}
            if extra := set(entity) - allowed:
                raise AF3ValidationError(f"Unsupported {kind} fields: {sorted(extra)}")
            index = 0
            while _chain_name(index) in used:
                index += 1
            chain_ids = _ids(entity.get("id"), used, _chain_name(index))
            result: dict[str, Any] = {"id": chain_ids}
            copies = len(chain_ids) if isinstance(chain_ids, list) else 1
            if kind == "protein":
                seq = entity.get("sequence")
                if not isinstance(seq, str):
                    raise AF3ValidationError("Protein sequence must be a string")
                seq = "".join(seq.split()).upper()
                if not seq or not re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWYX]+", seq):
                    raise AF3ValidationError(
                        "Protein sequence must use canonical amino-acid letters or X; FASTA headers are not sequences"
                    )
                total_residues += len(seq) * copies
                if total_residues > 10000:
                    raise AF3ValidationError(
                        "Platform input limit is 10000 protein residues across all copies; actual AF3 capacity depends on hardware"
                    )
                result["sequence"] = seq
                if msa_mode == "none":
                    result.update(unpairedMsa="", pairedMsa="", templates=[])
            else:
                smiles = entity.get("smiles")
                if (
                    not isinstance(smiles, str)
                    or not 1 <= len(smiles) <= 20000
                    or any(c.isspace() for c in smiles)
                ):
                    raise AF3ValidationError(
                        "Ligand SMILES must be a nonempty string without whitespace (maximum 20000 characters)"
                    )
                try:
                    from rdkit import Chem, rdBase
                except ImportError as exc:
                    raise AF3ValidationError("RDKit is required to validate ligand SMILES") from exc
                with rdBase.BlockLogs():
                    molecule = Chem.MolFromSmiles(smiles)
                if molecule is None or molecule.GetNumAtoms() == 0:
                    raise AF3ValidationError("Invalid ligand SMILES")
                if len(Chem.GetMolFrags(molecule)) != 1:
                    raise AF3ValidationError(
                        "Use one connected molecule per ligand entity; represent salts/counterions separately"
                    )
                if any(atom.GetAtomicNum() == 0 for atom in molecule.GetAtoms()):
                    raise AF3ValidationError("Ligand cannot contain wildcard atoms")
                result["smiles"] = Chem.MolToSmiles(molecule, isomericSmiles=True)
            _description(entity, result)
            sequences.append({kind: result})
    if len(used) > 128:
        raise AF3ValidationError("Platform limit is 128 total chains")
    return {
        "name": name,
        "modelSeeds": seeds,
        "sequences": sequences,
        "dialect": "alphafold3",
        "version": AF3_SCHEMA_VERSION,
    }


def validate_input(data: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """Validate/rebuild this adapter's input subset, rejecting file references."""
    if not isinstance(data, Mapping) or set(data) != {
        "name",
        "modelSeeds",
        "sequences",
        "dialect",
        "version",
    }:
        raise AF3ValidationError("Input must contain only name, modelSeeds, sequences, dialect, version")
    if (
        data["dialect"] != "alphafold3"
        or type(data["version"]) is not int
        or data["version"] != AF3_SCHEMA_VERSION
    ):
        raise AF3ValidationError("This adapter requires alphafold3 dialect schema version 4")
    proteins, ligands, modes = [], [], set()
    if not isinstance(data["sequences"], list):
        raise AF3ValidationError("sequences must be a list")
    for entity in data["sequences"]:
        if not isinstance(entity, dict) or len(entity) != 1:
            raise AF3ValidationError("Each sequence must contain one protein or ligand")
        if "protein" in entity and isinstance(entity["protein"], dict):
            protein = dict(entity["protein"])
            msa_fields = {"unpairedMsa", "pairedMsa", "templates"}
            if msa_fields & set(protein):
                if (
                    not msa_fields <= set(protein)
                    or protein["unpairedMsa"] != ""
                    or protein["pairedMsa"] != ""
                    or protein["templates"] != []
                ):
                    raise AF3ValidationError(
                        "Only explicit empty MSAs/templates are supported; use upstream AF3 for custom MSA inputs"
                    )
                modes.add("none")
                for field in msa_fields:
                    protein.pop(field)
            else:
                modes.add("search")
            proteins.append(protein)
        elif "ligand" in entity and isinstance(entity["ligand"], dict):
            ligands.append(entity["ligand"])
        else:
            raise AF3ValidationError("Only protein and ligand entities are supported")
    if len(modes) > 1:
        raise AF3ValidationError("All proteins must use the same MSA mode")
    mode = next(iter(modes), "search")
    return build_input(data["name"], proteins, ligands, data["modelSeeds"], mode), mode


@dataclass(frozen=True)
class AF3Config:
    runner: str = "native"
    repo_dir: Path | None = None
    python_bin: str = sys.executable
    model_dir: Path | None = None
    database_dir: Path | None = None
    docker_image: str = f"herbfold-af3:{AF3_VERSION}"
    device: str = "gpu"
    num_diffusion_samples: int = 5
    num_recycles: int = 10
    max_template_date: str = "2021-09-30"
    flash_attention: str = "triton"

    def __post_init__(self) -> None:
        if self.runner not in {"native", "docker"}:
            raise AF3ValidationError("AF3_RUNNER must be native or docker")
        if self.device not in {"gpu", "cpu", "mps"}:
            raise AF3ValidationError("AF3_DEVICE must be gpu, cpu, or mps")
        if self.flash_attention not in {"triton", "cudnn", "xla"}:
            raise AF3ValidationError("AF3_FLASH_ATTENTION must be triton, cudnn, or xla")
        if self.runner == "docker" and self.device == "mps":
            raise AF3ValidationError("Apple MPS requires the native runner")
        _integer(self.num_diffusion_samples, "num_diffusion_samples", 1, 100)
        _integer(self.num_recycles, "num_recycles", 1, 100)
        date.fromisoformat(self.max_template_date)
        for field in ("repo_dir", "model_dir", "database_dir"):
            if (value := getattr(self, field)) is not None:
                object.__setattr__(self, field, Path(value).expanduser().resolve())
        if not self.python_bin or "\x00" in self.python_bin:
            raise AF3ValidationError("AF3_PYTHON must name an executable")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/:@-]*", self.docker_image):
            raise AF3ValidationError("Invalid Docker image reference")

    @classmethod
    def from_env(cls) -> "AF3Config":
        def path(key: str) -> Path | None:
            return Path(value) if (value := os.environ.get(key)) else None

        repo = path("AF3_REPO_DIR")
        return cls(
            runner=os.getenv("AF3_RUNNER", "native"),
            repo_dir=repo,
            python_bin=os.getenv("AF3_PYTHON")
            or (str(repo / ".venv/bin/python") if repo else sys.executable),
            model_dir=path("AF3_MODEL_DIR"),
            database_dir=path("AF3_DB_DIR"),
            docker_image=os.getenv("AF3_DOCKER_IMAGE", f"herbfold-af3:{AF3_VERSION}"),
            device=os.getenv("AF3_DEVICE", "gpu"),
            num_diffusion_samples=int(os.getenv("AF3_NUM_DIFFUSION_SAMPLES", "5")),
            num_recycles=int(os.getenv("AF3_NUM_RECYCLES", "10")),
            max_template_date=os.getenv("AF3_MAX_TEMPLATE_DATE", "2021-09-30"),
            flash_attention=os.getenv("AF3_FLASH_ATTENTION", "triton"),
        )


def execution_environment() -> dict[str, str]:
    """Isolate the AF3 pip CUDA stack from a conflicting global CUDA toolkit.

    The full environment is passed only to the child process, never persisted
    in job artifacts. Operators can explicitly opt into a library path using
    AF3_LD_LIBRARY_PATH or pin a physical device with AF3_CUDA_VISIBLE_DEVICES.
    """
    env = dict(os.environ)
    env.pop("LD_LIBRARY_PATH", None)
    if "AF3_LD_LIBRARY_PATH" in env:
        env["LD_LIBRARY_PATH"] = env["AF3_LD_LIBRARY_PATH"]
    env["XLA_PYTHON_CLIENT_PREALLOCATE"] = env.get("AF3_PREALLOCATE", "false")
    if "AF3_CUDA_VISIBLE_DEVICES" in env:
        env["CUDA_VISIBLE_DEVICES"] = env["AF3_CUDA_VISIBLE_DEVICES"]
    if "AF3_XLA_FLAGS" in env:
        env["XLA_FLAGS"] = env["AF3_XLA_FLAGS"]
    if env.get("AF3_HMMER_DIR"):
        hmmer_dir = str(Path(env["AF3_HMMER_DIR"]).expanduser().resolve())
        env["PATH"] = hmmer_dir + os.pathsep + env.get("PATH", os.defpath)
    return env


def _probe(
    command: list[str], cwd: Path | None = None, timeout: int = 20, *, env: dict[str, str] | None = None
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
            env=env,
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout.strip()[-8000:],
            "stderr": result.stderr.strip()[-4000:],
            "returncode": result.returncode,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "stderr": str(exc)}


def capabilities(
    config: AF3Config | None = None, *, msa_mode: str = "search", probe_runtime: bool = False
) -> dict[str, Any]:
    """Read-only readiness check. Runtime probes use the configured environment.

    Readiness is a preflight, not proof that model loading or a large run succeeds.
    ``probe_runtime=True`` additionally imports AF3/JAX and checks its device.
    """
    try:
        config = config or AF3Config.from_env()
    except (ValueError, OSError) as exc:
        return {"ready": False, "runnable": False, "blockers": [str(exc)], "version": AF3_VERSION}
    blockers, warnings = [], []
    provenance: dict[str, Any] = {
        "expected_version": AF3_VERSION,
        "expected_commit": AF3_COMMIT,
        "verified_date": AF3_VERIFIED_DATE,
        "release_url": AF3_RELEASE_URL,
        "runner": config.runner,
        "device": config.device,
        "runtime_probed": False,
    }
    from .af3_parameters import inspect_parameters

    parameters = inspect_parameters(config)
    provenance["parameters"] = {"status": parameters["status"], **parameters["provenance"]}
    blockers.extend(parameters["blockers"])
    if parameters["runnable"]:
        warnings.append(
            "Parameter prefix inspection passed, but trained-model provenance is not authenticated; a nonzero identifier alone does not establish Google-issued trained weights."
        )
    if not config.model_dir or not config.model_dir.is_dir():
        blockers.append("AF3_MODEL_DIR is missing; place Google-issued model parameters there")
    elif not any(p.is_file() and p.stat().st_size > 0 for p in config.model_dir.glob("*.bin*")):
        blockers.append("AF3_MODEL_DIR contains no nonempty AF3 .bin/.bin.zst parameter files")
    if msa_mode == "search":
        from .af3_databases import inspect_databases

        databases = inspect_databases(config)
        provenance["databases"] = {"status": databases["status"], **databases["provenance"]}
        blockers.extend(databases["blockers"])
    else:
        warnings.append("MSA-free/template-free mode may substantially reduce prediction accuracy")
    if config.runner == "native":
        if not config.repo_dir or not (config.repo_dir / "run_alphafold.py").is_file():
            blockers.append("AF3_REPO_DIR must point to the pinned upstream repository")
        else:
            git = _probe(["git", "-C", str(config.repo_dir), "rev-parse", "HEAD"])
            provenance["source_commit"] = git.get("stdout") if git["ok"] else None
            if provenance["source_commit"] != AF3_COMMIT:
                blockers.append(f"AF3 repository must be checked out at {AF3_VERSION} ({AF3_COMMIT})")
            dirty = _probe(
                ["git", "-C", str(config.repo_dir), "status", "--porcelain", "--untracked-files=no"]
            )
            provenance["source_modified"] = bool(dirty.get("stdout")) if dirty["ok"] else None
            if provenance["source_modified"]:
                warnings.append(
                    "AF3 source has tracked modifications; archive and review these before comparing benchmarks"
                )
            provenance["entrypoint_sha256"] = hashlib.sha256(
                (config.repo_dir / "run_alphafold.py").read_bytes()
            ).hexdigest()
        if not shutil.which(config.python_bin):
            blockers.append("AF3_PYTHON is not an available executable")
        elif (
            probe_runtime
            and parameters["runnable"]
            and config.repo_dir
            and (config.repo_dir / "run_alphafold.py").is_file()
        ):
            script = (
                "import importlib.metadata,json,alphafold3,jax; "
                f"print(json.dumps({{'version':importlib.metadata.version('alphafold3'),'module':alphafold3.__file__,'jax_version':jax.__version__,'jaxlib_version':importlib.metadata.version('jaxlib'),'devices':[str(d) for d in jax.local_devices(backend={config.device!r})]}}))"
            )
            provenance["runtime_probed"] = True
            probe = _probe(
                [config.python_bin, "-c", script], config.repo_dir, timeout=30, env=execution_environment()
            )
            if not probe["ok"]:
                blockers.append("AF3 Python/JAX/device preflight failed: " + probe.get("stderr", "")[-1200:])
            else:
                try:
                    runtime = json.loads(probe["stdout"].splitlines()[-1])
                    provenance["runtime"] = runtime
                    if runtime.get("version", "").split("+")[0] != AF3_VERSION:
                        blockers.append("Installed alphafold3 package does not match the pinned version")
                    if not runtime.get("devices"):
                        blockers.append(f"JAX found no {config.device} device")
                except (ValueError, IndexError):
                    blockers.append("Could not parse AF3 Python/JAX runtime probe")
        if msa_mode == "search":
            provenance["hmmer_tools"] = {}
            for binary in ("jackhmmer", "nhmmer", "hmmalign", "hmmsearch", "hmmbuild"):
                executable = shutil.which(binary, path=execution_environment().get("PATH"))
                if not executable:
                    blockers.append(f"HMMER executable is unavailable on worker PATH: {binary}")
                else:
                    from .af3_databases import file_stat

                    provenance["hmmer_tools"][binary] = {
                        "path": str(Path(executable).resolve()),
                        "stat": file_stat(Path(executable)),
                    }
    else:
        inspected = _probe(["docker", "image", "inspect", config.docker_image, "--format", "{{json .}}"])
        if not inspected["ok"]:
            blockers.append("Pinned AF3 Docker image is unavailable to this worker")
        else:
            try:
                info = json.loads(inspected["stdout"])
                provenance["docker_image_id"] = info["Id"]
                labels = info.get("Config", {}).get("Labels") or {}
                provenance["image_source_commit"] = labels.get("org.opencontainers.image.revision")
                if provenance["image_source_commit"] != AF3_COMMIT:
                    blockers.append(
                        "Docker image must have org.opencontainers.image.revision set to the pinned AF3 commit"
                    )
            except (ValueError, KeyError, TypeError):
                blockers.append("Could not read AF3 Docker image provenance")
        if config.device == "gpu" and not shutil.which("nvidia-smi"):
            warnings.append(
                "nvidia-smi is not on PATH; Docker GPU runtime must expose a compatible NVIDIA device"
            )
    if config.device == "cpu":
        warnings.append("CPU inference is supported by AF3 3.0.4 but can be very slow and memory intensive")
    if config.device == "mps":
        warnings.append("Apple Silicon MPS support uses the experimental community jax-mps backend")
    warnings.append(
        "Readiness checks do not load model parameters, validate complete databases, or guarantee sufficient device memory"
    )
    return {
        "version": AF3_VERSION,
        "schema_version": AF3_SCHEMA_VERSION,
        "ready": not blockers,
        "runnable": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "provenance": provenance,
        "parameter_status": parameters["status"],
        "confidence_note": CONFIDENCE_NOTE,
    }


def build_command(
    config: AF3Config,
    input_path: Path,
    output_dir: Path,
    *,
    msa_mode: str = "search",
    stage: str = "combined",
) -> tuple[list[str], str | None]:
    """Build argv only; callers execute with shell=False in a background worker."""
    if msa_mode not in {"search", "none"}:
        raise AF3ValidationError("Invalid MSA mode")
    if stage not in {"combined", "data_pipeline", "inference"}:
        raise AF3ValidationError("Invalid AF3 execution stage")
    if stage == "data_pipeline" and msa_mode != "search":
        raise AF3ValidationError("Data pipeline requires search mode")
    if config.model_dir is None:
        raise AF3ValidationError("AF3_MODEL_DIR must be configured to construct a command")
    if msa_mode == "search" and config.database_dir is None:
        raise AF3ValidationError("AF3_DB_DIR must be configured for MSA search")
    input_path, output_dir = Path(input_path).resolve(), Path(output_dir).resolve()
    data_pipeline = msa_mode == "search" and stage != "inference"
    inference = stage != "data_pipeline"
    device = config.device if inference else "cpu"
    common = [
        f"--run_data_pipeline={'true' if data_pipeline else 'false'}",
        f"--run_inference={'true' if inference else 'false'}",
        f"--jax_backend={device}",
        f"--flash_attention_implementation={'xla' if device in {'cpu', 'mps'} else config.flash_attention}",
        f"--num_diffusion_samples={config.num_diffusion_samples}",
        f"--num_recycles={config.num_recycles}",
        f"--max_template_date={config.max_template_date}",
    ]
    if config.runner == "native":
        if config.repo_dir is None:
            raise AF3ValidationError("AF3_REPO_DIR must be configured for native execution")
        command = [
            config.python_bin,
            str(config.repo_dir / "run_alphafold.py"),
            f"--json_path={input_path}",
            f"--output_dir={output_dir}",
            f"--model_dir={config.model_dir}",
        ]
        if config.database_dir is not None:
            command.append(f"--db_dir={config.database_dir}")
        return command + common, str(config.repo_dir)
    # Colon would change Docker's volume syntax. Never parse any client input as flags.
    mounts = [
        (input_path.parent, "/af_input", "ro"),
        (output_dir, "/af_output", "rw"),
        (config.model_dir, "/models", "ro"),
    ]
    if config.database_dir is not None:
        mounts.append((config.database_dir, "/databases", "ro"))
    command = ["docker", "run", "--rm", "--network=none"]
    if device == "gpu":
        command += ["--gpus", "all"]
    # Environment on the Docker client is not inherited by its container.
    # Forward only the documented AF3 settings, never the full host environment.
    container_env = execution_environment()
    container_env["PYTHONUNBUFFERED"] = "1"
    if not inference:
        container_env["CUDA_VISIBLE_DEVICES"] = ""
        container_env["JAX_PLATFORMS"] = "cpu"
    for key in ("CUDA_VISIBLE_DEVICES", "XLA_FLAGS", "XLA_PYTHON_CLIENT_PREALLOCATE", "JAX_PLATFORMS", "PYTHONUNBUFFERED"):
        if key in container_env:
            command += ["--env", f"{key}={container_env[key]}"]
    for host, target, mode in mounts:
        if ":" in str(host) or "\n" in str(host):
            raise AF3ValidationError("Docker mount paths cannot contain colons or newlines")
        command += ["--volume", f"{host}:{target}:{mode}"]
    command += [
        config.docker_image,
        "python",
        "run_alphafold.py",
        f"--json_path=/af_input/{input_path.name}",
        "--output_dir=/af_output",
        "--model_dir=/models",
    ]
    if config.database_dir is not None:
        command.append("--db_dir=/databases")
    return command + common, None


def prepare_job(
    job_dir: Path | str, input_data: Mapping[str, Any], config: AF3Config | None = None
) -> dict[str, Any]:
    """Write reproducible input/manifest even when inference is not configured."""
    validated, msa_mode = validate_input(input_data)
    config = config or AF3Config.from_env()
    job_dir = Path(job_dir).resolve()
    job_dir.mkdir(parents=True, exist_ok=True)
    input_path, output_dir = job_dir / "fold_input.json", job_dir / "output"
    output_dir.mkdir(exist_ok=True)
    for path in (input_path, output_dir, job_dir / "af3_manifest.json"):
        if not path.resolve().is_relative_to(job_dir):
            raise AF3ValidationError("Job paths must remain within the job directory")
    encoded = (json.dumps(validated, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    if input_path.exists() and input_path.read_bytes() != encoded:
        raise AF3ValidationError(
            "A prepared job cannot be overwritten with different input; use a new job directory"
        )
    input_path.write_bytes(encoded)
    status = capabilities(config, msa_mode=msa_mode, probe_runtime=True)
    command, cwd = [], None
    try:
        command, cwd = build_command(config, input_path, output_dir, msa_mode=msa_mode)
    except AF3ValidationError as exc:
        if str(exc) not in status["blockers"]:
            status["blockers"].append(str(exc))
        status.update(ready=False, runnable=False)
    # Docker image IDs are immutable; execute the inspected image, not a mutable tag.
    if (
        config.runner == "docker"
        and (image_id := status.get("provenance", {}).get("docker_image_id"))
        and command
    ):
        command[command.index(config.docker_image)] = image_id
    child_env = execution_environment()
    status["provenance"]["cuda_environment"] = {
        key: child_env.get(key)
        for key in ("LD_LIBRARY_PATH", "CUDA_VISIBLE_DEVICES", "XLA_PYTHON_CLIENT_PREALLOCATE", "XLA_FLAGS")
    }
    result = {
        **status,
        "command": command,
        "cwd": cwd,
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "input_sha256": hashlib.sha256(encoded).hexdigest(),
        "msa_mode": msa_mode,
        "model_seeds": validated["modelSeeds"],
        "samples_per_seed": config.num_diffusion_samples,
        "num_recycles": config.num_recycles,
        "max_template_date": config.max_template_date,
        "status": "prepared",
        "stage": {"name": "prepared", "label": "실행 준비"},
        "msa_features": {
            "status": "awaiting_search" if msa_mode == "search" else "not_requested",
            "cache_hit": False,
            "database_fingerprint": status["provenance"].get("databases", {}).get("fingerprint_sha256"),
            "unpaired_msa_sequences": None,
            "paired_msa_sequences": None,
            "template_count": None,
            "non_query_sequences": None,
        },
    }
    (job_dir / "af3_manifest.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result


def safe_output_path(output_dir: Path | str, relative_path: str) -> Path:
    """Resolve a returned artifact reference without traversal or symlink escape."""
    root = Path(output_dir).resolve()
    rel = Path(relative_path)
    if rel.is_absolute() or ".." in rel.parts or "\\" in relative_path:
        raise AF3ValidationError("Output artifact paths must be relative and cannot traverse directories")
    resolved = (root / rel).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise AF3ValidationError("Output artifact is missing or outside the result directory")
    return resolved


def _score(value: Any, name: str, lower: float, upper: float) -> float | None:
    if value is None:
        return None  # AF3 uses JSON null for unavailable/NaN confidences.
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        or not lower <= value <= upper
    ):
        raise AF3ValidationError(f"Invalid AF3 metric {name}")
    return float(value)


def _read_json(path: Path, max_bytes: int = 4_000_000) -> dict[str, Any]:
    if path.stat().st_size > max_bytes:
        raise AF3ValidationError("Confidence JSON is larger than the supported import limit")
    try:
        data = json.loads(
            path.read_text(),
            parse_constant=lambda value: (_ for _ in ()).throw(
                AF3ValidationError(f"Non-finite JSON number: {value}")
            ),
        )
    except (ValueError, UnicodeError) as exc:
        raise AF3ValidationError(f"Malformed AF3 confidence JSON: {path.name}") from exc
    if not isinstance(data, dict):
        raise AF3ValidationError("Confidence JSON must be an object")
    return data


def _file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def parse_outputs(output_dir: Path | str) -> dict[str, Any]:
    """Import on-disk AF3 summaries and matching mmCIF files without inventing scores.

    Paths returned are relative to output_dir and require safe_output_path at
    download time. Artifacts alone cannot attest which program created them.
    """
    root = Path(output_dir).resolve()
    if not root.is_dir():
        raise AF3ValidationError("AF3 output directory does not exist")
    models, warnings = [], []
    summaries = sorted(root.rglob("*_summary_confidences.json"))
    if len(summaries) > 10001:
        raise AF3ValidationError("Too many AF3 summary artifacts")
    for candidate in summaries:
        relative = candidate.relative_to(root).as_posix()
        summary_path = safe_output_path(root, relative)
        summary = _read_json(summary_path)
        if not any(key in summary for key in ("ptm", "iptm", "ranking_score")):
            raise AF3ValidationError(f"No recognized AF3 confidence fields in {relative}")
        prefix = candidate.name.removesuffix("_summary_confidences.json")
        structure_candidate = candidate.with_name(prefix + "_model.cif")
        structure_relative = structure_candidate.relative_to(root).as_posix()
        structure = safe_output_path(root, structure_relative)
        with structure.open() as stream:
            head = stream.read(2_000_000)
        if "data_" not in head or "_atom_site." not in head:
            raise AF3ValidationError(
                f"Result does not contain an mmCIF atom-site table: {structure_relative}"
            )
        metrics = {
            key: _score(summary.get(key), key, lower, upper)
            for key, lower, upper in (
                ("ptm", 0, 1),
                ("iptm", 0, 1),
                ("ranking_score", -100, 1.5),
                ("fraction_disordered", 0, 1),
            )
        }
        has_clash = summary.get("has_clash")
        if (
            has_clash is not None
            and type(has_clash) is not bool
            and not (type(has_clash) in {float, int} and has_clash in (0, 1))
        ):
            raise AF3ValidationError("has_clash must be a boolean or 0/1")
        metrics["has_clash"] = None if has_clash is None else bool(has_clash)
        chain_ids = summary.get("chain_ids")
        if chain_ids is not None:
            if (
                not isinstance(chain_ids, list)
                or not chain_ids
                or not all(isinstance(s, str) and re.fullmatch(r"[A-Z]+", s) for s in chain_ids)
            ):
                raise AF3ValidationError("Invalid summary chain_ids")
            # v3.0.4 confidence_types.py:194 serializes token_chain_ids here,
            # despite documentation describing one ID per chain. Preserve the
            # order of contiguous token groups; chain metrics remain chain-sized.
            ordered_ids = list(dict.fromkeys(chain_ids))
            if len(ordered_ids) != len(chain_ids):
                runs = [
                    chain
                    for index, chain in enumerate(chain_ids)
                    if index == 0 or chain != chain_ids[index - 1]
                ]
                if runs != ordered_ids:
                    raise AF3ValidationError("Token-level summary chain_ids must have contiguous chains")
                warnings.append(f"Normalized AF3 token-level chain_ids to chain order: {prefix}")
                chain_ids = ordered_ids
        for field in ("chain_ptm", "chain_iptm", "chain_pair_iptm", "chain_pair_pae_min"):
            values = summary.get(field)
            if values is None:
                continue
            if not isinstance(values, list) or len(values) > 128:
                raise AF3ValidationError(f"Invalid {field} array")
            if chain_ids is not None and len(values) != len(chain_ids):
                raise AF3ValidationError(f"{field} does not match chain_ids")
            is_matrix = field.startswith("chain_pair_")
            cleaned = []
            for value in values:
                if is_matrix:
                    if not isinstance(value, list) or len(value) != len(values):
                        raise AF3ValidationError(f"{field} must be square")
                    cleaned.append(
                        [_score(v, field, 0, 1 if field.endswith("iptm") else 10000) for v in value]
                    )
                else:
                    cleaned.append(_score(value, field, 0, 1))
            metrics[field] = cleaned
        match = re.search(r"(?:^|/)seed-(\d+)_sample-(\d+)(?:/|$)", relative)
        entry = {
            "name": prefix,
            "summary_path": relative,
            "structure_path": structure_relative,
            "metrics": metrics,
            "chain_ids": chain_ids,
            "seed": int(match[1]) if match else None,
            "sample": int(match[2]) if match else None,
            "is_top_ranked_copy": match is None,
            "structure_sha256": _file_sha256(structure),
        }
        confidence_candidate = candidate.with_name(prefix + "_confidences.json")
        if confidence_candidate.exists():
            confidence_relative = confidence_candidate.relative_to(root).as_posix()
            safe_output_path(root, confidence_relative)
            entry["confidence_path"] = confidence_relative
        models.append(entry)
        if any(metrics[key] is None for key in ("ptm", "iptm", "ranking_score")):
            warnings.append(f"Some structural confidence metrics are unavailable: {prefix}")
        if metrics["has_clash"]:
            warnings.append(f"AF3 reports significant atomic clashes: {prefix}")
    models.sort(
        key=lambda item: (
            item["metrics"]["ranking_score"] is not None,
            item["metrics"]["ranking_score"] if item["metrics"]["ranking_score"] is not None else -math.inf,
        ),
        reverse=True,
    )
    if not models:
        warnings.append("No AF3 prediction artifacts found; inference has not produced a structure")
    return {
        "models": models,
        "model_count": len(models),
        "warnings": warnings,
        "confidence_note": CONFIDENCE_NOTE,
        "affinity_prediction": None,
        "source": "imported_af3_artifacts",
        "execution_verified": False,
    }
