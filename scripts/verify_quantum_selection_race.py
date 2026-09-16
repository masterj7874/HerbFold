"""Read-only regression using two genuine stored analysis responses.

Run system Chrome only after the Browser skill's connection troubleshooting
confirms no browser is available. No fixture data or execution POST is allowed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse


async def audit(args, report):
    from playwright.async_api import async_playwright, expect

    receipt = json.loads(args.receipt.read_text())
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=shutil.which("google-chrome"), headless=True,
            args=["--no-sandbox", "--enable-unsafe-swiftshader"],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1080}, reduced_motion="reduce",
        )
        page = await context.new_page()
        page.set_default_timeout(60000)
        page.on("pageerror", lambda error: report["javascript_errors"].append(str(error)))

        async def guard(route):
            request = route.request
            path = urlparse(request.url).path
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                if request.method == "POST" and path in {
                    "/api/molecular/conformer", "/api/molecules/describe",
                    "/api/molecules/svg", "/api/molecular/resolve",
                }:
                    report["read_style_posts"].append(path)
                else:
                    report["blocked_mutations"].append({"method": request.method, "path": path})
                    await route.abort()
                    return
            await route.continue_()

        await context.route("**/api/**", guard)
        release = asyncio.Event()
        try:
            response = await context.request.get(args.url + "/api/jobs/" + receipt["store_job_id"])
            assert response.ok
            job = await response.json()
            source_id = job["payload"]["source_analysis_id"]
            assert job["status"] == "completed" and job["result"]["hardware_executed"] is True
            response = await context.request.get(args.url + "/api/analyses")
            assert response.ok
            rows = (await response.json())["analyses"]
            other = next(row for row in rows if row["id"] != source_id and row["status"] == "completed")
            source_index = next(i for i, row in enumerate(rows) if row["id"] == source_id)
            other_index = next(i for i, row in enumerate(rows) if row["id"] == other["id"])
            await page.goto(args.url, wait_until="domcontentloaded")
            await page.get_by_role("button", name="에이전트 분석", exact=True).click()
            await page.locator(".analysis-history-item").nth(source_index).click()
            workspace = page.get_by_test_id("quantum-workspace")
            await expect(workspace).to_have_attribute("data-source-analysis", source_id)
            await expect(page.get_by_test_id("quantum-linked-job")).to_have_attribute("data-job-id", job["id"])
            ready, finished = asyncio.Event(), asyncio.Event()
            held = {}

            async def delay_genuine_response(route):
                response = await route.fetch()
                actual = await response.json()
                assert actual["id"] == other["id"] and response.ok
                held["actual_analysis_id"] = actual["id"]
                held["actual_status"] = actual["status"]
                ready.set()
                await release.wait()
                await route.fulfill(response=response)
                held["delivered_after_new_selection"] = True
                finished.set()

            await page.route(args.url + "/api/analyses/" + other["id"], delay_genuine_response)
            await page.locator(".analysis-history-item").nth(other_index).click()
            await asyncio.wait_for(ready.wait(), 60)
            async with page.expect_response(lambda r: urlparse(r.url).path == "/api/analyses/" + source_id) as event:
                await page.locator(".analysis-history-item").nth(source_index).click()
            await (await event.value).body()
            await expect(workspace).to_have_attribute("data-source-analysis", source_id)
            async with page.expect_response(lambda r: urlparse(r.url).path == "/api/analyses/" + other["id"]) as event:
                release.set()
            await asyncio.wait_for(finished.wait(), 60)
            await (await event.value).body()
            await page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            await expect(workspace).to_have_attribute("data-source-analysis", source_id)
            await expect(page.get_by_test_id("quantum-linked-job")).to_have_attribute("data-job-id", job["id"])
            report.update(source_analysis_id=source_id, linked_job_id=job["id"], delayed=held,
                          selection_preserved=True, synthetic_data_used=False)
            await page.set_viewport_size({"width": 360, "height": 800})
            projected = page.get_by_test_id("quantum-linked-job").get_by_test_id("quantum-result")
            report["mobile_layout"] = await workspace.evaluate("""element => {
              const parents=[];
              for(let node=element;node&&node!==document.body;node=node.parentElement){
                const style=getComputedStyle(node),rect=node.getBoundingClientRect();
                parents.push({className:node.className,width:rect.width,
                  paddingLeft:style.paddingLeft,paddingRight:style.paddingRight,
                  marginLeft:style.marginLeft});
              }
              const panels=[...element.querySelectorAll('[data-testid="quantum-result"]')]
                .map(node=>({method:node.dataset.method,top:node.getBoundingClientRect().top,
                  bottom:node.getBoundingClientRect().bottom,width:node.clientWidth}));
              return {viewport:innerWidth,documentOverflow:document.documentElement.scrollWidth>innerWidth,
                ancestors:parents,panels,verticalOrder:panels[0].bottom<=panels[1].top};
            }""")
            assert report["mobile_layout"]["verticalOrder"] and not report["mobile_layout"]["documentOverflow"]
            await projected.evaluate("element => element.scrollIntoView({block:'start'})")
            image = Path("docs/images/quantum-projected-measured-mobile360-viewport.png")
            await page.screenshot(path=str(image), animations="disabled")
            report["mobile_viewport_screenshot"] = str(image)
            assert not report["javascript_errors"] and not report["blocked_mutations"]
        finally:
            release.set()
            await browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8873")
    parser.add_argument("--receipt", type=Path, default=Path("docs/quantum-projected-verification.json"))
    parser.add_argument("--output", type=Path, default=Path("docs/quantum-selection-race-verification.json"))
    args = parser.parse_args()
    report = {"created_at": datetime.now(UTC).isoformat(), "passed": False,
              "javascript_errors": [], "blocked_mutations": [], "read_style_posts": [],
              "qpu_submissions": 0}
    try:
        asyncio.run(audit(args, report))
        report["passed"] = True
    except Exception as error:
        report["error"] = str(error)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "output": str(args.output), "error": report.get("error")}))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
