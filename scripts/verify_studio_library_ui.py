"""Check the live discovery-backed studio after Browser discovery is unavailable.

Uses an isolated system-Chrome profile. No AF3/QPU/analysis job is submitted.
The only permitted POSTs compute local descriptors or resolve stored structures.
"""
from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:9018"
OUT = ROOT / "docs" / "images"
REPORT = ROOT / "docs" / "single-compound-flow-verification.json"


def main():
    token = (ROOT / "runtime" / "access-token.txt").read_text().strip()
    opener = build_opener(ProxyHandler({}))

    def get(path):
        with opener.open(Request(BASE + path, headers={"Authorization": "Bearer " + token}), timeout=30) as response:
            return json.load(response)

    before_jobs = {job["id"] for job in get("/api/jobs")}
    total = get("/api/discovery/summary")["catalog"]["compound_count"]
    report = {"checked_at": datetime.now(UTC).isoformat(), "database_total": total,
              "javascript_errors": [], "blocked_mutations": [], "checks": {}, "screenshots": []}
    OUT.mkdir(parents=True, exist_ok=True)
    expect.set_options(timeout=30000)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=shutil.which("google-chrome"), headless=True,
                                             args=["--no-sandbox", "--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": 1440, "height": 1080}, reduced_motion="reduce")
        page = context.new_page()
        page.on("pageerror", lambda error: report["javascript_errors"].append(str(error)))

        def guard(route):
            request = route.request
            path = urlparse(request.url).path
            if urlparse(request.url).netloc != urlparse(BASE).netloc:
                route.abort()
                return
            if request.method not in {"GET", "HEAD", "OPTIONS"} and path not in {
                "/api/molecular/resolve", "/api/molecules/describe", "/api/molecules/svg", "/api/compare/structures",
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

        def screenshot(name, locator=None):
            path = OUT / f"single-compound-{name}.png"
            if locator is None:
                page.screenshot(path=str(path), animations="disabled")
            else:
                locator.screenshot(path=str(path), animations="disabled")
            report["screenshots"].append(str(path.relative_to(ROOT)))

        try:
            page.goto(BASE + "/#alphafold", wait_until="domcontentloaded")
            library = page.locator(".compound-library")
            rows = library.locator(".studio-compound-list .compound-row")
            expect(library.get_by_role("alert")).to_contain_text("API authentication required")
            login()
            expect(page.get_by_test_id("studio-database-total")).to_contain_text(f"{total:,}")
            expect(rows).to_have_count(30)
            expect(library.locator(".selection-check")).to_have_count(0)
            expect(library.locator(".studio-compound-selected")).to_have_count(0)
            expect(page.locator(".candidate-section")).to_have_count(0)
            report["checks"]["single_compound_primary_has_no_comparison_inputs"] = True

            first_ids = set(rows.evaluate_all("rows => rows.map(row => row.dataset.compoundId)"))
            library.get_by_role("button", name="다음 성분 페이지").click()
            expect(library.locator(".studio-compound-pagination")).to_contain_text("31–60")
            next_ids = set(rows.evaluate_all("rows => rows.map(row => row.dataset.compoundId)"))
            assert len(next_ids) == 30 and not first_ids.intersection(next_ids)
            report["checks"]["bounded_distinct_pages"] = True

            search = library.get_by_role("textbox", name="라이브러리 성분 검색")
            search.fill("황금")
            expect(library.locator(".studio-compound-resolution")).to_contain_text("Scutellaria baicalensis")
            expect(rows).to_have_count(30)
            report["checks"]["korean_herb_search"] = library.locator(".studio-compound-results-label").inner_text()
            library.get_by_role("combobox", name="성분 데이터베이스 출처").select_option("lotus-2026-04")
            expected = get("/api/discovery/compounds?search=%ED%99%A9%EA%B8%88&source=lotus-2026-04&limit=30")
            expect(library.locator(".studio-compound-results-label")).to_contain_text(f"{expected['total']:,}개")
            report["checks"]["source_and_herb_filter"] = expected["total"]
            search.fill("aspirin")
            library.get_by_role("button", name="기존 약물", exact=True).click()
            expect(rows).to_have_count(1)
            expect(rows.first.locator("strong")).to_have_text("Aspirin")
            aspirin = get("/api/discovery/compounds?search=aspirin&kind=drug&limit=30")["items"][0]
            rows.first.locator(".compound-main").click()
            expect(rows.first.locator(".compound-main")).to_have_attribute("aria-pressed", "true")
            expect(page.get_by_test_id("alphafold-selected-compound")).to_have_text("Aspirin")
            expect(page.get_by_test_id("single-compound-analysis")).to_have_attribute("data-compound-id", f"discovery_{aspirin['id']}")
            expect(page.get_by_test_id("single-compound-analysis").locator(".formula-display")).to_contain_text("MOLECULAR FORMULA")
            expect(page.get_by_test_id("af3-calculation-heading")).to_contain_text("Aspirin")
            expect(page.get_by_test_id("molecule-canvas")).to_be_visible(timeout=60000)
            expect(page.get_by_test_id("selected-structure-context")).to_have_attribute("data-structure-mode", "alphafold3_prediction")
            report["checks"]["single_compound_descriptors_and_stored_af3_structure"] = aspirin["id"]
            target = page.get_by_test_id("alphafold-target-input")
            target.fill("P23219")
            expect(page.get_by_test_id("alphafold-open-calculation")).to_be_disabled()
            target.fill("P35354")
            page.get_by_test_id("alphafold-open-calculation").click()
            expect(page.get_by_test_id("af3-calculation-heading")).to_be_focused()
            expect(page.get_by_test_id("af3-calculation-heading")).to_contain_text("Aspirin")
            report["checks"]["af3_action_uses_single_selected_compound_and_applied_target"] = True
            expect(page.locator(".toast")).to_have_count(0)
            page.evaluate("window.scrollTo(0,0)")
            screenshot("alphafold-desktop")
            screenshot("alphafold-library", library)
            page.locator(".studio-grid").scroll_into_view_if_needed()
            screenshot("af3-structure")

            page.locator('nav [data-workspace="comparison"]').click()
            expect(page.get_by_test_id("comparison-workspace")).to_be_visible()
            expect(page.get_by_test_id("comparison-input")).to_have_count(0)
            expect(page.get_by_test_id("compare-structures")).to_be_disabled()
            library.get_by_role("button", name="내 목록", exact=False).click()
            for compound_id in ("quercetin", "baicalein"):
                row = library.locator(f'.compound-row[data-compound-id="{compound_id}"]')
                row.locator(".selection-check").click()
                expect(row.locator(".selection-check")).to_have_attribute("aria-pressed", "true")
            expect(page.get_by_test_id("comparison-input")).to_have_count(2)
            with page.expect_response(lambda response: urlparse(response.url).path == "/api/compare/structures") as event:
                page.get_by_test_id("compare-structures").click()
            response = event.value
            assert response.status == 200
            comparison = response.json()
            assert len(comparison) == 1
            result_rows = page.get_by_test_id("comparison-result-row")
            expect(result_rows).to_have_count(1)
            expect(result_rows.first).to_contain_text(f"{comparison[0]['tanimoto']:.3f}")
            report["checks"]["same_category_comparison"] = comparison[0]
            login()
            expect(page.get_by_test_id("comparison-input")).to_have_count(2)
            expect(result_rows).to_have_count(1)
            report["checks"]["comparison_survives_catalog_refresh"] = True
            row = library.locator('.compound-row[data-compound-id="quercetin"]')
            row.locator(".selection-check").click()
            expect(result_rows).to_have_count(0)
            expect(page.get_by_test_id("comparison-results")).to_contain_text("비교할 성분이 변경되었습니다")
            row.locator(".selection-check").click()
            expect(result_rows).to_have_count(1)
            report["checks"]["stale_comparison_results_hidden"] = True
            expect(page.locator(".comparison-advanced > summary")).to_be_visible()
            expect(page.locator(".candidate-section")).to_be_hidden()
            report["checks"]["candidate_design_in_separate_optional_section"] = True
            expect(page.locator(".toast")).to_have_count(0)
            page.evaluate("window.scrollTo(0,0)")
            screenshot("comparison-desktop")
            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_test_id("comparison-results").scroll_into_view_if_needed()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            screenshot("comparison-mobile")
            page.locator('nav [data-workspace="alphafold"]').click()
            expect(page.get_by_test_id("comparison-workspace")).to_have_count(0)
            expect(page.get_by_test_id("alphafold-selected-compound")).to_have_text("Aspirin")
            expect(library.locator(".selection-check")).to_have_count(0)
            expect(page.locator(".candidate-section")).to_have_count(0)
            library.scroll_into_view_if_needed()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            screenshot("alphafold-mobile")
            report["checks"]["primary_selection_independent_of_comparison"] = True
            report["checks"]["mobile_no_horizontal_overflow"] = True
            assert not report["javascript_errors"], report["javascript_errors"]
            assert not report["blocked_mutations"], report["blocked_mutations"]
            assert before_jobs == {job["id"] for job in get("/api/jobs")}
            report["checks"]["no_jobs_created"] = True
            report["status"] = "passed"
        except Exception as error:
            report["status"] = "failed"
            report["error"] = f"{type(error).__name__}: {error}"
            screenshot("failure")
            raise
        finally:
            REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            browser.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
