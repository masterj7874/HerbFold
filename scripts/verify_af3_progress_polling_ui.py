"""Live AF3 interruption UI and isolated browser response-replay regressions.

Browser discovery is unavailable in this environment; uses isolated system Chrome.
Replays alter browser responses only. No AF3 jobs are prepared or executed.
"""
from __future__ import annotations

import copy
import json
import shutil
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import ProxyHandler, Request, build_opener

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:9018"
OUT = ROOT / "docs" / "images"
REPORT = ROOT / "docs" / "af3-progress-polling-verification.json"
ACTUAL_JOB = "308e4b99d57a432ebfd11c90a67ec02c"


def main():
    token = (ROOT / "runtime" / "access-token.txt").read_text().strip()
    opener = build_opener(ProxyHandler({}))

    def get(path):
        with opener.open(Request(BASE + path, headers={"Authorization": "Bearer " + token}), timeout=30) as response:
            return json.load(response)

    before_jobs = {job["id"]: job["status"] for job in get("/api/jobs")}
    actual = get(f"/api/molecular/predictions/{ACTUAL_JOB}")
    assert actual["job"]["status"] == "interrupted"
    with sqlite3.connect(f"file:{ROOT / 'runtime/discovery/discovery.sqlite3'}?mode=ro", uri=True) as database:
        database.row_factory = sqlite3.Row
        compound = dict(database.execute(
            "SELECT * FROM discovery_compounds WHERE canonical_smiles = ?",
            (actual["requested"]["canonical_smiles"],),
        ).fetchone())
    aspirin = get("/api/discovery/compounds?search=aspirin&kind=drug&limit=30")["items"][0]
    query = urlencode({"smiles": aspirin["canonical_smiles"], "target_accession": "P35354"})
    completed = next(entry for entry in get("/api/molecular/predictions?" + query)["items"]
                     if entry["job"]["status"] == "completed" and entry.get("output_validation", {}).get("identity_verified"))
    pending = copy.deepcopy(completed)
    pending["job"]["status"] = "running"
    pending["stage"] = {"name": "inference", "label": "AF3 추론 중", "started_at": datetime.now(UTC).isoformat()}
    pending["output_validation"] = None
    replay_id = completed["job"]["id"]
    report = {
        "checked_at": datetime.now(UTC).isoformat(), "status": "running",
        "actual_job_id": ACTUAL_JOB, "actual_compound": compound,
        "replay_scope": "Isolated browser responses only; a stored Aspirin completed job is shown as pending then its actual completed response. No server job records are changed.",
        "replay_completed_job_id": replay_id, "checks": {}, "screenshots": [],
        "javascript_errors": [], "blocked_mutations": [],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    expect.set_options(timeout=30000)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=shutil.which("google-chrome"), headless=True,
                                             args=["--no-sandbox", "--enable-unsafe-swiftshader"])
        page = None
        held_routes = []

        def release_held_routes():
            while held_routes:
                route = held_routes.pop()
                try:
                    route.abort()
                except Exception:
                    pass  # Its browser request may already have been aborted.

        def close_context(context):
            release_held_routes()
            context.close()

        def session(replay=None):
            context = browser.new_context(viewport={"width": 1440, "height": 1080}, reduced_motion="reduce")
            tab = context.new_page()
            tab.on("pageerror", lambda error: report["javascript_errors"].append(str(error)))
            counters = {"status": 0, "held_logs": 0, "held_status": 0}

            def guard(route):
                request = route.request
                parsed = urlparse(request.url)
                path = parsed.path
                if parsed.netloc != urlparse(BASE).netloc:
                    route.abort()
                    return
                if request.method not in {"GET", "HEAD", "OPTIONS"} and path not in {
                    "/api/molecular/resolve", "/api/molecules/describe", "/api/molecules/svg",
                }:
                    report["blocked_mutations"].append({"method": request.method, "path": path})
                    route.abort()
                    return
                if replay and request.method == "GET":
                    if path == "/api/molecular/predictions" and parse_qs(parsed.query).get("smiles") == [aspirin["canonical_smiles"]]:
                        route.fulfill(json={"items": [pending]})
                        return
                    if path == f"/api/molecular/predictions/{replay_id}/log" and replay == "hung_log":
                        counters["held_logs"] += 1
                        held_routes.append(route)
                        return  # Intentionally leave only this browser request pending.
                    if path == f"/api/molecular/predictions/{replay_id}":
                        counters["status"] += 1
                        if replay == "selection_abort" or (replay == "hung_status" and counters["status"] == 1):
                            counters["held_status"] += 1
                            held_routes.append(route)
                            return
                        route.fulfill(json=pending if replay == "hung_log" and counters["status"] == 1 else completed)
                        return
                route.continue_()

            context.route("**/*", guard)
            tab.goto(BASE + "/#alphafold", wait_until="domcontentloaded")
            tab.locator('.sidebar button[title="연산 엔진 설정"]').click()
            dialog = tab.get_by_role("dialog", name="연산 엔진 설정")
            dialog.locator('input[type="password"]').fill(token)
            dialog.get_by_role("button", name="연결 상태 새로 고침", exact=True).click()
            dialog.get_by_role("button", name="엔진 설정 닫기").click()
            expect(tab.locator(".compound-library .studio-compound-list .compound-row")).to_have_count(30)
            if replay:
                tab.evaluate("""() => {
                  const label = document.createElement('div');
                  label.textContent = 'REPLAY · 브라우저 응답 재현 검증 · 서버 작업 변경 없음';
                  label.style.cssText = 'position:fixed;top:0;right:0;z-index:9999;padding:6px 14px;background:#713f12;color:#fff;font:13px sans-serif';
                  document.body.appendChild(label);
                }""")
            return context, tab, counters

        def select_actual(tab):
            library = tab.locator(".compound-library")
            library.get_by_role("textbox", name="라이브러리 성분 검색").fill("")
            library.get_by_role("button", name="전체", exact=True).click()
            row = library.locator(f'.compound-row[data-compound-id="discovery_{compound["id"]}"]')
            expect(row).to_be_visible()
            row.locator(".compound-main").click()
            expect(tab.get_by_test_id("studio-af3-job")).to_have_attribute("data-job-id", ACTUAL_JOB)

        def select_aspirin(tab):
            library = tab.locator(".compound-library")
            library.get_by_role("textbox", name="라이브러리 성분 검색").fill("aspirin")
            library.get_by_role("button", name="기존 약물", exact=True).click()
            row = library.locator(f'.compound-row[data-compound-id="discovery_{aspirin["id"]}"]')
            expect(row).to_be_visible()
            row.locator(".compound-main").click()
            expect(tab.get_by_test_id("studio-af3-job")).to_have_attribute("data-job-id", replay_id)

        def screenshot(tab, name, locator=None):
            path = OUT / f"af3-progress-{name}.png"
            (locator or tab).screenshot(path=str(path), animations="disabled")
            report["screenshots"].append(str(path.relative_to(ROOT)))

        try:
            context, page, _ = session()
            select_actual(page)
            job = page.get_by_test_id("studio-af3-job")
            expect(job).to_have_attribute("data-job-status", "interrupted")
            expect(job.locator(".afc-status .spin")).to_have_count(0)
            expect(page.get_by_test_id("studio-af3-job-error")).to_contain_text("GPU 메모리")
            expect(page.get_by_test_id("studio-af3-retry")).to_be_enabled()
            expect(page.get_by_test_id("studio-af3-view-result")).to_have_count(0)
            job.scroll_into_view_if_needed()
            expect(page.locator(".toast")).to_have_count(0)
            screenshot(page, "actual-interrupted-desktop", page.get_by_test_id("af3-calculation-panel"))
            page.set_viewport_size({"width": 390, "height": 844})
            job.scroll_into_view_if_needed()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.get_by_test_id("studio-af3-job-error").evaluate("element => element.scrollIntoView({block: 'center'})")
            screenshot(page, "actual-interrupted-mobile")
            report["checks"]["actual_interruption_has_no_running_spinner_and_retains_msa"] = {
                "status": actual["job"]["status"], "error": actual["job"]["error"],
                "msa_features_status": actual.get("msa_features", {}).get("status"),
                "retry_enabled": True, "mobile_no_horizontal_overflow": True,
            }
            close_context(context)

            context, page, counters = session("hung_log")
            started = time.monotonic()
            select_aspirin(page)
            expect(page.get_by_test_id("studio-af3-job")).to_have_attribute("data-job-status", "running")
            expect(page.get_by_test_id("studio-af3-view-result")).to_be_visible(timeout=12000)
            elapsed = time.monotonic() - started
            assert elapsed < 15 and counters["held_logs"] > 0
            expect(page.get_by_test_id("studio-af3-job")).to_have_attribute("data-job-status", "completed")
            expect(page.get_by_test_id("molecule-canvas")).to_be_visible(timeout=60000)
            expect(page.get_by_test_id("selected-structure-context")).to_have_attribute("data-structure-mode", "alphafold3_prediction")
            page.get_by_test_id("studio-af3-job").scroll_into_view_if_needed()
            screenshot(page, "replay-completed-with-hung-log")
            report["checks"]["replay_completed_status_and_structure_before_log_deadline"] = {"result_button_seconds": round(elapsed, 2), **counters}
            close_context(context)

            context, page, counters = session("hung_status")
            select_aspirin(page)
            expect(page.get_by_test_id("studio-af3-status-error")).to_be_visible(timeout=20000)
            expect(page.get_by_test_id("studio-af3-status-error")).to_contain_text("자동으로 연결을 다시 확인")
            expect(page.get_by_test_id("studio-af3-job").locator(".afc-status .spin")).to_have_count(0)
            page.get_by_test_id("studio-af3-status-error").scroll_into_view_if_needed()
            screenshot(page, "replay-status-timeout")
            expect(page.get_by_test_id("studio-af3-view-result")).to_be_visible(timeout=12000)
            expect(page.get_by_test_id("studio-af3-status-error")).to_have_count(0)
            report["checks"]["replay_status_timeout_auto_recovers"] = dict(counters)
            close_context(context)

            context, page, counters = session("selection_abort")
            select_aspirin(page)
            deadline = time.monotonic() + 5
            while counters["held_status"] == 0 and time.monotonic() < deadline:
                page.wait_for_timeout(25)
            assert counters["held_status"] >= 1
            select_actual(page)
            # Structure diagnostics can independently read the same job while
            # it is selected. None of those readers may retry after switching.
            old_requests_at_switch = counters["status"]
            page.wait_for_timeout(16000)
            expect(page.get_by_test_id("studio-af3-job")).to_have_attribute("data-job-id", ACTUAL_JOB)
            expect(page.get_by_test_id("studio-af3-job")).to_have_attribute("data-job-status", "interrupted")
            expect(page.get_by_test_id("studio-af3-status-error")).to_have_count(0)
            assert counters["status"] == old_requests_at_switch
            report["checks"]["replay_selection_change_aborts_old_status_without_retry_or_error"] = dict(counters)
            close_context(context)
            assert not report["javascript_errors"], report["javascript_errors"]
            assert not report["blocked_mutations"], report["blocked_mutations"]
            assert before_jobs == {job["id"]: job["status"] for job in get("/api/jobs")}
            report["checks"]["no_jobs_created_or_status_changed"] = True
            report["status"] = "passed"
        except Exception as error:
            report["status"] = "failed"
            report["error"] = f"{type(error).__name__}: {error}"
            if page and not page.is_closed():
                screenshot(page, "failure")
            raise
        finally:
            REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            release_held_routes()
            browser.close()
    print(json.dumps({"status": report["status"], "checks": report["checks"], "report": str(REPORT)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
