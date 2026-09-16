"""Bounded fidelity kernels and explicit, asynchronous IBM Quantum execution.

Kernel values are molecular feature similarities, not binding affinities. IBM
jobs are submitted only by ``submit_kernel(..., execute=True)``. No operation
ever simulates a hardware-sized circuit on the local statevector simulator.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Sequence

import numpy as np

MAX_LOCAL_QUBITS = 16
MAX_FEATURES = 4096
MAX_KERNEL_CIRCUITS = 4096
ENCODING = "atan_fold_or_repeat_v1"
FEATURE_MAP = "ry_rz_cz_reupload_v1"


class QuantumConfigurationError(ValueError):
    """Invalid input, unavailable credentials or an exceeded declared budget."""


class QuantumExecutionError(RuntimeError):
    """A submission failed; the manifest retains every known accepted job ID."""

    def __init__(self, message: str, manifest: dict[str, Any] | None = None):
        super().__init__(message)
        self.manifest = manifest


def _provider_call(function: Any, action: str, *args: Any, **kwargs: Any) -> Any:
    """Do not propagate provider exception text containing account/request data."""
    try:
        return function(*args, **kwargs)
    except Exception as exc:
        raise QuantumExecutionError(f"IBM Quantum {action} failed ({type(exc).__name__}).") from None


def _features(features: Sequence[Sequence[float]]) -> np.ndarray:
    try:
        array = np.asarray(features, dtype=float)
    except (TypeError, ValueError):
        raise QuantumConfigurationError("Features must be a rectangular numeric matrix.") from None
    if array.ndim != 2 or not all(array.shape):
        raise QuantumConfigurationError("Features must have at least one row and column.")
    if array.shape[1] > MAX_FEATURES:
        raise QuantumConfigurationError(f"At most {MAX_FEATURES} feature columns are supported.")
    if not np.isfinite(array).all():
        raise QuantumConfigurationError("Features must contain only finite values.")
    return array


def _integer(value: Any, name: str, lower: int, upper: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise QuantumConfigurationError(f"{name} must be an integer.")
    if not lower <= value <= upper:
        raise QuantumConfigurationError(f"{name} must be between {lower} and {upper}.")
    return int(value)


def _pairs(n_samples: int) -> list[list[int]]:
    # Include measured diagonals on hardware; do not hide noise by imposing 1.
    return [[i, j] for i in range(n_samples) for j in range(i, n_samples)]


def _fingerprint(array: np.ndarray) -> str:
    payload = json.dumps(array.tolist(), separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def credential_status() -> dict[str, Any]:
    """Inspect credential *presence* without returning API keys or account data."""
    token = bool(os.getenv("IBM_QUANTUM_TOKEN") or os.getenv("QISKIT_IBM_TOKEN"))
    named = bool(os.getenv("IBM_QUANTUM_ACCOUNT"))
    saved = False
    try:
        from qiskit_ibm_runtime import QiskitRuntimeService

        accounts = QiskitRuntimeService.saved_accounts()
        saved = any(a.get("channel") in ("ibm_quantum_platform", "ibm_cloud") for a in accounts.values())
    except (ImportError, OSError, ValueError):
        pass
    return {
        "configured": token or saved,
        "environment_token": token,
        "saved_account": saved,
        "named_account_requested": named,
    }


def _service(service: Any = None) -> Any:
    if service is not None:
        return service
    if not credential_status()["configured"]:
        raise QuantumConfigurationError(
            "IBM Quantum credentials are absent. Set IBM_QUANTUM_TOKEN and optionally "
            "IBM_QUANTUM_INSTANCE, or configure a saved Qiskit Runtime account."
        )
    from qiskit_ibm_runtime import QiskitRuntimeService

    kwargs: dict[str, Any] = {"channel": "ibm_quantum_platform"}
    if os.getenv("IBM_QUANTUM_ACCOUNT"):
        kwargs["name"] = os.environ["IBM_QUANTUM_ACCOUNT"]
    elif os.getenv("IBM_QUANTUM_TOKEN") or os.getenv("QISKIT_IBM_TOKEN"):
        kwargs["token"] = os.getenv("IBM_QUANTUM_TOKEN") or os.getenv("QISKIT_IBM_TOKEN")
    instance = os.getenv("IBM_QUANTUM_INSTANCE") or os.getenv("QISKIT_IBM_INSTANCE")
    if instance:
        kwargs["instance"] = instance
    try:
        return QiskitRuntimeService(**kwargs)
    except Exception as exc:
        # Provider exception strings can include request/account information.
        raise QuantumConfigurationError(
            f"IBM Quantum authentication failed ({type(exc).__name__}); verify the configured account."
        ) from None


def _backend_info(backend: Any) -> dict[str, Any]:
    status = backend.status()
    properties = backend.properties() if callable(getattr(backend, "properties", None)) else None
    faulty = set(properties.faulty_qubits()) if properties is not None else set()
    configuration = backend.configuration() if callable(getattr(backend, "configuration", None)) else None
    name = backend.name() if callable(backend.name) else backend.name
    usable = [q for q in range(backend.num_qubits) if q not in faulty]
    return {
        "name": name,
        "num_qubits": int(backend.num_qubits),
        "usable_qubits": len(usable),
        "physical_qubits": usable,
        "faulty_qubits": sorted(faulty),
        "operational": bool(status.operational),
        "simulator": bool(getattr(configuration, "simulator", False)),
        "pending_jobs": int(getattr(status, "pending_jobs", 0)),
        "status_message": str(getattr(status, "status_msg", "unknown")),
    }


def _backend_inventory(service: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    entries, objects = [], {}
    for backend in service.backends(operational=True, simulator=False):
        entry = _backend_info(backend)
        if entry["operational"] and not entry["simulator"] and entry["usable_qubits"]:
            entries.append(entry)
            objects[entry["name"]] = backend
    entries.sort(key=lambda b: (-b["usable_qubits"], b["pending_jobs"], b["name"]))
    return entries, objects


def inspect_backends(service: Any = None) -> dict[str, Any]:
    """Read account-accessible hardware; never submit a job or expose credentials."""
    credentials = credential_status() if service is None else {"configured": True, "injected_service": True}
    if not credentials["configured"]:
        return {
            "available": False,
            "status": "credentials_required",
            "credentials": credentials,
            "backends": [],
            "selected_max_backend": None,
        }
    try:
        entries, _ = _backend_inventory(_service(service))
    except Exception as exc:
        return {
            "available": False,
            "status": "connection_failed",
            "error_type": type(exc).__name__,
            "credentials": credentials,
            "backends": [],
            "selected_max_backend": None,
        }
    return {
        "available": bool(entries),
        "status": "ready" if entries else "no_operational_backend",
        "credentials": credentials,
        "backends": entries,
        "selected_max_backend": entries[0]["name"] if entries else None,
    }


def plan_quantum(
    features: Sequence[Sequence[float]],
    mode: Literal["local", "ibm"] = "local",
    qubits: int | Literal["max"] | None = None,
    shots: int = 1024,
    max_circuits: int = 64,
    circuits_per_job: int = 16,
    max_total_shots: int = 65536,
    max_jobs: int = 4,
    max_execution_time: int = 120,
    layers: int = 2,
    backend_name: str | None = None,
    service: Any = None,
    kernel_method: Literal["fidelity", "projected"] = "fidelity",
    block_size: int = 6,
    gamma: float = 1.0,
) -> dict[str, Any]:
    """Produce a bounded, JSON-serializable plan; IBM defaults to maximum usable width.

    ``max_execution_time`` is QPU seconds *per job*, not wall time or money.
    Account quota/price must be checked in IBM Quantum; no invented cost estimate
    or assumption of access to a particular publicized QPU is made here.
    """
    if kernel_method == "projected":
        from .quantum_projected import plan_projected

        return plan_projected(
            features,
            mode=mode,
            qubits=qubits,
            shots=shots,
            max_circuits=max_circuits,
            circuits_per_job=circuits_per_job,
            max_total_shots=max_total_shots,
            max_jobs=max_jobs,
            max_execution_time=max_execution_time,
            layers=layers,
            backend_name=backend_name,
            service=service,
            block_size=block_size,
            gamma=gamma,
        )
    if kernel_method != "fidelity":
        raise QuantumConfigurationError("kernel_method must be 'fidelity' or 'projected'.")
    array = _features(features)
    if mode not in ("local", "ibm"):
        raise QuantumConfigurationError("mode must be 'local' or 'ibm'.")
    shots = _integer(shots, "shots", 1, 100000)
    layers = _integer(layers, "layers", 1, 8)
    max_circuits = _integer(max_circuits, "max_circuits", 1, MAX_KERNEL_CIRCUITS)
    circuits_per_job = _integer(circuits_per_job, "circuits_per_job", 1, 256)
    max_total_shots = _integer(max_total_shots, "max_total_shots", 1, 100000000)
    max_jobs = _integer(max_jobs, "max_jobs", 1, 256)
    max_execution_time = _integer(max_execution_time, "max_execution_time", 1, 10800)
    n = len(array)
    circuit_count = n * (n + 1) // 2
    if circuit_count > max_circuits:
        raise QuantumConfigurationError(
            f"Kernel needs {circuit_count} circuits; max_circuits={max_circuits}. Reduce samples or raise the explicit budget."
        )
    jobs = math.ceil(circuit_count / circuits_per_job) if mode == "ibm" else 0
    total_shots = circuit_count * shots if mode == "ibm" else 0
    if jobs > max_jobs or total_shots > max_total_shots:
        raise QuantumConfigurationError(
            f"Plan needs {jobs} jobs / {total_shots} shots; budget is {max_jobs} jobs / {max_total_shots} shots."
        )
    if qubits not in (None, "max"):
        _integer(qubits, "qubits", 1, 100000)
    plan: dict[str, Any] = {
        "schema_version": 1,
        "status": "ready",
        "mode": mode,
        "n_samples": n,
        "n_features": int(array.shape[1]),
        "feature_sha256": _fingerprint(array),
        "encoding": ENCODING,
        "feature_map": FEATURE_MAP,
        "layers": layers,
        "selection_strategy": "max_accessible_operational"
        if qubits in (None, "max") and mode == "ibm"
        else "fixed",
        "n_qubits": None,
        "backend_name": None,
        "physical_qubits": [],
        "shots": shots,
        "circuit_count": circuit_count,
        "job_count": jobs,
        "circuits_per_job": circuits_per_job,
        "total_shots": total_shots,
        "max_execution_time_per_job": max_execution_time,
        "max_total_qpu_seconds": jobs * max_execution_time,
        "budget": {"max_circuits": max_circuits, "max_jobs": max_jobs, "max_total_shots": max_total_shots},
        "quota_status": "not_checked; verify instance quota and pricing in IBM Quantum"
        if mode == "ibm"
        else "not_applicable",
        "pairs": _pairs(n),
        "warnings": [
            "Fidelity is a feature similarity, not a binding affinity or evidence of drug efficacy.",
            "Increasing qubits can increase noise and kernel concentration; quantum advantage is unproven.",
        ],
    }
    if mode == "local":
        q = 4 if qubits is None else MAX_LOCAL_QUBITS if qubits == "max" else qubits
        q = _integer(q, "local qubits", 1, MAX_LOCAL_QUBITS)
        if n * (2**q) * 16 > 128 * 1024**2:
            raise QuantumConfigurationError(
                "Local statevectors would exceed the 128 MiB state storage budget."
            )
        plan.update(n_qubits=q, physical_qubits=list(range(q)), backend_name="local_statevector")
    else:
        inventory = inspect_backends(service)
        plan["backend_status"] = inventory["status"]
        if not inventory["available"]:
            plan["status"] = inventory["status"]
            return plan
        candidates = inventory["backends"]
        if backend_name:
            candidates = [entry for entry in candidates if entry["name"] == backend_name]
        if qubits not in (None, "max"):
            candidates = [entry for entry in candidates if entry["usable_qubits"] >= qubits]
        if not candidates:
            raise QuantumConfigurationError(
                "No accessible operational backend satisfies the requested name/qubit count."
            )
        chosen = candidates[0]
        q = chosen["usable_qubits"] if qubits in (None, "max") else int(qubits)
        plan.update(
            n_qubits=q,
            backend_name=chosen["name"],
            physical_qubits=chosen["physical_qubits"][:q],
            backend_num_qubits=chosen["num_qubits"],
            backend_faulty_qubits=chosen["faulty_qubits"],
        )
        if q > MAX_LOCAL_QUBITS:
            plan["warnings"].append(
                "Hardware width exceeds the local simulator limit; no local fallback will run."
            )
    return plan


def encode_features(features: Sequence[Sequence[float]], n_qubits: int) -> np.ndarray:
    """Fit-free bounded encoding; fold all columns or reupload to every qubit.

    Each descriptor is mapped by 2*atan(x). If there are more descriptors than
    qubits, strided column groups are averaged. Otherwise columns are repeated.
    Use externally train-fitted scaling for heterogeneous physical descriptors.
    """
    array = _features(features)
    q = _integer(n_qubits, "n_qubits", 1, 100000)
    bounded = 2 * np.arctan(array)
    d = array.shape[1]
    return np.column_stack([bounded[:, j::q].mean(axis=1) if d >= q else bounded[:, j % d] for j in range(q)])


def build_feature_map(
    parameters: Sequence[Any], layers: int = 2, edges: Sequence[tuple[int, int]] | None = None
) -> Any:
    """Feature-dependent rotations on every logical qubit, with CZ reuploading."""
    from qiskit import QuantumCircuit

    q = len(parameters)
    _integer(q, "n_qubits", 1, 100000)
    _integer(layers, "layers", 1, 8)
    edges = list(edges) if edges is not None else [(i, i + 1) for i in range(q - 1)]
    circuit = QuantumCircuit(q)
    circuit.h(range(q))
    for layer in range(layers):
        for i in range(q):
            circuit.ry(parameters[(i + layer) % q], i)
            circuit.rz(parameters[(i + 2 * layer + 1) % q] / 2, i)
        # Alternating edge groups reduce depth for the default chain.
        for a, b in edges[::2] + edges[1::2]:
            circuit.cz(a, b)
    return circuit


def local_kernel(
    features: Sequence[Sequence[float]],
    n_qubits: int = 4,
    layers: int = 2,
    max_circuits: int = 64,
    *,
    kernel_method: Literal["fidelity", "projected"] = "fidelity",
    block_size: int = 6,
    gamma: float = 1.0,
) -> dict[str, Any]:
    """Exact noiseless Gram matrix; bounded to 16 qubits before simulation."""
    if kernel_method == "projected":
        from .quantum_projected import local_projected

        return local_projected(
            features,
            n_qubits=n_qubits,
            layers=layers,
            max_circuits=max_circuits,
            block_size=block_size,
            gamma=gamma,
        )
    if kernel_method != "fidelity":
        raise QuantumConfigurationError("kernel_method must be 'fidelity' or 'projected'.")
    plan = plan_quantum(features, mode="local", qubits=n_qubits, layers=layers, max_circuits=max_circuits)
    from qiskit.quantum_info import Statevector

    encoded = encode_features(features, plan["n_qubits"])
    states = [Statevector.from_instruction(build_feature_map(row, layers)).data for row in encoded]
    kernel = np.abs(np.asarray(states).conj() @ np.asarray(states).T) ** 2
    kernel = np.clip(kernel.real, 0, 1)
    return {
        "kernel": kernel.tolist(),
        "metadata": plan
        | {
            "estimator": "exact_statevector_fidelity",
            "shots_used": 0,
            "hardware_executed": False,
            "min_eigenvalue": float(np.linalg.eigvalsh(kernel).min()),
        },
    }


def _hardware_edges(backend: Any, physical_qubits: list[int]) -> list[tuple[int, int]]:
    """Use actual healthy couplings; identity layout avoids routing onto faulty qubits."""
    mapping = {physical: logical for logical, physical in enumerate(physical_qubits)}
    coupling = getattr(backend, "coupling_map", None)
    if coupling is None:
        return [(i, i + 1) for i in range(len(physical_qubits) - 1)]
    properties = backend.properties() if callable(getattr(backend, "properties", None)) else None
    faulty_pairs = set()
    if properties is not None:
        faulty_pairs = {
            tuple(sorted(gate.qubits)) for gate in properties.faulty_gates() if len(gate.qubits) == 2
        }
    edges = sorted(
        {
            tuple(sorted((mapping[a], mapping[b])))
            for a, b in coupling.get_edges()
            if a in mapping and b in mapping and tuple(sorted((a, b))) not in faulty_pairs
        }
    )
    # A spanning forest uses every connected usable qubit while bounding depth.
    parent = list(range(len(physical_qubits)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    forest = []
    for a, b in edges:
        aa, bb = find(a), find(b)
        if aa != bb:
            parent[aa] = bb
            forest.append((a, b))
    return forest


def _compile_template(backend: Any, plan: dict[str, Any]) -> tuple[Any, Any, Any, dict[str, Any]]:
    from qiskit.circuit import ParameterVector
    from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager

    q = plan["n_qubits"]
    x, y = ParameterVector("x", q), ParameterVector("y", q)
    edges = _hardware_edges(backend, plan["physical_qubits"])
    circuit = build_feature_map(x, plan["layers"], edges)
    # Keep the overlap layers during compilation, including equal-input diagonals.
    circuit.barrier()
    circuit.compose(build_feature_map(y, plan["layers"], edges).inverse(), inplace=True)
    circuit.measure_all()
    manager = generate_preset_pass_manager(
        backend=backend, optimization_level=1, initial_layout=plan["physical_qubits"], seed_transpiler=42
    )
    compiled = manager.run(circuit)
    active = {
        compiled.find_bit(bit).index
        for instruction in compiled.data
        if instruction.operation.name not in ("barrier", "measure", "delay")
        for bit in instruction.qubits
    }
    if not set(plan["physical_qubits"]).issubset(active):
        raise QuantumConfigurationError("Compilation did not retain all requested physical qubits.")
    return (
        compiled,
        x,
        y,
        {
            "logical_qubits": q,
            "transpiled_width": compiled.num_qubits,
            "active_physical_qubits": sorted(active),
            "depth": compiled.depth(),
            "operation_counts": dict(compiled.count_ops()),
            "entangling_edges": len(edges),
            "entangling_physical_edges": [
                [plan["physical_qubits"][a], plan["physical_qubits"][b]] for a, b in edges
            ],
            "connected_components": q - len(edges),
        },
    )


def _write_manifest(path: Path, manifest: dict[str, Any], *, create: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False)
    if create:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return
    fd, name = tempfile.mkstemp(prefix=".quantum-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def submit_kernel(
    features: Sequence[Sequence[float]],
    manifest_path: str | Path,
    *,
    execute: bool = False,
    service: Any = None,
    **plan_kwargs: Any,
) -> dict[str, Any]:
    """Submit bounded SamplerV2 jobs without waiting; persist each accepted job ID.

    Without ``execute=True`` this returns only a plan and does not write a
    manifest. Existing manifests are never overwritten/resubmitted. Partial
    submission failures preserve known IDs and are never automatically retried.
    """
    if plan_kwargs.get("mode", "ibm") != "ibm":
        raise QuantumConfigurationError("submit_kernel supports IBM execution only.")
    if not isinstance(execute, bool):
        raise QuantumConfigurationError("execute must be an explicit boolean.")
    if plan_kwargs.get("kernel_method") == "projected":
        from .quantum_projected import submit_projected

        return submit_projected(features, manifest_path, execute=execute, service=service, **plan_kwargs)
    plan_kwargs["mode"] = "ibm"
    plan = plan_quantum(features, service=service, **plan_kwargs)
    if not execute:
        return {"status": "not_submitted", "plan": plan, "jobs": [], "execution_requested": False}
    if plan["status"] == "credentials_required":
        raise QuantumConfigurationError("IBM Quantum credentials are required before execution.")
    if plan["status"] != "ready":
        raise QuantumConfigurationError(f"IBM plan cannot run: {plan['status']}.")
    path = Path(manifest_path)
    if path.exists():
        raise QuantumConfigurationError(
            "Manifest already exists; retrieve it or use a new path for a new experiment."
        )
    svc = _service(service)
    backend = _provider_call(svc.backend, "backend lookup", plan["backend_name"])
    current = _provider_call(_backend_info, "backend status", backend)
    if (
        not current["operational"]
        or current["simulator"]
        or not set(plan["physical_qubits"]).issubset(current["physical_qubits"])
    ):
        raise QuantumConfigurationError("Backend availability changed after planning; create a fresh plan.")
    template, x, y, compiled_metadata = _provider_call(
        _compile_template, "circuit compilation", backend, plan
    )
    encoded = encode_features(features, plan["n_qubits"])
    from qiskit_ibm_runtime import SamplerV2

    sampler = SamplerV2(
        mode=backend,
        options={
            "max_execution_time": plan["max_execution_time_per_job"],
            "default_shots": plan["shots"],
            "environment": {"job_tags": ["herbfold", "fidelity-kernel"]},
        },
    )
    manifest = {
        "schema_version": 1,
        "status": "submitting",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "plan": plan,
        "compiled": compiled_metadata,
        "jobs": [],
        "hardware_executed": None,
    }
    try:
        _write_manifest(path, manifest, create=True)
    except FileExistsError:
        raise QuantumConfigurationError("Manifest already exists; submission has been refused.") from None
    try:
        for offset in range(0, plan["circuit_count"], plan["circuits_per_job"]):
            pairs = plan["pairs"][offset : offset + plan["circuits_per_job"]]
            circuits = [
                template.assign_parameters({**dict(zip(x, encoded[i])), **dict(zip(y, encoded[j]))})
                for i, j in pairs
            ]
            job = sampler.run(circuits, shots=plan["shots"])
            manifest["jobs"].append({"job_id": job.job_id(), "pairs": pairs, "status": "SUBMITTED"})
            _write_manifest(path, manifest)
        manifest["status"] = "submitted"
        _write_manifest(path, manifest)
    except Exception as exc:
        manifest["status"] = "partial_submission" if manifest["jobs"] else "submission_failed"
        manifest["error_type"] = type(exc).__name__
        manifest["retry_policy"] = (
            "No automatic retries. A network failure can leave an accepted job ID unknown; inspect IBM jobs before resubmitting."
        )
        try:
            _write_manifest(path, manifest)
        except OSError:
            pass
        raise QuantumExecutionError(
            f"IBM submission failed ({type(exc).__name__}); recover known jobs using the manifest. No jobs were retried.",
            manifest,
        ) from None
    return manifest


def retrieve_kernel(manifest_path: str | Path | dict[str, Any], service: Any = None) -> dict[str, Any]:
    """Poll once without waiting for jobs; decode measured zeros when all finish.

    Raw noisy diagonals and possibly negative eigenvalues are preserved. Missing
    or failed jobs never become zero-filled or synthetic kernel measurements.
    """
    path = None if isinstance(manifest_path, dict) else Path(manifest_path)
    manifest = json.loads(json.dumps(manifest_path)) if path is None else json.loads(path.read_text())
    if manifest.get("schema_version") == 2 and manifest.get("kernel_method") == "projected":
        from .quantum_projected import retrieve_projected

        return retrieve_projected(manifest_path, service=service)
    if manifest.get("schema_version") != 1 or "plan" not in manifest:
        raise QuantumConfigurationError("Unsupported quantum manifest.")
    if manifest.get("status") == "completed" and "kernel" in manifest:
        return manifest
    plan = manifest["plan"]
    expected_pairs = _pairs(_integer(plan["n_samples"], "n_samples", 1, 90))
    recorded_pairs = [pair for record in manifest.get("jobs", []) for pair in record["pairs"]]
    if recorded_pairs != expected_pairs[: len(recorded_pairs)] or len(recorded_pairs) > len(expected_pairs):
        raise QuantumConfigurationError("Manifest pair order is invalid.")
    if not manifest.get("jobs"):
        return manifest
    svc = _service(service)
    measured: dict[tuple[int, int], float] = {}
    statuses = []
    for record in manifest["jobs"]:
        job = _provider_call(svc.job, "job lookup", record["job_id"])
        raw_status = _provider_call(job.status, "job status")
        status = str(getattr(raw_status, "name", raw_status)).upper()
        record["status"] = status
        statuses.append(status)
        if status == "DONE":
            result = _provider_call(job.result, "job result", timeout=1)
            if len(result) != len(record["pairs"]):
                raise QuantumExecutionError(
                    "Runtime result count does not match the stored pair mapping.", manifest
                )
            record["observations"] = []
            for pair, pub_result in zip(record["pairs"], result):
                bits = pub_result.data.meas
                counts = bits.get_counts()
                total = sum(counts.values())
                if total != plan["shots"]:
                    raise QuantumExecutionError(
                        "Runtime result shots do not match the declared experiment.", manifest
                    )
                zeros = counts.get("0" * plan["n_qubits"], 0)
                probability = zeros / total
                measured[tuple(pair)] = probability
                # Wilson CI behaves sensibly even when no all-zero shots occur.
                z = 1.959963984540054
                center = (probability + z * z / (2 * total)) / (1 + z * z / total)
                radius = (
                    z
                    * math.sqrt(probability * (1 - probability) / total + z * z / (4 * total * total))
                    / (1 + z * z / total)
                )
                record["observations"].append(
                    {
                        "pair": pair,
                        "zero_counts": zeros,
                        "shots": total,
                        "fidelity": probability,
                        "wilson_95": [max(0, center - radius), min(1, center + radius)],
                    }
                )
            try:
                usage = job.metrics().get("usage", {})
                record["quantum_seconds"] = usage.get("quantum_seconds")
            except Exception:
                record["quantum_seconds"] = None
    if any(status in ("ERROR", "CANCELLED") for status in statuses):
        manifest["status"] = "failed"
    elif len(recorded_pairs) != len(expected_pairs):
        manifest["status"] = "partial_submission"
    elif all(status == "DONE" for status in statuses):
        kernel = np.empty((plan["n_samples"], plan["n_samples"]))
        for (i, j), value in measured.items():
            kernel[i, j] = kernel[j, i] = value
        manifest.update(
            status="completed",
            hardware_executed=True,
            kernel=kernel.tolist(),
            estimator="sampler_v2_all_zero_probability",
            kernel_diagnostics={
                "min_eigenvalue": float(np.linalg.eigvalsh(kernel).min()),
                "mean_diagonal": float(np.diag(kernel).mean()),
                "psd_projection_applied": False,
            },
        )
    else:
        manifest["status"] = "running"
    if path is not None:
        _write_manifest(path, manifest)
    return manifest
