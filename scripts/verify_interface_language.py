"""Verify the English/Korean interface of every page against the running local server.

Read-only: every non-GET API call is blocked except side-effect-free local molecular
computations, so no AlphaFold, LLM, QPU, design or assay work is submitted. Saved records
are reopened, never recomputed. Research values (compound names entered by the researcher,
herb names, run and campaign names, SMILES, identifiers) are expected to stay unchanged in
both languages and are reported separately from untranslated interface text.

    uv run --extra dev python scripts/verify_interface_language.py --output docs/interface-language-verification.json

The exit status is non-zero when interface text is still untranslated in English mode, when a
view scrolls horizontally, when a language switch loses state, or when a page error occurs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
WORKSPACES = [
    "alphafold",
    "design-pipeline",
    "comparison",
    "quantum",
    "discovery",
    "validation",
    "agents",
    "evidence",
    "archive",
]
# Local, side-effect-free computations: structure rendering and fingerprint comparison.
SAFE_POST = {
    "/api/molecular/resolve",
    "/api/molecular/conformer",
    "/api/molecules/describe",
    "/api/molecules/svg",
    "/api/molecules/compare",
    "/api/molecular/compare",
    "/api/compare/structures",
}
HANGUL = re.compile("[가-힣]")

# Text nodes and translatable attributes that still contain Hangul while the page is in English.
SCAN = """() => {
  const visible = (el) => { if (!el) return false; const r = el.getBoundingClientRect();
    const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden'; };
  const found = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode, parent = node.parentElement;
    if (!parent || !/[가-힣]/.test(node.textContent || '')) continue;
    if (parent.closest('script,style,pre,code,textarea')) continue;
    const inOption = !!parent.closest('option');
    if (!inOption && !visible(parent)) continue;
    if (parent.closest('.language-switcher, .lang-switch')) continue;
    const research = parent.closest('[data-research-value]');
    found.push({ kind: inOption ? 'option' : 'text', text: node.textContent.trim(), tag: parent.tagName,
      testid: parent.closest('[data-testid]')?.dataset?.testid || '',
      research: research ? research.dataset.researchValue : null });
  }
  for (const el of document.querySelectorAll('[title],[aria-label],[placeholder],[alt],[aria-description],[aria-valuetext]')) {
    for (const attr of ['title', 'aria-label', 'placeholder', 'alt', 'aria-description', 'aria-valuetext']) {
      const value = el.getAttribute(attr);
      if (!value || !/[가-힣]/.test(value) || !(visible(el) || el.tagName === 'INPUT')) continue;
      // The language control names each language in that language; that is the control, not untranslated text.
      if (el.closest('.language-switcher, .lang-switch')) continue;
      const research = el.closest('[data-research-value]');
      found.push({ kind: 'attr:' + attr, text: value.trim(), tag: el.tagName,
        testid: el.closest('[data-testid]')?.dataset?.testid || '',
        research: research ? research.dataset.researchValue : null });
    }
  }
  if (/[가-힣]/.test(document.title)) found.push({ kind: 'title', text: document.title, tag: 'TITLE', testid: '' });
  for (const el of document.querySelectorAll('input,textarea'))
    if (/[가-힣]/.test(el.value || ''))
      found.push({ kind: 'input-value', text: (el.value || '').slice(0, 120), tag: el.tagName,
        testid: el.dataset?.testid || '', research: 'researcher-input' });
  return found.filter((item) => item.text);
}"""


def research_values(url: str, token: str) -> set[str]:
    """Korean values that belong to the research records and are shown verbatim in both languages."""
    values: set[str] = set()
    aliases = ROOT / "src/herbfold/herb_aliases.py"
    if aliases.is_file():
        text = aliases.read_text(encoding="utf-8")
        for pattern in (r'"([^"]*[가-힣][^"]*)"', r"'([^']*[가-힣][^']*)'"):
            values.update(match.group(1).strip() for match in re.finditer(pattern, text))
    for path in ("/api/catalog",):
        try:
            request = urllib.request.Request(url.rstrip("/") + path, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - loopback only
                payload = json.loads(response.read())
        except Exception as error:  # noqa: BLE001 - the allow-list is optional evidence
            print(f"Research allow-list unavailable from {path}: {error}", flush=True)
            continue
        for item in payload if isinstance(payload, list) else []:
            if item.get("name_ko"):
                values.add(item["name_ko"].strip())
            for example in item.get("botanical_examples") or []:
                for key in ("herb_name_ko", "herb_ko"):
                    if example.get(key):
                        values.add(example[key].strip())
    return values


def verify(args, report) -> None:  # noqa: C901, PLR0915 - one linear browser session
    from playwright.sync_api import expect, sync_playwright

    expect.set_options(timeout=45000)
    allowed = research_values(args.url, args.token_file.read_text().strip())
    report["research_values_allowed"] = len(allowed)

    def is_research(text: str) -> bool:
        stripped = text.strip()
        if stripped in allowed:
            return True
        parts = [part for part in re.split(r"\s*[·,/×]\s*", stripped) if HANGUL.search(part)]
        return bool(parts) and all(part in allowed for part in parts)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=args.chrome, headless=True,
            args=["--no-sandbox", "--enable-unsafe-swiftshader"],
        )
        context = browser.new_context(
            viewport={"width": 1600, "height": 1000}, reduced_motion="reduce",
            extra_http_headers={"Authorization": "Bearer " + args.token_file.read_text().strip()},
        )

        def guard(route):
            path = urlparse(route.request.url).path
            if route.request.method in {"GET", "HEAD", "OPTIONS"}:
                return route.continue_()
            if route.request.method == "POST" and path in SAFE_POST:
                report["local_computations"].append(path)
                return route.continue_()
            report["blocked_mutations"].append(path)
            route.abort()

        context.route("**/api/**", guard)
        page = context.new_page()
        page.set_default_timeout(45000)
        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))

        def settle(ms: int = 1200) -> None:
            page.wait_for_timeout(ms)
            try:
                page.wait_for_function("() => !document.querySelector('[aria-busy=\"true\"]')", timeout=20000)
            except Exception:  # noqa: BLE001 - a busy panel is reported by the scan itself
                pass
            page.wait_for_timeout(250)

        def inspect(view: str, note: str = "", target_page=None) -> None:
            scanned = target_page or page
            entry = report["views"].setdefault(view, {"untranslated": [], "research_values": [], "horizontal_overflow": False})
            seen = {(item["kind"], item["text"]) for item in entry["untranslated"] + entry["research_values"]}
            for item in scanned.evaluate(SCAN):
                key = (item["kind"], item["text"])
                if key in seen:
                    continue
                seen.add(key)
                bucket = "research_values" if item.get("research") or is_research(item["text"]) else "untranslated"
                entry[bucket].append({**item, "where": note})
            entry["horizontal_overflow"] = entry["horizontal_overflow"] or scanned.evaluate(
                "document.documentElement.scrollWidth > innerWidth + 1"
            )
            (args.output.parent / args.output.name).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")

        def click_if(selector: str, timeout: int = 3000) -> bool:
            locator = page.locator(selector).first
            try:
                if locator.count() and locator.is_visible():
                    locator.click(timeout=timeout)
                    return True
            except Exception:  # noqa: BLE001 - optional control for this record
                pass
            return False

        def expand(view: str) -> None:
            opened = page.evaluate("() => { let n = 0; for (const d of document.querySelectorAll('details:not([open])')) { d.open = true; n++; } return n; }")
            for _ in range(25):
                button = page.locator('button[aria-expanded="false"]').first
                try:
                    if not (button.count() and button.is_visible()):
                        break
                    button.click(timeout=2000)
                    page.wait_for_timeout(200)
                except Exception:  # noqa: BLE001
                    break
            settle(400)
            inspect(view, f"expanded {opened} details")

        def english() -> None:
            page.get_by_test_id("language-en").click()
            expect(page.locator("html")).to_have_attribute("lang", "en")

        def roundtrip() -> None:
            """EN → KO → EN must not submit a request or change the selected record."""
            before = len(report["blocked_mutations"]) + len(report["local_computations"])
            page.get_by_test_id("language-ko").click()
            expect(page.locator("html")).to_have_attribute("lang", "ko")
            expect(page.get_by_test_id("language-ko")).to_have_attribute("aria-pressed", "true")
            english()
            expect(page.get_by_test_id("language-en")).to_have_attribute("aria-pressed", "true")
            report["switch_requests"].append(len(report["blocked_mutations"]) + len(report["local_computations"]) - before)

        def workspace(tab: str) -> None:
            page.locator(f'[data-workspace="{tab}"]').first.click()
            expect(page.get_by_test_id(f"workspace-{tab}")).to_be_visible()
            settle()

        page.goto(args.url.rstrip("/") + "/#alphafold", wait_until="domcontentloaded")
        settle(2500)
        english()
        settle(1200)
        inspect("shell", "initial")

        # Studio form state, the WebGL scene and the saved selection survive a switch.
        workspace("alphafold")
        settle(2000)
        inspect("alphafold", "initial")
        if page.get_by_test_id("alphafold-open-calculation").count():
            click_if('[data-testid="alphafold-open-calculation"]')
            settle(1500)
            inspect("alphafold", "af3 calculation")
        mode = page.get_by_test_id("studio-af3-mode")
        if mode.count():
            mode.select_option("none")
            page.get_by_test_id("studio-af3-exploratory-ack").check()
            page.get_by_test_id("studio-af3-seed").fill("0")
            page.evaluate("window.__scene = document.querySelector('.molecular-viewer canvas')")
            selection = page.evaluate("JSON.parse(sessionStorage.getItem('herbfold.studio.selection.v1'))")
            roundtrip()
            expect(page.get_by_test_id("studio-af3-mode")).to_have_value("none")
            expect(page.get_by_test_id("studio-af3-exploratory-ack")).to_be_checked()
            expect(page.get_by_test_id("studio-af3-seed")).to_have_value("0")
            report["state_preserved"]["studio"] = {
                "mode": True, "acknowledgement": True, "seed_zero": True,
                "same_canvas_node": page.evaluate("window.__scene === document.querySelector('.molecular-viewer canvas')"),
                "selection_unchanged": selection == page.evaluate("JSON.parse(sessionStorage.getItem('herbfold.studio.selection.v1'))"),
            }
            mode.select_option("search")
            page.get_by_test_id("studio-af3-seed").fill("1")
        query = page.get_by_test_id("studio-compound-query")
        if query.count() and query.is_visible():
            for term in ("quercetin", "황금"):
                query.fill(term)
                query.press("Enter")
                settle(2000)
                inspect("alphafold", f"library search {term}")
            query.fill("")
            query.press("Enter")
            settle(800)
        expand("alphafold")

        for tab in WORKSPACES:
            if tab != "alphafold":
                workspace(tab)
                settle(1800)
                inspect(tab, "initial")
            if tab == "design-pipeline":
                click_if(".ddp-topline button")
                settle(1000)
                inspect(tab, "run history")
                if click_if(".ddp-history-list button"):
                    settle(2500)
                    inspect(tab, "saved run")
                    name = page.locator(".ddp-form-grid input").first
                    if name.count():
                        name.fill("연구 입력 · C[C@H](O)F · 0")
                        candidates = page.locator(".ddp-candidate-list").inner_text()
                        roundtrip()
                        report["state_preserved"]["design_pipeline"] = {
                            "researcher_input_unchanged": name.input_value() == "연구 입력 · C[C@H](O)F · 0",
                            "candidate_list_unchanged": candidates == page.locator(".ddp-candidate-list").inner_text(),
                        }
                        name.fill("Natural-product structure exploration")
                click_if(".ddp-candidate-list button")
                settle(1200)
                inspect(tab, "candidate")
            if tab == "comparison":
                click_if('.studio-compound-views button:has-text("My List")')
                settle(1000)
                for compound in ("Quercetin", "Aspirin"):
                    checkbox = page.locator(f'.selection-check[aria-label*="{compound}"]').first
                    if checkbox.count():
                        checkbox.click()
                        settle(500)
                if click_if('[data-testid="compare-structures"]', timeout=8000):
                    settle(4000)
                    inspect(tab, "structural comparison")
            if tab == "quantum":
                record = page.get_by_test_id("quantum-studio-workarea")
                record_id = record.get_attribute("data-record-id") if record.count() else None
                for testid in ("quantum-plan-button", "quantum-recompute-open", "quantum-projected-collapse", "quantum-global-collapse"):
                    if click_if(f'[data-testid="{testid}"]'):
                        settle(700)
                        inspect(tab, testid)
                steps = page.locator('[data-testid="quantum-journey"] button')
                for index in range(min(steps.count(), 14)):
                    try:
                        steps.nth(index).click(timeout=1500)
                        settle(350)
                    except Exception:  # noqa: BLE001
                        pass
                inspect(tab, "walkthrough steps")
                if record_id:
                    roundtrip()
                    report["state_preserved"]["quantum_record"] = record.get_attribute("data-record-id") == record_id
            if tab == "discovery":
                search = page.get_by_test_id("discovery-search")
                if search.count():
                    for term in ("황금", "quercetin"):
                        search.fill(term)
                        click_if('[data-testid="discovery-search-submit"]')
                        settle(2500)
                        inspect(tab, f"search {term}")
                if click_if('[data-testid="discovery-campaigns-tab"]'):
                    settle(1500)
                    inspect(tab, "campaigns")
                click_if('[data-testid="discovery-new-campaign"]')
                settle(800)
                inspect(tab, "campaign form")
                click_if(".ds-campaign-history button")
                settle(1200)
                inspect(tab, "campaign detail")
            if tab == "validation":
                tabs = page.locator('[data-testid^="validation-tab-"]')
                for testid in [tabs.nth(i).get_attribute("data-testid") for i in range(tabs.count())]:
                    click_if(f'[data-testid="{testid}"]')
                    settle(1500)
                    inspect(tab, testid)
                click_if('[data-testid="validation-inspect-candidate"]')
                settle(1500)
                inspect(tab, "candidate detail")
            if tab == "agents":
                detail = page.get_by_test_id("af3-workflow-detail")
                workflow_id = detail.get_attribute("data-workflow-id") if detail.count() else None
                for testid in ("af3-workflow-refresh", "af3-workflow-log"):
                    if click_if(f'[data-testid="{testid}"]'):
                        settle(1000)
                        inspect(tab, testid)
                if workflow_id:
                    roundtrip()
                    report["state_preserved"]["agent_workflow"] = {
                        "workflow_id": workflow_id,
                        "preserved": detail.get_attribute("data-workflow-id") == workflow_id,
                        "job_id": detail.get_attribute("data-job-id"),
                    }
            if tab in {"evidence", "archive"}:
                buttons = page.locator(f'[data-testid="workspace-{tab}"] button')
                for index in range(min(buttons.count(), 6)):
                    try:
                        button = buttons.nth(index)
                        if button.is_visible():
                            button.click(timeout=1500)
                            settle(600)
                    except Exception:  # noqa: BLE001
                        pass
                inspect(tab, "records opened")
            if tab != "alphafold":
                expand(tab)

        # Phone width: the longer English labels must not force horizontal scrolling.
        page.set_viewport_size({"width": 390, "height": 844})
        for tab in WORKSPACES:
            workspace(tab)
            settle(1500)
            inspect(f"mobile-{tab}", "initial")
        page.set_viewport_size({"width": 1600, "height": 1000})

        # The preference survives a reload and synchronizes between tabs of the same origin.
        page.reload(wait_until="domcontentloaded")
        settle(2500)
        expect(page.locator("html")).to_have_attribute("lang", "en")
        second = context.new_page()
        second.goto(args.url.rstrip("/") + "/#alphafold", wait_until="domcontentloaded")
        second.wait_for_timeout(2500)
        expect(second.locator("html")).to_have_attribute("lang", "en")
        second.get_by_test_id("language-ko").click()
        expect(page.locator("html")).to_have_attribute("lang", "ko")
        second.get_by_test_id("language-en").click()
        expect(page.locator("html")).to_have_attribute("lang", "en")
        second.close()
        report["preference"] = {"restored_on_reload": True, "synchronized_between_tabs": True}

        # The switch still works when the browser blocks storage.
        blocked = context.new_page()
        blocked.add_init_script("Object.defineProperty(window, 'localStorage', { get() { throw new Error('blocked'); } })")
        blocked.goto(args.url.rstrip("/") + "/#alphafold", wait_until="domcontentloaded")
        blocked.wait_for_timeout(2500)
        blocked.get_by_test_id("language-en").click()
        expect(blocked.locator("html")).to_have_attribute("lang", "en")
        report["preference"]["works_without_storage"] = True
        blocked.close()

        # Legacy compatibility view at /legacy shares the stored preference.
        legacy = context.new_page()
        legacy.on("pageerror", lambda error: report["page_errors"].append("legacy: " + str(error)))
        legacy.goto(args.url.rstrip("/") + "/legacy", wait_until="domcontentloaded")
        legacy.wait_for_timeout(3000)
        legacy.get_by_test_id("legacy-language-en").click()
        legacy.wait_for_timeout(1200)
        inspect("legacy", "english", target_page=legacy)
        legacy.locator('button.nav[data-tab="structure"]').click()
        legacy.wait_for_timeout(700)
        legacy.locator("#af3-name").fill("herbfold_case_1")
        legacy.locator("#msa-mode").select_option("none")
        legacy.get_by_test_id("legacy-language-ko").click()
        legacy.wait_for_timeout(700)
        report["legacy"] = {
            "english_title": None,
            "korean_restored": legacy.locator("html").get_attribute("lang") == "ko",
            "form_preserved": legacy.locator("#af3-name").input_value() == "herbfold_case_1"
            and legacy.locator("#msa-mode").input_value() == "none",
        }
        legacy.get_by_test_id("legacy-language-en").click()
        legacy.wait_for_timeout(700)
        report["legacy"]["english_title"] = legacy.title()
        report["legacy"]["roundtrip"] = legacy.locator("html").get_attribute("lang") == "en"
        legacy.close()

        context.close()
        browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:9018")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/interface-language-verification.json")
    parser.add_argument("--token-file", type=Path, default=ROOT / "runtime/access-token.txt")
    parser.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = parser.parse_args()
    if urlparse(args.url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("This verification runs against the local research server only.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "timestamp": datetime.now(UTC).isoformat(),
        "url": args.url,
        "views": {},
        "page_errors": [],
        "blocked_mutations": [],
        "local_computations": [],
        "switch_requests": [],
        "state_preserved": {},
        "preference": {},
        "legacy": {},
    }
    try:
        verify(args, report)
    finally:
        untranslated = {view: data["untranslated"] for view, data in report["views"].items() if data["untranslated"]}
        overflow = [view for view, data in report["views"].items() if data["horizontal_overflow"]]
        lost_state = [key for key, value in report["state_preserved"].items() if value is False or (isinstance(value, dict) and not all(value.values()))]
        report["summary"] = {
            "views_checked": len(report["views"]),
            "untranslated_interface_text": sum(len(items) for items in untranslated.values()),
            "research_values_kept": sum(len(data["research_values"]) for data in report["views"].values()),
            "horizontal_overflow": overflow,
            "state_lost_on_switch": lost_state,
            "page_errors": report["page_errors"],
            "requests_caused_by_switching": sum(report["switch_requests"]),
            "blocked_mutations": sorted(set(report["blocked_mutations"])),
            "local_computations": sorted(set(report["local_computations"])),
        }
        report["passed"] = not (
            untranslated or overflow or lost_state or report["page_errors"] or sum(report["switch_requests"])
        )
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
        print(json.dumps({"passed": report.get("passed"), **report["summary"]}, ensure_ascii=False, indent=1))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
