"""Exercise the illustrated Quantum Studio using archived results, without submitting jobs.

Standalone Playwright is used after Browser runtime discovery returned no available
browser. Synthetic malformed/pending records exist only in intercepted browser
responses and are explicitly separated from archived observations.
"""
import argparse
import copy
import hashlib
import json
import math
import shutil
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8873")
    parser.add_argument("--output", default="tmp/quantum-studio-v3/verification")
    args = parser.parse_args()
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    jobs = httpx.get(args.url + "/api/quantum/results", timeout=30).json()["items"]
    observed = [j for j in jobs if j.get("result", {}).get("kernel_method") == "projected"
                and j.get("result", {}).get("hardware_executed") is True
                and j.get("status") == "completed"]
    assert len(observed) >= 2
    primary = next((j for j in observed if j["id"] == "f1b0f1c6867b4d21b40193e65ab980bf"), observed[0])
    other = next(j for j in observed if j["id"] != primary["id"])
    result = primary["result"]
    checks, errors, forbidden = {}, [], []
    snapshots = {j["id"]: hashlib.sha256(json.dumps(j, sort_keys=True).encode()).hexdigest() for j in (primary, other)}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=shutil.which("google-chrome") or shutil.which("chromium"),
                                    headless=True, args=["--no-sandbox", "--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": 1440, "height": 1050}, reduced_motion="reduce", accept_downloads=True)

        def protect(route):
            req = route.request
            path = urlparse(req.url).path
            if req.method != "GET" and path != "/api/molecules/svg":
                forbidden.append({"method": req.method, "path": path}); route.abort()
            else:
                route.continue_()

        context.route("**/api/**", protect)
        page = context.new_page(); page.on("pageerror", lambda e: errors.append(str(e)))

        def open_job(job):
            page.get_by_test_id("quantum-studio-history-tab").click()
            page.get_by_test_id("quantum-studio-job-" + job["id"]).click()
            page.wait_for_function("id => document.querySelector('[data-testid=quantum-studio-workarea]')?.dataset.recordId === id", arg=job["id"])
            page.get_by_test_id("quantum-journey").wait_for()

        def stage(n):
            page.get_by_test_id(f"quantum-stage-{n}").click()
            page.wait_for_function("n => document.querySelector('[data-testid=quantum-journey]')?.dataset.stage === String(n)", arg=n)
            page.locator([".qj-input-visual", ".qj-encoding", ".qj-circuit-scroll, .qj-fidelity-diagram, .qj-large-empty", ".qj-measurement-controls, .qj-global-observation", ".qj-comparison"][n]).first.wait_for(state="visible")

        page.goto(args.url + "/#quantum", wait_until="networkidle")
        open_job(primary)
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(out / "desktop-overview.png"))
        journey = page.get_by_test_id("quantum-journey")
        assert page.get_by_test_id("quantum-explainer-play").is_disabled()
        checks["reduced_motion_disables_auto_play"] = True
        stage(2)
        assert page.locator(".qj-block-map>button").count() == len(result["plan"]["blocks"])
        first_angle = str(float(f"{2 * math.atan(primary['payload']['features'][0][0]):.6g}"))
        assert first_angle + " rad" in page.locator(".qj-circuit-scroll").inner_text()
        checks["archived_block_count_and_first_angle"] = {"blocks": len(result["plan"]["blocks"]), "angle": first_angle}
        selected_block = len(result["plan"]["blocks"]) - 1
        page.get_by_test_id(f"quantum-block-{selected_block}").click()
        for q in result["plan"]["blocks"][selected_block]["logical_qubits"]:
            assert f"q{q}" in page.locator(".qj-circuit-scroll").inner_text()
        checks["block_selection_uses_recorded_nodes"] = True
        page.get_by_test_id("quantum-block-0").click()
        page.get_by_role("combobox", name="회로 측정 축").select_option("Y")
        assert "S† → H" in page.locator(".qj-circuit-scroll").inner_text()
        with page.expect_download() as event:
            page.get_by_role("button", name="회로 SVG", exact=True).click()
        exported = out / "selected-circuit.svg"; event.value.save_as(exported)
        assert "S†" in exported.read_text() and "<svg" in exported.read_text()
        checks["actual_selected_circuit_svg_download"] = True
        page.get_by_role("combobox", name="회로 측정 축").select_option("Z")
        journey.scroll_into_view_if_needed(); page.screenshot(path=str(out / "desktop-circuit.png"), full_page=False)
        stage(3)
        page.get_by_test_id("quantum-journey-qubit").select_option("0")
        sphere = page.get_by_test_id("quantum-bloch-sphere")
        norm = math.sqrt(sum(x*x for x in result["projected_features"]["values"][0][0]))
        assert abs(float(sphere.get_attribute("data-vector-norm")) - norm) < 1e-12
        page.get_by_test_id("quantum-journey-sample").select_option("1")
        expected = math.sqrt(sum(x*x for x in result["projected_features"]["values"][1][0]))
        assert abs(float(sphere.get_attribute("data-vector-norm")) - expected) < 1e-12
        checks["sample_selection_updates_actual_xyz"] = [norm, expected]
        outside = next((s, q, math.sqrt(sum(x*x for x in xyz))) for s, rows in enumerate(result["projected_features"]["values"])
                       for q, xyz in enumerate(rows) if sum(x*x for x in xyz) > 1.000001)
        page.get_by_test_id("quantum-journey-sample").select_option(str(outside[0]))
        page.get_by_test_id("quantum-journey-qubit").select_option(str(outside[1]))
        assert abs(float(sphere.get_attribute("data-vector-norm")) - outside[2]) < 1e-12
        assert "|r| > 1" in sphere.inner_text()
        checks["unphysical_raw_estimate_not_normalized"] = {"sample": outside[0], "qubit": outside[1], "raw_norm": outside[2]}
        canvas = page.get_by_test_id("quantum-bloch-canvas")
        page.get_by_role("button", name="기대값 구 확대", exact=True).click()
        assert float(canvas.get_attribute("data-zoom")) > 1
        page.get_by_role("button", name="기대값 구 축소", exact=True).click()
        assert float(canvas.get_attribute("data-zoom")) == 1
        before = float(canvas.get_attribute("data-yaw")); canvas.focus(); page.keyboard.press("ArrowRight")
        assert float(canvas.get_attribute("data-yaw")) != before
        canvas.scroll_into_view_if_needed(); box = canvas.bounding_box()
        before = float(canvas.get_attribute("data-yaw"))
        page.mouse.move(box["x"] + box["width"]*.5, box["y"] + box["height"]*.5)
        page.mouse.down(); page.mouse.move(box["x"] + box["width"]*.65, box["y"] + box["height"]*.55, steps=8); page.mouse.up()
        assert float(canvas.get_attribute("data-yaw")) != before
        page.get_by_role("button", name="기대값 구 시점 초기화", exact=True).click()
        assert float(canvas.get_attribute("data-zoom")) == 1
        checks["sphere_pointer_keyboard_zoom_reset"] = True
        page.get_by_test_id("quantum-journey-sample").select_option("0"); page.get_by_test_id("quantum-journey-qubit").select_option("0")
        sphere.scroll_into_view_if_needed(); page.screenshot(path=str(out / "desktop-measurement.png"))
        stage(4)
        assert page.locator(".qj-mini-kernel").count() == 3
        page.locator(".qj-mini-kernel").first.get_by_role("button").nth(1).click()
        expected_pair = result["kernel"][0][1]
        shown = float(page.locator(".qj-comparison-detail>div").first.locator("strong").inner_text())
        assert abs(shown - expected_pair) < 1e-6
        checks["matrix_selection_reports_actual_pair"] = {"expected": expected_pair, "displayed": shown}
        journey.scroll_into_view_if_needed(); page.screenshot(path=str(out / "desktop-comparison.png"))
        stage(0)
        page.locator(".qj-molecule-sketch img").wait_for()
        assert page.locator(".qj-molecule-sketch img").evaluate("e => e.complete && e.naturalWidth > 0")
        assert primary["payload"]["sample_ids"][0] in page.locator(".qj-chemical-card").inner_text()
        checks["actual_identity_linked_2d_structure"] = True
        stage(1)
        assert page.locator(".qj-angle-grid>div").count() == min(12, result["plan"]["n_qubits"])
        page.get_by_test_id("quantum-stage-1").focus(); page.keyboard.press("ArrowRight")
        assert journey.get_attribute("data-stage") == "2"
        checks["all_five_stages_and_keyboard_tabs"] = True
        open_job(other); stage(2)
        assert page.locator(".qj-block-map>button").count() == len(other["result"]["plan"]["blocks"])
        assert journey.get_attribute("data-feature-sha") == other["result"]["plan"]["feature_sha256"]
        checks["record_switch_updates_topology_and_identity"] = {"first": len(result["plan"]["blocks"]), "second": len(other["result"]["plan"]["blocks"])}
        legacy = next(j for j in jobs if j.get("result", {}).get("kernel_method") != "projected"
                      and any((o.get("zero_counts") == 0) for x in j.get("result", {}).get("jobs", []) for o in x.get("observations", [])))
        open_job(legacy); stage(3)
        assert not page.get_by_test_id("quantum-bloch-sphere").count()
        assert "0 /" in page.locator(".qj-global-observation").inner_text()
        checks["global_zero_is_observation_not_missing"] = True

        open_job(primary); stage(2)
        page.emulate_media(reduced_motion="no-preference")
        page.get_by_test_id("quantum-explainer-play").click()
        journey.scroll_into_view_if_needed()
        page.wait_for_function("document.querySelector('[data-testid=quantum-journey]')?.dataset.animationPlaying === 'true'")
        page.wait_for_function("document.querySelector('[data-testid=quantum-journey]')?.dataset.stage === '3'", timeout=10000)
        page.get_by_test_id("quantum-explainer-play").click()
        assert journey.get_attribute("data-animation-playing") == "false"
        checks["animation_advances_and_pauses_without_job_submission"] = True
        page.get_by_test_id("quantum-explainer-play").click()
        page.wait_for_function("document.querySelector('[data-testid=quantum-journey]')?.dataset.animationPlaying === 'true'")
        page.emulate_media(reduced_motion="reduce")
        page.wait_for_function("document.querySelector('[data-testid=quantum-explainer-play]')?.disabled === true")
        assert journey.get_attribute("data-animation-playing") == "false"
        assert page.get_by_test_id("quantum-explainer-play").get_attribute("aria-pressed") == "false"
        checks["motion_preference_changes_apply_without_reload_and_stop_playback"] = True
        page.set_viewport_size({"width": 390, "height": 844})
        for n in (0, 1, 2, 3, 4):
            stage(n); journey.scroll_into_view_if_needed()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.screenshot(path=str(out / f"mobile-stage-{n}.png"))
            if n == 3:
                page.get_by_test_id("quantum-bloch-sphere").screenshot(path=str(out / "mobile-sphere-detail.png"))
        checks["five_mobile_stages_no_page_overflow"] = True

        # Deliberately injected browser-only records; not stored nor treated as real results.
        synthetic = copy.deepcopy(primary)
        synthetic["id"] = "e" * 32; synthetic["status"] = "queued"
        synthetic["result"] = {"status": "queued", "mode": "ibm", "kernel_method": "projected",
            "hardware_executed": False, "plan": {"mode": "ibm", "n_qubits": 2, "shots": 1024,
                "encoding": "unsupported-fixture-version", "feature_map": "unknown-fixture",
                "blocks": [{"logical_qubits": [0, 1], "edges": [[0, 1]]}], "layers": 2},
            "projected_features": {"values": []},
            "feature_definition": {"features": synthetic["payload"]["features"]}}
        stub = context.new_page(); stub.on("pageerror", lambda e: errors.append(str(e)))
        stub.route("**/api/quantum/results**", lambda route: route.fulfill(json={"items": [synthetic]}))
        stub.goto(args.url + "/#quantum", wait_until="networkidle")
        stub.get_by_test_id("quantum-studio-history-tab").click()
        stub.get_by_test_id("quantum-studio-job-" + synthetic["id"]).click()
        assert "지원되는 회로 구성을 확인할 수 없습니다" in stub.get_by_test_id("quantum-journey").inner_text()
        stub.get_by_test_id("quantum-stage-1").click(); assert not stub.locator(".qj-angle-grid>div").count()
        stub.get_by_test_id("quantum-stage-3").click()
        assert stub.get_by_test_id("quantum-bloch-sphere").get_attribute("data-has-vector") == "false"
        stub.get_by_test_id("quantum-stage-4").click()
        stub.locator(".qj-comparison").wait_for()
        assert not stub.locator(".qj-mini-grid").count()
        checks["injected_pending_missing_axes_unknown_schemas_remain_unavailable"] = True
        local_fixture = copy.deepcopy(primary)
        local_fixture["id"] = "f" * 32
        local_fixture["result"].update(mode="local", hardware_executed=False, jobs=[])
        local_fixture["result"]["plan"]["mode"] = "local"
        local_fixture["result"]["projected_features"].pop("wilson_95", None)
        local_page = context.new_page(); local_page.on("pageerror", lambda e: errors.append(str(e)))
        local_page.route("**/api/quantum/results**", lambda route: route.fulfill(json={"items": [local_fixture]}))
        local_page.goto(args.url + "/#quantum", wait_until="networkidle")
        local_page.get_by_test_id("quantum-studio-history-tab").click()
        local_page.get_by_test_id("quantum-studio-job-" + local_fixture["id"]).click()
        local_page.get_by_test_id("quantum-stage-3").click()
        local_page.locator(".qj-observable-bars").wait_for()
        assert "계산한 기대값" in local_page.locator(".qj-observable-bars").inner_text()
        assert "로컬 시뮬레이터" in local_page.locator(".qj-stage-copy").inner_text()
        assert "따로 측정한" not in local_page.get_by_test_id("quantum-bloch-sphere").inner_text()
        checks["injected_local_mode_labels_calculation_and_does_not_claim_hardware_measurement"] = True
        assert not errors, errors
        assert not forbidden, forbidden
        browser.close()
    later = httpx.get(args.url + "/api/quantum/results", timeout=30).json()["items"]
    checks["archived_records_unchanged"] = all(hashlib.sha256(json.dumps(j, sort_keys=True).encode()).hexdigest() == snapshots[j["id"]]
                                              for j in later if j["id"] in snapshots)
    assert checks["archived_records_unchanged"]
    report = {"passed": True, "scope": "UI/data-binding tests using two archived projected jobs, one legacy job and explicitly injected browser-only malformed/pending and local-mode fixtures",
              "checks": checks, "page_errors": errors, "blocked_mutation_attempts": forbidden,
              "new_hardware_or_llm_jobs": 0, "source_job_hashes": snapshots,
              "screenshots": [str(x) for x in sorted(out.glob("*.png"))]}
    (out / "verification.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
