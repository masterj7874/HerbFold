"""End-to-end v2 UI verification; submits only a local deterministic analysis.

Requires a running server and Playwright/system Chrome. Does not submit LLM,
AF3 or IBM jobs. Writes screenshots and an evidence JSON under tmp/ by default.
"""
import argparse
import json
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8873')
    parser.add_argument('--output', default='tmp/astra-ui-verification.json')
    args = parser.parse_args()
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    checks = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=shutil.which('google-chrome') or shutil.which('chromium'),
            headless=True, args=['--no-sandbox', '--enable-unsafe-swiftshader'],
        )
        page = browser.new_page(viewport={'width': 1600, 'height': 1100}, reduced_motion='reduce')
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(args.url, wait_until='networkidle')
        page.wait_for_selector('.mv-canvas canvas')
        assert page.locator('.compound-row').count() == 7
        checks['catalog'] = 7
        page.get_by_test_id('atom-search').fill('0')
        page.get_by_test_id('atom-search-submit').click()
        page.get_by_test_id('atom-inspector').wait_for()
        assert page.locator('.bond-details').inner_text()
        checks['atom_inspection'] = True
        page.get_by_test_id('viewer-ruler').click()
        for atom_id in ('0', '1'):
            page.get_by_test_id('atom-search').fill(atom_id)
            page.get_by_test_id('atom-search-submit').click()
        distance_text = page.locator('.mv-ruler-instruction').inner_text()
        assert 'Å' in distance_text and '↔' in distance_text
        checks['distance_label'] = distance_text
        for mode in ('stick', 'spacefill', 'ballstick'):
            page.get_by_test_id(f'representation-{mode}').click()
        page.get_by_test_id('viewer-zoom-in').click()
        page.get_by_test_id('viewer-reset').click()
        checks['representations_zoom_reset'] = True
        with page.expect_response(lambda response: response.url.endswith('/api/workflows') and response.request.method == 'POST') as generated:
            page.get_by_role('button', name='조각 재조합', exact=True).click()
        generated_count = len(generated.value.json()['result']['candidates'])
        page.wait_for_function('(count) => document.querySelectorAll(".candidate-card").length === count', arg=generated_count)
        checks['generated_candidates'] = generated_count
        assert checks['generated_candidates'] > 0
        page.locator('.candidate-card').first.click()
        page.wait_for_selector('.mv-canvas canvas')
        page.screenshot(path=str(destination.parent / 'astra-studio.png'), full_page=True)
        opener = page.get_by_role('button', name='새 분석', exact=True)
        opener.click()
        dialog = page.get_by_role('dialog', name='새 에이전트 분석')
        dialog.wait_for()
        assert page.evaluate('document.querySelector("main").inert')
        page.keyboard.press('Shift+Tab')
        assert page.evaluate('document.querySelector("[role=dialog]").contains(document.activeElement)')
        page.keyboard.press('Escape')
        dialog.wait_for(state='detached')
        assert opener.evaluate('(el) => document.activeElement === el')
        assert not page.evaluate('document.querySelector("main").inert')
        checks['modal_focus_trap_restore'] = True
        opener.click()
        page.get_by_label('오케스트레이터').select_option('local')
        page.get_by_role('button', name='에이전트 분석 시작').click()
        page.wait_for_function('document.querySelectorAll(".agent-node.completed").length === 8')
        assert '0 LLM 호출' in page.locator('.run-metadata').inner_text()
        checks['local_analysis_stages'] = 8
        checks['local_analysis_llm_calls'] = 0
        page.get_by_role('button', name='실측 · 검증', exact=True).click()
        page.get_by_role('button', name='PTGS2 실측 예제', exact=True).click()
        page.wait_for_function('document.querySelector(".code-textarea")?.value.startsWith("[")')
        page.get_by_role('button', name='중복·assay 점검', exact=True).click()
        page.wait_for_selector('.results-json')
        checks['audit_rendered'] = '21' in page.locator('.results-json').inner_text()
        page.get_by_role('button', name='평가', exact=True).click()
        page.wait_for_function('document.querySelector(".results-json")?.textContent.includes("rmse")')
        checks['measured_evaluation'] = True
        page.screenshot(path=str(destination.parent / 'astra-evidence.png'), full_page=True)
        page.get_by_role('button', name='연구 기록', exact=True).click()
        page.get_by_role('button', name='단백질 구조', exact=True).click()
        checks['archive_structure_rows'] = page.locator('.archive-row:not(.table-head)').count()
        page.get_by_role('button', name='분자 스튜디오', exact=True).click()
        page.get_by_role('button', name='COX-2 실험 복합체').click()
        page.wait_for_function('document.querySelector(".mv-title-block")?.textContent.includes("5IKR")')
        page.get_by_test_id('representation-cartoon').click()
        page.get_by_test_id('viewer-settings').click()
        assert page.get_by_test_id('viewer-color-mode').locator('option[value=confidence]').is_disabled()
        checks['experimental_pdb_no_plddt'] = True
        page.get_by_test_id('viewer-settings').click()
        page.screenshot(path=str(destination.parent / 'astra-protein.png'), full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        checks['mobile_overflow'] = False
        page.screenshot(path=str(destination.parent / 'astra-mobile.png'), full_page=True)
        assert not errors, errors
        browser.close()
    destination.write_text(json.dumps({'passed': True, 'checks': checks, 'javascript_errors': errors,
                                      'llm_submitted': False, 'hardware_submitted': False}, ensure_ascii=False, indent=2))
    print(destination.read_text())


if __name__ == '__main__':
    main()
