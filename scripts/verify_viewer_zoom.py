"""Verify real camera zoom, viewport wheel isolation, keyboard controls and touch pinch.

Run with the UI server active: python scripts/verify_viewer_zoom.py --url http://127.0.0.1:8873
No LLM, AF3 or QPU jobs are submitted.
"""

import argparse
import json
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright


def camera_distance(page):
    return float(page.get_by_test_id("viewer-zoom-level").get_attribute("data-camera-distance"))


def assert_distance(page, reference, comparator):
    page.wait_for_function(
        """({reference, comparator}) => {
            const distance = Number(document.querySelector('[data-testid="viewer-zoom-level"]').dataset.cameraDistance);
            return comparator === 'less' ? distance < reference * .97 : distance > reference * 1.03;
        }""",
        arg={"reference": reference, "comparator": comparator},
        timeout=5000,
    )
    page.wait_for_timeout(450)
    return camera_distance(page)


def reset_view(page):
    page.get_by_test_id("viewer-reset").click()
    page.wait_for_timeout(850)
    return camera_distance(page)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8873")
    parser.add_argument("--output", default="tmp/viewer-zoom-verification.json")
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=shutil.which("google-chrome") or shutil.which("chromium"),
            headless=True,
            args=["--no-sandbox", "--enable-unsafe-swiftshader"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1080})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url, wait_until="networkidle")
        page.get_by_test_id("viewer-zoom-level").wait_for()
        page.wait_for_timeout(1000)
        canvas = page.get_by_test_id("molecule-canvas")
        canvas.scroll_into_view_if_needed()
        original = reset_view(page)
        page.get_by_test_id("viewer-zoom-in").click()
        button_in = assert_distance(page, original, "less")
        page.get_by_test_id("viewer-zoom-out").click()
        button_out = assert_distance(page, button_in, "greater")
        fitted = reset_view(page)
        assert abs(fitted / original - 1) < .01
        box = canvas.bounding_box()
        page.mouse.move(box["x"] + box["width"] * .52, box["y"] + box["height"] * .52)
        scroll_before = page.evaluate("window.scrollY")
        page.mouse.wheel(0, -320)
        wheel_in = assert_distance(page, fitted, "less")
        assert abs(page.evaluate("window.scrollY") - scroll_before) < 2
        page.mouse.wheel(0, 320)
        wheel_out = assert_distance(page, wheel_in, "greater")
        assert abs(page.evaluate("window.scrollY") - scroll_before) < 2
        canvas.focus()
        canvas.press("+")
        key_in = assert_distance(page, wheel_out, "less")
        canvas.press("-")
        key_out = assert_distance(page, key_in, "greater")
        canvas.press("0")
        page.wait_for_timeout(850)
        assert abs(camera_distance(page) / original - 1) < .01
        # Wheel away from the viewport still scrolls the page.
        page.evaluate("window.scrollTo(0, 0)")
        page.mouse.move(350, 150)
        page.mouse.wheel(0, 400)
        page.wait_for_timeout(450)
        assert page.evaluate("window.scrollY") > 20
        canvas.scroll_into_view_if_needed()
        page.screenshot(path=str(output.with_name("viewer-desktop.png")), full_page=True)

        # Native OrbitControls receives two genuine touch pointers from Chromium.
        mobile = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        mobile.on("pageerror", lambda error: errors.append(str(error)))
        mobile.goto(args.url, wait_until="networkidle")
        mobile.get_by_test_id("viewer-zoom-level").wait_for()
        mobile.get_by_test_id("molecule-canvas").scroll_into_view_if_needed()
        mobile.wait_for_timeout(1000)
        mobile_original = camera_distance(mobile)
        box = mobile.get_by_test_id("molecule-canvas").bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] * .52
        session = mobile.context.new_cdp_session(mobile)
        def points(spread):
            return [{"x": x - spread, "y": y, "id": 1}, {"x": x + spread, "y": y, "id": 2}]
        session.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": points(30)})
        for spread in (38, 48, 60, 75):
            session.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": points(spread)})
            mobile.wait_for_timeout(40)
        session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        pinch_in = assert_distance(mobile, mobile_original, "less")
        assert not mobile.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        reset_view(mobile)
        mobile.screenshot(path=str(output.with_name("viewer-mobile.png")), full_page=True)

        reduced = browser.new_page(viewport={"width": 1440, "height": 1080}, reduced_motion="reduce")
        reduced.goto(args.url, wait_until="networkidle")
        reduced.get_by_test_id("viewer-zoom-level").wait_for()
        reduced.wait_for_timeout(500)
        reduced_original = camera_distance(reduced)
        assert reduced.get_by_test_id("viewer-spin").is_disabled()
        reduced.get_by_test_id("viewer-zoom-in").click()
        reduced_zoom = assert_distance(reduced, reduced_original, "less")
        assert not errors, errors
        browser.close()
    result = {
        "passed": True,
        "camera_distance_angstrom": {"fit": original, "button_in": button_in, "button_out": button_out,
                                     "wheel_in": wheel_in, "wheel_out": wheel_out, "keyboard_in": key_in,
                                     "keyboard_out": key_out, "mobile_fit": mobile_original,
                                     "pinch_in": pinch_in, "reduced_motion_fit": reduced_original,
                                     "reduced_motion_zoom": reduced_zoom},
        "wheel_prevents_viewport_page_scroll": True,
        "wheel_outside_viewport_scrolls_page": True,
        "keyboard_fit_restored": True,
        "mobile_overflow": False,
        "javascript_errors": errors,
        "hardware_submitted": False,
    }
    output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
