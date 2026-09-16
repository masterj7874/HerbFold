"""Exercise actual selected-compound complex resolution in the production UI.

Run with system Python/Playwright and an active local server. Downloads real
registered structures and reads existing selected-compound predictions. It never
prepares or executes AF3, LLM, generation or QPU jobs. Delayed-race checks forward
genuine server responses; production coordinates are never replaced with fixtures.
The historical ibuprofen output used random performance-test parameters and
must remain quarantined, unavailable to selected-compound prediction lookup.
"""

import argparse
import asyncio
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright


async def camera(page):
    await page.wait_for_function(
        "Number(document.querySelector('[data-testid=viewer-zoom-level]')?.dataset.cameraDistance) > 0"
    )
    return float(await page.get_by_test_id("viewer-zoom-level").get_attribute("data-camera-distance"))


async def settled(page):
    await page.wait_for_function(
        "document.querySelector('[data-testid=selected-structure-context]')?.getAttribute('aria-busy') === 'false'"
    )


async def compound(page, name):
    await page.locator(".compound-main").filter(has_text=name).first.click()


async def eligible_predictions(context, base_url, smiles):
    """Derive expectations from persisted completed jobs, independently of UI lookup."""
    response = await context.request.get(
        base_url + "/api/molecular/predictions",
        params={"smiles": smiles, "target_accession": "P35354"},
    )
    assert response.ok, await response.text()
    items = (await response.json())["items"]
    return {
        item["job"]["id"]: item
        for item in items
        if item["job"]["status"] == "completed"
        and (item["job"].get("result") or {}).get("execution_verified") is True
        and (item["job"].get("result") or {}).get("prediction_eligible") is not False
        and (item.get("output_validation") or {}).get("identity_verified") is True
    }


async def resolution(page, action, expected):
    # Selecting a compound with a completed studio job may cancel generic
    # resolution and load that exact job. Both routes return MolecularResolution;
    # observe the real selected response without requiring a superseded request.
    def is_resolution_response(response):
        path = response.url.split("?", 1)[0]
        return (path.endswith("/api/molecular/resolve") and response.request.method == "POST") or (
            response.request.method == "GET"
            and re.search(r"/api/molecular/predictions/[a-f0-9]{32}/scene$", path) is not None
        )

    async with page.expect_response(is_resolution_response) as response:
        await action()
    reply = await response.value
    assert reply.ok, await reply.text()
    data = await reply.json()
    await settled(page)
    assert data["status"] == expected, data.get("reason")
    if expected == "matched":
        await page.get_by_test_id("molecule-canvas").wait_for()
        assert await page.get_by_test_id("molecule-canvas").get_attribute("data-structure-sha256") == data["scene"]["metadata"]["sha256"]
    else:
        await page.get_by_test_id("studio-structure-unavailable").wait_for()
        assert data["scene"] is None
        assert not await page.get_by_test_id("molecule-canvas").count()
    return data


def compact(data):
    scene = data.get("scene") or {}
    metadata = scene.get("metadata", {})
    selected = set(metadata.get("selected_ligand_atom_ids", []))
    atoms = [atom for atom in scene.get("atoms", []) if atom["id"] in selected]
    bonds = [bond for bond in scene.get("bonds", []) if bond["source"] in selected and bond["target"] in selected]
    return {"status": data["status"], "requested": data["requested"],
            "sha256": metadata.get("sha256"), "job_id": metadata.get("job_id"),
            "pdb_id": metadata.get("pdb_id"), "selected_ligand_atom_count": len(atoms),
            "selected_ligand_heavy_atom_count": sum(atom["element"] not in {"H", "D"} for atom in atoms),
            "selected_ligand_bond_count": len(bonds), "selection": metadata.get("selection"),
            "reason": data.get("reason"), "warnings": scene.get("warnings", [])}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8873")
    parser.add_argument("--output", default="docs/molecular-selection-verification.json")
    parser.add_argument("--quarantined-job-id", default="2e03b13d30d54f69b5582e24c6eceecb")
    args = parser.parse_args()
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    images = destination.parent / "images"
    images.mkdir(exist_ok=True)
    errors, forbidden = [], []
    checks = {}

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=shutil.which("google-chrome"), headless=True,
            args=["--no-sandbox", "--enable-unsafe-swiftshader"],
        )
        context = await browser.new_context(viewport={"width": 1440, "height": 1080}, reduced_motion="reduce")

        async def guard(route):
            request = route.request
            path = request.url.split("/api/", 1)[-1]
            if request.method == "POST":
                forbidden.append(path)
                await route.abort()
            else:
                await route.continue_()

        for pattern in ("analyses**", "af3/**", "molecular/predictions**", "quantum/**", "workflows"):
            await context.route(f"**/api/{pattern}", guard)
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(args.url, wait_until="networkidle")
        await settled(page)
        await page.get_by_test_id("molecule-canvas").wait_for()
        catalog = await (await context.request.get(args.url + "/api/catalog")).json()
        display = {row["name"]: row.get("name_ko") or row["name"] for row in catalog}
        catalog_by_name = {row["name"]: row for row in catalog}
        checks["frontend_assets"] = await page.locator("script[src]").evaluate_all("nodes => nodes.map(node => node.src)")

        await compound(page, "Ibuprofen")
        await settled(page)
        ibuprofen = await resolution(page, page.get_by_test_id("studio-mode-af3").click, "unavailable")
        assert ibuprofen["requested"]["target_accession"] == "P35354"
        assert display["Ibuprofen"] in await page.get_by_test_id("selected-structure-context").inner_text()
        assert ibuprofen["matches"] == []
        archived_response = await context.request.get(args.url + "/api/jobs/" + args.quarantined_job_id)
        assert archived_response.ok, await archived_response.text()
        quarantined = await archived_response.json()
        assert quarantined["status"] == "quarantined"
        assert quarantined["result"]["prediction_eligible"] is False
        assert quarantined["result"]["execution_verified"] is True
        checks["quarantined_ibuprofen_is_not_a_prediction"] = {
            **compact(ibuprofen), "archive_job_id": quarantined["id"],
            "archive_status": quarantined["status"], "execution_record_preserved": True,
            "parameter_audit": quarantined["result"].get("parameter_audit"),
        }

        saved_aspirin = await eligible_predictions(context, args.url, catalog_by_name["Aspirin"]["smiles"])
        aspirin = await resolution(page, lambda: compound(page, "Aspirin"), "matched" if saved_aspirin else "unavailable")
        assert await page.get_by_test_id("selected-structure-context").get_attribute("data-structure-mode") == "alphafold3_prediction"
        assert display["Aspirin"] in await page.get_by_test_id("selected-structure-context").inner_text()
        if aspirin["status"] == "matched":
            metadata = aspirin["scene"]["metadata"]
            assert metadata["job_id"] in saved_aspirin
            own_job = saved_aspirin[metadata["job_id"]]
            assert metadata["job_id"] != quarantined["id"]
            assert metadata["execution_verified"] is True
            assert metadata["selection"]["target_accession"] == "P35354"
            assert metadata["selection"]["canonical_smiles"] == own_job["requested"]["canonical_smiles"]
            assert metadata["smiles"] == aspirin["requested"]["canonical_smiles"]
            assert compact(aspirin)["selected_ligand_heavy_atom_count"] == 13
            checks["aspirin_own_eligible_prediction"] = {
                "job_id": metadata["job_id"], "output_validation": own_job["output_validation"],
                "execution_completed_is_not_quality_pass": True,
            }
        checks["aspirin_does_not_reuse_ibuprofen"] = compact(aspirin)
        await page.locator(".molecule-workbench").screenshot(path=str(images / "molecular-selection-aspirin-af3.png"))

        experimental_missing = await resolution(page, page.get_by_test_id("studio-mode-experimental").click, "unavailable")
        checks["aspirin_experimental_missing"] = compact(experimental_missing)
        references = (await (await context.request.get(args.url + "/api/molecular/references")).json())["items"]
        ref_by_pdb = {ref["pdb_id"]: ref for ref in references}
        for pdb in ("5IKR", "5IKT"):
            data = await resolution(page, lambda pdb=pdb: page.get_by_test_id("studio-reference-select").select_option(ref_by_pdb[pdb]["id"]), "matched")
            assert data["scene"]["metadata"]["pdb_id"] == pdb
            canvas = page.get_by_test_id("molecule-canvas")
            selected_ids = set(data["scene"]["metadata"]["selected_ligand_atom_ids"])
            ligand_atoms = [atom for atom in data["scene"]["atoms"] if atom["id"] in selected_ids]
            first = ligand_atoms[0]
            first_instance = [atom for atom in ligand_atoms if (atom["chain_id"], atom["residue_id"], atom["residue_name"]) == (first["chain_id"], first["residue_id"], first["residue_name"])]
            assert sum(atom["element"] not in {"H", "D"} for atom in first_instance) == 18
            assert int(await canvas.get_attribute("data-selected-ligand-atom-count")) == len(first_instance)
            focused = await camera(page)
            await page.get_by_test_id("viewer-reset").click()
            await page.wait_for_function("document.querySelector('[data-testid=viewer-zoom-level]')?.textContent.includes('100')")
            full = await camera(page)
            assert focused < full * .5, (pdb, focused, full)
            await page.get_by_test_id("viewer-focus-ligand").click()
            await page.wait_for_function("expected => Math.abs(Number(document.querySelector('[data-testid=viewer-zoom-level]')?.dataset.cameraDistance)/expected - 1) < .01", arg=focused)
            checks[pdb] = {**compact(data), "focus_camera_distance": focused, "full_camera_distance": full}
            await page.get_by_test_id("studio-expand").click()
            await page.wait_for_timeout(350)
            await page.locator(".molecule-workbench").screenshot(path=str(images / f"molecular-selection-{pdb}.png"))
            await page.get_by_test_id("studio-expand").click()
        assert checks["5IKR"]["sha256"] != checks["5IKT"]["sha256"]
        checks["actual_experimental_coordinates_differ"] = True

        # A genuine old result is delayed until the newer selection has completed.
        await resolution(page, lambda: compound(page, "Aspirin"), "unavailable")
        ready, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        delayed = {}

        async def delay_real_reference(route):
            body = route.request.post_data_json
            if body["smiles"] != ref_by_pdb["5IKR"]["smiles"]:
                await route.continue_()
                return
            try:
                real = await route.fetch()
                actual = await real.json()
                delayed.update(compact(actual))
                assert actual["status"] == "matched"
                ready.set()
                await release.wait()
                try:
                    await route.fulfill(response=real)
                except Exception as exc:
                    delayed["delivery_cancelled"] = str(exc)
            finally:
                finished.set()

        await page.route("**/api/molecular/resolve", delay_real_reference)
        await page.get_by_test_id("studio-reference-select").select_option(ref_by_pdb["5IKR"]["id"])
        await asyncio.wait_for(ready.wait(), 40)
        assert not await page.get_by_test_id("molecule-canvas").count()
        await resolution(page, lambda: compound(page, "Aspirin"), "unavailable")
        release.set()
        await asyncio.wait_for(finished.wait(), 10)
        await page.wait_for_timeout(350)
        assert display["Aspirin"] in await page.get_by_test_id("selected-structure-context").inner_text()
        assert not await page.get_by_test_id("molecule-canvas").count()
        assert await page.get_by_test_id("studio-structure-unavailable").is_visible()
        await page.unroute("**/api/molecular/resolve", delay_real_reference)
        checks["late_previous_real_response_does_not_replace_current_selection"] = True
        checks["delayed_result"] = delayed

        # Conformer fallback and a second compound preserve actual distinct graphs.
        async with page.expect_response(lambda r: r.url.endswith("/api/molecular/conformer")) as conformer_response:
            await page.get_by_test_id("structure-fallback-conformer").click()
        conformer = await (await conformer_response.value).json()
        await settled(page)
        assert conformer["source"] == "rdkit_conformer"
        assert display["Aspirin"] in await page.get_by_test_id("selected-structure-context").inner_text()
        assert await page.get_by_role("button", name="단독 분자 3D SDF").count() == 1
        aspirin_atoms = len(conformer["atoms"])
        await compound(page, "Ibuprofen")  # Cached initial conformer.
        await settled(page)
        assert display["Ibuprofen"] in await page.get_by_test_id("selected-structure-context").inner_text()
        assert await page.get_by_test_id("selected-structure-context").get_attribute("data-structure-mode") == "rdkit_conformer"
        checks["free_conformer_fallback"] = {"selected": "Aspirin", "source": conformer["source"], "atoms": aspirin_atoms}

        # Direct archival inspection stays explicitly separate from compound lookup.
        await page.get_by_role("button", name="연구 기록", exact=True).click()
        archive_row = page.locator(".archive-row").filter(has_text=quarantined["id"][:16])
        await archive_row.get_by_role("button", name="결과 보기").click()
        async with page.expect_response(lambda r: f"/api/molecular/jobs/{quarantined['id']}/scene" in r.url) as archive_response:
            await page.get_by_role("button", name="3D 구조 열기").first.click()
        archive_scene = await (await archive_response.value).json()
        assert archive_scene["source"] == "structure_file"
        assert archive_scene["metadata"]["prediction_eligible"] is False
        assert archive_scene["metadata"]["confidence_kind"] is None
        assert "예측 사용 제외" in archive_scene["label"]
        await settled(page)
        assert await page.get_by_test_id("selected-structure-context").get_attribute("data-structure-mode") == "archive"
        assert "파일 직접 열기" in await page.get_by_test_id("selected-structure-context").inner_text()
        await compound(page, "Aspirin")
        await settled(page)
        assert await page.get_by_test_id("selected-structure-context").get_attribute("data-structure-mode") == "rdkit_conformer"
        checks["archive_file_is_separate_from_selection"] = True

        positive_reference = await resolution(page, lambda: page.get_by_test_id("studio-reference-select").select_option(ref_by_pdb["5IKR"]["id"]), "matched")
        await page.get_by_role("button", name="새 분석", exact=True).click()
        missing_target = await resolution(page, lambda: page.get_by_label("표적 UniProt ID").fill("Q05769"), "unavailable")
        await page.keyboard.press("Escape")
        assert missing_target["requested"]["target_accession"] == "Q05769"
        assert "Q05769" in await page.get_by_test_id("selected-structure-context").inner_text()
        await page.get_by_role("button", name="새 분석", exact=True).click()
        restored_target = await resolution(page, lambda: page.get_by_label("표적 UniProt ID").fill("P35354"), "matched")
        await page.keyboard.press("Escape")
        assert restored_target["scene"]["metadata"]["sha256"] == positive_reference["scene"]["metadata"]["sha256"]
        checks["target_change_never_reuses_another_species_structure"] = True

        mobile_context = await browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
        for pattern in ("analyses**", "af3/**", "molecular/predictions**", "quantum/**", "workflows"):
            await mobile_context.route(f"**/api/{pattern}", guard)
        mobile = await mobile_context.new_page()
        mobile.on("pageerror", lambda error: errors.append(str(error)))
        await mobile.goto(args.url, wait_until="networkidle")
        await settled(mobile)
        await mobile.get_by_test_id("studio-reference-select").select_option(ref_by_pdb["5IKT"]["id"])
        await settled(mobile)
        await mobile.get_by_test_id("viewer-focus-ligand").wait_for()
        await camera(mobile)
        await mobile.get_by_test_id("molecule-canvas").scroll_into_view_if_needed()
        await mobile.wait_for_timeout(350)
        mobile_canvas = await mobile.get_by_test_id("molecule-canvas").bounding_box()
        mobile_tools = await mobile.locator(".mv-tools").bounding_box()
        mobile_zoom = await mobile.locator(".mv-zoom-bar").bounding_box()
        assert mobile_canvas["height"] >= 600
        assert mobile_tools["y"] + mobile_tools["height"] <= mobile_zoom["y"]
        assert not await mobile.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        await mobile.locator(".molecule-workbench").screenshot(path=str(images / "molecular-selection-mobile.png"))
        checks["mobile_overflow"] = False
        checks["mobile_canvas_height"] = mobile_canvas["height"]
        checks["mobile_controls_do_not_overlap"] = True
        checks["javascript_errors"] = errors
        checks["execution_requests"] = forbidden
        assert not errors, errors
        assert not forbidden, forbidden
        await browser.close()

    result = {"passed": True, "verified_at": datetime.now(timezone.utc).isoformat(), "url": args.url, "checks": checks}
    destination.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
