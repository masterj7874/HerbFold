"""Check restored AF3 selection/auth recovery and bounded browser reads.

Uses isolated Chrome after unavailable in-app Browser discovery. Replays change
browser responses only; no AF3 input, execution, or server job state is mutated.
"""
from __future__ import annotations

import copy
import json
import shutil
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:9018"
JOB_ID = "31dfd5615cb848c5a98c78dc657c3692"
REPORT = ROOT / "docs" / "af3-reconnect-verification.json"
OUT = ROOT / "docs" / "images"


def main():
    token = (ROOT / "runtime/access-token.txt").read_text().strip()
    opener = build_opener(ProxyHandler({}))

    def get(path):
        with opener.open(Request(BASE + path, headers={"Authorization": "Bearer " + token}), timeout=30) as response:
            return json.load(response)

    before_jobs = {job["id"]: job["status"] for job in get("/api/jobs")}
    completed = get(f"/api/molecular/predictions/{JOB_ID}")
    assert completed["job"]["status"] == "completed"
    with sqlite3.connect(f"file:{ROOT / 'runtime/discovery/discovery.sqlite3'}?mode=ro", uri=True) as database:
        database.row_factory = sqlite3.Row
        compound = dict(database.execute("SELECT id, display_name, canonical_smiles, formula FROM discovery_compounds WHERE canonical_smiles = ?", (completed["requested"]["canonical_smiles"],)).fetchone())
    selection = {
        "compound": {"id": f"discovery_{compound['id']}", "name": compound["display_name"], "smiles": compound["canonical_smiles"], "category": "natural_product", "generated": False},
        "target": completed["requested"]["target_accession"], "mode": "alphafold3_prediction", "predictionJobId": JOB_ID,
    }
    pending = copy.deepcopy(completed)
    pending["job"]["status"] = "running"
    pending["output_validation"] = None
    pending["stage"] = {"name": "inference", "label": "AF3 구조 추론 중", "started_at": datetime.now(UTC).isoformat()}
    report = {"checked_at": datetime.now(UTC).isoformat(), "job_id": JOB_ID, "compound": compound, "checks": {}, "screenshots": [], "javascript_errors": [], "blocked_mutations": []}
    OUT.mkdir(parents=True, exist_ok=True)
    expect.set_options(timeout=30000)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=shutil.which("google-chrome"), headless=True, args=["--no-sandbox", "--enable-unsafe-swiftshader"])
        page = None
        held = []

        def release_held():
            while held:
                try:
                    held.pop().abort()
                except Exception:
                    pass  # Timeout/selection change may already have aborted it.

        def session(mode=None):
            context = browser.new_context(viewport={"width": 1440, "height": 1080}, reduced_motion="reduce")
            tab = context.new_page()
            tab.add_init_script('sessionStorage.setItem("herbfold.studio.selection.v1", ' + json.dumps(json.dumps(selection)) + ");")
            tab.on("pageerror", lambda problem: report["javascript_errors"].append(str(problem)))
            state = {"mode": mode, "status_requests": 0, "scene_requests": 0, "held_log_requests": 0, "held_scene_requests": 0}

            def guard(route):
                request = route.request
                parsed = urlparse(request.url)
                path = parsed.path
                if parsed.netloc != urlparse(BASE).netloc:
                    route.abort()
                    return
                if request.method not in {"GET", "HEAD", "OPTIONS"} and path not in {"/api/molecular/resolve", "/api/molecules/describe", "/api/molecules/svg"}:
                    report["blocked_mutations"].append({"method": request.method, "path": path})
                    route.abort()
                    return
                if request.headers.get("authorization") == "Bearer " + token:
                    if path == f"/api/molecular/predictions/{JOB_ID}":
                        state["status_requests"] += 1
                        if state["mode"] == "hung_log":
                            route.fulfill(json=pending if state["status_requests"] == 1 else completed)
                            return
                    if path == "/api/molecular/predictions" and state["mode"] == "hung_log":
                        route.fulfill(json={"items": [pending]})
                        return
                    if path == f"/api/molecular/predictions/{JOB_ID}/log" and state["mode"] == "hung_log":
                        state["held_log_requests"] += 1
                        held.append(route)
                        return
                    if path == f"/api/molecular/predictions/{JOB_ID}/scene":
                        state["scene_requests"] += 1
                        if state["mode"] == "hung_scene":
                            state["held_scene_requests"] += 1
                            held.append(route)
                            return
                route.continue_()

            context.route("**/*", guard)
            tab.goto(BASE + "/#alphafold", wait_until="domcontentloaded")
            expect(tab.get_by_test_id("studio-af3-error")).to_contain_text("API authentication required")
            if mode:
                tab.evaluate("""() => {
                  const label = document.createElement('div');
                  label.textContent = 'REPLAY · 브라우저 지연 응답 검증 · 서버 계산 변경 없음';
                  label.style.cssText = 'position:fixed;top:0;right:0;z-index:9999;padding:6px 14px;background:#713f12;color:#fff;font:13px sans-serif';
                  document.body.appendChild(label);
                }""")
            return context, tab, state

        def login(tab):
            tab.locator('.sidebar button[title="연산 엔진 설정"]').click()
            dialog = tab.get_by_role("dialog", name="연산 엔진 설정")
            dialog.locator('input[type="password"]').fill(token)
            dialog.get_by_role("button", name="연결 상태 새로 고침", exact=True).click()
            dialog.get_by_role("button", name="엔진 설정 닫기").click()

        def completed_view(tab):
            expect(tab.get_by_test_id("studio-af3-job")).to_have_attribute("data-job-id", JOB_ID)
            expect(tab.get_by_test_id("studio-af3-job")).to_have_attribute("data-job-status", "completed")
            expect(tab.get_by_test_id("studio-af3-view-result")).to_be_visible()
            expect(tab.get_by_test_id("molecule-canvas")).to_be_visible(timeout=60000)
            expect(tab.get_by_test_id("selected-structure-context")).to_have_attribute("data-structure-mode", "alphafold3_prediction")
            expect(tab.get_by_test_id("alphafold-diagnostics")).to_have_attribute("data-job-id", JOB_ID)
            expect(tab.get_by_test_id("alphafold-diagnostics")).to_have_attribute("data-diagnostic-status", "verified_identity_quality_unassessed")
            profile = tab.get_by_test_id("single-compound-analysis")
            expect(profile.locator(".formula-display")).to_contain_text(compound["formula"])
            expect(profile.locator(".single-analysis-error")).to_have_count(0)
            expect(profile).not_to_contain_text("API authentication required")

        def screenshot(tab, name, locator=None):
            path = OUT / f"af3-reconnect-{name}.png"
            (locator or tab).screenshot(path=str(path), animations="disabled")
            report["screenshots"].append(str(path.relative_to(ROOT)))

        try:
            context, page, state = session()
            login(page)
            completed_view(page)
            expect(page.get_by_test_id("studio-af3-error")).to_have_count(0)
            report["checks"]["restored_selection_recovers_list_scene_and_diagnostics_after_token_refresh"] = True
            report["checks"]["molecular_profile_formula_recovers_without_stale_auth_error"] = True
            expect(page.locator(".toast")).to_have_count(0)
            screenshot(page, "actual-completed-desktop", page.get_by_test_id("af3-calculation-panel"))
            page.locator(".studio-grid").scroll_into_view_if_needed()
            screenshot(page, "actual-structure-desktop")
            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_test_id("studio-af3-view-result").evaluate("element => element.scrollIntoView({block: 'center'})")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            screenshot(page, "actual-completed-mobile")
            page.set_viewport_size({"width": 1440, "height": 1080})
            page.wait_for_timeout(500)
            before = state["status_requests"]
            page.evaluate("window.dispatchEvent(new Event('online')); window.dispatchEvent(new Event('online')); document.dispatchEvent(new Event('visibilitychange'))")
            deadline = time.monotonic() + 5
            while state["status_requests"] == before and time.monotonic() < deadline:
                page.wait_for_timeout(25)
            page.wait_for_timeout(350)
            assert state["status_requests"] == before + 1, state
            report["checks"]["online_and_visible_events_coalesce_into_one_immediate_status_read"] = True
            release_held()
            context.close()

            context, page, state = session("hung_log")
            started = time.monotonic()
            login(page)
            expect(page.get_by_test_id("studio-af3-view-result")).to_be_visible(timeout=12000)
            elapsed = time.monotonic() - started
            assert elapsed < 15 and state["held_log_requests"] > 0
            completed_view(page)
            report["checks"]["completed_structure_independent_of_hung_log_replay"] = {"result_button_seconds": round(elapsed, 2), **state}
            release_held()
            context.close()

            context, page, state = session("hung_scene")
            login(page)
            expect(page.get_by_test_id("studio-af3-job")).to_have_attribute("data-job-status", "completed")
            unavailable = page.get_by_test_id("studio-structure-unavailable")
            expect(unavailable).to_contain_text("구조 조회 응답 시간이 초과", timeout=35000)
            expect(page.get_by_test_id("selected-structure-context")).to_have_attribute("aria-busy", "false")
            expect(page.get_by_test_id("structure-retry")).to_be_enabled()
            assert state["held_scene_requests"] > 0
            unavailable.scroll_into_view_if_needed()
            screenshot(page, "replay-scene-timeout")
            state["mode"] = None
            release_held()
            login(page)
            completed_view(page)
            expect(page.get_by_test_id("studio-structure-unavailable")).to_have_count(0)
            report["checks"]["scene_timeout_releases_loading_and_connection_refresh_recovers_actual_structure"] = state
            release_held()
            context.close()
            assert not report["javascript_errors"], report["javascript_errors"]
            assert not report["blocked_mutations"], report["blocked_mutations"]
            after_jobs = {job["id"]: job["status"] for job in get("/api/jobs")}
            report["observed_external_job_changes"] = [{"id": job_id, "before": before_jobs.get(job_id), "after": after_jobs.get(job_id)} for job_id in sorted(before_jobs.keys() | after_jobs.keys()) if before_jobs.get(job_id) != after_jobs.get(job_id)]
            report["checks"]["test_sent_no_af3_prepare_execute_or_job_mutations"] = True
            report["status"] = "passed"
        except Exception as problem:
            report["status"] = "failed"
            report["error"] = f"{type(problem).__name__}: {problem}"
            if page and not page.is_closed():
                screenshot(page, "failure")
            raise
        finally:
            REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            release_held()
            browser.close()
    print(json.dumps({"status": report["status"], "checks": report["checks"], "report": str(REPORT)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
