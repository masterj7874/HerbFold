"""Independent arithmetic and artifact-integrity checks; no network or hardware."""
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pytest

_SPEC = importlib.util.spec_from_file_location('projected_audit', Path(__file__).parents[1] / 'scripts/audit_projected_quantum.py')
audit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit)


def test_integer_bit_order_and_known_pauli_expectations():
    result = audit.analyze_counts({'01': 3, '10': 1}, 2, 4)
    np.testing.assert_array_equal(result['expectations'], [-0.5, 0.5])
    np.testing.assert_array_equal(result['zero_counts'], [1, 3])
    assert result['mean_hamming_weight'] == 1
    assert result['all_zero_counts'] == 0


@pytest.mark.parametrize('counts,width,shots', [
    ({'000': 2}, 2, 2), ({'0x': 2}, 2, 2), ({'00': 1}, 2, 2),
    ({'00': True}, 2, 1), ({'00': 0}, 2, 1), ({}, 2, 1), ({'00': 2.0}, 2, 2),
])
def test_corrupt_raw_measurements_fail(counts, width, shots):
    with pytest.raises(audit.AuditError):
        audit.analyze_counts(counts, width, shots)


def test_wilson_boundary_does_not_claim_exact_zero_true_probability():
    interval = audit.wilson([0], 1024)[0]
    np.testing.assert_allclose(interval, [0, 0.0037374040400176777], atol=1e-15)
    r = audit.analyze_counts({'1': 1024}, 1, 1024)
    np.testing.assert_allclose(r['wilson_95'][0], [-1, -0.9925251919199647], atol=1e-15)


def test_projected_rbf_uses_all_axes_and_two_times_qubit_denominator():
    values = [[[1, 0, 0], [1, 0, 0]], [[0, 0, -1], [0, 0, -1]]]
    kernel, distance = audit.projected_kernel(values, 1)
    np.testing.assert_allclose(distance, [[0, 1], [1, 0]])
    np.testing.assert_allclose(kernel, [[1, math.exp(-1)], [math.exp(-1), 1]])
    # Raw finite-shot vectors can lie outside the Bloch ball; do not normalize.
    raw, _ = audit.projected_kernel([[[1, 1, 1]], [[0, 0, 0]]], 1)
    assert raw[0, 1] == pytest.approx(math.exp(-1.5))


def test_factorized_reference_and_strided_descriptor_encoding():
    plan = {'n_qubits': 2, 'layers': 1, 'blocks': [
        {'logical_qubits': [0], 'edges': []}, {'logical_qubits': [1], 'edges': []}]}
    values, _ = audit.independent_ideal([[0], [1]], plan)
    np.testing.assert_allclose(values, [[[1, 0, 0], [1, 0, 0]], [[0, 0, -1], [0, 0, -1]]], atol=1e-15)
    encoded = audit.encoded_descriptors([[1, 2, 3, 4, 5]], 2)
    np.testing.assert_allclose(encoded, [[np.mean(2*np.arctan([1, 3, 5])), np.mean(2*np.arctan([2, 4]))]])
    # |+> rotated around Z produces positive Y: Sdg followed by H reads +Y as 0.
    angle = math.pi / 3
    state = np.array([np.exp(-.5j*angle), np.exp(.5j*angle)]) / math.sqrt(2)
    sdg = np.diag([1, -1j])
    h = np.array([[1, 1], [1, -1]]) / math.sqrt(2)
    measured = h @ sdg @ state
    assert abs(measured[0])**2 - abs(measured[1])**2 == pytest.approx(math.sin(angle))


def fixture_manifest(tmp_path):
    n, width, shots = 2, 2, 8
    features = [[0.0], [1.0]]
    plan = {'n_samples': n, 'n_features': 1, 'n_qubits': width, 'shots': shots, 'layers': 1,
            'gamma': 1.0, 'block_size': 1, 'physical_qubits': [3, 7], 'backend_faulty_qubits': [4],
            'blocks': [{'logical_qubits': [0], 'edges': []}, {'logical_qubits': [1], 'edges': []}],
            'measurement_schedule': audit.expected_schedule(n),
            'feature_sha256': hashlib.sha256(json.dumps(features, separators=(',', ':')).encode()).hexdigest()}
    plan['topology_sha256'] = audit.digest({k: plan[k] for k in ('physical_qubits', 'blocks')})
    manifest = {'schema_version': 2, 'kernel_method': 'projected', 'status': 'completed', 'hardware_executed': True,
                'features': features, 'plan': plan, 'compiled': {axis: {'measurement_physical_qubits': [3, 7],
                'active_physical_qubits': [3, 7]} for axis in ('X', 'Y', 'Z', 'readout0', 'readout1')}}
    manifest['request_sha256'] = audit.digest({'plan': plan, 'features': features})
    count_rows = [{'00': 8}, {'00': 4, '11': 4}, {'00': 4, '11': 4},
                  {'01': 4, '10': 4}, {'01': 4, '10': 4}, {'11': 8},
                  {'00': 7, '11': 1}, {'00': 4, '11': 4}, {'00': 3, '11': 5},
                  {'00': 6, '11': 2}, {'00': 1, '11': 7}]
    pubs = [{'pub_index': i, 'mapping': row, 'counts': count_rows[i]}
            for i, row in enumerate(plan['measurement_schedule'])]
    raw = {'schema_version': 1, 'job_id': 'fixture-hardware-record', 'request_sha256': manifest['request_sha256'],
           'physical_qubits_in_clbit_order': [3, 7], 'pubs': pubs}
    payload = audit.canonical_bytes(raw)
    (tmp_path/'counts.json').write_bytes(payload)
    decoded = [audit.analyze_counts(counts, width, shots) for counts in count_rows]
    observations = [{'pub_index': i, **row, 'shots': shots,
                     'expectations': decoded[i]['expectations'].tolist(),
                     'wilson_95': decoded[i]['wilson_95'].tolist()}
                    for i, row in enumerate(plan['measurement_schedule'])]
    manifest['jobs'] = [{'job_id': raw['job_id'], 'status': 'DONE', 'pub_indices': list(range(11)),
                         'raw_counts': {'artifact': 'counts.json', 'sha256': hashlib.sha256(payload).hexdigest(),
                                        'size_bytes': len(payload)}, 'observations': observations}]
    values = np.array([[[1., 0., 0.], [1., 0., 0.]], [[0., 0., -1.], [0., 0., -1.]]])
    intervals = np.array([d['wilson_95'] for d in decoded[:6]]).reshape(2, 3, 2, 2).transpose(0, 2, 1, 3)
    manifest['projected_features'] = {'axes': ['X', 'Y', 'Z'], 'values': values.tolist(), 'wilson_95': intervals.tolist()}
    manifest['kernel'] = [[1, math.exp(-1)], [math.exp(-1), 1]]
    manifest['controls'] = {'duplicate': {'sample_index': 0, 'features': [[.75, 0, -.25], [.75, 0, -.25]],
        'squared_distance': .0625, 'kernel_to_original': math.exp(-.0625),
        'wilson_95': np.array([d['wilson_95'] for d in decoded[6:9]]).transpose(1, 0, 2).tolist()},
        'readout': {'zero_error_rates': [.25, .25], 'one_error_rates': [.125, .125], 'mean_error': .1875,
                    'zero_error_wilson_95': audit.wilson([2, 2], shots).tolist(),
                    'one_error_wilson_95': audit.wilson([1, 1], shots).tolist(), 'mitigation_applied': False}}
    manifest['ideal_reference'] = {'features': values.tolist(), 'kernel': manifest['kernel']}
    manifest['classical_reference'] = {'kernel': [[1, math.exp(-math.pi**2/8)], [math.exp(-math.pi**2/8), 1]]}
    manifest['kernel_diagnostics'] = {'shot_noise_distance_floor': .25, 'mean_squared_feature_distance': 1.,
                                     'signal_to_shot_noise': 4., 'ideal_kernel_mean_absolute_error': 0.}
    manifest['kernel_uncertainty'] = {'status': 'omitted_cpu_budget'}
    path = tmp_path/'quantum.json'
    path.write_bytes(audit.canonical_bytes(manifest))
    return path, manifest


def test_complete_saved_counts_recompute_without_relabeling_duplicate(tmp_path):
    path, _ = fixture_manifest(tmp_path)
    before = path.read_bytes()
    result = audit.audit_manifest(path)
    assert result['status'] == 'passed'
    assert result['duplicate_kernel_recomputed'] == pytest.approx(math.exp(-.0625))
    assert result['readout_prepared_state_mean_error'] == .1875
    assert result['measurement_usefulness_diagnostics']['pairs'][0]['distance_to_shot_noise_ratio'] == 4
    assert result['new_qpu_jobs_submitted'] == result['provider_requests'] == 0
    assert path.read_bytes() == before


@pytest.mark.parametrize('field', ['hash', 'order', 'basis', 'layout', 'kernel', 'features', 'block', 'duplicate', 'readout'])
def test_changed_artifact_or_scientific_contract_is_rejected(tmp_path, field):
    path, manifest = fixture_manifest(tmp_path)
    m = copy.deepcopy(manifest)
    if field == 'hash':
        (tmp_path/'counts.json').write_text((tmp_path/'counts.json').read_text() + ' ')
    elif field == 'order':
        m['jobs'][0]['pub_indices'][0:2] = [1, 0]
    elif field == 'basis':
        m['jobs'][0]['observations'][1]['basis'] = 'X'
    elif field == 'layout':
        m['compiled']['Y']['measurement_physical_qubits'] = [7, 3]
    elif field == 'kernel':
        m['kernel'][0][1] = .9
    elif field == 'features':
        m['projected_features']['values'][0][0][1] = .5
    elif field == 'block':
        m['plan']['blocks'][0]['logical_qubits'] = [0, 1]
    elif field == 'duplicate':
        m['controls']['duplicate']['kernel_to_original'] = 1.
    elif field == 'readout':
        m['controls']['readout']['mitigation_applied'] = True
    path.write_bytes(audit.canonical_bytes(m))
    with pytest.raises(audit.AuditError):
        audit.audit_manifest(path)


@pytest.mark.parametrize('wrong_y_sign', [False, True])
def test_saved_native_qpy_checks_y_basis_and_nontrivial_physical_mapping(tmp_path, wrong_y_sign):
    import io

    from qiskit import QuantumCircuit, qpy

    schedule = audit.expected_schedule(1)
    physical = [2]
    circuits = []
    for row in schedule:
        qc = QuantumCircuit(3, 1)
        if row['kind'] == 'readout':
            if row['prepared_bit']:
                qc.x(2)
        else:
            qc.h(2)
            qc.s(2)  # +Y state, so a reversed Y basis has an observable sign error.
            if row['basis'] == 'X':
                qc.h(2)
            elif row['basis'] == 'Y':
                qc.s(2) if wrong_y_sign else qc.sdg(2)
                qc.h(2)
        qc.measure(2, 0)
        circuits.append(qc)
    output = io.BytesIO()
    qpy.dump(circuits, output)
    raw = output.getvalue()
    (tmp_path/'bound.qpy').write_bytes(raw)
    ref = {'artifact': 'bound.qpy', 'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}
    batch = {'job_id': 'fixture-job', 'artifact': ref, 'pub_indices': list(range(len(schedule))),
             'measurement_mapping': schedule, 'physical_qubits_in_clbit_order': physical}
    manifest = {'plan': {'physical_qubits': physical, 'measurement_schedule': schedule,
                        'blocks': [{'logical_qubits': [0], 'edges': []}]},
                'circuit_batches': [batch],
                'jobs': [{'job_id': 'fixture-job', 'pub_indices': batch['pub_indices'], 'circuit_artifact': ref}]}
    if wrong_y_sign:
        with pytest.raises(audit.AuditError, match='basis/feature semantics'):
            audit.audit_bound_qpy(manifest, tmp_path/'quantum.json', np.array([[[0., 1., 0.]]]))
    else:
        result = audit.audit_bound_qpy(manifest, tmp_path/'quantum.json', np.array([[[0., 1., 0.]]]))
        assert result['basis_gate_semantics_verified']
