"""Exercise AF3 agent handoff against the isolated, labelled synthetic CPU server.

Run only after Browser skill discovery finds no connected browser. The test
requires /api/test-fixture to explicitly identify synthetic controls and never
accepts production runtime. No genuine AF3, LLM, QPU or biological measurements.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse


def verify(args, report):
    import requests
    from playwright.sync_api import expect, sync_playwright

    identity = requests.get(args.url + "/api/test-fixture", timeout=5).json()
    assert identity == {"test_fixture": True, "scientific_results": False, "gpu_execution": False}
    expect.set_options(timeout=60000)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=shutil.which("google-chrome"), headless=True,
                                   args=["--no-sandbox", "--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": 1500, "height": 1040}, reduced_motion="reduce")
        page = context.new_page()
        page.set_default_timeout(60000)
        page.on("pageerror", lambda error: report["javascript_errors"].append(str(error)))
        posts = []
        context.on("request", lambda request: posts.append(urlparse(request.url).path) if request.method == "POST" else None)
        allowed = {"/api/molecules/describe", "/api/molecules/svg", "/api/molecular/conformer",
                   "/api/molecular/sdf", "/api/molecular/resolve", "/api/af3-workflows", "/api/af3-workflows/attach"}

        def guard(route):
            request, path = route.request, urlparse(route.request.url).path
            if request.method == "POST" and path not in allowed and not (path.startswith("/api/af3-workflows/") and path.endswith("/resume")):
                report["blocked_mutations"].append(path)
                route.abort()
            else:
                route.continue_()
        context.route("**/api/**", guard)

        def shot(name):
            path = args.output / f"{name}.png"
            page.screenshot(path=str(path), full_page=True, animations="disabled")
            report["screenshots"][name] = str(path)

        page.goto(args.url + "/#alphafold", wait_until="domcontentloaded")
        launcher = page.get_by_test_id("af3-workflow-launcher")
        expect(launcher).to_be_visible()
        expect(launcher).to_contain_text("퀘르세틴")
        page.get_by_test_id("studio-af3-mode").select_option("none")
        expect(page.get_by_test_id("studio-af3-workflow-start")).to_be_disabled()
        page.get_by_test_id("studio-af3-exploratory-ack").check()
        page.get_by_test_id("studio-af3-seed").fill("31")
        launcher.get_by_label("실행 범위").select_option("prepare")
        shot("af3-workflow-launcher")
        with page.expect_response(lambda r: r.url.endswith("/api/af3-workflows") and r.request.method == "POST") as submitted:
            page.get_by_test_id("studio-af3-workflow-start").click()
        workflow_id = submitted.value.json()["id"]
        report["workflow_id"] = workflow_id
        expect(page).to_have_url(args.url + "/#agents")
        detail = page.get_by_test_id("af3-workflow-detail")
        expect(detail).to_have_attribute("data-workflow-id", workflow_id)
        expect(detail).to_have_attribute("data-workflow-status", "prepared")
        job_id = detail.get_attribute("data-job-id")
        report["job_id"] = job_id
        assert job_id
        expect(page.locator(".afw-timeline > li")).to_have_count(6)
        expect(page.locator('[data-stage="msa"]')).to_have_attribute("data-status", "skipped")
        expect(page.get_by_test_id("af3-workflow-resume")).to_be_disabled()
        # Reload restores the same workflow, without repeating POST or executing prepared input.
        mutation_count = len(posts)
        page.reload(wait_until="domcontentloaded")
        expect(detail).to_have_attribute("data-workflow-id", workflow_id)
        expect(detail).to_have_attribute("data-workflow-status", "prepared")
        assert not any(path.startswith("/api/af3-workflows") for path in posts[mutation_count:])
        report["reload_without_submission"] = True
        page.get_by_test_id("af3-workflow-exploratory-ack").check()
        page.get_by_test_id("af3-workflow-resume").click()
        expect(detail).to_have_attribute("data-workflow-status", "running")
        shot("af3-workflow-running")
        # A real browser disconnect changes display state and only GETs on reconnect.
        mutation_count = len(posts)
        context.set_offline(True)
        expect(page.locator(".afw-connection")).to_contain_text("오프라인")
        context.set_offline(False)
        expect(detail).to_have_attribute("data-workflow-status", "validating")
        expect(page.locator('[data-stage="validation"]')).to_have_attribute("data-status", "running")
        expect(page.get_by_test_id("af3-workflow-view-result")).to_have_count(0)
        report["process_completed_validation_still_active"] = True
        shot("af3-workflow-validating")
        expect(detail).to_have_attribute("data-workflow-status", "completed")
        expect(page.get_by_test_id("af3-workflow-view-result")).to_be_visible()
        assert not any(path.startswith("/api/af3-workflows") for path in posts[mutation_count:])
        report["offline_reconnect_without_submission"] = True
        expect(detail).to_have_attribute("data-job-id", job_id)
        expect(page.get_by_test_id("af3-workflow-log")).not_to_have_text("기록된 실행 로그가 아직 없습니다.")
        shot("af3-workflow-completed")
        page.get_by_test_id("af3-workflow-view-result").click()
        expect(page).to_have_url(args.url + "/#alphafold")
        # No generic/latest result fallback; exact job is persisted in Studio selection.
        stored = page.evaluate("JSON.parse(sessionStorage.getItem('herbfold.studio.selection.v1') || 'null')")
        assert stored["predictionJobId"] == job_id and stored["target"] == "P35354"
        report["studio_selection"] = stored
        # Read stable DOM result state and selection select, covering the actual handoff.
        expect(page.get_by_test_id("studio-af3-job-select")).to_have_value(job_id)
        expect(page.locator(".molecular-viewer canvas")).to_be_visible()
        shot("af3-workflow-result-studio")
        report["exact_result_handoff"] = True
        page.get_by_test_id("studio-af3-workflow-attach").click()
        expect(detail).to_have_attribute("data-workflow-id", workflow_id)
        # Saved old job attach is idempotent and reuses the first workflow.
        assert len(requests.get(args.url + "/api/af3-workflows", timeout=10).json()["items"]) == 1
        report["existing_job_attach_reuses_workflow"] = True
        page.get_by_role("button", name="기존 연구·양자 분석", exact=True).click()
        expect(page.get_by_test_id("af3-workflow-detail")).to_have_count(0)
        page.get_by_role("button", name="AlphaFold 워크플로우", exact=True).click()
        expect(detail).to_have_attribute("data-workflow-id", workflow_id)
        report["legacy_analysis_menu_preserved"] = True
        page.set_viewport_size({"width": 390, "height": 844})
        expect(detail).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        shot("af3-workflow-mobile")
        report["mobile_width"] = 390
        assert not report["javascript_errors"]
        assert not report["blocked_mutations"]
        context.close()
        browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:9022")
    parser.add_argument("--output", type=Path, default=Path("tmp/af3_workflow_20260917/ui"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"timestamp": datetime.now(UTC).isoformat(), "synthetic_control_flow_only": True,
              "scientific_results": False, "javascript_errors": [], "blocked_mutations": [], "screenshots": {}, "passed": False}
    try:
        verify(args, report)
        report["passed"] = True
    finally:
        (args.output / "verification.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
