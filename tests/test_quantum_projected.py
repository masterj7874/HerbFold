import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest

from herbfold import quantum as qk
from herbfold import quantum_projected as pq


def fixture(monkeypatch, *, fail_second=False, width=3, shots_bias=False):
    import qiskit_ibm_runtime
    from qiskit.primitives import StatevectorSampler
    from qiskit.providers.fake_provider import GenericBackendV2

    backend = GenericBackendV2(width, seed=23)
    backend.status = lambda: SimpleNamespace(operational=True, pending_jobs=0, status_msg="active")
    jobs = {}
    service = SimpleNamespace(
        backends=lambda **kwargs: [backend], backend=lambda name: backend, job=lambda job_id: jobs[job_id]
    )
    sampler = StatevectorSampler(seed=29)

    class Job:
        current_status = "DONE"

        def __init__(self, index, results):
            self.index, self.results = index, results

        def job_id(self):
            return f"fixture-{self.index}"

        def status(self):
            return self.current_status

        def result(self, timeout):
            assert timeout == 1
            assert self.current_status == "DONE"
            return self.results

        def metrics(self):
            return {"usage": {"quantum_seconds": 0.1}}

    class Sampler:
        def __init__(self, *, mode, options):
            assert mode is backend
            assert options["environment"]["job_tags"] == ["herbfold", "projected-kernel"]

        def run(self, circuits, *, shots):
            if fail_second and jobs:
                raise RuntimeError("secret-token-do-not-print")
            result = sampler.run(circuits, shots=shots).result()
            if shots_bias:
                original = result[0].data.meas.get_counts()
                key = next(iter(original))
                original[key] += 1
                result[0].data.meas.get_counts = lambda: original
            job = Job(len(jobs), result)
            jobs[job.job_id()] = job
            return job

    monkeypatch.setattr(qiskit_ibm_runtime, "SamplerV2", Sampler)
    return service, jobs


def test_factorized_reference_matches_full_exact_projected_observables():
    from qiskit.quantum_info import Pauli, Statevector

    rows = [[0.1, 0.8, -0.3], [0.6, -0.2, 0.7], [0.1, 0.8, -0.3]]
    result = qk.local_kernel(
        rows, n_qubits=7, layers=2, max_circuits=14, kernel_method="projected", block_size=3
    )
    plan = result["plan"]
    exact = np.empty((3, 7, 3))
    for i, angles in enumerate(qk.encode_features(rows, 7)):
        state = Statevector.from_instruction(pq._feature_circuit(angles, plan))
        for j in range(7):
            for k, axis in enumerate(pq.AXES):
                word = ["I"] * 7
                word[-j - 1] = axis
                exact[i, j, k] = state.expectation_value(Pauli("".join(word))).real
    np.testing.assert_allclose(result["projected_features"]["values"], exact, atol=1e-12)
    expected = np.exp(-((exact[:, None] - exact[None, :]) ** 2).sum(axis=(2, 3)) / 14)
    np.testing.assert_allclose(result["kernel"], expected, atol=1e-12)
    assert np.linalg.eigvalsh(expected).min() >= -1e-12
    assert result["kernel"][0] == result["kernel"][2]
    assert result["metadata"]["hardware_executed"] is False
    assert result["plan"]["feature_sha256"] == qk._fingerprint(np.asarray(rows))
    assert result["controls"]["status"] == "not_measured_local_exact"


def test_max_width_reference_never_allocates_full_width_statevector(monkeypatch):
    from qiskit.quantum_info import Statevector

    original = Statevector.from_instruction
    widths = []

    def checked(circuit):
        widths.append(circuit.num_qubits)
        assert circuit.num_qubits <= 6
        return original(circuit)

    monkeypatch.setattr(Statevector, "from_instruction", checked)
    result = qk.local_kernel(
        [[0.1, 0.4], [0.2, 0.8]], n_qubits=156, layers=1, kernel_method="projected", block_size=6
    )
    assert result["plan"]["n_qubits"] == 156
    assert len(widths) == 52
    assert result["kernel"][0][1] < 0.999


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_circuits": 10},
        {"max_total_shots": 10240},
        {"max_jobs": 1, "circuits_per_job": 10},
        {"gamma": 0},
        {"gamma": float("nan")},
        {"gamma": True},
        {"block_size": 7},
        {"layers": False},
    ],
)
def test_projected_budgets_fail_before_provider_access(monkeypatch, kwargs):
    monkeypatch.setattr(qk, "inspect_backends", lambda *args: pytest.fail("network must not run"))
    with pytest.raises(qk.QuantumConfigurationError):
        qk.plan_quantum([[1], [2]], mode="ibm", kernel_method="projected", **kwargs)


def test_three_per_sample_and_five_controls_not_legacy_quadratic_budget():
    result = qk.plan_quantum([[i] for i in range(24)], kernel_method="projected", max_circuits=77)
    assert result["n_samples"] == 24
    assert result["circuit_count"] == 77
    assert result["measurement_schedule"][-5:] == [
        {"kind": "duplicate", "sample_index": 0, "basis": "X"},
        {"kind": "duplicate", "sample_index": 0, "basis": "Y"},
        {"kind": "duplicate", "sample_index": 0, "basis": "Z"},
        {"kind": "readout", "prepared_bit": 0, "basis": "Z"},
        {"kind": "readout", "prepared_bit": 1, "basis": "Z"},
    ]


def test_hardware_blocks_avoid_faulty_qubits_and_edges(monkeypatch):
    from qiskit.transpiler import CouplingMap

    backend = SimpleNamespace(
        name="hardware",
        num_qubits=8,
        status=lambda: SimpleNamespace(operational=True, pending_jobs=0, status_msg="active"),
        configuration=lambda: SimpleNamespace(simulator=False),
        properties=lambda: SimpleNamespace(
            faulty_qubits=lambda: [4], faulty_gates=lambda: [SimpleNamespace(qubits=[1, 2])]
        ),
        coupling_map=CouplingMap([[i, i + 1] for i in range(7)]),
    )
    svc = SimpleNamespace(backends=lambda **kwargs: [backend], backend=lambda name: backend)
    result = qk.plan_quantum(
        [[1], [2]], mode="ibm", qubits="max", service=svc, kernel_method="projected", block_size=3
    )
    assert result["n_qubits"] == 7
    assert 4 not in result["physical_qubits"]
    physical_edges = [
        (result["physical_qubits"][a], result["physical_qubits"][b])
        for block in result["blocks"]
        for a, b in block["edges"]
    ]
    assert (1, 2) not in physical_edges
    assert sorted(i for block in result["blocks"] for i in block["logical_qubits"]) == list(range(7))
    assert all(len(block["logical_qubits"]) <= 3 for block in result["blocks"])


def test_compile_real_large_fake_backend_all_selected_and_bounded_blocks():
    from qiskit_ibm_runtime.fake_provider import FakeTorino

    backend = FakeTorino()
    service = SimpleNamespace(backends=lambda **kwargs: [backend], backend=lambda name: backend)
    plan = qk.plan_quantum(
        [[0.2], [0.8]],
        mode="ibm",
        qubits="max",
        service=service,
        kernel_method="projected",
        block_size=4,
        layers=1,
    )
    templates, parameters, metadata = pq._compile(backend, plan)
    assert len(parameters) == plan["n_qubits"]
    for axis in pq.AXES:
        assert set(metadata[axis]["active_physical_qubits"]) == set(plan["physical_qubits"])
        assert templates[axis].num_clbits == plan["n_qubits"]
        assert metadata[axis]["measurement_physical_qubits"] == plan["physical_qubits"]
    assert max(len(block["logical_qubits"]) for block in plan["blocks"]) <= 4


def test_decode_bit_order_and_wilson_not_global_all_zero():
    means, interval, _, _ = pq._decode({"001": 3, "010": 1}, 3, 4)
    np.testing.assert_allclose(means, [-0.5, 0.5, 1])
    assert interval.shape == (3, 2)
    assert interval[2, 0] < 1 and interval[2, 1] == pytest.approx(1)
    # No global zero observed, but local marginals retain information.
    assert all(np.isfinite(means))


@pytest.mark.parametrize("counts", [{}, {"0": 4}, {"abc": 4}, {"000": -1}, {"000": 3}, {"000": True}])
def test_invalid_counts_do_not_create_measured_kernel(counts):
    with pytest.raises(qk.QuantumConfigurationError):
        pq._decode(counts, 3, 4)


def test_qiskit_measurements_roundtrip_controls_bootstrap_and_raw_artifact(monkeypatch, tmp_path):
    svc, jobs = fixture(monkeypatch)
    rows = [[0.1, 0.4, -0.2], [0.8, -0.6, 0.7]]
    path = tmp_path / "quantum.json"
    submitted = qk.submit_kernel(
        rows,
        path,
        execute=True,
        service=svc,
        kernel_method="projected",
        layers=1,
        shots=4096,
        max_total_shots=50000,
        circuits_per_job=4,
    )
    assert submitted["status"] == "submitted"
    assert submitted["hardware_executed"] is None
    assert len(jobs) == 3
    jobs["fixture-1"].current_status = "QUEUED"
    pending = qk.retrieve_kernel(path, svc)
    assert pending["status"] == "running" and "kernel" not in pending
    jobs["fixture-1"].current_status = "DONE"
    result = qk.retrieve_kernel(path, svc)
    assert result["status"] == "completed" and result["hardware_executed"] is True
    measured = np.asarray(result["projected_features"]["values"])
    ideal = np.asarray(result["ideal_reference"]["features"])
    np.testing.assert_allclose(measured, ideal, atol=0.05)
    expected = np.exp(-((measured[:, None] - measured[None, :]) ** 2).sum(axis=(2, 3)) / 6)
    np.testing.assert_allclose(result["kernel"], expected, atol=1e-12)
    assert result["controls"]["duplicate"]["kernel_to_original"] > 0.99
    assert result["controls"]["readout"]["mean_error"] == 0
    assert result["kernel_uncertainty"]["status"] == "available"
    assert result["kernel_uncertainty"]["lower_95"][0][0] == 1
    assert result["kernel_uncertainty"]["upper_95"][0][0] == 1
    assert result["kernel_diagnostics"]["psd_projection_applied"] is False
    for record in result["jobs"]:
        reference = record["raw_counts"]
        raw = (tmp_path / reference["artifact"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == reference["sha256"]
        data = json.loads(raw)
        assert data["physical_qubits_in_clbit_order"] == result["plan"]["physical_qubits"]
        for pub in data["pubs"]:
            assert pub["mapping"] == result["plan"]["measurement_schedule"][pub["pub_index"]]
            assert sum(pub["counts"].values()) == 4096
    assert qk.retrieve_kernel(path, None) == result
    with pytest.raises(qk.QuantumConfigurationError, match="already exists"):
        qk.submit_kernel(rows, path, execute=True, service=svc, kernel_method="projected")


def test_partial_submission_keeps_ids_and_never_fills_missing_observables(monkeypatch, tmp_path):
    svc, jobs = fixture(monkeypatch, fail_second=True)
    path = tmp_path / "partial.json"
    with pytest.raises(qk.QuantumExecutionError) as exc:
        qk.submit_kernel(
            [[1], [2]], path, execute=True, service=svc, kernel_method="projected", circuits_per_job=4
        )
    assert len(jobs) == 1
    assert "secret-token" not in str(exc.value) + path.read_text()
    result = qk.retrieve_kernel(path, svc)
    assert result["status"] == "partial_submission"
    assert "kernel" not in result
    assert result["jobs"][0]["raw_counts"]["artifact"]


def test_manifest_input_tampering_rejected_without_provider(monkeypatch, tmp_path):
    svc, _ = fixture(monkeypatch)
    path = tmp_path / "job.json"
    manifest = qk.submit_kernel([[0.1], [0.2]], path, execute=True, service=svc, kernel_method="projected")
    manifest["features"][0][0] = 123
    monkeypatch.setattr(qk, "_service", lambda *a: pytest.fail("network must not run"))
    with pytest.raises(qk.QuantumConfigurationError, match="digest"):
        qk.retrieve_kernel(manifest, svc)


def test_projected_bootstrap_omission_is_explicit_and_bounded(monkeypatch):
    decoded = [pq._decode({"000": 2, "111": 2}, 3, 4)] * 11
    monkeypatch.setattr(pq, "MAX_BOOTSTRAP_WORK", 1)
    result = pq._bootstrap(decoded, {"n_qubits": 3, "n_samples": 2, "shots": 4, "gamma": 1})
    assert result["status"] == "omitted_cpu_budget"
    assert "lower_95" not in result


def test_legacy_default_and_unknown_method_compatibility():
    old = qk.local_kernel([[0.1], [0.4]], n_qubits=2)
    assert old["metadata"]["estimator"] == "exact_statevector_fidelity"
    assert old["metadata"]["schema_version"] == 1
    with pytest.raises(qk.QuantumConfigurationError, match="kernel_method"):
        qk.local_kernel([[1]], kernel_method="invented")


def test_bound_qpy_archived_before_submission_and_matches_measurement_mapping(monkeypatch, tmp_path):
    from qiskit import qpy

    svc, _ = fixture(monkeypatch)
    path = tmp_path / "job.json"
    result = qk.submit_kernel(
        [[0.1, 0.2], [0.4, 0.8]],
        path,
        execute=True,
        service=svc,
        kernel_method="projected",
        layers=1,
        circuits_per_job=6,
    )
    assert len(result["circuit_batches"]) == 2
    all_indices = []
    for batch in result["circuit_batches"]:
        reference = batch["artifact"]
        raw = (tmp_path / reference["artifact"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == reference["sha256"]
        with (tmp_path / reference["artifact"]).open("rb") as handle:
            circuits = qpy.load(handle)
        assert len(circuits) == len(batch["pub_indices"])
        assert batch["status"] == "submitted"
        for index, mapping, circuit in zip(batch["pub_indices"], batch["measurement_mapping"], circuits):
            assert mapping == result["plan"]["measurement_schedule"][index]
            assert len(circuit.parameters) == 0
            measured = {
                circuit.find_bit(op.clbits[0]).index: circuit.find_bit(op.qubits[0]).index
                for op in circuit.data
                if op.operation.name == "measure"
            }
            assert measured == dict(enumerate(result["plan"]["physical_qubits"]))
        all_indices.extend(batch["pub_indices"])
    assert all_indices == list(range(11))


@pytest.mark.parametrize("damage", ["raw_modify", "raw_missing", "qpy_modify"])
def test_completed_cache_checks_raw_and_actual_circuit_integrity(monkeypatch, tmp_path, damage):
    svc, _ = fixture(monkeypatch)
    path = tmp_path / "job.json"
    qk.submit_kernel([[0.1], [0.5]], path, execute=True, service=svc, kernel_method="projected")
    result = qk.retrieve_kernel(path, svc)
    reference = (
        result["jobs"][0]["raw_counts"]
        if damage.startswith("raw")
        else result["circuit_batches"][0]["artifact"]
    )
    artifact = tmp_path / reference["artifact"]
    if damage == "raw_missing":
        artifact.unlink()
    else:
        raw = bytearray(artifact.read_bytes())
        raw[-1] ^= 1  # Same-size corruption must be detected by SHA, not just stat.
        artifact.write_bytes(raw)
    monkeypatch.setattr(qk, "_service", lambda *a: pytest.fail("cache integrity must not contact provider"))
    with pytest.raises(qk.QuantumConfigurationError, match="integrity|missing"):
        qk.retrieve_kernel(path)


def test_bootstrap_is_reproducible_from_canonical_sidecar_outcome_order():
    counts = {"111": 5, "000": 3, "001": 7, "010": 1}
    first = pq._decode(counts, 3, 16)
    restored = pq._decode(json.loads(pq._json(counts)), 3, 16)
    for a, b in zip(first, restored):
        np.testing.assert_array_equal(a, b)
    plan = {"n_qubits": 3, "n_samples": 2, "shots": 16, "gamma": 1}
    assert pq._bootstrap([first] * 11, plan) == pq._bootstrap([restored] * 11, plan)


def test_qpu_usage_falls_back_to_actual_numeric_usage_without_invented_values():
    assert pq._usage(SimpleNamespace(metrics=lambda: {"usage": {}}, usage=lambda: 3.125)) == 3.125
    assert pq._usage(SimpleNamespace(metrics=lambda: {"usage": {}}, usage=lambda: float("nan"))) is None
    assert pq._usage(SimpleNamespace(metrics=lambda: {"usage": {}}, usage=lambda: "3.125")) is None
