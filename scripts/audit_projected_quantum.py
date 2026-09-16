"""Independently audit saved projected-kernel counts; no provider calls or job submission.

Uses only NumPy and the standard library. It does not import the producer's
quantum implementation. Full-width statevectors are never allocated: the optional
noiseless reference is rebuilt one declared block (at most six qubits) at a time.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

AXES = ("X", "Y", "Z")
MAX_FILE_BYTES = 128 * 1024**2


class AuditError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise AuditError(message)


def canonical_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def read_json(path):
    path = Path(path)
    before = path.stat()
    require(path.is_file() and not path.is_symlink() and before.st_size <= MAX_FILE_BYTES,
            "Audit input must be a bounded regular file")
    raw = path.read_bytes()
    after = path.stat()
    require((before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino)
            == (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino),
            "Audit input changed while reading")
    return json.loads(raw), hashlib.sha256(raw).hexdigest(), len(raw)


def integer(value, name, low, high):
    require(type(value) is int and low <= value <= high, f"Invalid {name}")
    return value


def close(actual, expected, name, tolerance=2e-12):
    try:
        actual, expected = np.asarray(actual, dtype=float), np.asarray(expected, dtype=float)
    except (TypeError, ValueError):
        raise AuditError(f"Invalid numeric field: {name}") from None
    require(actual.shape == expected.shape and np.isfinite(actual).all()
            and np.isfinite(expected).all(), f"Shape/nonfinite mismatch: {name}")
    require(np.allclose(actual, expected, atol=tolerance, rtol=0), f"Recalculation mismatch: {name}")


def wilson(successes, shots):
    successes = np.asarray(successes, dtype=float)
    p = successes / shots
    z = 1.959963984540054
    denominator = 1 + z * z / shots
    center = (p + z * z / (2 * shots)) / denominator
    radius = z * np.sqrt(p * (1 - p) / shots + z * z / (4 * shots * shots)) / denominator
    return np.stack((np.maximum(0, center - radius), np.minimum(1, center + radius)), axis=-1)


def analyze_counts(counts, width, shots):
    """Classical bit 0 is the least-significant integer bit, not string[0]."""
    integer(width, "bit width", 1, 512)
    integer(shots, "shots", 1, 100000)
    require(isinstance(counts, dict) and 0 < len(counts) <= shots, "Invalid count dictionary")
    for bits, count in counts.items():
        require(isinstance(bits, str) and len(bits) == width and not (set(bits) - {"0", "1"}),
                "Count bitstring width/alphabet mismatch")
        integer(count, "outcome count", 1, shots)
    require(sum(counts.values()) == shots, "Count shot total mismatch")
    outcomes = sorted(counts)
    values = [int(bits, 2) for bits in outcomes]
    frequencies = np.asarray([counts[bits] for bits in outcomes], dtype=np.int64)
    signs = np.asarray([[1 - 2 * ((value >> bit) & 1) for bit in range(width)]
                        for value in values], dtype=np.int64)
    zero_counts = np.asarray([sum(counts[bits] for bits, value in zip(outcomes, values, strict=True)
                                 if not ((value >> bit) & 1)) for bit in range(width)])
    expectations = 2 * zero_counts / shots - 1
    return {"expectations": expectations, "wilson_95": 2 * wilson(zero_counts, shots) - 1,
            "zero_counts": zero_counts, "signs": signs, "frequencies": frequencies,
            "all_zero_counts": counts.get("0" * width, 0),
            "mean_hamming_weight": sum(counts[bits] * value.bit_count()
                                       for bits, value in zip(outcomes, values, strict=True)) / shots}


def projected_kernel(values, gamma):
    values = np.asarray(values, dtype=float)
    require(values.ndim == 3 and values.shape[2] == 3 and values.shape[1] > 0
            and np.isfinite(values).all(), "Invalid projected feature tensor")
    require(isinstance(gamma, (int, float)) and not isinstance(gamma, bool)
            and math.isfinite(gamma) and 0 < gamma <= 100, "Invalid gamma")
    n, width, _ = values.shape
    distances = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            distances[i, j] = distances[j, i] = float(np.sum((values[i] - values[j]) ** 2) / (2 * width))
    return np.exp(-gamma * distances), distances


def expected_schedule(n):
    rows = [{"kind": "sample", "sample_index": i, "basis": basis}
            for i in range(n) for basis in AXES]
    rows += [{"kind": "duplicate", "sample_index": 0, "basis": basis} for basis in AXES]
    rows += [{"kind": "readout", "prepared_bit": bit, "basis": "Z"} for bit in (0, 1)]
    return rows


def validate_plan(manifest):
    require(manifest.get("schema_version") == 2 and manifest.get("kernel_method") == "projected",
            "Expected a projected schema-2 measurement manifest")
    require(manifest.get("status") == "completed" and manifest.get("hardware_executed") is True,
            "Audit requires completed hardware measurements")
    plan = manifest["plan"]
    n = integer(plan["n_samples"], "sample count", 1, 90)
    width = integer(plan["n_qubits"], "qubit count", 1, 512)
    integer(plan["shots"], "shots", 1, 100000)
    integer(plan["block_size"], "block size", 1, 6)
    integer(plan["layers"], "layers", 1, 8)
    require(plan.get("measurement_schedule") == expected_schedule(n), "PUB schedule mismatch")
    physical = plan["physical_qubits"]
    require(isinstance(physical, list) and len(physical) == width and len(set(physical)) == width
            and all(type(i) is int and i >= 0 for i in physical), "Physical layout mismatch")
    require(not set(physical) & set(plan.get("backend_faulty_qubits", [])), "Plan uses faulty qubits")
    blocks = plan["blocks"]
    nodes = []
    for block in blocks:
        qubits = block["logical_qubits"]
        require(0 < len(qubits) <= plan["block_size"] and len(set(qubits)) == len(qubits),
                "Invalid block width or duplicate qubit")
        require(all(type(q) is int and 0 <= q < width for q in qubits), "Block qubit outside plan")
        edges = block["edges"]
        require(all(len(edge) == 2 and edge[0] != edge[1] and set(edge) <= set(qubits)
                    for edge in edges), "Entangling edge crosses independent blocks")
        require(len({tuple(sorted(edge)) for edge in edges}) == len(edges), "Duplicate entangling edge")
        nodes.extend(qubits)
    require(sorted(nodes) == list(range(width)), "Blocks must partition every logical qubit once")
    require(plan["topology_sha256"] == digest({"physical_qubits": physical, "blocks": blocks}),
            "Topology digest mismatch")
    require(manifest["request_sha256"] == digest({"plan": plan, "features": manifest["features"]}),
            "Request digest mismatch")
    features = np.asarray(manifest["features"], dtype=float)
    require(features.ndim == 2 and features.shape == (n, plan["n_features"])
            and np.isfinite(features).all(), "Input feature shape/content mismatch")
    feature_sha = hashlib.sha256(json.dumps(features.tolist(), separators=(",", ":"),
                                           allow_nan=False).encode()).hexdigest()
    require(feature_sha == plan["feature_sha256"], "Input feature digest mismatch")
    for basis in (*AXES, "readout0", "readout1"):
        compiled = manifest["compiled"][basis]
        require(compiled["measurement_physical_qubits"] == physical,
                "Compiled measurement layout mismatch")
        if basis in AXES:
            require(sorted(compiled["active_physical_qubits"]) == sorted(physical),
                    "Compiled basis circuit does not activate every selected qubit")
    return plan


def encoded_descriptors(features, width):
    bounded = 2 * np.arctan(np.asarray(features, dtype=float))
    if bounded.shape[1] < width:
        return bounded[:, np.arange(width) % bounded.shape[1]]
    return np.asarray([[np.mean(row[qubit::width]) for qubit in range(width)] for row in bounded])


def independent_ideal(features, plan):
    """Direct <=64-amplitude NumPy reconstruction, including block-local indexing."""
    encoded = encoded_descriptors(features, plan["n_qubits"])
    result = np.empty((len(encoded), plan["n_qubits"], 3))
    for sample, angles in enumerate(encoded):
        for block in plan["blocks"]:
            nodes = block["logical_qubits"]
            width = len(nodes)
            require(1 <= width <= 6, "No hardware-width statevector simulation permitted")
            theta = angles[nodes]
            indices = np.arange(1 << width)
            state = np.full(1 << width, 1 / math.sqrt(1 << width), dtype=complex)
            for layer in range(plan["layers"]):
                for q in range(width):
                    zero = indices[(indices & (1 << q)) == 0]
                    one = zero | (1 << q)
                    a, b = state[zero].copy(), state[one].copy()
                    angle = theta[(q + layer) % width]
                    co, si = math.cos(angle / 2), math.sin(angle / 2)
                    state[zero], state[one] = co * a - si * b, si * a + co * b
                    phase = theta[(q + 2 * layer + 1) % width] / 2
                    state[zero] *= np.exp(-0.5j * phase)
                    state[one] *= np.exp(0.5j * phase)
                for edge in block["edges"]:
                    a, b = nodes.index(edge[0]), nodes.index(edge[1])
                    state[((indices >> a) & 1).astype(bool) & ((indices >> b) & 1).astype(bool)] *= -1
            for q, logical in enumerate(nodes):
                zero = indices[(indices & (1 << q)) == 0]
                one = zero | (1 << q)
                cross = np.vdot(state[zero], state[one])
                result[sample, logical] = [2 * cross.real, 2 * cross.imag,
                                           float(np.sum(abs(state[zero]) ** 2 - abs(state[one]) ** 2))]
    return result, encoded



def audit_bound_qpy(manifest, manifest_path, ideal):
    """Verify persisted bound circuits by factorized native-gate calculation."""
    from qiskit import qpy

    batches = manifest.get("circuit_batches", [])
    if not batches:
        return {"status": "unavailable", "basis_gate_semantics_verified": False}
    plan = manifest["plan"]
    schedule, physical = plan["measurement_schedule"], plan["physical_qubits"]
    block_for_physical = {physical[q]: (index, local)
                          for index, block in enumerate(plan["blocks"])
                          for local, q in enumerate(block["logical_qubits"])}
    evidence, indices, largest_error = [], [], 0.0
    for batch in batches:
        ref = batch["artifact"]
        relative = Path(ref["artifact"])
        require(not relative.is_absolute() and ".." not in relative.parts, "Unsafe QPY path")
        path = Path(manifest_path).parent / relative
        require(path.resolve().is_relative_to(Path(manifest_path).parent.resolve())
                and path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_FILE_BYTES,
                "QPY artifact unavailable or outside audit scope")
        before = path.stat()
        raw = path.read_bytes()
        after = path.stat()
        require((before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino)
                == (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino),
                "QPY artifact changed while reading")
        require(len(raw) == ref["size_bytes"] and hashlib.sha256(raw).hexdigest() == ref["sha256"],
                "Bound QPY hash/size mismatch")
        require(batch["physical_qubits_in_clbit_order"] == physical
                and batch["measurement_mapping"] == [schedule[i] for i in batch["pub_indices"]],
                "QPY batch role/layout mapping mismatch")
        matching_jobs = [job for job in manifest["jobs"] if job["job_id"] == batch["job_id"]]
        require(len(matching_jobs) == 1 and matching_jobs[0]["pub_indices"] == batch["pub_indices"]
                and matching_jobs[0]["circuit_artifact"] == ref, "QPY batch not linked to measured job")
        circuits = qpy.load(io.BytesIO(raw))
        require(len(circuits) == len(batch["pub_indices"]), "QPY PUB count mismatch")
        for index, circuit in zip(batch["pub_indices"], circuits, strict=True):
            require(not circuit.parameters and circuit.num_clbits == len(physical),
                    "QPY circuit is unbound or has unexpected classical width")
            grouped = [[] for _ in plan["blocks"]]
            measurements, measured_qubits = {}, set()
            for instruction in circuit.data:
                operation = instruction.operation
                qubits = [circuit.find_bit(q).index for q in instruction.qubits]
                if operation.name == "measure":
                    require(len(qubits) == 1 and qubits[0] not in measured_qubits,
                            "Duplicate or malformed QPY measurement")
                    classical = circuit.find_bit(instruction.clbits[0]).index
                    require(classical not in measurements, "Duplicate classical measurement bit")
                    measurements[classical] = qubits[0]
                    measured_qubits.add(qubits[0])
                elif operation.name in ("barrier", "delay"):
                    continue
                else:
                    require(1 <= len(qubits) <= 2 and not set(qubits) & measured_qubits,
                            "Unsupported dynamic or multi-qubit gate in QPY")
                    require(all(q in block_for_physical for q in qubits), "Gate uses unselected physical qubit")
                    locations = [block_for_physical[q] for q in qubits]
                    require(len({block for block, _ in locations}) == 1, "Native gate crosses declared independent blocks")
                    matrix = np.asarray(operation.to_matrix(), dtype=complex)
                    require(matrix.shape == (1 << len(qubits), 1 << len(qubits))
                            and np.isfinite(matrix).all(), "Native gate matrix is invalid")
                    grouped[locations[0][0]].append((matrix, [local for _, local in locations]))
            require(measurements == dict(enumerate(physical)), "Actual bound QPY measurement layout mismatch")
            actual = np.empty(len(physical))
            for block, gates in zip(plan["blocks"], grouped, strict=True):
                width = len(block["logical_qubits"])
                require(1 <= width <= 6, "No full-width QPY statevector simulation permitted")
                state = np.zeros(1 << width, dtype=complex)
                state[0] = 1
                all_indices = np.arange(1 << width)
                for matrix, qargs in gates:
                    mask = sum(1 << q for q in qargs)
                    for base in range(1 << width):
                        if base & mask:
                            continue
                        positions = [base | sum(((pattern >> k) & 1) << q for k, q in enumerate(qargs))
                                     for pattern in range(1 << len(qargs))]
                        state[positions] = matrix @ state[positions]
                for local, logical in enumerate(block["logical_qubits"]):
                    signs = 1 - 2 * ((all_indices >> local) & 1)
                    actual[logical] = float(np.sum(abs(state) ** 2 * signs))
            role = schedule[index]
            expected = (np.full(len(physical), 1 - 2 * role["prepared_bit"])
                        if role["kind"] == "readout"
                        else ideal[role["sample_index"], :, AXES.index(role["basis"])])
            close(actual, expected, f"bound QPY PUB {index} basis/feature semantics", tolerance=2e-10)
            largest_error = max(largest_error, float(np.max(abs(actual - expected))))
            indices.append(index)
        evidence.append({"path": str(path), "sha256": ref["sha256"], "size_bytes": len(raw),
                         "job_id": batch["job_id"], "pub_indices": batch["pub_indices"]})
    require(indices == list(range(len(schedule))), "Bound QPY schedule incomplete or reordered")
    return {"status": "passed", "artifacts": evidence, "basis_gate_semantics_verified": True,
            "actual_native_gates_stay_inside_declared_blocks": True,
            "all_measurement_physical_mappings_verified": True,
            "maximum_expectation_difference_from_independent_reference": largest_error,
            "scope": "Saved bound submission QPY decoded and each <=6-qubit block simulated from native gates; no provider hardware execution attestation."}

def audit_manifest(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest, manifest_sha, _ = read_json(manifest_path)
    plan = validate_plan(manifest)
    n, width, shots = plan["n_samples"], plan["n_qubits"], plan["shots"]
    schedule = expected_schedule(n)
    require(sum(len(job["pub_indices"]) for job in manifest["jobs"]) == len(schedule),
            "Missing or additional measured PUBs")
    decoded, raw_evidence, observations = {}, [], {}
    actual_order = []
    for job in manifest["jobs"]:
        require(job["status"] == "DONE", "Provider job did not complete")
        ref = job["raw_counts"]
        artifact = Path(ref["artifact"])
        require(not artifact.is_absolute() and ".." not in artifact.parts, "Unsafe count artifact path")
        path = manifest_path.parent / artifact
        require(path.resolve().is_relative_to(manifest_path.parent), "Count artifact escapes manifest directory")
        raw, raw_sha, raw_size = read_json(path)
        require(raw_sha == ref["sha256"] and raw_size == ref["size_bytes"], "Raw count artifact hash/size mismatch")
        require(raw["schema_version"] == 1 and raw["job_id"] == job["job_id"]
                and raw["request_sha256"] == manifest["request_sha256"], "Raw source job/request mismatch")
        require(raw["physical_qubits_in_clbit_order"] == plan["physical_qubits"], "Raw count layout mismatch")
        indices = [pub["pub_index"] for pub in raw["pubs"]]
        require(indices == job["pub_indices"], "Raw PUB order differs from provider job mapping")
        actual_order.extend(indices)
        raw_evidence.append({"path": str(path), "sha256": raw_sha, "size_bytes": raw_size,
                             "job_id": job["job_id"], "pub_indices": indices})
        for observation in job["observations"]:
            index = observation["pub_index"]
            require(index not in observations, "Duplicate observation index")
            observations[index] = observation
        for pub in raw["pubs"]:
            index = integer(pub["pub_index"], "PUB index", 0, len(schedule) - 1)
            require(index not in decoded and pub["mapping"] == schedule[index], "Duplicate or mislabeled raw PUB")
            decoded[index] = analyze_counts(pub["counts"], width, shots)
    require(actual_order == list(range(len(schedule))) and sorted(observations) == actual_order,
            "Measurement schedule incomplete or reordered")
    for index, item in decoded.items():
        row = observations[index]
        require(all(row.get(k) == v for k, v in schedule[index].items()) and row["shots"] == shots,
                "Observation role/basis/shots differs from schedule")
        close(row["expectations"], item["expectations"], f"PUB {index} expectations")
        close(row["wilson_95"], item["wilson_95"], f"PUB {index} Wilson interval")
    values = np.empty((n, width, 3))
    intervals = np.empty((n, width, 3, 2))
    for sample in range(n):
        for axis in range(3):
            values[sample, :, axis] = decoded[3 * sample + axis]["expectations"]
            intervals[sample, :, axis] = decoded[3 * sample + axis]["wilson_95"]
    require(manifest["projected_features"]["axes"] == list(AXES), "Projected feature axes reordered")
    close(manifest["projected_features"]["values"], values, "projected features")
    close(manifest["projected_features"]["wilson_95"], intervals, "projected feature Wilson intervals")
    kernel, distances = projected_kernel(values, plan["gamma"])
    close(manifest["kernel"], kernel, "raw Bloch RBF kernel")
    duplicate = np.stack([decoded[3 * n + axis]["expectations"] for axis in range(3)], axis=1)
    duplicate_distance = float(np.sum((duplicate - values[0]) ** 2) / (2 * width))
    duplicate_kernel = math.exp(-plan["gamma"] * duplicate_distance)
    control = manifest["controls"]["duplicate"]
    require(control["sample_index"] == 0, "Duplicate control sample differs")
    close(control["features"], duplicate, "duplicate Bloch features")
    close(control["squared_distance"], duplicate_distance, "duplicate squared distance")
    close(control["kernel_to_original"], duplicate_kernel, "duplicate kernel")
    readout = manifest["controls"]["readout"]
    errors = [(1 - decoded[3 * n + 3]["expectations"]) / 2,
              (1 + decoded[3 * n + 4]["expectations"]) / 2]
    close(readout["zero_error_rates"], errors[0], "prepared zero errors")
    close(readout["one_error_rates"], errors[1], "prepared one errors")
    close(readout["mean_error"], np.mean(errors), "prepared-state mean error")
    close(readout["zero_error_wilson_95"], wilson(errors[0] * shots, shots), "zero error interval")
    close(readout["one_error_wilson_95"], wilson(errors[1] * shots, shots), "one error interval")
    require(readout["mitigation_applied"] is False, "Expected raw unmitigated estimates")
    per_qubit_errors = [{"logical_qubit": q, "physical_qubit": plan["physical_qubits"][q],
                         "prepared_zero_error": float(errors[0][q]),
                         "prepared_one_error": float(errors[1][q]),
                         "mean_prepared_state_error": float((errors[0][q] + errors[1][q]) / 2)}
                        for q in range(width)]
    readout_audit = {"mean_error": float(np.mean(errors)),
                    "maximum_prepared_zero_error": float(np.max(errors[0])),
                    "maximum_prepared_one_error": float(np.max(errors[1])),
                    "qubits_with_either_error_above_5_percent": int(np.sum(np.maximum(*errors) > .05)),
                    "qubits_with_either_error_above_10_percent": int(np.sum(np.maximum(*errors) > .1)),
                    "qubits_sorted_by_worst_prepared_state_error": sorted(per_qubit_errors,
                        key=lambda row: max(row["prepared_zero_error"], row["prepared_one_error"]), reverse=True),
                    "interpretation": "Average error can conceal severe individual-qubit SPAM failures. 5%/10% are descriptive reporting bins, not acceptance standards. Prepared one combines X preparation and detection errors."}
    close(control["wilson_95"], np.stack([decoded[3 * n + axis]["wilson_95"]
                                        for axis in range(3)], axis=1), "duplicate Wilson intervals")
    ideal, encoded = independent_ideal(manifest["features"], plan)
    ideal_kernel, _ = projected_kernel(ideal, plan["gamma"])
    close(manifest["ideal_reference"]["features"], ideal, "independent ideal Bloch reference")
    close(manifest["ideal_reference"]["kernel"], ideal_kernel, "independent ideal kernel")
    classical = np.exp(-plan["gamma"] * ((encoded[:, None] - encoded[None, :]) ** 2).mean(axis=2) / 2)
    close(manifest["classical_reference"]["kernel"], classical, "classical descriptor reference")
    variances = np.maximum(0, 1 - values ** 2) / shots
    per_sample_variance = variances.sum(axis=(1, 2))
    noise = (per_sample_variance[:, None] + per_sample_variance[None, :]) / (2 * width)
    pairs = [{"pair": [i, j], "kernel": float(kernel[i, j]),
              "squared_feature_distance": float(distances[i, j]),
              "plugin_shot_noise_distance_floor": float(noise[i, j]),
              "distance_to_shot_noise_ratio": float(distances[i, j] / noise[i, j]) if noise[i, j] > 0 else None}
             for i in range(n) for j in range(i + 1, n)]
    mean_floor = float(np.mean([row["plugin_shot_noise_distance_floor"] for row in pairs])) if pairs else None
    mean_distance = float(np.mean([row["squared_feature_distance"] for row in pairs])) if pairs else None
    diagnostic = manifest["kernel_diagnostics"]
    if pairs:
        close(diagnostic["shot_noise_distance_floor"], mean_floor, "mean shot-noise distance floor")
        close(diagnostic["mean_squared_feature_distance"], mean_distance, "mean feature distance")
        if mean_floor > 0:
            close(diagnostic["signal_to_shot_noise"], mean_distance / mean_floor, "distance to shot-noise ratio")
    ideal_mae = float(np.mean(np.abs(kernel - ideal_kernel)))
    close(diagnostic["ideal_kernel_mean_absolute_error"], ideal_mae, "ideal reference kernel MAE")
    centered = np.eye(n) - np.ones((n, n)) / n
    actual_centered, classical_centered = centered @ kernel @ centered, centered @ classical @ centered
    centered_norm_product = float(np.linalg.norm(actual_centered) * np.linalg.norm(classical_centered))
    alignment = float(np.sum(actual_centered * classical_centered) / centered_norm_product) if centered_norm_product > 1e-15 else None
    duplicate_floor = float(np.sum(1 - values[0] ** 2 + 1 - duplicate ** 2) / (shots * 2 * width))
    usefulness = {"pairs": pairs, "mean_pair_distance": mean_distance,
                  "mean_plugin_shot_noise_distance_floor": mean_floor,
                  "duplicate_squared_distance": duplicate_distance,
                  "duplicate_plugin_shot_noise_distance_floor": duplicate_floor,
                  "duplicate_distance_to_shot_noise_ratio": duplicate_distance / duplicate_floor if duplicate_floor > 0 else None,
                  "ideal_reference_kernel_mae": ideal_mae,
                  "centered_kernel_alignment_with_classical_encoded_descriptors": alignment,
                  "alignment_method": "Frobenius inner product of H K H and H Kclassical H, normalized; no label or held-out predictive task.",
                  "interpretation": "Nonzero finite pair differences are not sufficient evidence of useful molecular discrimination. Plug-in shot-noise floors omit hardware bias/drift; duplicate observations are one repeat, not replication evidence. Similarity or disagreement with an ideal/classical kernel establishes no quantum advantage."}
    qpy_audit = audit_bound_qpy(manifest, manifest_path, ideal)
    uncertainty = manifest["kernel_uncertainty"]
    uncertainty_audit = {"status": uncertainty["status"], "producer_interval_reproduced": False}
    if uncertainty["status"] == "available":
        repeats = integer(uncertainty["replicates"], "bootstrap replicate count", 20, 1000)
        require(sum(len(decoded[i]["frequencies"]) for i in range(3 * n)) * width * repeats
                <= 160_000_000, "Bootstrap audit exceeds bounded CPU work")
        rng = np.random.default_rng(integer(uncertainty["seed"], "bootstrap seed", 0, 2**32 - 1))
        draws = np.empty((repeats, n, width, 3))
        for index in range(3 * n):
            item = decoded[index]
            weights = rng.multinomial(shots, item["frequencies"] / shots, size=repeats)
            draws[:, index // 3, :, index % 3] = weights @ item["signs"] / shots
        kernels = np.stack([projected_kernel(draw, plan["gamma"])[0] for draw in draws])
        bounds = np.quantile(kernels, [0.025, 0.975], axis=0)
        close(uncertainty["lower_95"], bounds[0], "canonical-outcome bootstrap lower interval")
        close(uncertainty["upper_95"], bounds[1], "canonical-outcome bootstrap upper interval")
        uncertainty_audit.update(producer_interval_reproduced=True, replicates=repeats,
                                 method="whole-bitstring resampling in canonical outcome order",
                                 interpretation="These are 2.5th/97.5th percentiles of empirically resampled kernel estimates, not a validated true/noiseless-kernel confidence interval. Plug-in estimates can lie outside because resampling adds finite-shot squared-distance bias. No centering or bias correction has been applied.",
                                 off_diagonal_point_estimates_outside_percentile_range=[
                                     {"pair": [i, j], "observed_estimate": float(kernel[i, j]),
                                      "resampled_percentile_2_5": float(bounds[0, i, j]),
                                      "resampled_percentile_97_5": float(bounds[1, i, j])}
                                     for i in range(n) for j in range(i + 1, n)
                                     if kernel[i, j] < bounds[0, i, j] or kernel[i, j] > bounds[1, i, j]])
    require(read_json(manifest_path)[1] == manifest_sha, "Manifest changed during audit")
    return {"schema_version": 1, "status": "passed", "checked_at": datetime.now(UTC).isoformat(),
            "manifest": str(manifest_path), "manifest_sha256": manifest_sha,
            "raw_artifacts": raw_evidence, "n_samples": n, "n_qubits": width, "shots_per_pub": shots,
            "pub_count": len(schedule), "block_count": len(plan["blocks"]),
            "maximum_simulated_reference_block_qubits": max(len(b["logical_qubits"]) for b in plan["blocks"]),
            "physical_qubits_in_clbit_order": plan["physical_qubits"],
            "measurement_basis_review": {"X": "H then Z measurement", "Y": "Sdg then H then Z measurement",
                                         "Z": "Z measurement", "bit_zero": "rightmost string bit",
                                         "scope": "Source and saved bound QPY are reviewed when present; raw counts alone cannot authenticate executed basis gates."},
            "all_raw_counts_and_request_hashes_verified": True,
            "all_feature_values_and_pointwise_wilson_intervals_recomputed": True,
            "kernel_recomputed": kernel.tolist(), "squared_feature_distances": distances.tolist(),
            "raw_bloch_norm_above_one_fraction": float(np.mean(np.linalg.norm(values, axis=2) > 1 + 1e-10)),
            "duplicate_kernel_recomputed": duplicate_kernel,
            "readout_prepared_state_mean_error": float(np.mean(errors)),
            "readout_per_qubit_audit": readout_audit,
            "factorized_ideal_and_classical_references_recomputed": True,
            "kernel_uncertainty_audit": uncertainty_audit,
            "bound_qpy_audit": qpy_audit,
            "measurement_usefulness_diagnostics": usefulness,
            "new_qpu_jobs_submitted": 0, "provider_requests": 0, "inputs_modified": False,
            "limitations": [
                "Independent CPU arithmetic/integrity audit of saved observations, not provider execution attestation or useful-model validation.",
                "Diagonal one is imposed by the RBF definition of a vector compared with itself; the duplicate control is a separate hardware observation.",
                "Pointwise Wilson intervals reflect binomial shot uncertainty per Pauli component, not simultaneous coverage or calibration/drift uncertainty.",
                "The empirical bootstrap preserves within-shot bit correlations but covers neither hardware bias nor unseen outcomes; 64 replicates give coarse tail estimates.",
                "Prepared-state controls combine preparation and readout errors and do not isolate detector calibration; no mitigation or Bloch-ball projection applied.",
                "Independent blocks are classically simulable; extra repeated descriptor qubits add no molecular input information.",
                "These XYZ single-state measurements differ from legacy Z-only pairwise overlap measurements, which cannot be relabeled as projected features.",
                "No affinity, efficacy, safety, biological predictive benefit, or quantum advantage is inferred.",
            ]}



def audit_verification_report(audit_result, report_path):
    report, report_sha, _ = read_json(report_path)
    manifest, current_sha, _ = read_json(audit_result["manifest"])
    require(current_sha == audit_result["manifest_sha256"], "Manifest changed before report comparison")
    require(report.get("status") == "completed" and report.get("hardware_executed") is True,
            "Verification report does not describe completed hardware data")
    require(report["store_job_id"] == Path(audit_result["manifest"]).parent.name,
            "Verification report points to another stored job")
    require(all(report["result"].get(key) == value for key, value in manifest.items()),
            "Verification report results differ from the audited manifest")
    baseline, baseline_sha, _ = read_json(report["baseline"]["manifest_path"])
    require(baseline_sha == report["baseline"]["manifest_sha256"], "Original baseline manifest changed")
    require(baseline["plan"]["feature_sha256"] == manifest["plan"]["feature_sha256"],
            "Baseline and projected measurements do not use the same input feature rows")
    return {"path": str(Path(report_path).resolve()), "sha256": report_sha,
            "all_measurement_result_fields_match": True,
            "original_baseline_manifest_sha256_unchanged": baseline_sha,
            "baseline_feature_rows_sha256_equal": True,
            "comparison_scope": "Same stored input features; kernel method and shot count changed. New nonzero projected values do not retroactively correct or replace old all-zero global-fidelity measurements."}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verification-report", type=Path)
    args = parser.parse_args()
    require(args.output.resolve() != args.manifest.resolve(), "Audit output cannot overwrite its input manifest")
    if args.verification_report:
        require(args.output.resolve() != args.verification_report.resolve(), "Cannot overwrite verification report")
    result = audit_manifest(args.manifest)
    if args.verification_report:
        result["verification_report_audit"] = audit_verification_report(result, args.verification_report)
    input_artifacts = result["raw_artifacts"] + result["bound_qpy_audit"].get("artifacts", [])
    require(args.output.resolve() not in {Path(row["path"]).resolve() for row in input_artifacts},
            "Audit output cannot overwrite raw observations")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pending = args.output.with_suffix(args.output.suffix + ".part")
    pending.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    pending.replace(args.output)
    print(json.dumps({key: result[key] for key in ("status", "n_samples", "n_qubits", "pub_count")}))


if __name__ == "__main__":
    main()
