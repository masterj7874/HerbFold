"""Exercise actual local pipeline UI and WebGL in an isolated browser.

Use only after Browser skill discovery/troubleshooting establishes no connected
browser is available. Start a loopback server with an isolated data directory.
The inhibition inputs below are SYNTHETIC arithmetic controls, never research
measurements. No AF3, LLM or QPU jobs are submitted.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse


def verify(args, report):
    from playwright.sync_api import expect, sync_playwright

    expect.set_options(timeout=60000)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=shutil.which("google-chrome"), headless=True,
            args=["--no-sandbox", "--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": 1500, "height": 1040}, reduced_motion="reduce")
        page = context.new_page()
        page.set_default_timeout(60000)
        page.on("pageerror", lambda error: report["javascript_errors"].append(str(error)))
        allowed_posts = {"/api/molecules/describe", "/api/molecules/svg", "/api/molecular/conformer",
                         "/api/molecular/sdf", "/api/molecular/resolve", "/api/design-pipeline/runs",
                         "/api/design-pipeline/combination-assay"}

        def guard(route):
            request = route.request
            path = urlparse(request.url).path
            if request.method == "POST" and path not in allowed_posts and not (
                    path.startswith("/api/design-pipeline/runs/") and path.endswith("/cancel")):
                report["blocked_mutations"].append(path)
                route.abort()
            else:
                route.continue_()

        context.route("**/api/**", guard)

        def shot(name, locator=None):
            path = args.output / f"{name}.png"
            (locator or page).screenshot(path=str(path), animations="disabled")
            report["screenshots"][name] = str(path)

        page.goto(args.url + "/#design-pipeline", wait_until="domcontentloaded")
        panel = page.get_by_test_id("drug-design-pipeline")
        expect(panel).to_be_visible()
        expect(page.get_by_role("heading", name="신약 설계 파이프라인", exact=True)).to_be_visible()
        expect(panel.get_by_role("button", name="퀘르세틴", exact=True)).to_be_visible()
        shot("design-pipeline-desktop")
        panel.get_by_role("button", name="퀘르세틴", exact=True).click()
        panel.get_by_role("button", name="약물 비교군", exact=True).click()
        panel.get_by_role("button", name="아스피린", exact=True).click()
        panel.get_by_label("최대 설계 후보 수").select_option("4")
        panel.get_by_label("설계 실행 이름").fill("UI verification — actual BRICS")
        with page.expect_response(lambda response: response.url.endswith("/api/design-pipeline/runs")
                                  and response.request.method == "POST") as submission:
            page.get_by_test_id("design-run-start").click()
        created = submission.value.json()
        report["hybrid_run"] = created["id"]
        results = page.get_by_test_id("design-results")
        expect(results.locator(".ddp-result-count")).to_have_text("4")
        expect(results.locator(".ddp-structure-preview img")).to_be_visible()
        results.get_by_role("button", name="3D 회전 · 확대", exact=True).click()
        viewer = page.get_by_test_id("design-molecule-viewer")
        expect(viewer.locator("canvas")).to_be_visible()
        expect(viewer.locator(".molecular-viewer")).to_be_visible()
        viewer.scroll_into_view_if_needed()
        page.wait_for_function("""() => {
            const c = document.querySelector('[data-testid="design-molecule-viewer"] canvas');
            return c && c.clientWidth > 500 && c.clientHeight > 250;
        }""")
        # Real pointer interactions on the rendered Three.js molecular canvas.
        canvas = viewer.locator("canvas")
        box = canvas.bounding_box()
        assert box and box["width"] > 100 and box["height"] > 100
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] / 2 + 90, box["y"] + box["height"] / 2 + 25, steps=12)
        page.mouse.up()
        page.mouse.wheel(0, -220)
        report["webgl_canvas"] = {"width": box["width"], "height": box["height"], "rotation_and_zoom_exercised": True}
        shot("design-pipeline-webgl", viewer)
        results.get_by_role("button", name="2D 구조", exact=True).click()
        with page.expect_download() as saved:
            results.get_by_role("button", name="SDF 저장", exact=True).click()
        sdf = args.output / "actual-hybrid.sdf"
        saved.value.save_as(str(sdf))
        assert "M  END" in sdf.read_text() and "$$$$" in sdf.read_text()
        with page.expect_download() as saved:
            results.get_by_role("button", name="전체 결과 JSON", exact=True).click()
        artifact = args.output / "actual-hybrid-run.json"
        saved.value.save_as(str(artifact))
        run = json.loads(artifact.read_text())
        assert run["status"] == "completed" and len(run["result"]["candidates"]) == 4
        assert all(candidate["provenance"]["both_parent_fragments_verified"] for candidate in run["result"]["candidates"])
        report["actual_hybrid_count"] = 4
        page.get_by_test_id("design-mode-combination").click()
        panel.get_by_label("설계 실행 이름").fill("UI verification — separate constituents")
        page.get_by_test_id("design-run-start").click()
        expect(results.locator(".ddp-result-count")).to_have_text("1")
        expect(results.get_by_role("group", name="조합 내 확인할 성분")).to_be_visible()
        shot("design-pipeline-combination", results)
        assay = page.get_by_test_id("combination-assay-panel")
        assay.locator("summary").click()
        assay.get_by_label("성분 A", exact=True).fill("SYNTHETIC TEST A")
        assay.get_by_label("성분 B", exact=True).fill("SYNTHETIC TEST B")
        assay.get_by_label("실험 조건", exact=True).fill("Synthetic arithmetic control, not biological observations")
        assay.get_by_label("원자료 출처", exact=True).fill("Isolated UI test fixture")
        assay.get_by_role("textbox", name="측정값 붙여넣기", exact=False).fill("1,2,20,30,50\n0,0,0,0,0")
        assay.get_by_role("checkbox").check()
        assay.get_by_role("button", name="기준 모델과 비교", exact=True).click()
        expect(assay.locator(".ca-result")).to_be_visible()
        expect(assay.locator("tbody tr").first).to_contain_text("+6.00")
        expect(assay.locator("tbody tr").first).to_contain_text("+20.00")
        expect(assay.locator("tbody tr").nth(1)).to_contain_text("0.00%")
        report["synthetic_assay_arithmetic"] = "Bliss +6 pp; HSA +20 pp; valid zero retained"
        page.get_by_test_id("design-mode-transform").click()
        panel.get_by_label("설계 실행 이름").fill("UI verification — phenolic transformation")
        page.get_by_test_id("design-run-start").click()
        expect(results.locator(".ddp-result-count")).to_have_text("4")
        expect(results.locator(".ddp-structure-preview img")).to_be_visible()
        shot("design-pipeline-transform", results)
        page.reload(wait_until="domcontentloaded")
        page.get_by_role("button", name="실행 기록", exact=False).click()
        page.get_by_role("button", name="UI verification — phenolic transformation", exact=False).first.click()
        expect(results.locator(".ddp-result-count")).to_have_text("4")
        report["history_restores_results_after_reload"] = True
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("window.scrollTo(0,0)")
        shot("design-pipeline-mobile")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "Page overflows mobile width"
        results.scroll_into_view_if_needed()
        shot("design-pipeline-mobile-result")
        report["mobile_width"] = 390
        results.get_by_role("button", name="AF3 스튜디오에서 검토", exact=False).click()
        expect(page.get_by_test_id("workspace-alphafold")).to_be_visible()
        report["af3_studio_handoff"] = True
        assert not report["javascript_errors"], report["javascript_errors"]
        assert not report["blocked_mutations"], report["blocked_mutations"]
        browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:9020")
    parser.add_argument("--output", type=Path, default=Path("tmp/design_pipeline_20260917/ui"))
    args = parser.parse_args()
    if urlparse(args.url).hostname not in {"127.0.0.1", "localhost"}:
        parser.error("Use an isolated loopback server")
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"started_utc": datetime.now(UTC).isoformat(), "javascript_errors": [], "screenshots": {},
              "blocked_mutations": [], "status": "running", "url": args.url}
    try:
        verify(args, report)
        report["status"] = "passed"
    except Exception as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        report["finished_utc"] = datetime.now(UTC).isoformat()
        (args.output / "verification.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
