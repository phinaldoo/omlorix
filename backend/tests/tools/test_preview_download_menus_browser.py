"""Shared sidebar download actions, using production markup and dropdown code."""
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
import pytest


def test_preview_download_icons_and_format_actions(tmp_path):
    sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright
    from playwright.sync_api import expect

    frontend = Path(__file__).resolve().parents[3] / 'frontend'
    index = BeautifulSoup((frontend / 'index.html').read_text(), 'html.parser')
    controls = ''.join(str(index.select_one(selector)) for selector in [
        '.notes-download-controls', '#deepResearchExportControls',
        '#latex-pdf-PreviewDownload', '#filesSidebarPreviewDownload',
    ])
    notes_source = (frontend / 'js/chat/notes/sidebar.js').read_text()
    notes_markup = notes_source.split('panel.innerHTML = `', 1)[1].split('`;', 1)[0]
    shell = '''<!doctype html><html data-mode="dark"><head><meta charset="utf-8">
    <link rel="stylesheet" href="/css/common/init.css">
    <link rel="stylesheet" href="/css/common/elements.css">
    <link rel="stylesheet" href="/css/common/elementsNew.css">
    <link rel="stylesheet" href="/css/chat/canvas-widget.css">
    <link rel="stylesheet" href="/css/chat/notes.css">
    <link rel="stylesheet" href="/css/chat/deep-research-widget.css">
    <link rel="stylesheet" href="/css/chat/slide-presentation-widget.css">
    <style>body{margin:0;padding:24px}#canvas{width:720px;max-width:100%;container-type:inline-size;container-name:canvas-preview}#other{display:flex;align-items:center;gap:16px;margin-top:30px}#notes-tool{margin-top:30px}</style>
    </head><body><div id="canvas"></div><div id="notes-tool"></div><div id="other">''' + controls + '''</div>
    <script src="/js/common/icons.js"></script><script src="/js/common/dropdown.js"></script>
    <script src="/js/chat/downloadControls.js"></script><script src="/js/chat/canvas-widget/header.js"></script>
    <script>function escapeHtml(text){const element=document.createElement('span');element.textContent=text;return element.innerHTML;}
    __omlorixCanvasWidgetModules.header.ensureCanvasPreviewHeader(document.querySelector('#canvas'));
    </script></body></html>'''
    errors = []

    def route_request(route):
        path = urlsplit(route.request.url).path
        if path == '/':
            route.fulfill(body=shell, content_type='text/html')
        else:
            route.fulfill(body=(frontend / path[1:]).read_text(), content_type='text/javascript' if path.startswith('/js/') else 'text/css')

    with sync_playwright() as browser_api:
        if not Path(browser_api.chromium.executable_path).exists():
            pytest.skip('Install Playwright Chromium for the browser smoke test')
        browser = browser_api.chromium.launch()
        page = browser.new_page(viewport={'width': 1100, 'height': 700})
        page.route('**/*', route_request)
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto('http://downloads.test/')
            page.evaluate('''markup => {
                const NotesRender={escapeHtml}; const notesT=(_key,fallback)=>fallback;
                document.querySelector('#notes-tool').innerHTML=eval('`'+markup+'`');
                window.downloads=[];
            }''', notes_markup)
            for select_id, button_id in [
                ('canvas-markdown-DownloadFormat', 'canvas-markdown-PreviewDownload'),
                ('notesDownloadFormat', 'notesDownloadBtn'),
                ('notes-tool-DownloadFormat', 'notes-tool-PreviewDownload'),
                ('deepResearchExportFormat', 'deepResearchExportButton'),
            ]:
                page.evaluate('''([selectId,buttonId]) => {
                    const select=document.getElementById(selectId), button=document.getElementById(buttonId);
                    select.parentElement.hidden=false;select.hidden=false;
                    chatDownloadControls.setDownloadControlsEnabled({button,select,enabled:true,disabledClass:'disabled'});
                    chatDownloadControls.bindDownloadFormatMenu(select, {downloadButton:button, onDownload:async()=>{
                        downloads.push([buttonId,select.value]);
                        chatDownloadControls.setDownloadBusy({button,select,busy:true,enabled:true});
                        await new Promise(resolve=>window.finishDownload=resolve);
                        chatDownloadControls.setDownloadBusy({button,select,busy:false,enabled:true});
                    }});
                }''', [select_id, button_id])
                button = page.locator('#' + button_id)
                expect(page.locator('#' + select_id)).to_be_hidden()
                assert button.inner_text().strip() == ''
                assert button.locator('svg').count() == 1
                assert button.evaluate("element => element.classList.contains('om-button')")
                assert button.evaluate('element => getComputedStyle(element).height') == '36px'
                button.click()
                menu = page.get_by_role('menu')
                expect(menu.get_by_role('menuitem')).to_have_count(2)
                expect(menu.get_by_role('menuitem').first).to_be_focused()
                menu.get_by_role('menuitem').first.press('End')
                expect(menu.get_by_role('menuitem').last).to_be_focused()
                menu.get_by_role('menuitem').last.press('Escape')
                expect(button).to_be_focused()
                button.click()
                menu.get_by_role('menuitem', name='PDF', exact=True).click()
                expect(button).to_be_disabled()
                expect(button).to_have_attribute('aria-busy', 'true')
                assert page.evaluate('downloads.at(-1)') == [button_id, 'pdf']
                page.evaluate('finishDownload()')
                expect(button).to_be_enabled()
                expect(button).to_be_focused()
                assert button.inner_text().strip() == ''
                assert button.locator('svg').count() == 1
            canvas = page.locator('#canvas-markdown-PreviewDownload')
            # Preserve per-format availability (e.g. stale LaTeX PDF) and labels.
            page.evaluate("""() => {const select=document.getElementById('canvas-markdown-DownloadFormat');
                select.options[1].disabled=true;select.options[0].textContent='Markdown source';} """)
            canvas.click()
            expect(page.get_by_role('menuitem', name='PDF')).to_be_disabled()
            expect(page.get_by_role('menuitem', name='Markdown source')).to_be_enabled()
            page.get_by_role('menu').screenshot(path=str(tmp_path / 'preview-download-menu.png'))
            page.locator('#canvas-markdown-PreviewTitle').click()
            expect(page.get_by_role('menu')).to_have_count(0)
            # A single-format file downloads directly and has no menu affordance.
            page.evaluate("document.getElementById('canvas-markdown-DownloadFormat').hidden=true")
            expect(canvas).not_to_have_attribute('aria-haspopup', 'menu')
            canvas.click()
            expect(page.get_by_role('menu')).to_have_count(0)
            assert page.evaluate('downloads.length') == 5
            page.evaluate('finishDownload()')
            expect(canvas).to_be_enabled()
            # The already-direct file/PDF buttons remain plain icon buttons.
            for button_id in ['latex-pdf-PreviewDownload', 'filesSidebarPreviewDownload']:
                button = page.locator('#' + button_id)
                assert button.inner_text().strip() == ''
                assert button.locator('svg').count() == 1
                assert button.evaluate("element => element.classList.contains('om-button')")
            page.evaluate('''() => {
                document.getElementById('canvas-markdown-EditorControls').style.display='flex';
                document.getElementById('canvas-markdown-PreviewTitle').textContent='email-template.md';
                document.getElementById('canvas-markdown-PreviewStatus').textContent='Saved';
                document.getElementById('canvas-markdown-ShareBtn').hidden=false;
                document.getElementById('canvas-markdown-DownloadFormat').hidden=false;
            }''')
            page.locator('#canvas').screenshot(path=str(tmp_path / 'preview-download-header.png'))
            page.set_viewport_size({'width': 390, 'height': 700})
            page.emulate_media(reduced_motion='reduce')
            canvas.click()
            menu_box = page.get_by_role('menu').bounding_box()
            assert 0 <= menu_box['x'] < menu_box['x'] + menu_box['width'] <= 390
            button_box = canvas.bounding_box()
            assert 0 <= button_box['x'] < button_box['x'] + button_box['width'] <= 390
            page.locator('#canvas').screenshot(path=str(tmp_path / 'preview-download-narrow.png'))
            page.get_by_role('menuitem', name='Markdown source').press('Escape')
            expect(canvas).to_be_focused()
            assert not errors
        finally:
            browser.close()
