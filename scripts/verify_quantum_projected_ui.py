"""Read-only UI audit of an actual IBM projected result and its preserved source.

Use this system-Chrome fallback only after the Browser skill's documented
discovery/troubleshooting confirms no connected browser. No QPU submissions,
LLM calls, AF3 executions or synthetic measurement responses are permitted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import traceback
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def number(value):
    if value == 0:
        return "0"
    if abs(value) < .0001 or abs(value) >= 1e6:
        return f"{value:.4e}".replace("e-0", "e-").replace("e+0", "e+")
    return format(value, ".6g")


def expect_numeric_text(locator, expected):
    """Compare actual displayed numbers at their declared significant precision.

    Python and JavaScript round exact ties differently. Accept only the display
    quantization error, while still rejecting zero substituted for a small value.
    """
    text = locator.inner_text()
    observed = [float(token.replace(",", "")) for token in re.findall(
        r"(?<![A-Za-z0-9])[-+]?(?:\d+(?:,\d{3})*(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", text)]
    tolerance = 5e-5 if 0 < abs(expected) < .0001 or abs(expected) >= 1e6 else 5e-6
    require(any(value == 0 if expected == 0 else value != 0 and math.isclose(value, expected, rel_tol=tolerance, abs_tol=0)
                for value in observed), f"Displayed measurement differs from raw value {expected}: {text}")


def get(page, base, path):
    response = page.request.get(base + "/api" + path, timeout=60000)
    require(response.ok, f"GET {path}: {response.status}")
    return response.json()


def layout(page, locator):
    result = locator.evaluate("""element => ({width:element.clientWidth, scrollWidth:element.scrollWidth,
      documentOverflow:document.documentElement.scrollWidth>innerWidth,
      controls:[...element.querySelectorAll('button,select')].filter(e=>e.getClientRects().length).map(e=>{
        const r=e.getBoundingClientRect();return {label:e.getAttribute('aria-label')||e.textContent.trim(),height:r.height};})})""")
    require(not result["documentOverflow"] and result["scrollWidth"] <= result["width"] + 1, "Panel overflows viewport")
    require(all(row["height"] >= 43.9 for row in result["controls"]), "Visible control is smaller than 44px")
    return result


def audit(args, report, receipt):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=shutil.which("google-chrome"), headless=True,
            args=["--no-sandbox", "--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": 1440, "height": 1080}, reduced_motion="reduce", accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(60000)
        page.on("pageerror", lambda error: report["javascript_errors"].append(str(error)))

        def guard(route):
            request = route.request
            path = urlparse(request.url).path
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                record = {"method": request.method, "path": path}
                if request.method == "POST" and path in {"/api/molecular/conformer", "/api/molecules/describe", "/api/molecules/svg", "/api/molecular/resolve", "/api/quantum/plan"}:
                    report["read_style_posts"].append(record)
                else:
                    report["blocked_mutations"].append(record)
                    route.abort()
                    return
            route.continue_()

        context.route("**/api/**", guard)

        def screenshot(locator, label):
            path = args.images / f"quantum-projected-{label}.png"
            locator.screenshot(path=str(path), animations="disabled", timeout=60000)
            report["screenshots"][label] = str(path)

        try:
            page.goto(args.url, wait_until="domcontentloaded")
            store_id = receipt["store_job_id"]
            job = get(page, args.url, "/jobs/" + store_id)
            source_id = job["payload"]["source_analysis_id"]
            require(job["status"] == "completed" and job["result"]["hardware_executed"] is True,
                    "Actual IBM job is not completed")
            require(job["result"]["kernel"] == receipt["result"]["kernel"], "Saved result differs from actual receipt")
            source = get(page, args.url, "/analyses/" + source_id)
            original = source["result"]["quantum"]
            report["source_analysis_id"], report["store_job_id"] = source_id, store_id
            report["original_kernel"] = original["kernel"]
            report["projected_kernel"] = job["result"]["kernel"]
            require(all(value == 0 for row in original["kernel"] for value in row), "This regression requires the actual collapsed source")
            page.get_by_role("button", name="에이전트 분석", exact=True).click()
            listing = get(page, args.url, "/analyses")["analyses"]
            index = next(i for i, row in enumerate(listing) if row["id"] == source_id)
            history = page.locator(".analysis-history-item").nth(index)
            expect(history).to_contain_text(source["request"]["goal"][:34])
            history.click()
            workspace = page.get_by_test_id("quantum-workspace")
            expect(workspace).to_have_attribute("data-source-analysis", source_id)
            linked = page.get_by_test_id("quantum-linked-job")
            expect(page.get_by_test_id("quantum-linked-job-select")).to_be_visible()
            page.get_by_test_id("quantum-linked-job-select").select_option(store_id)
            expect(linked).to_have_attribute("data-job-id", store_id)
            expect(linked).to_have_attribute("data-job-status", "completed")
            baseline_panel = workspace.locator('[data-testid="quantum-result"][data-method="fidelity"]')
            projected_panel = linked.get_by_test_id("quantum-result")
            expect(projected_panel).to_have_attribute("data-method", "projected")
            expect(projected_panel).to_have_attribute("data-origin", "hardware")
            expect(baseline_panel.get_by_test_id("quantum-global-collapse")).to_be_visible()
            observation = original["jobs"][0]["observations"][1]
            expect(baseline_panel.get_by_test_id("quantum-pair-detail")).to_contain_text(f"0 / {observation['shots']}")
            expect_numeric_text(baseline_panel.get_by_test_id("quantum-pair-detail"), observation["wilson_95"][1])
            for i, row in enumerate(job["result"]["kernel"]):
                for j, value in enumerate(row):
                    cell = projected_panel.get_by_test_id(f"quantum-cell-{i}-{j}")
                    expect_numeric_text(cell, value)
                    if i == j:
                        expect(cell).to_contain_text("정의값")
            expect(projected_panel).to_contain_text("자기 충실도를 실측한 결과가 아닙니다")
            expect(projected_panel).to_contain_text("고전 계산으로도 비교")
            pair = projected_panel.get_by_test_id("quantum-pair-detail")
            expect(pair).to_contain_text("95% shot 재표집 범위")
            for bound in ("lower_95", "upper_95"):
                expect_numeric_text(pair, job["result"]["kernel_uncertainty"][bound][0][1])
            diagnostic = projected_panel.get_by_test_id("quantum-diagnostics")
            for value in (job["result"]["controls"]["duplicate"]["kernel_to_original"],
                          job["result"]["controls"]["readout"]["mean_error"],
                          job["result"]["kernel_diagnostics"]["signal_to_shot_noise"]):
                expect_numeric_text(diagnostic, value)
            rates = job["result"]["controls"]["readout"]
            worst = max((rate, index, prepared) for prepared, key in enumerate(("zero_error_rates", "one_error_rates")) for index, rate in enumerate(rates[key]))
            expect_numeric_text(projected_panel.get_by_test_id("quantum-readout-worst"), worst[0])
            expect(projected_panel.get_by_test_id("quantum-readout-worst")).to_contain_text(f"논리 q{worst[1]}")
            expect(pair).to_contain_text("이상적 커널의 신뢰구간이 아니며")
            expect(pair).to_contain_text("관측값이 범위 밖일 수 있습니다")
            report["maximum_prepared_state_error"] = {"rate": worst[0], "logical_qubit": worst[1], "prepared_state": worst[2], "physical_qubit": job["result"]["plan"]["physical_qubits"][worst[1]]}
            report["desktop_layout"] = layout(page, workspace)
            screenshot(baseline_panel, "legacy-desktop")
            screenshot(projected_panel, "measured-desktop")
            observables = projected_panel.get_by_test_id("quantum-observables")
            observables.locator("summary").first.click()
            projected_panel.get_by_test_id("quantum-feature-sample").select_option("2")
            first_row = observables.locator("tbody tr").first
            for axis, value in enumerate(job["result"]["projected_features"]["values"][2][0]):
                expect_numeric_text(first_row.locator("td").nth(axis).locator("strong"), value)
            for axis, bounds in enumerate(job["result"]["projected_features"]["wilson_95"][2][0]):
                for value in bounds:
                    expect_numeric_text(first_row.locator("td").nth(axis).locator("small"), value)
            observables.get_by_role("button", name="다음 큐빗", exact=True).click()
            expect(observables.locator("tbody tr").first).to_contain_text("q12")
            screenshot(observables, "observables-desktop")
            observables.locator("summary").first.click()
            readout = projected_panel.get_by_test_id("quantum-readout")
            readout.locator("summary").first.click()
            for _ in range(worst[1] // 12):
                readout.get_by_role("button", name="다음 대조 큐빗", exact=True).click()
            expect_numeric_text(readout.locator("tbody tr").nth(worst[1] % 12), worst[0])
            screenshot(readout, "readout-worst-desktop")
            readout.locator("summary").first.click()
            references = projected_panel.get_by_test_id("quantum-references")
            references.locator("summary").first.click()
            for index, name in enumerate(("ideal_reference", "classical_reference")):
                table = references.locator("table").nth(index)
                expect_numeric_text(table.locator("tbody tr").first.locator("td").nth(1), job["result"][name]["kernel"][0][1])
            screenshot(references, "references-desktop")
            references.locator("summary").first.click()
            provenance = projected_panel.get_by_test_id("quantum-provenance")
            provenance.locator("summary").first.click()
            for provider_job in job["result"]["jobs"]:
                expect(provenance).to_contain_text(provider_job["job_id"])
            expect(provenance).to_contain_text(job["result"]["plan"]["feature_sha256"])
            screenshot(provenance, "provenance-desktop")
            provenance.locator("summary").first.click()
            with page.expect_download() as event:
                projected_panel.get_by_test_id("quantum-export").click()
            exported = json.loads(Path(event.value.path()).read_text())
            require(exported["kernel"] == job["result"]["kernel"] and exported["sample_ids"] == original["sample_ids"], "Displayed JSON export changed measurements or sample order")
            page.get_by_test_id("quantum-recompute-open").click()
            with page.expect_response(lambda response: urlparse(response.url).path == "/api/quantum/plan") as event:
                page.get_by_test_id("quantum-plan-button").click()
            response = event.value
            require(response.ok, "Read-only quantum plan failed")
            request = response.request.post_data_json
            require(request["features"] == original["feature_definition"]["features"] and request["sample_ids"] == original["sample_ids"] and request["source_analysis_id"] == source_id, "Plan changed the source feature order")
            preview = response.json()
            expect(page.get_by_test_id("quantum-plan")).to_contain_text(preview["backend_name"])
            expect(page.get_by_test_id("quantum-execute-button")).to_be_enabled()
            report["plan_preview"] = {"kernel_method": request["kernel_method"], "n_qubits": preview["n_qubits"], "backend": preview["backend_name"], "circuits": preview["circuit_count"], "shots": preview["total_shots"], "source_inputs_unchanged": True, "execute_button_not_clicked": True}
            screenshot(page.locator(".qp-recompute"), "plan-desktop")
            page.get_by_test_id("quantum-recompute-open").click()
            page.set_viewport_size({"width": 360, "height": 800})
            report["mobile_layout"] = layout(page, workspace)
            screenshot(baseline_panel, "legacy-mobile360")
            screenshot(projected_panel, "measured-mobile360")
            page.reload(wait_until="domcontentloaded")
            page.get_by_role("button", name="에이전트 분석", exact=True).click()
            expect(page.get_by_test_id("quantum-workspace")).to_have_attribute("data-source-analysis", source_id)
            expect(page.get_by_test_id("quantum-linked-job")).to_have_attribute("data-job-id", store_id)
            report["analysis_and_result_restored_after_reload"] = True
            page.set_viewport_size({"width": 1440, "height": 1080})
            page.locator("nav").get_by_role("button", name="연구 기록", exact=True).click()
            archive_row = page.locator(".archive-row").filter(has_text=store_id[:16])
            expect(archive_row).to_have_count(1)
            archive_row.get_by_role("button", name="결과 보기").click()
            archived = page.locator(".archive-detail").get_by_test_id("quantum-result")
            expect(archived).to_have_attribute("data-method", "projected")
            expect(archived).to_have_attribute("data-origin", "hardware")
            expect_numeric_text(archived.get_by_test_id("quantum-cell-0-1"), job["result"]["kernel"][0][1])
            screenshot(archived, "archive-desktop")
            report["archive_preserves_measured_method_and_values"] = True
            report["source_kernel_unchanged_after_qa"] = get(page, args.url, "/analyses/" + source_id)["result"]["quantum"]["kernel"] == original["kernel"]
            require(report["source_kernel_unchanged_after_qa"], "Source kernel changed")
            require(not report["blocked_mutations"] and not report["javascript_errors"], "Mutation attempted or browser error occurred")
        except Exception:
            try:
                screenshot(page.locator("body"), "failure")
            except Exception:
                pass
            raise
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8873")
    parser.add_argument("--receipt", type=Path, default=Path("docs/quantum-projected-verification.json"))
    parser.add_argument("--output", type=Path, default=Path("docs/quantum-projected-ui-verification.json"))
    parser.add_argument("--images", type=Path, default=Path("docs/images"))
    args = parser.parse_args()
    report = {"created_at": datetime.now(UTC).isoformat(), "passed": False, "javascript_errors": [],
              "blocked_mutations": [], "read_style_posts": [], "screenshots": {}, "synthetic_data_used": False,
              "qpu_submissions": 0, "scope": "Actual UI/provenance/plan review only; no scientific advantage or efficacy validation"}
    try:
        data = args.receipt.read_bytes()
        receipt = json.loads(data)
        require(receipt["status"] == "completed" and receipt["hardware_executed"] is True, "Completed real IBM receipt required")
        report["receipt_sha256"] = hashlib.sha256(data).hexdigest()
        args.images.mkdir(parents=True, exist_ok=True)
        audit(args, report, receipt)
        report["passed"] = True
    except Exception as error:
        report["error"] = str(error)
        report["traceback"] = traceback.format_exc()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "output": str(args.output), "error": report.get("error")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
