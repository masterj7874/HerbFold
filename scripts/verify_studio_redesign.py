"""Verify the redesigned molecule studio against a running local server.

Uses existing Playwright/system Chrome when the in-app Browser has no connection.
Exercises only local molecular display; does not submit analyses, LLM generations,
AF3, candidate generation or QPU work. Screenshots and measured evidence go to tmp/.
"""

import argparse
import json
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright


def camera_distance(page):
    return float(page.get_by_test_id("viewer-zoom-level").get_attribute("data-camera-distance"))


def wait_distance(page, before, direction):
    page.wait_for_function(
        """({before, direction}) => {
          const output = document.querySelector('[data-testid="viewer-zoom-level"]');
          const current = Number(output?.dataset.cameraDistance);
          return Number.isFinite(current) && current > 0 &&
            (direction === 'in' ? current < before * 0.98 : current > before * 1.02);
        }""",
        arg={"before": before, "direction": direction},
    )
    return camera_distance(page)


def wait_fit(page, expected):
    page.wait_for_function(
        """expected => {
          const output = document.querySelector('[data-testid="viewer-zoom-level"]');
          return Math.abs(Number(output?.dataset.cameraDistance) / expected - 1) < 0.01;
        }""",
        arg=expected,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8873")
    parser.add_argument("--output", default="tmp/studio-redesign-verification.json")
    args = parser.parse_args()
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    errors, forbidden_requests = [], []
    checks = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=shutil.which("google-chrome") or shutil.which("chromium"),
            headless=True,
            args=["--no-sandbox", "--enable-unsafe-swiftshader"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page.on("pageerror", lambda error: errors.append(str(error)))

        def reject_execution(route):
            request = route.request
            if request.method == "POST":
                forbidden_requests.append(request.url)
                route.abort()
            else:
                route.continue_()

        for path in ("analyses**", "af3/**", "quantum/**", "workflows"):
            page.route(f"**/api/{path}", reject_execution)
        page.goto(args.url, wait_until="networkidle")
        page.wait_for_selector(".mv-canvas canvas")
        page.get_by_test_id("viewer-zoom-level").wait_for()
        assert not page.get_by_test_id("webgl-fallback").count()
        checks["frontend_assets"] = page.locator("script[src], link[rel=stylesheet]").evaluate_all(
            "elements => elements.map(element => element.src || element.href)"
        )
        checks["initial_catalog_count"] = page.locator(".compound-row").count()
        assert checks["initial_catalog_count"] >= 7
        page.get_by_test_id("viewer-reset").click()
        page.wait_for_function(
            "document.querySelector('[data-testid=viewer-zoom-level]')?.textContent.includes('100')"
        )
        fitted = camera_distance(page)
        assert fitted > 0
        checks["fit_distance_angstrom"] = fitted

        page.get_by_test_id("viewer-zoom-in").click()
        closer = wait_distance(page, fitted, "in")
        page.get_by_test_id("viewer-zoom-out").click()
        farther = wait_distance(page, closer, "out")
        checks["button_zoom_distances"] = [fitted, closer, farther]
        page.get_by_test_id("viewer-reset").click()
        wait_fit(page, fitted)

        canvas = page.get_by_test_id("molecule-canvas")
        canvas.scroll_into_view_if_needed()
        box = canvas.bounding_box()
        page.mouse.move(box["x"] + box["width"] * 0.55, box["y"] + box["height"] * 0.55)
        scroll_before = page.evaluate("window.scrollY")
        page.mouse.wheel(0, -350)
        wheel_closer = wait_distance(page, fitted, "in")
        assert abs(page.evaluate("window.scrollY") - scroll_before) <= 2
        page.mouse.wheel(0, 350)
        wheel_farther = wait_distance(page, wheel_closer, "out")
        checks["ordinary_wheel_zoom_distances"] = [fitted, wheel_closer, wheel_farther]
        checks["wheel_over_canvas_preserves_page_position"] = True

        page.get_by_test_id("viewer-reset").click()
        wait_fit(page, fitted)
        canvas.focus()
        page.keyboard.press("+")
        keyboard_closer = wait_distance(page, fitted, "in")
        page.keyboard.press("-")
        keyboard_farther = wait_distance(page, keyboard_closer, "out")
        page.keyboard.press("0")
        wait_fit(page, fitted)
        checks["keyboard_zoom_distances"] = [fitted, keyboard_closer, keyboard_farther]
        checks["keyboard_fit_restores_original_distance"] = True
        assert page.get_by_test_id("viewer-spin").is_disabled()
        checks["reduced_motion_disables_autorotation"] = True

        page.get_by_test_id("atom-search").fill("0")
        page.get_by_test_id("atom-search-submit").click()
        page.get_by_test_id("atom-inspector").wait_for()
        assert page.locator(".bond-details").inner_text()
        page.get_by_test_id("viewer-ruler").click()
        for atom_id in ("0", "1"):
            page.get_by_test_id("atom-search").fill(atom_id)
            page.get_by_test_id("atom-search-submit").click()
        distance_text = page.locator(".mv-ruler-instruction").inner_text()
        assert "Å" in distance_text and "↔" in distance_text
        checks["atom_selection_and_distance"] = distance_text
        page.get_by_test_id("viewer-ruler").click()
        page.get_by_role("button", name="원자 정보 닫기", exact=True).click()
        page.get_by_test_id("viewer-reset").click()
        for mode in ("stick", "spacefill", "ballstick"):
            page.get_by_test_id(f"representation-{mode}").click()
            assert page.get_by_test_id(f"representation-{mode}").get_attribute("aria-pressed") == "true"
        checks["representations"] = ["stick", "spacefill", "ballstick"]

        page.evaluate("window.scrollTo(0, 0)")
        page.mouse.move(1440 - 6, 500)
        page.mouse.wheel(0, 450)
        page.wait_for_function("window.scrollY > 5")
        checks["page_scroll_outside_canvas"] = True
        page.evaluate("window.scrollTo(0, 0)")

        opener = page.get_by_role("button", name="새 분석", exact=True)
        opener.click()
        dialog = page.get_by_role("dialog", name="새 에이전트 분석")
        dialog.wait_for()
        assert page.evaluate("document.querySelector('main').inert")
        page.keyboard.press("Shift+Tab")
        assert dialog.evaluate("element => element.contains(document.activeElement)")
        page.keyboard.press("Escape")
        dialog.wait_for(state="detached")
        assert opener.evaluate("element => element === document.activeElement")
        assert not page.evaluate("document.querySelector('main').inert")
        checks["analysis_dialog_focus_trap_and_restore"] = True

        original_width = page.locator(".molecule-workbench").bounding_box()["width"]
        page.get_by_test_id("studio-expand").click()
        assert page.locator(".studio-grid").evaluate("element => element.classList.contains('is-wide')")
        expanded_width = page.locator(".molecule-workbench").bounding_box()["width"]
        assert expanded_width > original_width + 100
        checks["expanded_viewer_widths"] = [original_width, expanded_width]
        page.get_by_test_id("studio-expand").click()
        assert page.locator(".compound-library").is_visible()
        assert page.locator(".molecule-inspector").is_visible()

        checks["responsive"] = []
        for width, height in ((1440, 1000), (1024, 900), (768, 1024), (390, 844), (360, 800)):
            page.set_viewport_size({"width": width, "height": height})
            page.get_by_test_id("viewer-zoom-in").scroll_into_view_if_needed()
            page.get_by_test_id("viewer-reset").click()
            page.wait_for_function(
                "document.querySelector('[data-testid=viewer-zoom-level]')?.textContent.includes('100')"
            )
            page.evaluate(
                "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
            )
            assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"), width
            zoom_rect = page.get_by_test_id("viewer-zoom-in").bounding_box()
            zoom_out_rect = page.get_by_test_id("viewer-zoom-out").bounding_box()
            assert zoom_rect["width"] >= 40 and zoom_rect["height"] >= 40, (width, zoom_rect)
            assert zoom_out_rect["width"] >= 40 and zoom_out_rect["height"] >= 40, (width, zoom_out_rect)
            assert zoom_rect["x"] >= 0 and zoom_rect["x"] + zoom_rect["width"] <= width + 1, (
                width,
                zoom_rect,
            )
            assert page.locator(".mv-zoom-bar").evaluate(
                "element => element.scrollWidth <= element.clientWidth + 1"
            ), width
            viewer_rect = page.get_by_test_id("molecule-viewer").bounding_box()
            for button in page.locator(".mv-zoom-bar > button").all():
                control_rect = button.bounding_box()
                assert control_rect["x"] >= viewer_rect["x"], (width, control_rect)
                assert control_rect["x"] + control_rect["width"] <= viewer_rect["x"] + viewer_rect["width"], (
                    width,
                    control_rect,
                )
            checks["responsive"].append(
                {
                    "width": width,
                    "height": height,
                    "horizontal_overflow": False,
                    "zoom_button_size": [zoom_rect["width"], zoom_rect["height"]],
                }
            )
            if width in (1440, 390):
                page.screenshot(path=str(destination.parent / f"studio-redesign-{width}.png"), full_page=True)

        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_test_id("viewer-reset").click()
        canvas.evaluate("element => element.scrollIntoView({block: 'center'})")
        page.wait_for_function(
            "document.querySelector('[data-testid=viewer-zoom-level]')?.textContent.includes('100')"
        )
        pinch_before = camera_distance(page)
        box = canvas.bounding_box()
        center_x = box["x"] + box["width"] / 2
        center_y = box["y"] + box["height"] * 0.52
        assert 40 < center_y < 800, box
        session = page.context.new_cdp_session(page)
        session.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 2})
        for index, spread in enumerate((45, 50, 60, 70, 80, 90)):
            session.send(
                "Input.dispatchTouchEvent",
                {
                    "type": "touchStart" if index == 0 else "touchMove",
                    "touchPoints": [
                        {"x": center_x - spread, "y": center_y, "id": 0},
                        {"x": center_x + spread, "y": center_y, "id": 1},
                    ],
                },
            )
        session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        pinch_after = wait_distance(page, pinch_before, "in")
        checks["chrome_emulated_two_touch_pinch_distances"] = [pinch_before, pinch_after]
        session.detach()

        assert not errors, errors
        assert not forbidden_requests, forbidden_requests
        browser.close()

    destination.write_text(
        json.dumps(
            {
                "passed": True,
                "checks": checks,
                "javascript_errors": errors,
                "analysis_submitted": False,
                "hardware_submitted": False,
                "forbidden_execution_requests": forbidden_requests,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(destination.read_text())


if __name__ == "__main__":
    main()
