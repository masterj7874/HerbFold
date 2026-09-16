"""Optional browser regression check; never submits an AF3 or IBM hardware job.

Run with a running local server and Playwright installed:
    python scripts/verify_ui.py --url http://127.0.0.1:8873
"""

import argparse
import json
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8873")
    parser.add_argument("--browser", default=shutil.which("google-chrome") or shutil.which("chromium"))
    parser.add_argument("--output", default="tmp/ui-verification.json")
    args = parser.parse_args()
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=args.browser, headless=True, args=["--no-sandbox", "--enable-unsafe-swiftshader"]
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url, wait_until="networkidle")
        page.wait_for_selector(".compound-card")
        catalog_count = page.locator(".compound-card").count()
        assert catalog_count == 7
        page.get_by_role("button", name="비교 · 후보 생성").click()
        page.wait_for_selector("#discovery-results:not([hidden])")
        candidates = page.locator("#candidate-grid .compound-card").count()
        assert candidates > 0
        page.get_by_role("button", name="Quantum Lab").click()
        page.get_by_role("button", name="커널 계산").click()
        page.wait_for_selector(".kernel div")
        assert page.locator(".kernel div").count() == 9
        assert page.locator(".kernel div").first.inner_text() == "1.000"
        page.get_by_role("button", name="분자 탐색").click()
        page.set_viewport_size({"width": 390, "height": 844})
        assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        assert not errors, errors
        browser.close()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "passed": True,
                "catalog_count": catalog_count,
                "candidates": candidates,
                "kernel_cells": 9,
                "mobile_overflow": False,
                "javascript_errors": errors,
                "hardware_submitted": False,
            },
            indent=2,
        )
    )
    print(f"Browser checks passed; {output}")


if __name__ == "__main__":
    main()
