"""Read-only browser QA using actual selected-job and raw-comparison reports.

Use standalone system Chrome/Playwright only after the Browser skill's connection
and documented troubleshooting establish that no browser is available. Reuse an
available skill browser instead if one is connected. Never inspect browser stores
or profiles. The earlier environment had no connected browser; this script is the
reproducible fallback for that local-UI verification, not an AF3 execution tool.

The script creates no prediction jobs. Its browser request guard blocks every API
mutation except the application's read-style conformer/describe/SVG/resolve requests.
Complete mode requires actual completed A/Q reports and a passing raw comparison.
Running-stage mode records only the current real stage, without claiming completed
or scientific validation. Missing reports fail before any browser is launched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import traceback
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
READ_STYLE_POSTS = {"/api/molecular/conformer", "/api/molecules/describe", "/api/molecules/svg", "/api/molecular/resolve"}
ACTIVE = {"queued", "running", "preparing", "submitted"}
COUNT_ROWS = {"unpaired": "unpaired_msa_sequences", "paired": "paired_msa_sequences",
              "non-query": "non_query_sequences", "templates": "template_count"}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def load_report(path):
    encoded = Path(path).read_bytes()
    return json.loads(encoded), {"path": str(path), "sha256": hashlib.sha256(encoded).hexdigest()}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8873")
    parser.add_argument("--msa-report", type=Path, default=ROOT / "docs/af3-msa-selected-predictions.json")
    parser.add_argument("--comparison-report", type=Path, default=ROOT / "docs/af3-msa-structure-comparison.json")
    parser.add_argument("--mode", choices=("complete", "running-stage"), default="complete")
    parser.add_argument("--output", type=Path, help="Default: docs/af3-msa-ui-{mode}-verification.json")
    parser.add_argument("--images", type=Path, default=ROOT / "docs/images")
    parser.add_argument("--chrome", default=shutil.which("google-chrome") or "/usr/bin/google-chrome")
    args = parser.parse_args(argv)
    args.output = args.output or ROOT / f"docs/af3-msa-ui-{args.mode}-verification.json"
    args.url = args.url.rstrip("/")
    return args


def load_inputs(args):
    msa, source = load_report(args.msa_report)
    require(isinstance(msa.get("jobs"), dict) and msa["jobs"], "Actual selected MSA job report is required")
    for name, entry in msa["jobs"].items():
        require(re.fullmatch(r"[a-f0-9]{32}", entry["job"]["id"]), f"Invalid actual job ID for {name}")
        require(entry["requested"]["msa_mode"] == "search", f"{name} is not an MSA-search job")
    comparison, comparison_source = None, None
    if args.mode == "complete":
        comparison, comparison_source = load_report(args.comparison_report)
        require(comparison.get("passed") is True and comparison.get("mode") == "baseline_vs_msa",
                "Complete UI QA requires a passing actual baseline/MSA raw-structure audit")
        require(set(msa["jobs"]) == {"aspirin", "quercetin"}, "Complete QA requires both actual aspirin and quercetin jobs")
        require(set(comparison["baseline"]) == set(comparison["msa"]) == set(msa["jobs"]), "Compared compounds differ from actual job report")
        for name, entry in msa["jobs"].items():
            require(entry["job"]["status"] == "completed" and entry["job"]["result"].get("execution_verified") is True
                    and entry.get("output_validation", {}).get("identity_verified") is True,
                    f"{name} is not an execution/identity-verified completed job")
            require(comparison["msa"][name]["job_id"] == entry["job"]["id"], "Raw audit refers to another MSA job")
    return msa, comparison, {"msa": source, "raw_comparison": comparison_source}


def api_get(page, base, path):
    response = page.request.get(base + "/api" + path, timeout=90000)
    require(response.ok, f"Read-only API failed: {path}, HTTP {response.status}")
    return response.json()


def panel_ready(page, expect):
    expect(page.get_by_test_id("af3-calculation-panel")).to_have_attribute("aria-busy", "false", timeout=90000)


def choose_job(page, expect, job_id):
    panel_ready(page, expect)
    current = page.get_by_test_id("studio-af3-job")
    if current.get_attribute("data-job-id") != job_id:
        selector = page.get_by_test_id("studio-af3-job-select")
        expect(selector).to_be_visible()
        selector.select_option(job_id)
    expect(current).to_have_attribute("data-job-id", job_id, timeout=90000)
    # This helper selects only the actual MSA report's search jobs. Do not infer
    # their mode from a comparison table that may still be rendering a prior job.
    expect(page.get_by_test_id("studio-af3-job-mode")).to_contain_text("표준 · MSA 검색")


def choose_compound(page, expect, name, catalog):
    record = next((row for row in catalog if row["id"] == name), None)
    require(record is not None, f"Selected compound {name} is absent from the current catalog")
    label = record.get("name_ko") or record["name"]
    button = page.locator(".compound-main").filter(has_text=label)
    expect(button).to_have_count(1)
    button.click()
    expect(page.get_by_test_id("selected-structure-context")).to_contain_text(label)
    expect(page.get_by_test_id("selected-structure-context")).to_have_attribute("data-structure-mode", "alphafold3_prediction")
    panel_ready(page, expect)
    return label


def camera_distance(page):
    page.wait_for_function("Number(document.querySelector('[data-testid=viewer-zoom-level]')?.dataset.cameraDistance)>0", timeout=90000)
    return float(page.get_by_test_id("viewer-zoom-level").get_attribute("data-camera-distance"))


def settled_camera(page):
    # Store only a transient test counter for a visible DOM camera measurement.
    page.evaluate("delete window.__msaUiCameraStability")
    page.wait_for_function("""() => {
      const distance=Number(document.querySelector('[data-testid=viewer-zoom-level]')?.dataset.cameraDistance || 0);
      const previous=window.__msaUiCameraStability || {distance:0,stable:0};
      const stable=distance>0 && Math.abs(previous.distance-distance)<0.0001 ? previous.stable+1 : 0;
      window.__msaUiCameraStability={distance,stable}; return stable>=12;
    }""", polling="raf", timeout=90000)
    return camera_distance(page)


def changed_camera(page, previous, direction):
    operator = "<" if direction == "in" else ">"
    factor = .95 if direction == "in" else 1.05
    page.wait_for_function(f"previous => Number(document.querySelector('[data-testid=viewer-zoom-level]').dataset.cameraDistance) {operator} previous * {factor}", arg=previous, timeout=45000)
    return settled_camera(page)


def layout(page, locator):
    locator.scroll_into_view_if_needed()
    measured = locator.evaluate("""element => {
      const r=element.getBoundingClientRect();
      const controls=[...element.querySelectorAll('button,input,select')].filter(e=>e.getClientRects().length).map(e=>{
        const b=e.getBoundingClientRect();return {label:e.getAttribute('aria-label')||e.textContent.trim(),height:b.height,left:b.left,right:b.right};});
      return {width:r.width,height:r.height,clientWidth:element.clientWidth,scrollWidth:element.scrollWidth,
        documentOverflow:document.documentElement.scrollWidth>innerWidth,
        clippedControls:controls.filter(c=>c.left<r.left-1||c.right>r.right+1),
        smallControls:controls.filter(c=>c.height<43.9)};
    }""")
    require(not measured["documentOverflow"] and measured["scrollWidth"] <= measured["clientWidth"] + 1,
            f"Horizontal overflow: {measured}")
    require(not measured["clippedControls"], f"Controls leave the panel: {measured}")
    require(not measured["smallControls"], f"Calculation controls below 44px: {measured}")
    return measured


def screenshot(locator, args, filename):
    path = args.images / filename
    locator.screenshot(path=str(path), animations="disabled", timeout=60000)
    return str(path)


def validate_displayed_scene(page, expect, job_id, expected_sha):
    expect(page.get_by_test_id("studio-af3-job")).to_have_attribute("data-job-id", job_id)
    expect(page.get_by_test_id("molecule-canvas")).to_have_attribute("data-structure-sha256", expected_sha, timeout=90000)
    expect(page.get_by_test_id("selected-structure-context")).to_have_attribute("aria-busy", "false", timeout=90000)
    settled_camera(page)
    # Download through the visible viewer, so verification covers the displayed
    # state rather than independently fetching an unrelated scene endpoint.
    with page.expect_download(timeout=45000) as event:
        page.locator(".viewer-bottomline").get_by_role("button", name="구조 JSON", exact=True).click()
    download = event.value
    scene = json.loads(Path(download.path()).read_text())
    metadata = scene["metadata"]
    require(metadata["job_id"] == job_id and metadata["sha256"] == expected_sha, "Displayed scene is not the exact selected audited job")
    require(scene["source"] == "alphafold3_prediction" and metadata.get("execution_verified") is True,
            "Viewer is not displaying actual executed AF3 coordinates")
    require(metadata.get("selected_ligand_atom_ids"), "Selected ligand atom identity is missing")
    return scene


def features_check(page, expect, features):
    panel = page.get_by_test_id("studio-af3-features")
    expect(panel).to_have_attribute("data-feature-status", features["status"])
    if features["status"] == "ready":
        cells = panel.locator(".afc-feature-counts dd")
        for i, key in enumerate(COUNT_ROWS.values()):
            require(type(features[key]) is int, f"Measured feature count is missing: {key}")
            expect(cells.nth(i)).to_contain_text(f"{features[key]:,}")
        expect(panel).to_contain_text("기존 검색 결과 재사용" if features["cache_hit"] else "이 작업에서 검색")
    else:
        expect(panel.locator(".afc-feature-counts")).to_have_count(0)
        for key in COUNT_ROWS.values():
            require(features.get(key) is None, f"Unmeasured search count was reported as numeric: {key}")
    return {"status": features["status"], "cache_hit": features.get("cache_hit"),
            "counts": {key: features.get(key) for key in COUNT_ROWS.values()}}


def verify_comparison(page, expect, raw_baseline, raw_msa, live):
    comparison = page.get_by_test_id("studio-af3-comparison")
    expect(comparison).to_be_visible()
    for i, value in enumerate((raw_baseline, raw_msa)):
        expect(comparison.locator("td[data-job-id]").nth(i)).to_have_attribute("data-job-id", value["job_id"])
        for metric in ("ptm", "iptm"):
            actual = value["top_ranked_copy"]["summary_metrics"][metric]
            expect(page.get_by_test_id("studio-af3-compare-" + metric).locator("td").nth(i)).to_have_text(f"{actual:.2f}")
        expect(page.get_by_test_id("studio-af3-compare-quality").locator("td").nth(i)).to_contain_text("미평가")
    features = live["msa_features"]
    audited = raw_msa["msa_features"]
    raw_counts = {"unpaired_msa_sequences": audited["unpaired"]["sequence_rows_including_query"],
                  "paired_msa_sequences": audited["paired"]["sequence_rows_including_query"],
                  "non_query_sequences": audited["unpaired"]["non_query_rows"], "template_count": len(audited["templates"])}
    require({key: features[key] for key in raw_counts} == raw_counts, "Live MSA counts differ from audited actual input")
    require(features["cache_hit"] == audited["cache_hit"], "Cache provenance differs from raw audit")
    for row, field in COUNT_ROWS.items():
        expect(page.get_by_test_id("studio-af3-compare-" + row).locator("td").nth(0)).to_have_text("생략")
        expect(page.get_by_test_id("studio-af3-compare-" + row).locator("td").nth(1)).to_have_text(f"{raw_counts[field]:,}")
    delta_text = page.get_by_test_id("studio-af3-confidence-delta")
    deltas = {}
    for metric, label in (("ptm", "pTM"), ("iptm", "ipTM")):
        value = raw_msa["top_ranked_copy"]["summary_metrics"][metric] - raw_baseline["top_ranked_copy"]["summary_metrics"][metric]
        rendered = ("+" if value > 0 else "") + f"{value:.2f}"
        expect(delta_text).to_contain_text(label + " " + rendered)
        deltas[metric] = value
    expect(delta_text).to_contain_text("정확도 향상을 입증하지 않습니다")
    expect(page.get_by_test_id("studio-af3-condition-mismatch")).to_have_count(0)
    details = page.get_by_test_id("studio-af3-comparison-provenance")
    details.locator("summary").first.click()
    for i, value in enumerate((raw_baseline, raw_msa)):
        column = details.locator(".afc-provenance-columns > div").nth(i)
        for expected in (value["job_id"], value["conditions"]["target_sequence_sha256"],
                         value["conditions"]["af3_commit"], value["conditions"]["model_stat_fingerprint_sha256"],
                         value["top_ranked_copy"]["structure_sha256"]):
            expect(column).to_contain_text(expected)
    search_column = details.locator(".afc-provenance-columns > div").nth(1)
    for key in ("feature_sha256", "source_job_id", "database_fingerprint", "max_template_date", "inference_input_sha256"):
        require(audited.get(key), f"Audited provenance missing {key}")
        expect(search_column).to_contain_text(audited[key])
    expect(details).to_contain_text("가중치 내용의 SHA-256이나 학습 출처 인증이 아닙니다")
    details.locator("summary").first.click()
    return {"baseline_job_id": raw_baseline["job_id"], "msa_job_id": raw_msa["job_id"],
            "counts": raw_counts, "cache_hit": audited["cache_hit"], "confidence_deltas": deltas,
            "provenance_verified": True, "quality_certification_claimed": False}


def complete_job(page, expect, args, name, raw_baseline, raw_msa, live):
    job_id = live["job"]["id"]
    require(live["job"]["status"] == "completed" and live["job"]["result"].get("execution_verified") is True
            and live["output_validation"]["identity_verified"] is True, "Live job is not a verified completed execution")
    require(live["output_validation"]["sha256"] == raw_msa["top_ranked_copy"]["structure_sha256"], "Live selected output differs from audited CIF")
    entry = {"job_id": job_id, "status": "completed", "features": features_check(page, expect, live["msa_features"]),
             "comparison": verify_comparison(page, expect, raw_baseline, raw_msa, live), "screens": {}}
    panel = page.get_by_test_id("af3-calculation-panel")
    comparison = page.get_by_test_id("studio-af3-comparison")
    entry["screens"]["desktop"] = {"layout": layout(page, panel),
        "comparison": screenshot(comparison, args, f"af3-msa-{name}-comparison-desktop.png")}
    provenance = page.get_by_test_id("studio-af3-comparison-provenance")
    provenance.locator("summary").first.click()
    entry["screens"]["desktop"]["provenance"] = screenshot(provenance, args, f"af3-msa-{name}-provenance-desktop.png")
    provenance.locator("summary").first.click()
    msa_sha = raw_msa["top_ranked_copy"]["structure_sha256"]
    baseline_sha = raw_baseline["top_ranked_copy"]["structure_sha256"]
    require(msa_sha != baseline_sha, "Baseline and new MSA outputs unexpectedly have identical complete CIF bytes")
    validate_displayed_scene(page, expect, job_id, msa_sha)
    page.get_by_test_id("studio-af3-compare-none").click()
    expect(page.get_by_test_id("studio-af3-job")).to_have_attribute("data-job-id", raw_baseline["job_id"])
    validate_displayed_scene(page, expect, raw_baseline["job_id"], baseline_sha)
    page.get_by_test_id("studio-af3-compare-search").click()
    expect(page.get_by_test_id("studio-af3-job")).to_have_attribute("data-job-id", job_id)
    scene = validate_displayed_scene(page, expect, job_id, msa_sha)
    entry["displayed_scene_switch"] = {"baseline_job_id": raw_baseline["job_id"], "baseline_sha256": baseline_sha,
                                       "msa_job_id": job_id, "msa_sha256": msa_sha, "viewer_downloads_matched": True}
    viewer = page.get_by_test_id("molecule-viewer")
    viewer.scroll_into_view_if_needed()
    page.get_by_test_id("viewer-reset").click()
    full = settled_camera(page)
    entry["screens"]["desktop"]["complex"] = screenshot(page.locator(".molecule-workbench"), args, f"af3-msa-{name}-complex-desktop.png")
    selected_ids = set(scene["metadata"]["selected_ligand_atom_ids"])
    selected_atom = next(atom for atom in scene["atoms"] if atom["id"] in selected_ids and atom["element"] not in {"H", "D"})
    page.get_by_test_id("atom-search").fill(str(selected_atom["index"]))
    page.get_by_test_id("atom-search-submit").click()
    expect(page.get_by_test_id("atom-inspector")).to_contain_text(f"ATOM #{selected_atom['index']}")
    atom_distance = changed_camera(page, full, "in")
    page.get_by_test_id("viewer-zoom-out").click()
    zoom_out = changed_camera(page, atom_distance, "out")
    page.get_by_test_id("viewer-zoom-in").click()
    zoom_in = changed_camera(page, zoom_out, "in")
    entry["camera"] = {"full_distance": full, "selected_atom_distance": atom_distance,
                       "zoom_out_distance": zoom_out, "zoom_in_distance": zoom_in,
                       "atom": {key: selected_atom.get(key) for key in ("index", "name", "element", "chain_id", "residue_id")}}
    entry["screens"]["desktop"]["atom"] = screenshot(page.locator(".molecule-workbench"), args, f"af3-msa-{name}-atom-desktop.png")
    page.get_by_role("button", name="원자 정보 닫기", exact=True).click()
    page.set_viewport_size({"width": 360, "height": 800})
    entry["screens"]["mobile360"] = {"layout": layout(page, panel),
        "comparison": screenshot(comparison, args, f"af3-msa-{name}-comparison-mobile360.png")}
    page.get_by_test_id("viewer-focus-ligand").click()
    mobile_distance = settled_camera(page)
    page.get_by_test_id("representation-stick").click()
    viewer.scroll_into_view_if_needed()
    require(viewer.bounding_box()["height"] >= 650, "Mobile context shrank the molecule viewport")
    require(not page.evaluate("document.documentElement.scrollWidth>innerWidth"), "Mobile viewer causes horizontal overflow")
    page.get_by_test_id("viewer-zoom-out").click()
    mobile_out = changed_camera(page, mobile_distance, "out")
    page.get_by_test_id("viewer-zoom-in").click()
    mobile_in = changed_camera(page, mobile_out, "in")
    entry["screens"]["mobile360"].update(viewer_height=viewer.bounding_box()["height"],
        camera={"ligand": mobile_distance, "out": mobile_out, "in": mobile_in},
        viewer=screenshot(page.locator(".molecule-workbench"), args, f"af3-msa-{name}-viewer-mobile360.png"))
    page.set_viewport_size({"width": 1440, "height": 1080})
    return entry


def running_job(page, expect, args, name, live):
    job_id = live["job"]["id"]
    observation = {"job_id": job_id, "mode": "running_stage_only", "completed_qa_performed": False}
    if live["job"]["status"] in ACTIVE:
        # Wait for a real automatic polling response; no refresh/submit action is
        # used to manufacture a polling success. A phase may change during QA.
        with page.expect_response(lambda response: urlparse(response.url).path == f"/api/molecular/predictions/{job_id}"
                                  and response.request.method == "GET", timeout=12000) as event:
            pass
        require(event.value.ok, "Automatic job polling failed")
        live = event.value.json()
        observation["automatic_polling_observed"] = True
    status = live["job"]["status"]
    observation["observed_status"] = status
    if status in ACTIVE:
        stage = (live.get("stage") or {}).get("name", status)
        expect(page.get_by_test_id("studio-af3-stage")).to_have_attribute("data-stage", stage, timeout=10000)
        expect(page.get_by_test_id("studio-af3-stage")).to_contain_text("자동 확인")
        observation["stage"] = stage
        features = live.get("msa_features")
        require(features, "Live job lacks MSA feature state")
        observation["features"] = features_check(page, expect, features)
        if features["status"] != "ready":
            for row in COUNT_ROWS:
                expect(page.get_by_test_id("studio-af3-compare-" + row).locator("td").nth(1)).to_have_text("검색 결과 대기")
        for metric in ("ptm", "iptm"):
            expect(page.get_by_test_id("studio-af3-compare-" + metric).locator("td").nth(1)).to_have_text("결과 대기")
        expect(page.get_by_test_id("studio-af3-confidence-delta")).to_have_count(0)
    else:
        observation["note"] = "Job became terminal before stage capture. This running-stage invocation does not validate completed results."
    observation["screens"] = {}
    # Pending searches already have measured installation provenance. Verify
    # those real DB receipts without inventing search counts or future inputs.
    features = live.get("msa_features") or {}
    databases = live.get("readiness", {}).get("databases") or {}
    require(databases.get("status") == "ready", "Running standard search lacks ready DB provenance")
    require(features.get("database_fingerprint") == databases.get("fingerprint_sha256")
            and features.get("database_fingerprint"), "Search/installed DB fingerprints differ")
    provenance = page.get_by_test_id("studio-af3-comparison-provenance")
    provenance.locator("summary").first.click()
    column = provenance.locator(".afc-provenance-columns > div").nth(1)
    for value in (job_id, features.get("protein_sequence_sha256"), features.get("database_fingerprint"),
                  features.get("af3_commit"), features.get("max_template_date"),
                  databases.get("manifest_sha256"), databases.get("source_tag")):
        require(value, "A running search provenance field is missing")
        expect(column).to_contain_text(value)
    components = databases.get("components") or []
    require(components, "Installed DB component receipts are absent")
    column.locator("details > summary").click()
    for component in components:
        expect(column).to_contain_text(component["relative_path"])
        expect(column).to_contain_text(component["sha256"])
    observation["database_provenance"] = {"status": databases["status"],
        "fingerprint_sha256": databases["fingerprint_sha256"], "manifest_sha256": databases["manifest_sha256"],
        "source_tag": databases["source_tag"], "component_count": len(components),
        "all_component_names_and_hashes_visible": True,
        "screenshot": screenshot(provenance, args, f"af3-msa-{name}-running-provenance-desktop.png")}
    column.locator("details > summary").click()
    provenance.locator("summary").first.click()
    for label, viewport in (("desktop", {"width": 1440, "height": 1080}), ("mobile360", {"width": 360, "height": 800})):
        page.set_viewport_size(viewport)
        panel = page.get_by_test_id("af3-calculation-panel")
        observation["screens"][label] = {"layout": layout(page, panel),
            "path": screenshot(panel, args, f"af3-msa-{name}-running-{label}.png")}
    page.set_viewport_size({"width": 1440, "height": 1080})
    return observation


def run(args, report, jobs, comparison):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chrome, headless=True,
            args=["--no-sandbox", "--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": 1440, "height": 1080}, reduced_motion="reduce", accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(45000)
        page.on("pageerror", lambda error: report["javascript_errors"].append(str(error)))

        def guard(route):
            request, path = route.request, urlparse(route.request.url).path
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                record = {"method": request.method, "path": path}
                if request.method == "POST" and path in READ_STYLE_POSTS:
                    report["read_style_posts"].append(record)
                else:
                    report["blocked_mutations"].append(record)
                    route.abort()
                    return
            route.continue_()

        context.route("**/api/**", guard)
        try:
            page.goto(args.url, wait_until="domcontentloaded")
            expect(page.get_by_test_id("studio-mode-af3")).to_be_visible(timeout=90000)
            page.get_by_test_id("studio-mode-af3").click()
            catalog = api_get(page, args.url, "/catalog")
            report["frontend_assets"] = page.locator("script[src]").evaluate_all("nodes=>nodes.map(node=>node.src)")
            for name, saved in jobs.items():
                print(f"Read-only UI check: {args.mode}/{name} {saved['job']['id']}", flush=True)
                label = choose_compound(page, expect, name, catalog)
                job_id = saved["job"]["id"]
                choose_job(page, expect, job_id)
                live = api_get(page, args.url, f"/molecular/predictions/{job_id}")
                require(live["requested"]["canonical_smiles"] == saved["requested"]["canonical_smiles"]
                        and live["requested"]["target_accession"] == saved["requested"]["target_accession"],
                        "Live job identity differs from selected report")
                report["jobs"][name] = complete_job(page, expect, args, name, comparison["baseline"][name], comparison["msa"][name], live) if args.mode == "complete" else running_job(page, expect, args, name, live)
                report["jobs"][name]["display_name"] = label
            if args.mode == "complete":
                last = next(reversed(jobs.values()))
                page.reload(wait_until="domcontentloaded")
                panel_ready(page, expect)
                expect(page.get_by_test_id("studio-af3-job")).to_have_attribute("data-job-id", last["job"]["id"], timeout=90000)
                expect(page.get_by_test_id("molecule-canvas")).to_have_attribute("data-structure-sha256", last["output_validation"]["sha256"], timeout=90000)
                report["selected_msa_job_restored_after_reload"] = True
                structures = [value["displayed_scene_switch"]["msa_sha256"] for value in report["jobs"].values()]
                require(len(set(structures)) == len(structures), "Different selected molecules unexpectedly display the same complete CIF")
                report["selected_molecules_have_distinct_actual_cifs"] = True
            require(not report["javascript_errors"], "Browser JavaScript errors occurred")
            require(not report["blocked_mutations"], "UI attempted a forbidden mutation; it was blocked")
        except Exception:
            try:
                report["failure_screenshot"] = screenshot(page.locator("body"), args, f"af3-msa-ui-{args.mode}-failure.png")
            except Exception:
                pass
            raise
        finally:
            browser.close()


def main(argv=None):
    args = parse_args(argv)
    report = {"created_at": datetime.now(UTC).isoformat(), "mode": args.mode, "url": args.url,
              "browser_surface": "System Chrome/Playwright fallback; use only after documented Browser unavailability",
              "mock_data_used": False, "gpu_jobs_submitted": 0, "blocked_mutations": [], "read_style_posts": [],
              "javascript_errors": [], "jobs": {}, "passed": False,
              "verification_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "scope": "Actual UI identity/provenance/controls only; no efficacy, safety or independent accuracy validation"}
    try:
        msa, comparison, provenance = load_inputs(args)
        report["input_reports"] = provenance
        args.images.mkdir(parents=True, exist_ok=True)
        run(args, report, msa["jobs"], comparison)
        report["passed"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        report["traceback"] = traceback.format_exc()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"passed": report["passed"], "mode": args.mode, "output": str(args.output), "error": report.get("error")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
