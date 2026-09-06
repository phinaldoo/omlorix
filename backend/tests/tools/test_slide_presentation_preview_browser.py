"""Real-browser check of the sidebar, using isolated synthetic API responses.

Run with Playwright and Chromium installed; no running app or provider is used.
"""
from pathlib import Path
from urllib.parse import urlsplit
import json

from bs4 import BeautifulSoup
import pytest

from app.tools.slide_presentation import playback


DECK = '''<!DOCTYPE html><html data-omlorix-interactive="1"><head><style>
.slide{width:1920px;height:1080px;position:relative;overflow:hidden;box-sizing:border-box}
body{font:48px sans-serif;background:white}button{font:48px sans-serif}
</style></head><body>
<section class="slide" data-slide-index="1" data-slide-title="Counter"><button id="counter">0</button></section>
<section class="slide" data-slide-index="2" data-slide-title="Next"><h1>Second slide</h1></section>
<section class="slide" data-slide-index="3" data-slide-title="Third"><h2>Third slide</h2></section>
<section class="slide" data-slide-index="4" data-slide-title="Fourth"><h2>Fourth slide</h2></section>
<script>OmlorixPresentation.ready.then(() => {
let n=0; document.querySelector('#counter').addEventListener('click', e => e.target.textContent=++n);
document.addEventListener('omlorix:slide-enter', e => {
e.target.dataset.running='true';
e.detail.signal.addEventListener('abort', () => e.target.dataset.running='false', {once:true});
});
try { parent.document.body.dataset.escaped='yes'; } catch { window.isolated=true; }
});</script></body></html>'''


def test_sidebar_runs_drafts_and_saved_html_without_raster_fetches(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from playwright.sync_api import expect
    frontend = Path(__file__).resolve().parents[3] / "frontend"
    index = BeautifulSoup((frontend / "index.html").read_text(), "html.parser")
    markup = str(index.select_one('#slide-presentation-PreviewPanel')) + str(index.select_one('#slide-presentation-SlideshowOverlay'))
    shell = '''<!doctype html><html><head>
    <link rel="stylesheet" href="/css/common/init.css">
    <link rel="stylesheet" href="/css/common/elements.css">
    <link rel="stylesheet" href="/css/chat/canvas-widget.css">
    <link rel="stylesheet" href="/css/chat/slide-presentation-widget.css">
    <style>:root{--surface-elevated:white}.slide-presentation-preview-panel{position:fixed!important;inset:0 0 0 auto!important;width:900px!important;height:100vh!important}.hidden{display:none!important}</style>
    </head><body>''' + markup + '''<script>
    window.Icons={loading_circle:'',desktop:'',check:''};window.authedFetch=(...args)=>fetch(...args);
    </script><script src="/js/chat/slide-presentation-widget.js"></script></body></html>'''
    requests, frames, errors = [], {}, []

    def route_request(route):
        path = urlsplit(route.request.url).path
        requests.append(path)
        if path == "/":
            route.fulfill(body=shell, content_type="text/html")
        elif path.startswith(("/js/", "/css/")):
            route.fulfill(body=(frontend / path[1:]).read_text(), content_type="text/javascript" if path.startswith('/js/') else "text/css")
        elif path == "/api/v1/presentations/preview" or path.endswith('/playback'):
            data = route.request.post_data_json
            is_preview = path.endswith('/preview')
            source = playback.prepare_preview_source(data['html']) if is_preview else DECK
            doc = playback.prepare_presentation_document(source, mode=data.get('mode', 'present'), slide_index=data['slide_index'])
            frame_id = str(len(frames) + 1)
            frames[frame_id] = doc
            route.fulfill(json={"frame_url": f"/api/v1/llm/widgets/frame/{frame_id}", "channel_id": doc['channel_id'], "slide_count": doc['slide_count']})
        elif path.startswith("/api/v1/llm/widgets/frame/"):
            doc = frames[path.rsplit('/', 1)[-1]]
            route.fulfill(body=doc['html'], content_type="text/html", headers={"Content-Security-Policy": "sandbox allow-scripts; " + doc['csp']})
        elif path.endswith('/editor'):
            route.fulfill(json={"html": DECK, "presentation_id": "saved", "file_id": "pptx", "slide_count": 4, "canvas_revision": 1, "render_revision": 0})
        else:
            route.fulfill(status=404, body="Not found")

    with sync_playwright() as browser_api:
        if not Path(browser_api.chromium.executable_path).exists():
            pytest.skip("Install Playwright Chromium for the browser smoke test")
        browser = browser_api.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/*', route_request)
        try:
            page.goto('http://preview.test/')
            # A streamed draft works before any saved presentation or image exists.
            page.evaluate("html => slidePresentationWidget.handleSlidePresentationEvent({event:'html_snapshot',data:{html}},'test')", DECK)
            preview = page.frame_locator('#slide-presentation-PreviewSlidesTrack iframe.interactive')
            page.locator('#slide-presentation-PreviewThumbnails button').first.wait_for()
            page.locator('#slide-presentation-PreviewThumbnails button').first.click()
            preview.locator('#counter').click()
            assert preview.locator('#counter').inner_text() == '1'
            expect(preview.locator('section.slide').nth(0)).to_be_in_viewport()
            expect(preview.locator('section.slide').nth(1)).to_be_in_viewport()
            assert page.locator('#slide-presentation-PreviewSlidesTrack iframe.interactive').count() == 1
            # Native wheel scrolling inside the live document updates the host.
            preview.locator('#counter').hover()
            page.mouse.wheel(0, 2000)
            expect(page.locator('#slide-presentation-PreviewThumbnails button').nth(3)).to_have_attribute('aria-current', 'true')
            expect(preview.locator('section.slide').first).to_have_attribute('data-running', 'false')
            expect(page.locator('#slide-presentation-PreviewCounter')).to_have_text('4 / 4')
            assert page.evaluate('window.scrollY') == 0
            page.locator('#slide-presentation-PreviewThumbnails button').nth(1).click()
            expect(preview.locator('h1')).to_be_visible()
            expect(page.locator('#slide-presentation-PreviewCounter')).to_have_text('2 / 4')
            page.locator('#slide-presentation-PreviewThumbnails button').first.click()
            expect(preview.locator('section.slide').first).to_have_attribute('data-running', 'true')
            assert preview.locator('#counter').inner_text() == '1'
            expect(preview.locator('#counter')).to_be_in_viewport()
            expect(page.locator('#slide-presentation-PreviewCounter')).to_have_text('1 / 4')
            page.set_viewport_size({"width": 1100, "height": 650})
            expect(preview.locator('#counter')).to_be_in_viewport()
            page.evaluate('slidePresentationWidget.hidePreviewPanel()')
            expect(preview.locator('section.slide').first).to_have_attribute('data-running', 'false')
            page.evaluate('slidePresentationWidget.toggleSlidePresentationPreview()')
            expect(preview.locator('section.slide').first).to_have_attribute('data-running', 'true')
            page.emulate_media(reduced_motion='reduce')
            preview.locator('section.slide').first.focus()
            page.keyboard.press('End')
            expect(page.locator('#slide-presentation-PreviewCounter')).to_have_text('4 / 4')
            page.keyboard.press('Home')
            expect(page.locator('#slide-presentation-PreviewCounter')).to_have_text('1 / 4')
            # Presenting an unsaved draft must use the single-slide runtime too.
            page.evaluate('slidePresentationWidget.openSlideshow()')
            draft_slideshow = page.frame_locator('#slide-presentation-SlideshowOverlay iframe.interactive')
            expect(draft_slideshow.locator('#counter')).to_be_visible()
            expect(draft_slideshow.locator('h1')).to_be_hidden()
            page.evaluate('slidePresentationWidget.closeSlideshow()')
            assert page.locator('body').get_attribute('data-escaped') is None
            # A newer draft replaces the source, without losing the selected slide.
            page.evaluate("html => slidePresentationWidget.handleSlidePresentationEvent({event:'html_snapshot',data:{html}},'test')", DECK.replace('>0<', '>Updated<'))
            expect(preview.locator('#counter')).to_have_text('Updated')
            page.evaluate("slidePresentationWidget.reset()")
            assert page.locator('#slide-presentation-PreviewSlidesTrack iframe').count() == 0
            page.evaluate("slidePresentationWidget.openExistingPresentationPreview({presentationId:'saved',fileId:'pptx'})")
            preview.locator('#counter').click()
            assert preview.locator('#counter').inner_text() == '1'
            page.evaluate('slidePresentationWidget.openSlideshow()')
            slideshow = page.frame_locator('#slide-presentation-SlideshowOverlay iframe.interactive')
            expect(slideshow.locator('#counter')).to_be_visible()
            expect(slideshow.locator('h1')).to_be_hidden()
            page.evaluate('slidePresentationWidget.closeSlideshow()')
            assert preview.locator('#counter').inner_text() == '1'
            assert not any('/slides/' in path or '/draft-slides/' in path for path in requests)
            assert not errors, json.dumps(errors)
            screenshot = tmp_path / 'interactive-sidebar.png'
            page.screenshot(path=str(screenshot))
            print(f'Sidebar screenshot: {screenshot}')
        finally:
            browser.close()
