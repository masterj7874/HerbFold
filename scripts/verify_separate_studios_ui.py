"""Read-only integration check against actual AF3 and IBM stored results.

Use the system Chrome fallback only after Browser skill discovery and its
documented troubleshooting establish that no connected browser is available.
The request guard rejects prediction preparation/execution, QPU submission,
LLM requests and all other mutations. No synthetic scientific values are served.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import traceback
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse

from verify_af3_msa_ui import api_get, choose_compound, choose_job, require
from verify_quantum_projected_ui import expect_numeric_text


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit(args, report):
    from playwright.sync_api import expect, sync_playwright

    expect.set_options(timeout=60000)

    quantum = json.loads(Path("docs/quantum-projected-verification.json").read_text())
    predictions = json.loads(Path("docs/af3-msa-selected-predictions.json").read_text())["jobs"]
    originals = [Path("runtime") / quantum["store_job_id"] / "quantum.json"]
    originals.extend(Path("runtime") / entry["job"]["id"] / "af3_manifest.json" for entry in predictions.values())
    original_shas = {str(path): file_sha(path) for path in originals}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=shutil.which("google-chrome"),
            headless=True, args=["--no-sandbox", "--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": 1440, "height": 1080}, reduced_motion="reduce")
        page = context.new_page()
        page.set_default_timeout(60000)
        page.on("pageerror", lambda error: report["javascript_errors"].append(str(error)))

        def guard(route):
            request, path = route.request, urlparse(route.request.url).path
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                if request.method == "POST" and path in {
                    "/api/molecular/conformer", "/api/molecules/describe",
                    "/api/molecules/svg", "/api/molecular/resolve",
                }:
                    report["read_style_posts"].append(path)
                else:
                    report["blocked_mutations"].append({"method": request.method, "path": path})
                    route.abort()
                    return
            route.continue_()

        context.route("**/api/**", guard)

        def screenshot(label, locator=None):
            path = args.images / f"separate-studios-{label}.png"
            if locator is None:
                page.screenshot(path=str(path), animations="disabled")
            else:
                locator.screenshot(path=str(path), animations="disabled")
            report["screenshots"][label] = str(path)

        try:
            page.goto(args.url + "/#quantum", wait_until="domcontentloaded")
            expect(page.get_by_test_id("workspace-quantum")).to_be_visible()
            expect(page.get_by_test_id("workspace-alphafold")).to_have_count(0)
            catalog = api_get(page, args.url, "/catalog")
            source_id = quantum["result"]["source_analysis_id"]
            report["quantum_source_analysis_id"] = source_id
            report["quantum_job_id"] = quantum["store_job_id"]
            page.get_by_test_id("quantum-studio-analysis-tab").click()
            page.get_by_test_id(f"quantum-studio-analysis-{source_id}").click()
            expect(page.get_by_test_id("quantum-workspace")).to_have_attribute("data-source-analysis", source_id)
            page.get_by_test_id("quantum-linked-job-select").select_option(quantum["store_job_id"])
            linked = page.get_by_test_id("quantum-linked-job")
            expect(linked).to_have_attribute("data-job-id", quantum["store_job_id"])
            expect_numeric_text(linked.get_by_test_id("quantum-cell-0-1"), quantum["result"]["kernel"][0][1])
            page.evaluate("window.scrollTo(0,0)")
            screenshot("quantum-desktop")
            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_test_id("workspace-quantum")).to_be_visible()
            expect(page.get_by_test_id("quantum-linked-job")).to_have_attribute("data-job-id", quantum["store_job_id"])
            report["quantum_route_and_result_restore"] = True

            page.locator('nav [data-workspace="alphafold"]').click()
            expect(page).to_have_url(args.url + "/#alphafold")
            expect(page.get_by_test_id("workspace-quantum")).to_have_count(0)
            expect(page.get_by_test_id("alphafold-studio-overview")).to_be_visible()
            page.get_by_test_id("alphafold-target-input").fill("P23219")
            expect(page.get_by_test_id("alphafold-open-calculation")).to_be_disabled()
            page.get_by_test_id("alphafold-target-input").fill("P35354")
            expect(page.get_by_test_id("alphafold-open-calculation")).to_be_enabled()
            report["unapplied_target_change_blocks_opening_old_target"] = True
            page.get_by_test_id("studio-mode-af3").click()
            report["af3_selections"] = {}
            for name in ("quercetin", "aspirin"):
                label = choose_compound(page, expect, name, catalog)
                job_id = predictions[name]["job"]["id"]
                choose_job(page, expect, job_id)
                resolved = api_get(page, args.url, f"/molecular/predictions/{job_id}/scene")
                expected_sha = resolved["scene"]["metadata"]["sha256"]
                expect(page.get_by_test_id("molecule-canvas")).to_have_attribute("data-structure-sha256", expected_sha)
                diagnostics = page.get_by_test_id("alphafold-diagnostics")
                expect(diagnostics).to_have_attribute("data-job-id", job_id)
                expect(diagnostics).to_have_attribute("data-structure-sha", expected_sha)
                expect(diagnostics).to_have_attribute("aria-busy", "false")
                expect(diagnostics).to_have_attribute("data-diagnostic-status", "verified_identity_quality_unassessed")
                expect(page.get_by_test_id("alphafold-selected-compound")).to_contain_text(label)
                scene = resolved["scene"]
                query = urlencode({"file": scene["metadata"]["artifact"], "target_accession": "P35354",
                                   "smiles": next(row["smiles"] for row in catalog if row["id"] == name)})
                diagnostic = api_get(page, args.url, f"/molecular/predictions/{job_id}/diagnostics?{query}")
                require(diagnostic["status"] == "available" and diagnostic["pae"]["status"] == "available", "Actual selected diagnostics unavailable")

                def shown_value(locator, expected):
                    observed = float(locator.inner_text().replace(",", ""))
                    require(math.isclose(observed, expected, rel_tol=0, abs_tol=.00501),
                            f"Displayed {observed} differs from selected file's {expected}")

                for metric in ("ptm", "iptm"):
                    shown_value(page.get_by_test_id(f"alphafold-diagnostic-{metric}").locator("dd"), diagnostic["summary_metrics"][metric])
                for card, group in (("protein", "protein"), ("ligand", "selected_ligand")):
                    shown_value(page.get_by_test_id(f"alphafold-diagnostic-{card}-plddt").locator("dd"), diagnostic["plddt"][group]["mean"])
                # Independently aggregate saved raw PAE token pairs, not backend helpers.
                raw_path = Path("runtime") / job_id / diagnostic["pae"]["artifact"]
                raw = json.loads(raw_path.read_text())
                raw_pairs = []
                for first, second in (("A", "B"), ("B", "A")):
                    rows = [i for i, chain in enumerate(raw["token_chain_ids"]) if chain == first]
                    cols = [i for i, chain in enumerate(raw["token_chain_ids"]) if chain == second]
                    values = [raw["pae"][i][j] for i in rows for j in cols]
                    expected = {"mean": math.fsum(values) / len(values), "min": min(values), "max": max(values), "count": len(values)}
                    pair = next(p for p in diagnostic["pae"]["chain_pairs"] if p["frame_chain"] == first and p["target_chain"] == second)
                    for metric, value in expected.items():
                        require(math.isclose(pair[metric], value, rel_tol=1e-12, abs_tol=1e-12), "API PAE differs from independent raw aggregation")
                        shown_value(page.get_by_test_id(f"pae-{metric}-{first}-{second}"), value)
                    raw_pairs.append({"frame_chain": first, "target_chain": second, **expected})
                report["af3_selections"][name] = {"job_id": job_id, "structure_sha256": expected_sha,
                    "summary_metrics": diagnostic["summary_metrics"], "plddt": diagnostic["plddt"],
                    "independent_raw_pae": raw_pairs, "confidence_sha256": file_sha(raw_path)}
                screenshot(f"af3-{name}-diagnostics", diagnostics)
            page.evaluate("window.scrollTo(0,0)")
            screenshot("alphafold-desktop")
            page.get_by_test_id("studio-mode-conformer").click()
            expect(page.get_by_test_id("selected-structure-context")).to_have_attribute("data-structure-mode", "rdkit_conformer")
            expect(page.get_by_test_id("alphafold-diagnostics")).not_to_have_attribute("data-job-id", predictions["aspirin"]["job"]["id"])
            report["conformer_does_not_retain_prediction_metrics"] = True
            expect(page.get_by_test_id("alphafold-diagnostic-ptm").locator("dd")).to_have_text("—")
            page.get_by_test_id("studio-mode-af3").click()
            choose_job(page, expect, predictions["aspirin"]["job"]["id"])
            page.locator('nav [data-workspace="quantum"]').click()
            expect(page.get_by_test_id("quantum-linked-job")).to_have_attribute("data-job-id", quantum["store_job_id"])
            page.get_by_test_id("quantum-studio-history-tab").click()
            page.get_by_test_id(f"quantum-studio-job-{quantum['store_job_id']}").click()
            expect(page.get_by_test_id("quantum-studio-workarea")).to_have_attribute("data-record-id", quantum["store_job_id"])
            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_test_id("quantum-studio-history-tab")).to_have_attribute("aria-pressed", "true")
            expect(page.get_by_test_id("quantum-studio-workarea")).to_have_attribute("data-record-id", quantum["store_job_id"])
            expect_numeric_text(page.get_by_test_id("quantum-studio-results").get_by_test_id("quantum-cell-0-1"), quantum["result"]["kernel"][0][1])
            report["quantum_individual_execution_restore"] = True
            page.go_back(wait_until="domcontentloaded")
            expect(page.get_by_test_id("workspace-alphafold")).to_be_visible()
            expect(page.get_by_test_id("studio-af3-job")).to_have_attribute("data-job-id", predictions["aspirin"]["job"]["id"])
            report["independent_selection_and_browser_back"] = True

            page.set_viewport_size({"width": 360, "height": 800})
            report["mobile"] = {}
            for tab in ("alphafold", "quantum"):
                page.locator(f'nav [data-workspace="{tab}"]').click()
                workspace = page.get_by_test_id(f"workspace-{tab}")
                expect(workspace).to_be_visible()
                size = workspace.evaluate("e=>({width:e.clientWidth,scrollWidth:e.scrollWidth,documentWidth:document.documentElement.scrollWidth,viewport:innerWidth})")
                require(size["width"] >= 320, f"{tab} mobile workspace too narrow")
                require(size["documentWidth"] <= size["viewport"], f"{tab} overflows mobile viewport")
                report["mobile"][tab] = size
                page.evaluate("window.scrollTo(0,0)")
                screenshot(f"{tab}-mobile360")
            require(not report["blocked_mutations"] and not report["javascript_errors"], "Mutation attempted or JavaScript error")
            report["original_artifacts"] = original_shas
            require(all(file_sha(path) == expected for path, expected in original_shas.items()), "Stored scientific artifact changed")
            report.update(passed=True, original_artifacts_unchanged=True, synthetic_data_used=False,
                          qpu_submissions=0, af3_submissions=0)
        except Exception:
            screenshot("failure")
            raise
        finally:
            context.close()
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8873")
    parser.add_argument("--output", type=Path, default=Path("docs/separate-studios-ui-verification.json"))
    parser.add_argument("--images", type=Path, default=Path("docs/images"))
    args = parser.parse_args()
    args.url = args.url.rstrip("/")
    args.images.mkdir(parents=True, exist_ok=True)
    report = {"created_at": datetime.now(UTC).isoformat(), "passed": False, "javascript_errors": [],
              "blocked_mutations": [], "read_style_posts": [], "screenshots": {}}
    try:
        audit(args, report)
    except Exception:
        report["error"] = traceback.format_exc()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "report": str(args.output), "error": report.get("error")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
