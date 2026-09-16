"""Bounded one-qubit XYZ projections, explicit hardware execution, and RBF kernels.

Small disconnected blocks permit an exact factorized *reference*, even when all
healthy physical qubits are used. It is never substituted for measurements.
Raw Bloch estimates are neither readout-corrected nor projected onto a Bloch ball.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from . import quantum as qk

AXES = ("X", "Y", "Z")
MAX_QUBITS = 512
MAX_SAMPLES = 90
MAX_RAW_BITS = 64 * 1024**2
BOOTSTRAP_REPLICATES = 64
MAX_BOOTSTRAP_WORK = 160_000_000
SEED = 42
METHOD = "local_observable_projected_rbf"


def _json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def _schedule(n: int) -> list[dict]:
    return (
        [{"kind": "sample", "sample_index": i, "basis": axis} for i in range(n) for axis in AXES]
        + [{"kind": "duplicate", "sample_index": 0, "basis": axis} for axis in AXES]
        + [{"kind": "readout", "prepared_bit": bit, "basis": "Z"} for bit in (0, 1)]
    )


def _blocks(width: int, edges: list[tuple[int, int]], size: int) -> list[dict]:
    adjacency = {i: set() for i in range(width)}
    for a, b in edges:
        adjacency[a].add(b)
        adjacency[b].add(a)
    remaining, blocks = set(range(width)), []
    while remaining:
        chosen, frontier = [], [min(remaining)]
        while frontier and len(chosen) < size:
            node = frontier.pop(0)
            if node not in remaining:
                continue
            chosen.append(node)
            remaining.remove(node)
            frontier.extend(sorted(adjacency[node] & remaining))
        nodes = set(chosen)
        blocks.append(
            {"logical_qubits": chosen, "edges": [[a, b] for a, b in edges if a in nodes and b in nodes]}
        )
    return blocks


def plan_projected(
    features,
    *,
    mode="local",
    qubits=None,
    shots=1024,
    max_circuits=64,
    circuits_per_job=16,
    max_total_shots=65536,
    max_jobs=4,
    max_execution_time=120,
    layers=1,
    backend_name=None,
    service=None,
    block_size=6,
    gamma=1.0,
    kernel_method="projected",
) -> dict:
    array = qk._features(features)
    n = qk._integer(len(array), "projected samples", 1, MAX_SAMPLES)
    if mode not in ("local", "ibm"):
        raise qk.QuantumConfigurationError("mode must be 'local' or 'ibm'.")
    size = qk._integer(block_size, "block_size", 1, 6)
    if (
        isinstance(gamma, bool)
        or not isinstance(gamma, (int, float))
        or not math.isfinite(gamma)
        or not 0 < gamma <= 100
    ):
        raise qk.QuantumConfigurationError("gamma must be finite and in (0, 100].")
    shots = qk._integer(shots, "shots", 1, 100000)
    layers = qk._integer(layers, "layers", 1, 8)
    max_circuits = qk._integer(max_circuits, "max_circuits", 1, qk.MAX_KERNEL_CIRCUITS)
    circuits_per_job = qk._integer(circuits_per_job, "circuits_per_job", 1, 256)
    max_jobs = qk._integer(max_jobs, "max_jobs", 1, 256)
    max_total_shots = qk._integer(max_total_shots, "max_total_shots", 1, 100000000)
    max_execution_time = qk._integer(max_execution_time, "max_execution_time", 1, 10800)
    schedule = _schedule(n)
    jobs = math.ceil(len(schedule) / circuits_per_job) if mode == "ibm" else 0
    total_shots = shots * len(schedule) if mode == "ibm" else 0
    if len(schedule) > max_circuits or jobs > max_jobs or total_shots > max_total_shots:
        raise qk.QuantumConfigurationError(
            f"Projected measurement needs {len(schedule)} circuits, {jobs} jobs, {total_shots} shots; "
            f"budgets are {max_circuits}, {max_jobs}, {max_total_shots}."
        )
    if qubits not in (None, "max"):
        qk._integer(qubits, "qubits", 1, MAX_QUBITS)
    # Reuse credential/backend selection, without accidentally imposing the legacy N² budget.
    base = qk.plan_quantum(
        array[:1],
        mode=mode,
        qubits=4 if mode == "local" else qubits,
        shots=shots,
        max_circuits=qk.MAX_KERNEL_CIRCUITS,
        circuits_per_job=256,
        max_jobs=256,
        max_total_shots=100000000,
        max_execution_time=max_execution_time,
        layers=layers,
        backend_name=backend_name,
        service=service,
    )
    base.update(
        schema_version=2,
        kernel_method="projected",
        block_size=size,
        gamma=float(gamma),
        n_samples=n,
        n_features=int(array.shape[1]),
        feature_sha256=qk._fingerprint(array),
        feature_map="independent_blocks_ry_rz_cz_v1",
        pairs=[],
        measurement_schedule=schedule,
        circuit_count=len(schedule),
        job_count=jobs,
        circuits_per_job=circuits_per_job,
        total_shots=total_shots,
        max_total_qpu_seconds=jobs * max_execution_time,
        budget={"max_circuits": max_circuits, "max_jobs": max_jobs, "max_total_shots": max_total_shots},
        transpiler_seed=SEED,
        bootstrap_seed=SEED,
        diagonal_definition="RBF of the same estimated feature vector is 1 by definition; not measured self-fidelity.",
        kernel_formula="exp(-gamma * sum_q,sum_XYZ((r_i-r_j)^2) / (2*n_qubits))",
        warnings=[
            "Projected kernel is descriptor similarity, not affinity, efficacy, safety, or demonstrated quantum advantage.",
            "Independent blocks are classically simulable; extra repeated descriptor qubits add no input information.",
            "Raw XYZ projections are not readout-mitigated; finite-shot and hardware errors can shrink or inflate similarities.",
            "Diagonal 1 follows the RBF definition. The separately measured duplicate is the repeatability control.",
        ],
    )
    if base["status"] != "ready":
        return base
    if mode == "local":
        width = 4 if qubits is None else MAX_QUBITS if qubits == "max" else qubits
        base.update(
            n_qubits=width, physical_qubits=list(range(width)), backend_name="local_factorized_statevector"
        )
        edges = [(i, i + 1) for i in range(width - 1)]
    else:
        width = qk._integer(base["n_qubits"], "projected hardware qubits", 1, MAX_QUBITS)
        backend = qk._provider_call(qk._service(service).backend, "backend lookup", base["backend_name"])
        # A missing coupling map does not justify inventing hardware edges.
        if getattr(backend, "coupling_map", None) is None:
            edges = []
        else:
            edges = qk._provider_call(qk._hardware_edges, "coupling lookup", backend, base["physical_qubits"])
    if total_shots * width > MAX_RAW_BITS:
        raise qk.QuantumConfigurationError("Projected raw shot bits exceed the 64 MiB experiment bound.")
    base["blocks"] = _blocks(width, edges, size)
    base["topology_sha256"] = _sha({"physical_qubits": base["physical_qubits"], "blocks": base["blocks"]})
    base["maximum_simulated_block_qubits"] = max(len(b["logical_qubits"]) for b in base["blocks"])
    base["raw_bit_storage_upper_bound"] = total_shots * width
    return base


def _block_circuit(angles, block, layers):
    nodes = block["logical_qubits"]
    local = {node: i for i, node in enumerate(nodes)}
    edges = [(local[a], local[b]) for a, b in block["edges"]]
    return qk.build_feature_map([angles[i] for i in nodes], layers, edges)


def _feature_circuit(angles, plan):
    from qiskit import QuantumCircuit

    circuit = QuantumCircuit(plan["n_qubits"])
    for block in plan["blocks"]:
        circuit.compose(_block_circuit(angles, block, plan["layers"]), block["logical_qubits"], inplace=True)
    return circuit


def _kernel(vectors, gamma):
    values = np.asarray(vectors, dtype=float)
    flat = values.reshape(len(values), -1)
    distances = ((flat[:, None, :] - flat[None, :, :]) ** 2).sum(axis=2) / (2 * values.shape[1])
    return np.exp(-gamma * distances), distances


def ideal_features(features, plan):
    from qiskit.quantum_info import Pauli, Statevector

    encoded = qk.encode_features(features, plan["n_qubits"])
    result = np.empty((len(encoded), plan["n_qubits"], 3))
    # Only a <=64-element state is allocated, never a full-width statevector.
    for i, row in enumerate(encoded):
        for block in plan["blocks"]:
            nodes = block["logical_qubits"]
            state = Statevector.from_instruction(_block_circuit(row, block, plan["layers"]))
            for j, node in enumerate(nodes):
                for k, axis in enumerate(AXES):
                    label = ["I"] * len(nodes)
                    label[len(nodes) - j - 1] = axis
                    result[i, node, k] = float(state.expectation_value(Pauli("".join(label))).real)
    return result


def _references(features, plan):
    values = ideal_features(features, plan)
    ideal, _ = _kernel(values, plan["gamma"])
    encoded = qk.encode_features(features, plan["n_qubits"])
    classical = np.exp(-plan["gamma"] * ((encoded[:, None] - encoded[None, :]) ** 2).mean(axis=2) / 2)
    return (
        {
            "status": "available",
            "hardware_executed": False,
            "features": values.tolist(),
            "kernel": ideal.tolist(),
            "estimator": "exact_factorized_projected_reference",
            "maximum_simulated_block_qubits": plan["maximum_simulated_block_qubits"],
            "topology_sha256": plan["topology_sha256"],
        },
        {
            "estimator": "rbf_on_same_encoded_descriptors",
            "kernel": classical.tolist(),
            "gamma": plan["gamma"],
            "fit": "none; predeclared gamma",
            "hardware_executed": False,
        },
    )


def _diagnostics(values, plan, shots=None):
    kernel, distances = _kernel(values, plan["gamma"])
    upper = np.triu_indices(len(values), 1)
    off, off_distance = kernel[upper], distances[upper]
    variance = np.maximum(0, 1 - np.asarray(values) ** 2) / shots if shots else np.zeros_like(values)
    noise = (variance.sum(axis=(1, 2))[:, None] + variance.sum(axis=(1, 2))[None, :]) / (2 * plan["n_qubits"])
    floor = float(noise[upper].mean()) if len(off) else None
    mean_distance = float(off_distance.mean()) if len(off) else None
    diagnostics = {
        "diagonal_definition": plan["diagonal_definition"],
        "mean_diagonal": float(np.diag(kernel).mean()),
        "min_eigenvalue": float(np.linalg.eigvalsh(kernel).min()),
        "psd_projection_applied": False,
        "off_diagonal_min": float(off.min()) if len(off) else None,
        "off_diagonal_mean": float(off.mean()) if len(off) else None,
        "off_diagonal_max": float(off.max()) if len(off) else None,
        "off_diagonal_std": float(off.std()) if len(off) else None,
        "mean_squared_feature_distance": mean_distance,
        "shot_noise_distance_floor": floor,
        "signal_to_shot_noise": mean_distance / floor if floor and mean_distance is not None else None,
        "shot_noise_floor_method": "sum marginal plug-in variances; excludes readout bias and drift",
        "collapsed": bool(len(off) and off.min() > 0.999),
        "near_zero_concentration": bool(len(off) and off.max() < 0.001),
        "diagnostic_thresholds": {"near_one": 0.999, "near_zero": 0.001},
        "feature_rms": float(np.sqrt(np.mean(np.asarray(values) ** 2))),
        "raw_bloch_norm_above_one_fraction": float(np.mean(np.linalg.norm(values, axis=2) > 1 + 1e-10)),
    }
    return kernel, diagnostics


def local_projected(features, *, n_qubits=4, layers=1, max_circuits=64, block_size=6, gamma=1):
    plan = plan_projected(
        features,
        qubits=n_qubits,
        layers=layers,
        max_circuits=max_circuits,
        block_size=block_size,
        gamma=gamma,
    )
    ideal, classical = _references(features, plan)
    values = np.asarray(ideal["features"])
    kernel, diagnostics = _diagnostics(values, plan)
    metadata = plan | {"hardware_executed": False, "shots_used": 0, "estimator": METHOD}
    return {
        "schema_version": 2,
        "kernel_method": "projected",
        "status": "completed",
        "estimator": METHOD,
        "hardware_executed": False,
        "plan": plan,
        "metadata": metadata,
        "kernel": kernel.tolist(),
        "projected_features": {"axes": list(AXES), "values": values.tolist(), "wilson_95": None},
        "controls": {"status": "not_measured_local_exact"},
        "kernel_diagnostics": diagnostics,
        "ideal_reference": ideal,
        "classical_reference": classical,
        "kernel_uncertainty": {"status": "not_applicable_exact_local", "caveat": "No hardware measurement."},
    }


def _compile(backend, plan):
    from qiskit import QuantumCircuit, qpy
    from qiskit.circuit import ParameterVector
    from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager

    params = ParameterVector("theta", plan["n_qubits"])
    allowed = set(plan["physical_qubits"])
    healthy_edges = (
        set(qk._hardware_edges(backend, plan["physical_qubits"]))
        if getattr(backend, "coupling_map", None) is not None
        else set()
    )
    planned_edges = {tuple(edge) for block in plan["blocks"] for edge in block["edges"]}
    if not planned_edges <= healthy_edges:
        raise qk.QuantumConfigurationError("Hardware coupling changed after planning; make a fresh plan.")
    manager = generate_preset_pass_manager(
        backend=backend,
        optimization_level=1,
        initial_layout=plan["physical_qubits"],
        seed_transpiler=SEED,
        routing_method="none",
    )
    templates, metadata = {}, {}
    for key in (*AXES, "readout0", "readout1"):
        if key in AXES:
            circuit = _feature_circuit(params, plan)
            if key == "X":
                circuit.h(range(plan["n_qubits"]))
            elif key == "Y":
                circuit.sdg(range(plan["n_qubits"]))
                circuit.h(range(plan["n_qubits"]))
        else:
            circuit = QuantumCircuit(plan["n_qubits"])
            if key == "readout1":
                circuit.x(range(plan["n_qubits"]))
        circuit.measure_all()
        compiled = manager.run(circuit)
        active, measured = set(), {}
        allowed_physical_edges = {
            tuple(sorted((plan["physical_qubits"][a], plan["physical_qubits"][b]))) for a, b in healthy_edges
        }
        for instruction in compiled.data:
            physical = [compiled.find_bit(bit).index for bit in instruction.qubits]
            if instruction.operation.name == "measure":
                measured[compiled.find_bit(instruction.clbits[0]).index] = physical[0]
            if instruction.operation.name not in ("barrier", "measure", "delay"):
                active.update(physical)
                if len(physical) == 2 and tuple(sorted(physical)) not in allowed_physical_edges:
                    raise qk.QuantumConfigurationError(
                        "Compiled circuit uses an unapproved or faulty coupling."
                    )
        if not active <= allowed or (key in AXES and active != allowed):
            raise qk.QuantumConfigurationError(
                "Compiled projected circuit did not preserve the selected active qubits."
            )
        if measured != dict(enumerate(plan["physical_qubits"])):
            raise qk.QuantumConfigurationError(
                "Compiled measurement layout changed logical-to-physical mapping."
            )
        buffer = io.BytesIO()
        qpy.dump(compiled, buffer)
        metadata[key] = {
            "depth": compiled.depth(),
            "operation_counts": dict(compiled.count_ops()),
            "active_physical_qubits": sorted(active),
            "measurement_physical_qubits": list(measured.values()),
            "qpy_sha256": hashlib.sha256(buffer.getvalue()).hexdigest(),
        }
        templates[key] = compiled
    return templates, params, metadata


def submit_projected(features, manifest_path, *, execute=False, service=None, **kwargs):
    kwargs["mode"] = "ibm"
    plan = plan_projected(features, service=service, **kwargs)
    if not execute:
        return {
            "schema_version": 2,
            "kernel_method": "projected",
            "status": "not_submitted",
            "plan": plan,
            "jobs": [],
            "hardware_executed": None,
            "execution_requested": False,
        }
    if plan["status"] != "ready":
        raise qk.QuantumConfigurationError(f"IBM projected plan cannot run: {plan['status']}.")
    path = Path(manifest_path)
    if path.exists():
        raise qk.QuantumConfigurationError("Manifest already exists; retrieve it or use a new path.")
    svc = qk._service(service)
    backend = qk._provider_call(svc.backend, "backend lookup", plan["backend_name"])
    current = qk._provider_call(qk._backend_info, "backend status", backend)
    if (
        not current["operational"]
        or current["simulator"]
        or not set(plan["physical_qubits"]) <= set(current["physical_qubits"])
    ):
        raise qk.QuantumConfigurationError("Backend availability changed after planning.")
    templates, parameters, compiled = qk._provider_call(_compile, "projected compilation", backend, plan)
    encoded = qk.encode_features(features, plan["n_qubits"])
    ideal, classical = _references(features, plan)
    from qiskit_ibm_runtime import SamplerV2

    sampler = SamplerV2(
        mode=backend,
        options={
            "max_execution_time": plan["max_execution_time_per_job"],
            "default_shots": plan["shots"],
            "environment": {"job_tags": ["herbfold", "projected-kernel"]},
        },
    )
    manifest = {
        "schema_version": 2,
        "kernel_method": "projected",
        "status": "submitting",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "plan": plan,
        "compiled": compiled,
        "features": qk._features(features).tolist(),
        "jobs": [],
        "circuit_batches": [],
        "hardware_executed": None,
        "ideal_reference": ideal,
        "classical_reference": classical,
    }
    manifest["request_sha256"] = _sha({"plan": plan, "features": manifest["features"]})
    try:
        qk._write_manifest(path, manifest, create=True)
    except FileExistsError:
        raise qk.QuantumConfigurationError("Manifest already exists; submission has been refused.") from None
    try:
        schedule = plan["measurement_schedule"]
        for offset in range(0, len(schedule), plan["circuits_per_job"]):
            pubs = schedule[offset : offset + plan["circuits_per_job"]]
            circuits = []
            for pub in pubs:
                if pub["kind"] == "readout":
                    circuit = templates[f"readout{pub['prepared_bit']}"]
                else:
                    template = templates[pub["basis"]]
                    circuit = template.assign_parameters(dict(zip(parameters, encoded[pub["sample_index"]])))
                circuits.append(circuit)
            from qiskit import qpy

            circuit_bytes = io.BytesIO()
            qpy.dump(circuits, circuit_bytes)
            artifact = _persist_bytes(
                path, f"projected-circuits-{offset:04d}", "qpy", circuit_bytes.getvalue()
            )
            pub_indices = list(range(offset, offset + len(pubs)))
            batch = {
                "pub_indices": pub_indices,
                "measurement_mapping": pubs,
                "physical_qubits_in_clbit_order": plan["physical_qubits"],
                "artifact": artifact,
                "status": "submission_intent",
            }
            manifest["circuit_batches"].append(batch)
            # Archive the actual bound circuits and intent before the provider call.
            qk._write_manifest(path, manifest)
            job = sampler.run(circuits, shots=plan["shots"])
            batch.update(status="submitted", job_id=job.job_id())
            manifest["jobs"].append(
                {
                    "job_id": job.job_id(),
                    "pub_indices": pub_indices,
                    "circuit_artifact": artifact,
                    "status": "SUBMITTED",
                }
            )
            qk._write_manifest(path, manifest)
        manifest["status"] = "submitted"
        qk._write_manifest(path, manifest)
    except Exception as exc:
        manifest["status"] = "partial_submission" if manifest["jobs"] else "submission_failed"
        manifest["error_type"] = type(exc).__name__
        manifest["retry_policy"] = (
            "No automatic retries; inspect provider jobs before resubmitting after network failure."
        )
        qk._write_manifest(path, manifest)
        raise qk.QuantumExecutionError(
            f"IBM submission failed ({type(exc).__name__}); known job IDs are preserved.", manifest
        ) from None
    return manifest


def _wilson(success, total):
    p = np.asarray(success, dtype=float) / total
    z = 1.959963984540054
    center = (p + z * z / (2 * total)) / (1 + z * z / total)
    radius = z * np.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return np.stack([np.maximum(0, center - radius), np.minimum(1, center + radius)], axis=-1)


def _decode(counts, width, shots):
    if not isinstance(counts, dict) or not counts:
        raise qk.QuantumConfigurationError("Projected measurement counts are empty.")
    if any(
        not isinstance(bits, str)
        or len(bits) != width
        or set(bits) - {"0", "1"}
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count <= 0
        for bits, count in counts.items()
    ):
        raise qk.QuantumConfigurationError("Projected count width/value mismatch.")
    if sum(counts.values()) != shots:
        raise qk.QuantumConfigurationError("Projected result shots do not match the declared experiment.")
    # Seeded whole-shot bootstrap must be reproducible from canonical saved JSON.
    counts = dict(sorted(counts.items()))
    # Qiskit bitstrings are MSB-first; clbit i is the character at -i-1.
    bit_values = np.asarray([[1 if c == "0" else -1 for c in bits[::-1]] for bits in counts], dtype=np.int8)
    frequencies = np.asarray(list(counts.values()), dtype=np.int64)
    means = frequencies @ bit_values / shots
    intervals = 2 * _wilson((means + 1) * shots / 2, shots) - 1
    return means, intervals, bit_values, frequencies


def _persist_raw(path, index, payload):
    return _persist_bytes(path, f"projected-counts-{index:04d}", "json", _json(payload))


def _persist_bytes(path, prefix, suffix, data):
    digest = hashlib.sha256(data).hexdigest()
    name = f"{prefix}-{digest[:16]}.{suffix}"
    raw = path.parent / name
    try:
        fd = os.open(raw, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if raw.read_bytes() != data:
            raise qk.QuantumConfigurationError(
                "Existing raw count artifact failed integrity verification."
            ) from None
    return {"artifact": name, "sha256": digest, "size_bytes": len(data)}


def _verify_artifact(path, reference):
    if "data" in reference:
        if _sha(reference["data"]) != reference["sha256"]:
            raise qk.QuantumConfigurationError("Inline raw artifact digest mismatch.")
        return
    if path is None:
        raise qk.QuantumConfigurationError(
            "A manifest path is required to verify its external raw artifacts."
        )
    name = reference.get("artifact")
    if not isinstance(name, str) or Path(name).name != name:
        raise qk.QuantumConfigurationError("Invalid projected artifact path.")
    artifact = path.parent / name
    size = reference.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_RAW_BITS * 4:
        raise qk.QuantumConfigurationError("Invalid projected artifact size.")
    try:
        if artifact.is_symlink() or artifact.stat().st_size != size:
            raise qk.QuantumConfigurationError("Projected artifact size or path integrity mismatch.")
        digest = hashlib.sha256()
        with artifact.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != reference.get("sha256"):
            raise qk.QuantumConfigurationError("Projected artifact SHA256 integrity mismatch.")
    except OSError:
        raise qk.QuantumConfigurationError("Projected source artifact is missing or unreadable.") from None


def _usage(job):
    value = None
    try:
        value = job.metrics().get("usage", {}).get("quantum_seconds")
    except Exception:
        pass
    if value is None:
        try:
            value = job.usage()
        except Exception:
            pass
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    ):
        return float(value)
    return None


def _bootstrap(decoded, plan):
    width, n = plan["n_qubits"], plan["n_samples"]
    work = sum(len(item[3]) for item in decoded[: 3 * n]) * width * BOOTSTRAP_REPLICATES
    caveat = (
        "Percentiles of 64 raw resampled kernels, not a calibrated confidence interval for the true kernel. "
        "Resampling adds shot noise to squared feature distances, so this percentile range can exclude "
        "the original point estimate; no centering or bias correction is applied. "
        "Empirical whole-bitstring shot bootstrap only; no calibration uncertainty, "
        "between-job drift, or systematic-bias coverage. Unobserved outcomes are absent; "
        "all-identical shots can yield a degenerate interval and do not establish zero uncertainty."
    )
    if work > MAX_BOOTSTRAP_WORK:
        return {
            "status": "omitted_cpu_budget",
            "caveat": caveat,
            "work_estimate": work,
            "work_budget": MAX_BOOTSTRAP_WORK,
        }
    rng = np.random.default_rng(SEED)
    features = np.empty((BOOTSTRAP_REPLICATES, n, width, 3))
    for index, (_, _, bits, frequencies) in enumerate(decoded[: 3 * n]):
        weights = rng.multinomial(plan["shots"], frequencies / plan["shots"], size=BOOTSTRAP_REPLICATES)
        features[:, index // 3, :, index % 3] = (weights @ bits) / plan["shots"]
    kernels = np.asarray([_kernel(row, plan["gamma"])[0] for row in features])
    lower, upper = np.quantile(kernels, [0.025, 0.975], axis=0)
    return {
        "status": "available",
        "method": "whole_bitstring_empirical_percentile_bootstrap",
        "replicates": BOOTSTRAP_REPLICATES,
        "seed": SEED,
        "lower_95": lower.tolist(),
        "upper_95": upper.tolist(),
        "caveat": caveat,
    }


def retrieve_projected(manifest_path, service=None):
    path = None if isinstance(manifest_path, dict) else Path(manifest_path)
    manifest = json.loads(json.dumps(manifest_path)) if path is None else json.loads(path.read_text())
    plan = manifest.get("plan", {})
    if manifest.get("schema_version") != 2 or manifest.get("kernel_method") != "projected":
        raise qk.QuantumConfigurationError("Unsupported projected manifest.")
    if manifest.get("request_sha256") != _sha({"plan": plan, "features": manifest.get("features")}):
        raise qk.QuantumConfigurationError("Projected request manifest digest mismatch.")
    n = qk._integer(plan["n_samples"], "samples", 1, MAX_SAMPLES)
    width = qk._integer(plan["n_qubits"], "projected qubits", 1, MAX_QUBITS)
    expected = _schedule(n)
    if plan.get("measurement_schedule") != expected:
        raise qk.QuantumConfigurationError("Projected PUB mapping mismatch.")
    recorded = [i for job in manifest.get("jobs", []) for i in job["pub_indices"]]
    if recorded != list(range(len(recorded))) or len(recorded) > len(expected):
        raise qk.QuantumConfigurationError("Projected PUB order is invalid.")
    for batch in manifest.get("circuit_batches", []):
        _verify_artifact(path, batch["artifact"])
    if manifest.get("status") == "completed" and "kernel" in manifest:
        if recorded != list(range(len(expected))):
            raise qk.QuantumConfigurationError("Completed projected measurement is incomplete.")
        for record in manifest["jobs"]:
            _verify_artifact(path, record["raw_counts"])
        return manifest
    if not manifest.get("jobs"):
        return manifest
    svc, decoded, statuses = qk._service(service), {}, []
    for job_index, record in enumerate(manifest["jobs"]):
        job = qk._provider_call(svc.job, "job lookup", record["job_id"])
        status = qk._provider_call(job.status, "job status")
        record["status"] = str(getattr(status, "name", status)).upper()
        statuses.append(record["status"])
        if record["status"] != "DONE":
            continue
        pubs = qk._provider_call(job.result, "job result", timeout=1)
        if len(pubs) != len(record["pub_indices"]):
            raise qk.QuantumExecutionError(
                "Runtime result count does not match projected PUB mapping.", manifest
            )
        raw, observations = [], []
        for index, pub in zip(record["pub_indices"], pubs):
            counts = pub.data.meas.get_counts()
            item = _decode(counts, width, plan["shots"])
            decoded[index] = item
            raw.append({"pub_index": index, "mapping": expected[index], "counts": counts})
            observations.append(
                {
                    "pub_index": index,
                    **expected[index],
                    "shots": plan["shots"],
                    "expectations": item[0].tolist(),
                    "wilson_95": item[1].tolist(),
                }
            )
        record["observations"] = observations
        raw_payload = {
            "schema_version": 1,
            "job_id": record["job_id"],
            "request_sha256": manifest["request_sha256"],
            "physical_qubits_in_clbit_order": plan["physical_qubits"],
            "bit_order": "MSB-first; logical i = clbit i = character -i-1",
            "pubs": raw,
        }
        record["raw_counts"] = (
            _persist_raw(path, job_index, raw_payload)
            if path
            else {"sha256": _sha(raw_payload), "data": raw_payload}
        )
        record["quantum_seconds"] = _usage(job)
    if any(s in ("ERROR", "CANCELLED") for s in statuses):
        manifest["status"] = "failed"
    elif len(recorded) != len(expected):
        manifest["status"] = "partial_submission"
    elif not all(s == "DONE" for s in statuses):
        manifest["status"] = "running"
    else:
        ordered = [decoded[i] for i in range(len(expected))]
        values = np.asarray([item[0] for item in ordered[: 3 * n]]).reshape(n, 3, width).transpose(0, 2, 1)
        intervals = (
            np.asarray([item[1] for item in ordered[: 3 * n]]).reshape(n, 3, width, 2).transpose(0, 2, 1, 3)
        )
        duplicate = np.asarray([item[0] for item in ordered[3 * n : 3 * n + 3]]).T
        distance = float(np.sum((values[0] - duplicate) ** 2) / (2 * width))
        zero_error, one_error = (1 - ordered[-2][0]) / 2, (1 + ordered[-1][0]) / 2
        kernel, diagnostics = _diagnostics(values, plan, plan["shots"])
        diagnostics["ideal_kernel_mean_absolute_error"] = float(
            np.abs(kernel - np.asarray(manifest["ideal_reference"]["kernel"])).mean()
        )
        manifest.update(
            status="completed",
            hardware_executed=True,
            estimator=METHOD,
            kernel=kernel.tolist(),
            projected_features={
                "axes": list(AXES),
                "values": values.tolist(),
                "wilson_95": intervals.tolist(),
            },
            controls={
                "duplicate": {
                    "sample_index": 0,
                    "kernel_to_original": float(np.exp(-plan["gamma"] * distance)),
                    "squared_distance": distance,
                    "features": duplicate.tolist(),
                    "wilson_95": np.asarray([item[1] for item in ordered[3 * n : 3 * n + 3]])
                    .transpose(1, 0, 2)
                    .tolist(),
                },
                "readout": {
                    "zero_error_rates": zero_error.tolist(),
                    "one_error_rates": one_error.tolist(),
                    "mean_error": float(np.mean([zero_error, one_error])),
                    "zero_error_wilson_95": _wilson(zero_error * plan["shots"], plan["shots"]).tolist(),
                    "one_error_wilson_95": _wilson(one_error * plan["shots"], plan["shots"]).tolist(),
                    "mitigation_applied": False,
                    "interpretation": "Prepared-state error includes state preparation and readout, not an isolated detector calibration.",
                },
            },
            kernel_diagnostics=diagnostics,
            kernel_uncertainty=_bootstrap(ordered, plan),
        )
    if path is not None:
        qk._write_manifest(path, manifest)
    return manifest
