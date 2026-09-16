"""Verify real UniProt registration and isolated browser failure/late-response replay.

Uses system Chrome only after unavailable in-app Browser discovery. Registers the
public P00533 target, restores P35354, and never prepares or executes an AF3 job.
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:9018"
REPORT = ROOT / "docs" / "target-registration-verification.json"
OUT = ROOT / "docs" / "images"


def main():
    token = (ROOT / "runtime" / "access-token.txt").read_text().strip()
    opener = build_opener(ProxyHandler({}))

    def get(path):
        with opener.open(Request(BASE + path, headers={"Authorization": "Bearer " + token}), timeout=30) as response:
            return json.load(response)

    before_jobs = {job["id"]: job["status"] for job in get("/api/jobs")}
    initially_registered = {record["accession"] for record in get("/api/molecular/targets")["items"]}
    assert get("/api/molecular/targets/P35354")["accession"] == "P35354"
    report = {"checked_at": datetime.now(UTC).isoformat(), "checks": {}, "screenshots": [],
              "javascript_errors": [], "blocked_mutations": [], "registration_requests": [], "p00533_registered_before_test": "P00533" in initially_registered}
    OUT.mkdir(parents=True, exist_ok=True)
    expect.set_options(timeout=30000)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=shutil.which("google-chrome"), headless=True,
                                             args=["--no-sandbox", "--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": 1440, "height": 1080}, reduced_motion="reduce")
        page = context.new_page()
        page.on("pageerror", lambda error: report["javascript_errors"].append(str(error)))
        held_routes = []
        replay = {"mode": None}

        def release_held(record=None):
            while held_routes:
                route = held_routes.pop()
                try:
                    if record:
                        route.fulfill(json=record)
                    else:
                        route.abort()
                except Exception:
                    pass  # The browser may already have cancelled this request.

        def guard(route):
            request = route.request
            parsed = urlparse(request.url)
            path = parsed.path
            if parsed.netloc != urlparse(BASE).netloc:
                route.abort()
                return
            if path == "/api/molecular/targets/register" and request.method == "POST":
                accession = request.post_data_json.get("accession")
                report["registration_requests"].append({"accession": accession, "replay": replay["mode"]})
                if replay["mode"] == "missing" and accession == "P99999":
                    route.fulfill(status=404, json={"detail": "REPLAY: UniProt에서 요청한 단백질을 찾지 못했습니다."})
                    return
                if replay["mode"] in {"late", "timeout"} and accession == "P00533":
                    held_routes.append(route)
                    return
                if accession not in {"P35354", "P00533"}:
                    report["blocked_mutations"].append({"method": request.method, "path": path, "accession": accession})
                    route.abort()
                    return
            elif request.method not in {"GET", "HEAD", "OPTIONS"} and path not in {
                "/api/molecular/resolve", "/api/molecules/describe", "/api/molecules/svg",
            }:
                report["blocked_mutations"].append({"method": request.method, "path": path})
                route.abort()
                return
            route.continue_()

        context.route("**/*", guard)

        def login():
            page.locator('.sidebar button[title="연산 엔진 설정"]').click()
            dialog = page.get_by_role("dialog", name="연산 엔진 설정")
            dialog.locator('input[type="password"]').fill(token)
            dialog.get_by_role("button", name="연결 상태 새로 고침", exact=True).click()
            dialog.get_by_role("button", name="엔진 설정 닫기").click()

        def details(accession):
            locator = page.get_by_test_id("alphafold-target-details")
            expect(locator).to_have_attribute("data-target-accession", accession)
            return locator

        def apply(accession):
            page.get_by_test_id("alphafold-target-input").fill(accession)
            page.get_by_test_id("alphafold-target-apply").click()

        def screenshot(name, locator=None):
            path = OUT / f"target-registration-{name}.png"
            (locator or page).screenshot(path=str(path), animations="disabled")
            report["screenshots"].append(str(path.relative_to(ROOT)))

        def mark_replay():
            page.evaluate("""() => {
              const label = document.createElement('div');
              label.textContent = 'REPLAY · 조회 실패·지연 재현 · 서버 표적 변경 없음';
              label.style.cssText = 'position:fixed;top:0;right:0;z-index:9999;padding:6px 14px;background:#713f12;color:#fff;font:13px sans-serif';
              document.body.appendChild(label);
            }""")

        try:
            page.goto(BASE + "/#alphafold", wait_until="domcontentloaded")
            login()
            details("P35354")
            expect(page.get_by_test_id("af3-calculation-heading")).to_contain_text("P35354")
            selected_compound = page.get_by_test_id("alphafold-selected-compound").inner_text()
            apply("p00533")
            selected = details("P00533")
            target = get("/api/molecular/targets/P00533")
            expect(selected).to_contain_text(target["name"])
            expect(selected).to_contain_text(target["organism"])
            expect(selected).to_contain_text(f"{target['length']:,} 아미노산")
            expect(selected.get_by_role("link", name="UniProt")).to_have_attribute("href", "https://www.uniprot.org/uniprotkb/P00533/entry")
            expect(page.get_by_test_id("af3-calculation-heading")).to_contain_text("P00533")
            expect(page.get_by_test_id("alphafold-selected-compound")).to_have_text(selected_compound)
            selected.locator("summary").click()
            assert selected.locator("code").inner_text().replace(" ", "").replace("…", "") == target["sequence"][:120]
            expect(page.locator(".toast")).to_have_count(0)
            screenshot("egfr-desktop", page.get_by_test_id("alphafold-studio-overview"))
            page.set_viewport_size({"width": 390, "height": 844})
            selected.evaluate("element => element.scrollIntoView({block: 'center'})")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            screenshot("egfr-mobile")
            report["checks"]["real_egfr_registration_metadata_and_applied_target"] = {
                "accession": target["accession"], "name": target["name"], "gene": target["gene"],
                "organism": target["organism"], "length": target["length"], "sequence_sha256": target["sequence_sha256"],
                "compound_unchanged": True, "mobile_no_horizontal_overflow": True,
            }
            page.set_viewport_size({"width": 1440, "height": 1080})
            page.reload(wait_until="domcontentloaded")
            login()
            details("P00533")
            expect(page.get_by_test_id("af3-calculation-heading")).to_contain_text("P00533")
            expect(page.locator(".sw-target-lookup-error")).to_have_count(0)
            report["checks"]["target_restored_after_reload_and_token_reconnection"] = True
            page.get_by_test_id("alphafold-target-saved").select_option("P35354")
            expect(page.get_by_test_id("alphafold-open-calculation")).to_be_disabled()
            expect(page.get_by_test_id("af3-calculation-heading")).to_contain_text("P00533")
            page.get_by_test_id("alphafold-target-apply").click()
            details("P35354")
            expect(page.get_by_test_id("af3-calculation-heading")).to_contain_text("P35354")
            report["checks"]["saved_target_selection_requires_apply_and_restores_default"] = True

            count = len(report["registration_requests"])
            apply("bad target")
            expect(page.get_by_test_id("alphafold-target-error")).to_contain_text("식별자를 입력")
            assert len(report["registration_requests"]) == count
            expect(page.get_by_test_id("af3-calculation-heading")).to_contain_text("P35354")
            report["checks"]["malformed_input_does_not_register_or_replace_target"] = True
            mark_replay()
            replay["mode"] = "missing"
            apply("P99999")
            expect(page.get_by_test_id("alphafold-target-error")).to_contain_text("현재 표적은 유지")
            expect(page.get_by_test_id("af3-calculation-heading")).to_contain_text("P35354")
            screenshot("replay-missing-target")
            report["checks"]["missing_uniprot_replay_preserves_applied_target"] = True

            replay["mode"] = "late"
            apply("P00533")
            deadline = time.monotonic() + 5
            while not held_routes and time.monotonic() < deadline:
                page.wait_for_timeout(25)
            assert held_routes
            page.get_by_test_id("alphafold-target-input").fill("P35354")
            expect(page.get_by_test_id("alphafold-target-apply")).to_be_enabled()
            release_held(target)
            page.wait_for_timeout(300)
            details("P35354")
            expect(page.get_by_test_id("af3-calculation-heading")).to_contain_text("P35354")
            expect(page.get_by_test_id("alphafold-target-error")).to_have_count(0)
            report["checks"]["late_registration_replay_cannot_overwrite_new_draft_or_applied_target"] = True

            replay["mode"] = "timeout"
            apply("P00533")
            expect(page.get_by_test_id("alphafold-target-error")).to_contain_text("응답이 지연", timeout=35000)
            expect(page.get_by_test_id("alphafold-target-apply")).to_be_enabled()
            expect(page.get_by_test_id("af3-calculation-heading")).to_contain_text("P35354")
            assert sum(item["replay"] == "timeout" for item in report["registration_requests"]) == 1
            release_held()
            page.get_by_test_id("alphafold-target-input").fill("P35354")
            report["checks"]["registration_timeout_replay_releases_busy_without_auto_resubmit"] = True
            assert not report["javascript_errors"], report["javascript_errors"]
            assert not report["blocked_mutations"], report["blocked_mutations"]
            after_jobs = {job["id"]: job["status"] for job in get("/api/jobs")}
            report["observed_external_job_changes"] = [
                {"id": job_id, "before": before_jobs.get(job_id), "after": after_jobs.get(job_id)}
                for job_id in sorted(before_jobs.keys() | after_jobs.keys())
                if before_jobs.get(job_id) != after_jobs.get(job_id)
            ]
            report["checks"]["test_sent_no_af3_prepare_execute_or_job_mutations"] = True
            report["status"] = "passed"
        except Exception as error:
            report["status"] = "failed"
            report["error"] = f"{type(error).__name__}: {error}"
            screenshot("failure")
            raise
        finally:
            REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            release_held()
            browser.close()
    print(json.dumps({"status": report["status"], "checks": report["checks"], "report": str(REPORT)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
