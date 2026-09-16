"""Check repaired AF3 readiness using isolated Chrome after Browser discovery failed.

Reads the real prepared job and displays its enabled execution action. This
check never prepares or submits a prediction and never changes stored jobs.
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:9018"


def main():
    token = (ROOT / "runtime/access-token.txt").read_text().strip()
    repair = json.loads((ROOT / "runtime/af3-readiness-repair-result.json").read_text())
    opener = build_opener(ProxyHandler({}))

    def get(path):
        request = Request(BASE + path, headers={"Authorization": "Bearer " + token})
        with opener.open(request, timeout=30) as response:
            return json.load(response)

    health = get("/api/health")["alphafold"]
    assert health["runnable"] and not health["blockers"], health["blockers"]
    before = {job["id"]: job["status"] for job in get("/api/jobs")}
    report = {"checked_at": datetime.now(UTC).isoformat(), "javascript_errors": [],
              "blocked_mutations": [], "job_id": repair["prepared_job_id"], "screenshots": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=shutil.which("google-chrome"), headless=True,
            args=["--no-sandbox", "--enable-unsafe-swiftshader"],
        )
        context = browser.new_context(viewport={"width": 1440, "height": 1080}, reduced_motion="reduce")
        page = context.new_page()
        page.on("pageerror", lambda error: report["javascript_errors"].append(str(error)))

        def guard(route):
            request = route.request
            parsed = urlparse(request.url)
            if parsed.netloc != urlparse(BASE).netloc:
                route.abort()
            elif request.method not in {"GET", "HEAD", "OPTIONS"} and parsed.path not in {
                "/api/molecular/resolve", "/api/molecules/describe", "/api/molecules/svg",
            }:
                report["blocked_mutations"].append({"method": request.method, "path": parsed.path})
                route.abort()
            else:
                route.continue_()

        context.route("**/*", guard)
        expect.set_options(timeout=30000)
        page.goto(BASE + "/#alphafold", wait_until="domcontentloaded")
        page.locator('.sidebar button[title="연산 엔진 설정"]').click()
        dialog = page.get_by_role("dialog", name="연산 엔진 설정")
        dialog.locator('input[type="password"]').fill(token)
        dialog.get_by_role("button", name="연결 상태 새로 고침", exact=True).click()
        dialog.get_by_role("button", name="엔진 설정 닫기").click()
        library = page.locator(".compound-library")
        expect(page.get_by_test_id("studio-database-total")).to_contain_text("766,417")
        library.get_by_role("textbox", name="라이브러리 성분 검색").fill("abscisic acid")
        row = library.locator('.compound-row[data-compound-id="discovery_15"]')
        row.locator(".compound-main").click()
        panel = page.get_by_test_id("af3-calculation-panel")
        expect(panel).to_have_attribute("aria-busy", "false")
        job = page.get_by_test_id("studio-af3-job")
        expect(job).to_have_attribute("data-job-id", repair["prepared_job_id"])
        expect(job).to_have_attribute("data-job-status", "prepared")
        expect(page.get_by_test_id("studio-af3-execute")).to_be_enabled()
        expect(page.get_by_test_id("studio-af3-blockers")).to_have_count(0)
        for label, viewport in (("desktop", {"width": 1440, "height": 1080}),
                                ("mobile", {"width": 390, "height": 844})):
            page.set_viewport_size(viewport)
            job.scroll_into_view_if_needed()
            expect(page.get_by_test_id("studio-af3-execute")).to_be_enabled()
            path = ROOT / "docs/images" / f"af3-readiness-repaired-{label}.png"
            page.screenshot(path=str(path), animations="disabled")
            report["screenshots"].append(str(path.relative_to(ROOT)))
        browser.close()
    after = {job["id"]: job["status"] for job in get("/api/jobs")}
    assert before == after, "UI verification changed jobs"
    assert not report["javascript_errors"] and not report["blocked_mutations"], report
    report.update(status="passed", current_runtime_ready=True, execution_button_enabled=True,
                  no_blockers_for_prepared_job=True, no_jobs_submitted=True)
    (ROOT / "docs/af3-readiness-repair-ui-verification.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
