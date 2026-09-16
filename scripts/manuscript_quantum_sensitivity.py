"""CPU-only sensitivity of one archived four-input IBM projected-kernel job.

No provider client, new shots, synthetic observations, GPU or model fitting.
Rebuilds raw Bloch features and circuit-matched ideal features with the independent
audit implementation, verifies archived hashes, and saves source-linked tables.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from audit_projected_quantum import (
    analyze_counts,
    close,
    independent_ideal,
    projected_kernel,
    read_json,
    require,
    wilson,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "runtime/f1b0f1c6867b4d21b40193e65ab980bf/quantum.json"
NAME = "figure-S4-quantum-sensitivity"
COLORS = {"measured": "#158A88", "ideal": "#C58B24", "classical": "#A54678"}
LABELS = {"measured": "IBM observations", "ideal": "Circuit-matched ideal", "classical": "Encoded classical RBF"}
GAMMAS = np.logspace(-2, 2, 81)
THRESHOLDS = [None, .2, .1, .05, .02, .01]
PSD_TOL = 1e-12


def relative(path):
    path = Path(path).resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def fingerprint(path):
    path = Path(path)
    raw = path.read_bytes()
    return {"path": relative(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def write_csv(path, rows):
    require(bool(rows), f"Empty CSV: {path}")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def centered(kernel):
    kernel = np.asarray(kernel, dtype=float)
    h = np.eye(len(kernel)) - np.ones_like(kernel) / len(kernel)
    return h @ kernel @ h


def alignment(left, right):
    left, right = centered(left), centered(right)
    denominator = np.linalg.norm(left) * np.linalg.norm(right)
    if denominator <= PSD_TOL ** 2:
        return None
    return float(np.sum(left * right) / denominator)


def spectrum(kernel):
    kernel = (kernel + kernel.T) / 2
    values = np.linalg.eigvalsh(kernel)
    tolerance = PSD_TOL * max(1., float(np.linalg.norm(kernel, ord=2)))
    require(float(values.min()) >= -tolerance, "Material negative eigenvalue: PSD failure")
    positive = values[values > tolerance]
    if not len(positive):
        return {"effective_rank": None, "numerical_rank": 0,
                "positive_spectrum_condition_number": None,
                "eigenvalues": values.tolist(), "psd_tolerance": tolerance}
    weights = positive / positive.sum()
    return {"effective_rank": float(np.exp(-np.sum(weights * np.log(weights)))),
            "numerical_rank": int(len(positive)),
            "positive_spectrum_condition_number": float(positive.max() / positive.min()),
            "eigenvalues": values.tolist(), "psd_tolerance": tolerance}


def kernel_metrics(kernel):
    require(np.allclose(kernel, kernel.T, atol=1e-14), "Asymmetric kernel")
    require(np.array_equal(np.diag(kernel), np.ones(len(kernel))), "Diagonal must equal definition value 1")
    raw, center = spectrum(kernel), spectrum(centered(kernel))
    off = kernel[np.triu_indices(len(kernel), 1)]
    require(center["numerical_rank"] <= len(kernel) - 1, "Centered rank exceeds n-1")
    return {"off_diagonal_min": float(off.min()), "off_diagonal_mean": float(off.mean()),
            "off_diagonal_max": float(off.max()), "off_diagonal_std_ddof0": float(off.std()),
            "off_diagonal_spread": float(np.ptp(off)),
            "centered_effective_rank": center["effective_rank"],
            "uncentered_effective_rank": raw["effective_rank"],
            "centered_numerical_rank": center["numerical_rank"],
            "uncentered_numerical_rank": raw["numerical_rank"],
            "centered_positive_spectrum_condition_number": center["positive_spectrum_condition_number"],
            "uncentered_condition_number": raw["positive_spectrum_condition_number"],
            "centered_eigenvalues": json.dumps(center["eigenvalues"]),
            "uncentered_eigenvalues": json.dumps(raw["eigenvalues"])}


def invariants():
    """Mathematical checks, not simulated molecular or measurement observations."""
    identity, constant = np.eye(4), np.ones((4, 4))
    close(spectrum(identity)["effective_rank"], 4., "identity uncentered rank")
    close(spectrum(centered(identity))["effective_rank"], 3., "centered identity rank")
    require(spectrum(centered(constant))["effective_rank"] is None, "Zero spectrum must be undefined")
    require(alignment(constant, identity) is None, "Zero centered norm must be undefined")
    close(alignment(identity, 3 * identity + 8 * constant), 1., "CKA positive scale/constant invariance")
    rejected = False
    try:
        spectrum(np.diag([1., 1., 1., -.001]))
    except ValueError:
        rejected = True
    require(rejected, "Material indefinite matrix was not rejected")
    return {"identity_rank_four": True, "centered_identity_rank_three": True,
            "zero_centered_spectrum_and_alignment_return_null": True,
            "positive_scale_and_constant_shift_cka_invariance": True,
            "material_negative_eigenvalues_rejected": True}


def load_observations(manifest_path, output, audit_python):
    audit_path = output / "independent_raw_reaudit.json"
    # The QPY reader must support the archived Qiskit format. Keep its environment
    # explicit; the plotting interpreter may have a different Qiskit installation.
    subprocess.run([str(audit_python), str(ROOT / "scripts/audit_projected_quantum.py"),
                    "--manifest", str(manifest_path), "--output", str(audit_path)],
                   check=True, capture_output=True, text=True, timeout=60)
    audit, _, _ = read_json(audit_path)
    require(audit["status"] == "passed", "Independent archived-data audit failed")
    manifest, _, _ = read_json(manifest_path)
    plan = manifest["plan"]
    require((plan["n_samples"], plan["n_qubits"], plan["shots"], plan["layers"])
            == (4, 156, 1024, 1), "Unexpected archived experiment")
    require(plan["physical_qubits"] == list(range(156))
            and [j["job_id"] for j in manifest["jobs"]] == ["dafncudnj4cs73agjjsg"],
            "This figure is bound to the declared archived job and physical layout")
    decoded, sources = {}, [fingerprint(manifest_path)]
    for job in manifest["jobs"]:
        for field in ("raw_counts", "circuit_artifact"):
            ref = job[field]
            path = manifest_path.parent / ref["artifact"]
            info = fingerprint(path)
            require(info["sha256"] == ref["sha256"] and info["bytes"] == ref["size_bytes"],
                    "Archived source artifact changed")
            sources.append(info)
        raw, _, _ = read_json(manifest_path.parent / job["raw_counts"]["artifact"])
        for pub in raw["pubs"]:
            decoded[pub["pub_index"]] = analyze_counts(pub["counts"], 156, 1024)
    values = np.stack([np.stack([decoded[3 * i + a]["expectations"] for a in range(3)], axis=1)
                       for i in range(4)])
    duplicate = np.stack([decoded[12 + a]["expectations"] for a in range(3)], axis=1)
    errors = np.array([(1 - decoded[15]["expectations"]) / 2,
                       (1 + decoded[16]["expectations"]) / 2])
    ideal, encoded = independent_ideal(manifest["features"], plan)
    close(values, manifest["projected_features"]["values"], "direct raw Bloch features")
    ids_path = ROOT / "research/manuscript/tables/quantum_input_descriptors.csv"
    with ids_path.open() as handle:
        identities = list(csv.DictReader(handle))
    close([[float(row[k]) for k in ("molecular_weight", "logp", "tpsa", "qed")]
           for row in identities], manifest["features"], "ledger input identity order")
    for path in [ids_path, Path(__file__), ROOT / "scripts/audit_projected_quantum.py",
                 ROOT / "docs/quantum-projected-independent-audit.json",
                 ROOT / "docs/quantum-projected-verification.json"]:
        sources.append(fingerprint(path))
    return manifest, values, duplicate, errors, ideal, encoded, identities, sources


def calculate(manifest, values, duplicate, errors, ideal, encoded):
    plan = manifest["plan"]
    pairs = list(zip(*np.triu_indices(4, 1), strict=True))
    gamma_rows, exclusion_rows, pair_rows, masks = [], [], [], []
    base, _ = projected_kernel(values, 1.)
    worst_errors = errors.max(axis=0)
    for cutoff in THRESHOLDS:
        keep = np.ones(156, dtype=bool) if cutoff is None else worst_errors <= cutoff
        count = int(keep.sum())
        require(count > 0, "No retained observed qubits")
        key = "all" if cutoff is None else f"max_error_le_{cutoff:g}"
        logical = np.flatnonzero(keep)
        masks.append({"scenario": key, "cutoff": cutoff,
                      "logical_qubits": logical.tolist(),
                      "physical_qubits": [plan["physical_qubits"][i] for i in logical],
                      "excluded_physical_qubits": [plan["physical_qubits"][i] for i in np.flatnonzero(~keep)]})
        measured_one, distance = projected_kernel(values[:, keep], 1.)
        _, ideal_distance = projected_kernel(ideal[:, keep], 1.)
        classical_distance = ((encoded[:, None, keep] - encoded[None, :, keep]) ** 2).mean(axis=2) / 2
        matrices = {"measured": distance, "ideal": ideal_distance, "classical": classical_distance}
        noise_by_input = ((1 - values[:, keep] ** 2) / plan["shots"]).sum(axis=(1, 2))
        noise = (noise_by_input[:, None] + noise_by_input[None, :]) / (2 * count)
        dup_distance = float(np.sum((values[0, keep] - duplicate[keep]) ** 2) / (2 * count))
        dup_noise = float(np.sum(2 - values[0, keep] ** 2 - duplicate[keep] ** 2)
                          / (2 * count * plan["shots"]))
        mean_distance = float(np.mean([distance[i, j] for i, j in pairs]))
        mean_noise = float(np.mean([noise[i, j] for i, j in pairs]))
        changed = np.array([abs(measured_one[i, j] - base[i, j]) for i, j in pairs])
        descriptor_counts = np.bincount(logical % plan["n_features"], minlength=4).tolist()
        row = {"scenario": key, "max_control_error_threshold": cutoff,
               "retained_qubits": count, "excluded_qubits": 156-count,
               "retained_encoded_descriptor_counts": json.dumps(descriptor_counts),
               "mean_retained_prepared_state_error": float(errors[:, keep].mean()),
               "max_retained_prepared_state_error": float(errors[:, keep].max()),
               "gamma": 1., "off_diagonal_mean_absolute_change_from_all": float(changed.mean()),
               "off_diagonal_max_absolute_change_from_all": float(changed.max()),
               "centered_alignment_to_all_measured": alignment(measured_one, base),
               "centered_alignment_to_matched_ideal": alignment(measured_one, np.exp(-ideal_distance)),
               "centered_alignment_to_matched_classical": alignment(measured_one, np.exp(-classical_distance)),
               "mean_pair_distance": mean_distance, "mean_plugin_shot_noise_floor": mean_noise,
               "mean_pair_distance_to_noise_floor": mean_distance / mean_noise,
               "duplicate_distance": dup_distance, "duplicate_plugin_shot_noise_floor": dup_noise,
               "duplicate_distance_to_noise_floor": dup_distance / dup_noise,
               "duplicate_kernel_gamma_one": math.exp(-dup_distance), **kernel_metrics(measured_one)}
        exclusion_rows.append(row)
        for gamma in GAMMAS:
            kernels = {method: np.exp(-gamma * d) for method, d in matrices.items()}
            for method, kernel in kernels.items():
                gamma_rows.append({"scenario": key, "retained_qubits": count,
                                   "gamma": float(gamma), "method": method,
                                   "alignment_to_matched_classical": alignment(kernel, kernels["classical"]),
                                   "alignment_to_matched_ideal": alignment(kernel, kernels["ideal"]),
                                   **kernel_metrics(kernel)})
                for i, j in pairs:
                    pair_rows.append({"scenario": key, "retained_qubits": count,
                                      "gamma": float(gamma), "method": method,
                                      "sample_i": int(i), "sample_j": int(j),
                                      "normalized_squared_distance": float(matrices[method][i, j]),
                                      "kernel": float(kernel[i, j]),
                                      "measured_plugin_shot_noise_floor": float(noise[i, j]) if method == "measured" else None})
        # One gamma-independent observation geometry underlies the entire sweep.
        for method in matrices:
            rows = [r for r in gamma_rows if r["scenario"] == key and r["method"] == method]
            require(all(a["off_diagonal_mean"] >= b["off_diagonal_mean"]
                        for a, b in zip(rows[:-1], rows[1:], strict=True)), "RBF gamma monotonicity failed")
    close(exclusion_rows[0]["duplicate_kernel_gamma_one"],
          manifest["controls"]["duplicate"]["kernel_to_original"], "duplicate archive match")
    close(base, manifest["kernel"], "gamma-one all-qubit archive match")
    return gamma_rows, exclusion_rows, pair_rows, masks


def make_figure(output, gamma_rows, exclusions, errors):
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8,
                         "axes.titlesize": 9, "axes.labelsize": 8, "legend.fontsize": 7,
                         "xtick.labelsize": 7, "ytick.labelsize": 7,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.edgecolor": "#BDC9D0", "text.color": "#233746",
                         "axes.labelcolor": "#233746", "pdf.fonttype": 42,
                         "svg.fonttype": "none", "savefig.facecolor": "white"})
    fig, axes = plt.subplots(3, 2, figsize=(7.4, 8.5))
    fig.subplots_adjust(left=.105, right=.97, top=.94, bottom=.15, hspace=.68, wspace=.46)
    titles = ["Bandwidth changes apparent spread", "Centered alignment changes with gamma",
              "Effective rank is bounded by input count", "Control errors vary by qubit",
              "Post hoc removal changes the kernel", "Distances relative to the shot floor"]
    for ax, letter, title in zip(axes.flat, "abcdef", titles, strict=True):
        ax.set_title(title, loc="left", pad=11)
        ax.text(-.17, 1.10, letter, transform=ax.transAxes, weight="bold", fontsize=12)
        ax.grid(axis="y", color="#E3E9ED", lw=.6, zorder=0)
    baseline = {method: [r for r in gamma_rows if r["scenario"] == "all" and r["method"] == method]
                for method in COLORS}
    for method, rows in baseline.items():
        x = [r["gamma"] for r in rows]
        axes[0, 0].plot(x, [r["off_diagonal_spread"] for r in rows], color=COLORS[method], label=LABELS[method], lw=1.6)
        axes[1, 0].plot(x, [r["centered_effective_rank"] for r in rows], color=COLORS[method], lw=1.6)
    rows = baseline["measured"]
    axes[0, 1].plot(GAMMAS, [r["alignment_to_matched_classical"] for r in rows], color=COLORS["classical"], label="IBM vs classical", lw=1.6)
    axes[0, 1].plot(GAMMAS, [r["alignment_to_matched_ideal"] for r in rows], color=COLORS["ideal"], label="IBM vs ideal", lw=1.6)
    for ax in (axes[0, 0], axes[0, 1], axes[1, 0]):
        ax.set_xscale("log")
        ax.set_xlabel("RBF gamma (same archived features)")
        ax.axvline(1, color="#82919C", ls=":", lw=.9)
    axes[0, 0].set_ylabel("Off-diagonal range (max − min)")
    axes[0, 0].legend(frameon=False, loc="upper left")
    axes[0, 1].set_ylabel("Centered kernel alignment")
    axes[0, 1].legend(frameon=False, loc="lower left")
    axes[1, 0].set_ylabel("Entropy effective rank of HKH")
    axes[1, 0].axhline(3, color="#82919C", ls="--", lw=.8)
    axes[1, 0].set_ylim(.9, 3.12)
    axes[1, 0].text(.03, .85, "Algebraic ceiling = n − 1 = 3", transform=axes[1, 0].transAxes, fontsize=7)
    ax = axes[1, 1]
    for rates, color, label in zip(errors, ["#183B56", "#A54678"], ["Prepared zero", "Prepared one"], strict=True):
        ax.scatter(np.arange(156), 100*rates, s=8, c=color, alpha=.8, label=label, zorder=3)
    ax.axhline(5, color="#82919C", lw=.6, ls=":")
    ax.axhline(10, color="#82919C", lw=.6, ls="--")
    ax.set(xlabel="Physical qubit (archived layout 0–155)", ylabel="Prepared-state error (%)", ylim=(-1, 47))
    ax.annotate("Q72: 38.57%", xy=(72, 100*errors[1, 72]), xytext=(5, 43), fontsize=7,
                arrowprops={"arrowstyle": "-", "lw": .7, "color": "#82919C"})
    ax.legend(frameon=False, loc="upper right")
    x = np.arange(len(exclusions))
    labels = ["All" if r["max_control_error_threshold"] is None else f"≤{r['max_control_error_threshold']*100:g}%" for r in exclusions]
    count_labels = [f"{label}\nq={r['retained_qubits']}" for label, r in zip(labels, exclusions, strict=True)]
    ax = axes[2, 0]
    ax.bar(x-.16, [r["off_diagonal_mean_absolute_change_from_all"] for r in exclusions], .29, color="#158A88", label="Mean of six pairs", zorder=3)
    ax.bar(x+.16, [r["off_diagonal_max_absolute_change_from_all"] for r in exclusions], .29, color="#183B56", label="Maximum pair", zorder=3)
    ax.set_xticks(x, count_labels)
    ax.set(xlabel="Retain if max control error ≤ cutoff", ylabel="Absolute change in K (γ = 1)")
    ax.legend(frameon=False, loc="upper left")
    ax = axes[2, 1]
    ax.plot(x, [r["mean_pair_distance_to_noise_floor"] for r in exclusions], "o-", ms=3.5, color="#158A88", label="Mean pair distance / mean floor")
    ax.plot(x, [r["duplicate_distance_to_noise_floor"] for r in exclusions], "s-", ms=3.5, color="#C58B24", label="One repeat distance / its floor")
    ax.axhline(1, color="#82919C", ls=":", lw=.8)
    ax.set_xticks(x, labels)
    ax.set_yscale("log")
    ax.set(xlabel="Post hoc prepared-state error cutoff", ylabel="Distance / estimated shot floor", ylim=(.5, 35))
    ax.legend(frameon=False, loc="center", fontsize=6.5)
    fig.text(.105, .04, "One IBM job; four inputs; 17 × 1,024 shots. Cutoffs remove observed coordinates only, without rerunning circuits.\nPost hoc sensitivity is not readout mitigation, uncertainty coverage, biological validation or quantum advantage.", fontsize=7.2, linespacing=1.5)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    width, height = fig.canvas.get_width_height()
    for label in fig.findobj(matplotlib.text.Text):
        if label.get_visible() and label.get_text() and not label.get_clip_on():
            bounds = label.get_window_extent(renderer)
            require(bounds.x0 >= -1 and bounds.y0 >= -1 and bounds.x1 <= width+1 and bounds.y1 <= height+1,
                    f"Text outside figure: {label.get_text()}")
    artifacts = []
    for suffix in ("svg", "pdf", "png"):
        path = output / f"{NAME}.{suffix}"
        fig.savefig(path, dpi=600, metadata={"Creator": "HerbFold CPU sensitivity analysis"} if suffix == "pdf" else None)
        artifacts.append(fingerprint(path))
    preview = ROOT / "tmp/manuscript-figures" / f"{NAME}.png"
    preview.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(preview, dpi=180)
    plt.close(fig)
    return artifacts, preview


CAPTION = """Supplementary Figure S4. Post hoc bandwidth and observed-qubit sensitivity of one archived projected-kernel experiment. All hardware-derived values use the same four inputs, 156 measured qubits in 51 independent blocks of at most four qubits, and 17 PUBs of 1,024 shots from IBM job dafncudnj4cs73agjjsg; no additional shots were acquired. (a) Range (maximum minus minimum) of the six unordered off-diagonal kernel entries over 81 logarithmically spaced gamma values from 0.01 to 100. The measured, circuit-matched ideal and encoded-descriptor classical kernels are distinct estimators. (b) Centered alignment of measured kernels with the two references at the same numerical gamma. Their feature spaces have different distance scales, so equal gamma does not imply matched effective bandwidth. (c) Entropy effective rank of the centered matrix HKH, with H = I − 11ᵀ/4; its algebraic ceiling is three. Rank and alignment are not measures of biological prediction accuracy. The dotted vertical lines in a–c mark the archived gamma of one; no gamma was selected by a label or performance objective. (d) Errors from prepared-zero and prepared-one controls; preparation and detection errors are not separable here. (e) Mean and maximum absolute change across six off-diagonal measured entries at gamma one after retaining qubits for which both observed control errors are at or below the indicated cutoff. The kernel is recomputed from the retained XYZ coordinates with the denominator changed from 2 × 156 to 2 × retained-qubit count. These coordinates still come from the original full-width circuits, including original entangling gates; this is neither readout mitigation nor a newly compiled reduced-width circuit. Cutoffs were chosen after inspecting archived data, without held-out calibration, and change descriptor-coordinate balance. (f) Mean pair distance divided by the mean plug-in shot-noise floor, and the separately observed first-input repeat distance divided by its own floor. The floors use component variances (1 − expectation²)/shots and omit hardware bias, drift and threshold-selection uncertainty; they are not confidence bounds or independent replicate evidence. Curves are deterministic sensitivity summaries, not confidence intervals. Four inputs, one provider job and classically tractable blocks support no supervised, efficacy, diagnostic or quantum-advantage claim."""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--output", type=Path, default=ROOT / "research/manuscript/q1_extension/quantum")
    parser.add_argument("--figures", type=Path, default=ROOT / "output/manuscript/figures")
    parser.add_argument("--audit-python", type=Path, default=ROOT / ".venv/bin/python",
                        help="Interpreter with Qiskit supporting archived QPY version 17")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    args.figures.mkdir(parents=True, exist_ok=True)
    checks = invariants()
    manifest, values, duplicate, errors, ideal, encoded, identities, sources = load_observations(args.manifest.resolve(), args.output, args.audit_python)
    gamma_rows, exclusions, pairs, masks = calculate(manifest, values, duplicate, errors, ideal, encoded)
    write_csv(args.output / "gamma_sensitivity.csv", gamma_rows)
    write_csv(args.output / "exclusion_sensitivity.csv", exclusions)
    write_csv(args.output / "pair_sensitivity.csv", pairs)
    write_json(args.output / "qubit_subsets.json", masks)
    controls, features = [], []
    for q, physical in enumerate(manifest["plan"]["physical_qubits"]):
        ci = wilson(errors[:, q] * 1024, 1024)
        controls.append({"logical_qubit": q, "physical_qubit": physical,
                         "prepared_zero_error": float(errors[0, q]), "prepared_one_error": float(errors[1, q]),
                         "max_prepared_state_error": float(errors[:, q].max()),
                         "zero_wilson_low": float(ci[0, 0]), "zero_wilson_high": float(ci[0, 1]),
                         "one_wilson_low": float(ci[1, 0]), "one_wilson_high": float(ci[1, 1])})
        for i, identity in enumerate(identities):
            for a, axis in enumerate("XYZ"):
                features.append({"sample_index": i, "sample_id": identity["sample_id"],
                                 "logical_qubit": q, "physical_qubit": physical, "basis": axis,
                                 "raw_expectation": float(values[i, q, a]),
                                 "independent_ideal_expectation": float(ideal[i, q, a]),
                                 "repeat_first_input_expectation": float(duplicate[q, a]) if i == 0 else None})
    write_csv(args.output / "qubit_controls.csv", controls)
    write_csv(args.output / "bloch_features.csv", features)
    write_csv(args.output / "source_hashes.csv", sources)
    write_json(args.output / "encoded_input_features.json", {"input_ids": [r["sample_id"] for r in identities],
               "normalized_input_descriptors": manifest["features"], "encoded_angles": encoded.tolist(),
               "feature_sha256": manifest["plan"]["feature_sha256"], "definition": "2*atan(normalized features), cyclic repeat to 156 coordinates"})
    artifacts, preview = make_figure(args.figures, gamma_rows, exclusions, errors)
    caption = args.figures / f"{NAME}.caption.txt"
    caption.write_text(CAPTION + "\n")
    artifacts.append(fingerprint(caption))
    # Source hashes are rechecked after all arithmetic and rendering.
    require(all(fingerprint(ROOT / s["path"])["sha256"] == s["sha256"] for s in sources),
            "Source file changed during analysis")
    baseline = [r for r in gamma_rows if r["scenario"] == "all" and r["method"] == "measured"]
    summary = {"schema_version": 1, "created_at_utc": datetime.now(UTC).isoformat(),
               "status": "passed", "analysis_kind": "post_hoc_cpu_sensitivity_of_archived_counts",
               "source_job_id": manifest["jobs"][0]["job_id"], "source_request_sha256": manifest["request_sha256"],
               "software": {"plotting_python": sys.version.split()[0], "python_executable": sys.executable,
                            "numpy": np.__version__, "matplotlib": matplotlib.__version__,
                            "qpy_audit_python": str(args.audit_python)},
               "sample_count": 4, "unordered_pair_count": 6, "hardware_job_count": 1,
               "original_qubits": 156, "original_shots_per_pub": 1024, "original_pub_count": 17,
               "new_shots": 0, "gpu_jobs": 0, "qpu_jobs": 0, "llm_jobs": 0,
               "experimental_invariants": {
                   "normalized_features_sha256": manifest["plan"]["feature_sha256"],
                   "topology_sha256": manifest["plan"]["topology_sha256"],
                   "blocks": len(manifest["plan"]["blocks"]),
                   "maximum_block_qubits": manifest["plan"]["maximum_simulated_block_qubits"],
                   "layers": manifest["plan"]["layers"],
                   "same_counts_all_scenarios": True, "new_transpilation": False,
                   "controls_independent_from_sample_pubs_but_same_provider_job": True},
               "gamma_sweep": {"values": GAMMAS.tolist(), "selection_objective": None,
                               "archived_gamma": 1, "all_qubit_measured_endpoint_rows": [baseline[0], baseline[40], baseline[-1]]},
               "exclusion_at_gamma_one": exclusions,
               "mathematical_definitions": {
                   "projected_distance": "sum_{q in S,P in XYZ}(v_iqP-v_jqP)^2/(2*|S|)",
                   "classical_distance": "sum_{q in S}(encoded_iq-encoded_jq)^2/(2*|S|)",
                   "kernel": "exp(-gamma*distance); diagonal one is defined, not measured self-fidelity",
                   "centered_alignment": "Frobenius inner product HKH,HLH divided by their Frobenius norms; undefined if a centered norm is numerically zero",
                   "entropy_effective_rank": "exp(-sum p*log(p)); p=positive eigenvalues/sum; centered maximum 3, uncentered maximum 4",
                   "eigenvalue_tolerance": "1e-12*max(1,spectral_norm); larger negative eigenvalues fail; near-zero discarded only in spectral diagnostics",
                   "condition_number": "ratio of largest to smallest positive eigenvalue above stated tolerance; centered full-matrix condition is singular by definition",
                   "shot_floor": "sum component variances (1-v^2)/shots across independent input PUBs divided by 2*retained_qubits"},
               "invariant_checks": checks,
               "limitations": [
                   "Single provider job, four inputs and one repeat; six pairs are dependent and gamma-grid rows are not new experiments.",
                   "Identical numerical gamma across distinct feature spaces does not equalize bandwidth; no gamma fitting or supervised labels were used.",
                   "Post hoc masks use observed prepared-state controls from the same job; selection bias and finite-shot uncertainty in the mask are not corrected.",
                   "Removing observed coordinates does not remove original gates, restore noisy qubits, apply readout mitigation or simulate a new circuit.",
                   "Subset renormalization changes the kernel definition and retained descriptor balance. Exact subsets and descriptor counts are archived.",
                   "Shot floors are plug-in expectations, not confidence intervals; no new bootstrap interval or claim of drift/calibration coverage.",
                   "Effective rank is an algebraic spectrum summary of four inputs, not model accuracy, independent observations or quantum advantage.",
                   "At gamma zero every kernel is all ones and centered diagnostics are undefined. For distinct feature vectors gamma approaching infinity yields identity kernels, so centered alignment approaches one trivially; higher alignment or rank after bandwidth changes need not indicate useful features.",
                   "Ideal references are calculated with exact factorized classical simulation of the same small blocks; no claim of computational advantage.",
                   "No molecular efficacy, affinity, clinical diagnosis, safety or supervised generalization conclusion."],
               "primary_method_sources": [
                   {"topic": "projected XYZ kernels", "url": "https://quantum.cloud.ibm.com/docs/en/tutorials/projected-quantum-kernels"},
                   {"topic": "entropy effective rank, Roy and Vetterli 2007", "url": "https://zenodo.org/records/40328"},
                   {"topic": "centered kernel alignment, Cortes et al. 2012", "url": "https://www.jmlr.org/papers/v13/cortes12a.html"}],
               "sources": sources, "sources_unchanged_after_analysis": True,
               "figure_artifacts": artifacts, "figure_preview": relative(preview),
               "visual_review": "pending independent full-figure inspection"}
    write_json(args.output / "summary.json", summary)
    products = [fingerprint(p) for p in sorted(args.output.iterdir()) if p.is_file() and p.name != "artifact_manifest.json"]
    write_json(args.output / "artifact_manifest.json", {"artifacts": products + artifacts})
    print(json.dumps({"summary": relative(args.output / "summary.json"), "gamma_rows": len(gamma_rows),
                      "exclusions": [{k: row[k] for k in ("scenario", "retained_qubits", "off_diagonal_max_absolute_change_from_all")}
                                     for row in exclusions], "preview": relative(preview)}, indent=2))


if __name__ == "__main__":
    main()
