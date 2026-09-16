from types import SimpleNamespace

import numpy as np
import pytest

from herbfold import quantum


def test_exact_kernel_is_a_fidelity_gram_matrix():
    values = [[0.1, 0.2, 0.3], [0.8, -0.4, 0.1], [0.1, 0.2, 0.3]]
    output = quantum.local_kernel(values, n_qubits=3)
    kernel = np.asarray(output["kernel"])
    assert kernel.shape == (3, 3)
    np.testing.assert_allclose(kernel, kernel.T, atol=1e-12)
    np.testing.assert_allclose(np.diag(kernel), 1, atol=1e-12)
    np.testing.assert_allclose(kernel[0], kernel[2], atol=1e-12)
    assert 0 < kernel[0, 1] < 0.99
    assert np.linalg.eigvalsh(kernel).min() >= -1e-12
    assert output["metadata"]["hardware_executed"] is False


@pytest.mark.parametrize("features", [[], [[]], [[1], [1, 2]], [[float("nan")]], [[float("inf")]], [1, 2]])
def test_invalid_features_are_rejected(features):
    with pytest.raises(quantum.QuantumConfigurationError):
        quantum.plan_quantum(features)


def test_local_hardware_sized_simulation_is_rejected():
    with pytest.raises(quantum.QuantumConfigurationError, match="16"):
        quantum.local_kernel([[1.0], [2.0]], n_qubits=127)


def test_encoding_uses_all_columns_and_repeats_for_every_qubit():
    first = quantum.encode_features([[1, 2, 3, 4, 5]], 2)
    second = quantum.encode_features([[1, 2, 3, 4, 6]], 2)
    assert first[0, 0] != second[0, 0]
    wide = quantum.encode_features([[1, 2]], 5)
    np.testing.assert_allclose(wide[0], 2 * np.arctan([1, 2, 1, 2, 1]))


@pytest.mark.parametrize(
    "budget",
    [
        {"max_circuits": 2},
        {"max_jobs": 1, "circuits_per_job": 2},
        {"max_total_shots": 2048},
        {"shots": 0},
        {"layers": True},
    ],
)
def test_budget_enforced_before_network_access(budget, monkeypatch):
    monkeypatch.setattr(quantum, "inspect_backends", lambda *a: pytest.fail("should validate before network"))
    with pytest.raises(quantum.QuantumConfigurationError):
        quantum.plan_quantum([[1], [2]], mode="ibm", **budget)


def test_submission_budget_and_flag_validated_before_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(quantum, "_service", lambda *a: pytest.fail("should validate before credentials"))
    with pytest.raises(quantum.QuantumConfigurationError, match="max_circuits"):
        quantum.submit_kernel([[1], [2]], tmp_path / "invalid.json", execute=True, max_circuits=1)
    with pytest.raises(quantum.QuantumConfigurationError, match="boolean"):
        quantum.submit_kernel([[1]], tmp_path / "invalid.json", execute="false")


class Backend:
    def __init__(self, name, qubits, *, operational=True, simulator=False, faulty=(), pending=0):
        self.name, self.num_qubits = name, qubits
        self._status = SimpleNamespace(operational=operational, pending_jobs=pending, status_msg="active")
        self._simulator, self._faulty = simulator, faulty

    def status(self):
        return self._status

    def configuration(self):
        return SimpleNamespace(simulator=self._simulator)

    def properties(self):
        return SimpleNamespace(faulty_qubits=lambda: self._faulty, faulty_gates=lambda: [])


class Service:
    def __init__(self, backends):
        self._backends = backends

    def backends(self, **kwargs):
        assert kwargs == {"operational": True, "simulator": False}
        return self._backends

    def backend(self, name):
        return next(b for b in self._backends if b.name == name)


def test_max_backend_uses_accessible_operational_healthy_qubits():
    service = Service(
        [
            Backend("huge-offline", 1000, operational=False),
            Backend("simulator", 5000, simulator=True),
            Backend("small", 5),
            Backend("large", 20, faulty=(3,), pending=3),
            Backend("same-busy", 19, pending=4),
        ]
    )
    result = quantum.inspect_backends(service)
    assert result["selected_max_backend"] == "large"
    plan = quantum.plan_quantum([[1], [2]], mode="ibm", service=service)
    assert plan["n_qubits"] == 19
    assert 3 not in plan["physical_qubits"]
    assert plan["total_shots"] == 3 * 1024
    assert plan["max_total_qpu_seconds"] == 120
    assert plan["selection_strategy"] == "max_accessible_operational"


def test_missing_credentials_is_explicit_and_never_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(quantum, "credential_status", lambda: {"configured": False})
    result = quantum.submit_kernel([[1], [2]], tmp_path / "job.json")
    assert result["status"] == "not_submitted"
    assert result["plan"]["status"] == "credentials_required"
    assert not (tmp_path / "job.json").exists()
    with pytest.raises(quantum.QuantumConfigurationError, match="credentials"):
        quantum.submit_kernel([[1]], tmp_path / "job.json", execute=True)


def test_diagnostics_do_not_expose_provider_errors():
    class BadService:
        def backends(self, **kwargs):
            raise RuntimeError("token=private-secret")

    result = quantum.inspect_backends(BadService())
    assert result["status"] == "connection_failed"
    assert "private-secret" not in str(result)
    with pytest.raises(quantum.QuantumExecutionError) as exc:
        quantum._provider_call(BadService().backends, "lookup")
    assert "private-secret" not in str(exc.value)


def test_hardware_template_really_uses_max_width_without_simulating():
    from qiskit_ibm_runtime.fake_provider import FakeTorino

    backend = FakeTorino()
    plan = {"n_qubits": backend.num_qubits, "physical_qubits": list(range(backend.num_qubits)), "layers": 2}
    circuit, x, y, metadata = quantum._compile_template(backend, plan)
    assert len(metadata["active_physical_qubits"]) == backend.num_qubits
    assert metadata["logical_qubits"] == 133
    assert circuit.num_clbits == 133
    assert len(x) == len(y) == 133
    assert len(circuit.parameters) == 266


class CompletedJob:
    def __init__(self, index, result):
        self.index, self._result = index, result
        self.current_status = "DONE"

    def job_id(self):
        return f"test-job-{self.index}"

    def status(self):
        return self.current_status

    def result(self, timeout=None):
        assert self.current_status == "DONE", "retrieve must never wait for queued jobs"
        return self._result

    def metrics(self):
        return {"usage": {"quantum_seconds": 0.25}}


def runtime_fixture(monkeypatch, *, fail_second=False):
    import qiskit_ibm_runtime
    from qiskit.primitives import StatevectorSampler
    from qiskit.providers.fake_provider import GenericBackendV2

    backend = GenericBackendV2(3, seed=42)
    backend.status = lambda: SimpleNamespace(operational=True, pending_jobs=0, status_msg="active")
    service = Service([backend])
    jobs = {}
    service.job = lambda job_id: jobs[job_id]
    statevector = StatevectorSampler(seed=52)

    class LocalSampler:
        def __init__(self, *, mode, options):
            assert mode is backend
            assert options["max_execution_time"] == 120

        def run(self, circuits, *, shots):
            if fail_second and jobs:
                raise RuntimeError("private token must not appear in errors")
            result = statevector.run(circuits, shots=shots).result()
            job = CompletedJob(len(jobs), result)
            jobs[job.job_id()] = job
            return job

    monkeypatch.setattr(qiskit_ibm_runtime, "SamplerV2", LocalSampler)
    return service, jobs


def test_actual_qiskit_sampler_results_roundtrip_in_deterministic_batches(monkeypatch, tmp_path):
    service, jobs = runtime_fixture(monkeypatch)
    path = tmp_path / "jobs.json"
    manifest = quantum.submit_kernel(
        [[0.1, 0.2], [0.3, 0.4], [-0.3, 0.7]],
        path,
        execute=True,
        service=service,
        circuits_per_job=2,
        shots=2048,
    )
    assert manifest["status"] == "submitted"
    assert len(manifest["jobs"]) == 3
    assert manifest["plan"]["n_qubits"] == 3
    assert (path.stat().st_mode & 0o777) == 0o600
    jobs["test-job-1"].current_status = "QUEUED"
    queued = quantum.retrieve_kernel(path, service)
    assert queued["status"] == "running" and "kernel" not in queued
    jobs["test-job-1"].current_status = "DONE"
    result = quantum.retrieve_kernel(path, service)
    from qiskit.quantum_info import Statevector

    encoded = quantum.encode_features([[0.1, 0.2], [0.3, 0.4], [-0.3, 0.7]], 3)
    edges = quantum._hardware_edges(service._backends[0], [0, 1, 2])
    states = np.asarray(
        [Statevector.from_instruction(quantum.build_feature_map(row, edges=edges)).data for row in encoded]
    )
    expected = np.abs(states.conj() @ states.T) ** 2
    kernel = np.asarray(result["kernel"])
    assert result["status"] == "completed"
    np.testing.assert_allclose(np.diag(kernel), 1, atol=0.01)
    np.testing.assert_allclose(kernel, kernel.T)
    np.testing.assert_allclose(kernel, expected, atol=0.04)
    assert np.min(kernel) >= 0 and np.max(kernel) <= 1
    assert result["jobs"][0]["pairs"] == [[0, 0], [0, 1]]
    assert result["jobs"][2]["pairs"] == [[1, 2], [2, 2]]
    assert result["kernel_diagnostics"]["psd_projection_applied"] is False
    assert quantum.retrieve_kernel(path, None) == result  # complete cache requires no credentials
    with pytest.raises(quantum.QuantumConfigurationError, match="already exists"):
        quantum.submit_kernel([[1]], path, execute=True, service=service)


def test_partial_submission_preserves_job_ids_and_does_not_retry(monkeypatch, tmp_path):
    import json

    service, jobs = runtime_fixture(monkeypatch, fail_second=True)
    path = tmp_path / "partial.json"
    with pytest.raises(quantum.QuantumExecutionError) as exc:
        quantum.submit_kernel([[1], [2]], path, execute=True, service=service, circuits_per_job=2)
    manifest = json.loads(path.read_text())
    assert manifest["status"] == "partial_submission"
    assert len(jobs) == 1
    assert manifest["jobs"][0]["job_id"] == "test-job-0"
    assert "private token" not in str(exc.value) + path.read_text()
    result = quantum.retrieve_kernel(path, service)
    assert result["status"] == "partial_submission" and "kernel" not in result
