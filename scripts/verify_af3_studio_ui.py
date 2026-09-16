"""Historical pre-acquisition check of blocked weights and synthetic CPU workflows.

Requires the earlier test-parameter-blocked server state on :8873, before official
Google weights were acquired/configured, and tests/helpers/af3_studio_fixture_server.py
on :8875. This is not a regression script for the current trained-weight production
configuration; do not restore old weights merely to run it.
Fixture artifacts are explicitly synthetic, kept outside production, and do not
establish AF3 prediction accuracy or trained inference. No GPU jobs are submitted.
"""

import asyncio
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

PRODUCTION = "http://127.0.0.1:8873"
FIXTURE = "http://127.0.0.1:8875"
OUTPUT = Path("docs/af3-studio-ui-verification.json")
IMAGES = Path("docs/images")


async def choose(page, name):
    await page.locator(".compound-main").filter(has_text=name).first.click()
    await page.wait_for_function("document.querySelector('[data-testid=selected-structure-context]')?.getAttribute('aria-busy') === 'false'")


async def panel_ready(page):
    await page.get_by_test_id("af3-calculation-panel").wait_for()
    await page.wait_for_function("document.querySelector('[data-testid=af3-calculation-panel]')?.getAttribute('aria-busy') === 'false'")


async def prepare(page, *, retry=False):
    async with page.expect_response(lambda response: response.url.endswith("/api/molecular/predictions/prepare")) as pending:
        await page.get_by_test_id("studio-af3-retry" if retry else "studio-af3-prepare").click()
    response = await pending.value
    assert response.ok, await response.text()
    data = await response.json()
    await page.wait_for_function("id => document.querySelector('[data-testid=studio-af3-job]')?.dataset.jobId === id", arg=data["job"]["id"])
    return data


async def submit(page):
    async with page.expect_response(lambda response: "/api/molecular/predictions/" in response.url and response.url.endswith("/execute")) as pending:
        await page.get_by_test_id("studio-af3-execute").click()
    response = await pending.value
    assert response.ok, await response.text()
    return await response.json()


async def status(page, expected):
    await page.wait_for_function("expected => document.querySelector('[data-testid=studio-af3-job]')?.dataset.jobStatus === expected", arg=expected, timeout=45000)


async def displayed_job(page, base, expected_id):
    await status(page, "completed")
    response = await page.request.get(f"{base}/api/molecular/predictions/{expected_id}/scene")
    result = await response.json()
    assert result["status"] == "matched", result.get("reason")
    metadata = result["scene"]["metadata"]
    assert metadata["job_id"] == expected_id
    await page.wait_for_function("sha => document.querySelector('[data-testid=molecule-canvas]')?.dataset.structureSha256 === sha", arg=metadata["sha256"], timeout=30000)
    return {"job_id": expected_id, "structure_sha256": metadata["sha256"],
            "selection": metadata["selection"], "source": result["source"]}


async def main():
    IMAGES.mkdir(parents=True, exist_ok=True)
    errors, production_execution_requests = [], []
    production, fixture = {}, {}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=shutil.which("google-chrome"), headless=True,
                                                   args=["--no-sandbox", "--enable-unsafe-swiftshader"])
        context = await browser.new_context(viewport={"width": 1440, "height": 1080}, reduced_motion="reduce")
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def forbid_production_execute(route):
            if route.request.method == "POST":
                production_execution_requests.append(route.request.url)
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/api/molecular/predictions/*/execute", forbid_production_execute)
        await page.goto(PRODUCTION, wait_until="networkidle")
        await page.get_by_test_id("studio-mode-af3").click()
        await panel_ready(page)
        standard = await prepare(page)
        assert standard["job"]["status"] == "blocked"
        assert standard["requested"]["msa_mode"] == "search"
        assert not standard["readiness"]["runnable"]
        assert len(standard["job"]["payload"]["sequences"][0]["protein"]["sequence"]) == 604
        assert "학습된 AF3 가중치 필요" in await page.get_by_test_id("studio-af3-blockers").inner_text()
        assert not await page.get_by_test_id("studio-af3-execute").count()
        production["standard"] = {"job_id": standard["job"]["id"], "status": standard["job"]["status"],
                                    "requested": standard["requested"], "blockers": standard["readiness"]["blockers"]}
        await page.get_by_test_id("af3-calculation-panel").screenshot(path=str(IMAGES / "af3-calculation-blocked-desktop.png"))

        await page.get_by_test_id("studio-af3-mode").select_option("none")
        assert await page.get_by_test_id("studio-af3-prepare").is_disabled()
        await page.get_by_test_id("studio-af3-exploratory-ack").check()
        exploratory = await prepare(page)
        assert exploratory["job"]["status"] == "blocked"
        assert exploratory["job"]["id"] != standard["job"]["id"]
        assert exploratory["job"]["payload"]["sequences"][0]["protein"]["unpairedMsa"] == ""
        assert not any("AF3_DB_DIR" in reason for reason in exploratory["readiness"]["blockers"])
        production["exploratory_still_rejects_test_weights"] = {"job_id": exploratory["job"]["id"],
                                                               "blockers": exploratory["readiness"]["blockers"]}
        await choose(page, "Aspirin")
        await panel_ready(page)
        aspirin = await prepare(page)
        assert aspirin["job"]["status"] == "blocked"
        assert aspirin["requested"]["canonical_smiles"] == "CC(=O)Oc1ccccc1C(=O)O"
        await page.reload(wait_until="networkidle")
        await panel_ready(page)
        assert "아스피린" in await page.get_by_test_id("af3-calculation-heading").inner_text()
        assert await page.get_by_test_id("studio-af3-job").get_attribute("data-job-id") == aspirin["job"]["id"]
        production["refresh_restores_selected_job"] = True
        retry = await prepare(page, retry=True)
        assert retry["retry_of"] == aspirin["job"]["id"] and retry["job"]["id"] != aspirin["job"]["id"]
        assert retry["job"]["status"] == "blocked"
        production["blocked_retry_preserves_previous_record"] = True
        # Direct API also rejects a blocked job, even without the disabled UI.
        rejected = await context.request.post(f"{PRODUCTION}/api/molecular/predictions/{retry['job']['id']}/execute", data={})
        assert rejected.status == 409
        log = await (await context.request.get(f"{PRODUCTION}/api/molecular/predictions/{retry['job']['id']}/log")).json()
        assert log["text"] == ""
        production["api_blocked_execution_status"] = rejected.status
        production["gpu_inference_started"] = False
        production["genuine_new_predictions_generated"] = 0

        # Check the actual production card on a small screen without preparing another job.
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.get_by_test_id("af3-calculation-panel").scroll_into_view_if_needed()
        assert not await page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        await page.get_by_test_id("af3-calculation-panel").screenshot(path=str(IMAGES / "af3-calculation-blocked-mobile.png"))
        production["mobile_horizontal_overflow"] = False

        # A separate origin, store, runner and browser context isolate synthetic tests.
        fixture_context = await browser.new_context(viewport={"width": 1440, "height": 1080}, reduced_motion="reduce")
        test_page = await fixture_context.new_page()
        test_page.on("pageerror", lambda error: errors.append(str(error)))
        identity = await (await fixture_context.request.get(FIXTURE + "/api/test-fixture")).json()
        assert identity == {"test_fixture": True, "scientific_results": False, "gpu_execution": False}
        await test_page.goto(FIXTURE, wait_until="networkidle")
        await test_page.locator("#af3-test-banner").wait_for()
        await choose(test_page, "Aspirin")
        await test_page.get_by_test_id("studio-mode-af3").click()
        await panel_ready(test_page)
        a = await prepare(test_page)
        assert a["job"]["status"] == "prepared" and a["readiness"]["runnable"]
        await submit(test_page)
        await choose(test_page, "Quercetin")
        await panel_ready(test_page)
        q = await prepare(test_page)
        await submit(test_page)
        await status(test_page, "queued")
        assert "대기 순번" in await test_page.get_by_test_id("studio-af3-job").inner_text()
        await test_page.reload(wait_until="networkidle")
        await panel_ready(test_page)
        assert "퀘르세틴" in await test_page.get_by_test_id("af3-calculation-heading").inner_text()
        q_scene = await displayed_job(test_page, FIXTURE, q["job"]["id"])
        assert q_scene["selection"]["canonical_smiles"] == q["requested"]["canonical_smiles"]
        fixture["queued_reload_completed_exact_result"] = q_scene
        await choose(test_page, "Aspirin")
        await panel_ready(test_page)
        a_scene = await displayed_job(test_page, FIXTURE, a["job"]["id"])
        assert a_scene["structure_sha256"] != q_scene["structure_sha256"]
        fixture["independent_compound_results"] = [a_scene, q_scene]
        await test_page.locator(".afc-log > summary").click()
        assert "SYNTHETIC CPU CONTRACT TEST" in await test_page.get_by_test_id("studio-af3-log").inner_text()
        fixture["actual_cpu_process_log_visible"] = True

        await test_page.get_by_test_id("studio-af3-seed").fill("13")
        failed = await prepare(test_page)
        await submit(test_page)
        await status(test_page, "failed")
        assert "7" in await test_page.get_by_test_id("studio-af3-job-error").inner_text()
        retry = await prepare(test_page, retry=True)
        assert retry["retry_of"] == failed["job"]["id"]
        assert retry["job"]["id"] != failed["job"]["id"] and retry["job"]["status"] == "prepared"
        fixture["failure_and_explicit_new_retry"] = {"failed_id": failed["job"]["id"], "retry_id": retry["job"]["id"]}

        await test_page.get_by_test_id("studio-af3-mode").select_option("none")
        await test_page.get_by_test_id("studio-af3-seed").fill("1")
        assert await test_page.get_by_test_id("studio-af3-prepare").is_disabled()
        await test_page.get_by_test_id("studio-af3-exploratory-ack").check()
        no_msa = await prepare(test_page)
        assert no_msa["requested"]["msa_mode"] == "none"
        await submit(test_page)
        none_scene = await displayed_job(test_page, FIXTURE, no_msa["job"]["id"])
        fixture["explicit_exploratory_workflow"] = none_scene
        await test_page.get_by_test_id("studio-af3-job-select").select_option(a["job"]["id"])
        await displayed_job(test_page, FIXTURE, a["job"]["id"])
        fixture["explicit_job_selection_loads_that_job"] = True
        await test_page.screenshot(path=str(IMAGES / "af3-calculation-fixture-only.png"), full_page=True)
        fixture.update(test_fixture=True, scientific_results=False, gpu_execution=False)
        assert not errors, errors
        assert not production_execution_requests, production_execution_requests
        await browser.close()
    report = {"passed": True, "verified_at": datetime.now(timezone.utc).isoformat(),
              "production": production, "isolated_control_flow_fixture": fixture,
              "javascript_errors": errors, "production_ui_execute_requests": production_execution_requests}
    OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
