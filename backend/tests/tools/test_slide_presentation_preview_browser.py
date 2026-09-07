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
<section class="slide" data-slide-index="1" data-slide-title="Counter"><button id="counter">0</button><p id="reveal" hidden>Revealed detail</p><input id="slider" type="range" min="0" max="10" value="5" aria-label="Scenario"></section>
<section class="slide" data-slide-index="2" data-slide-title="Next"><h1>Second slide</h1></section>
<section class="slide" data-slide-index="3" data-slide-title="Third"><h2>Third slide</h2></section>
<section class="slide" data-slide-index="4" data-slide-title="Fourth"><h2>Fourth slide</h2></section>
<script>OmlorixPresentation.ready.then(api => {
api.registerSteps(0, {count:2, render({step, animate}) {
const reveal=document.querySelector('#reveal'); reveal.hidden=step===0;
reveal.textContent=step===1?'First detail':'Complete explanation';
animate(reveal, [{opacity:0},{opacity:1}], {duration:250});
}});
api.registerSteps(3, {count:1, render({slide,step}) {slide.dataset.finalStep=step;}});
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
    <link rel="stylesheet" href="/css/chat/chatWorkspace.css">
    <link rel="stylesheet" href="/css/chat/slide-presentation-widget.css">
    <style>:root{--surface-elevated:white}.slide-presentation-preview-panel{position:fixed!important;inset:0 0 0 auto!important;width:900px!important;height:100vh!important}.hidden{display:none!important}</style>
    </head><body>''' + markup + '''<script>
    window.authedFetch=(...args)=>fetch(...args);
    function escapeHtml(text) {const element=document.createElement('span');element.textContent=text;return element.innerHTML;}
    // Isolated chat primitives: the real shared subagent event renderer calls these.
    function transcriptPart(id, kind, text) {
      const part=document.createElement('p'); part.dataset.kind=kind; part.textContent=text;
      document.getElementById('a-'+id).appendChild(part);
    }
    function appendAssistantContent(id,text,_last,count) {transcriptPart(id,'message',text);return count+1;}
    function appendAssistantReasoning(id,text,_last,count) {transcriptPart(id,'reasoning',text);return count+1;}
    function appendAssistantTool(id,_last,count,_unused,name) {transcriptPart(id,'tool',name);return count+1;}
    function processAssistantToolDeltaStreamEvent(id,last,count,raw) {transcriptPart(id,'tool-delta',raw.d.delta);return {assistantReasoningCount:count,lastAppendedMessageType:last};}
    function finalizeThinkingBlocks() {}
    function finalizeStreamingMarkdownInContainer() {}
    function flushAssistantStreamingContentForMessage() {}
    </script><script src="/js/common/icons.js"></script>
    <script src="/js/common/dropdown.js"></script>
    <script src="/js/chat/downloadControls.js"></script>
    <script src="/js/chat/chatScrollManager.js"></script>
    <script src="/js/chat/messages/subagents.js"></script>
    <script src="/js/chat/slide-presentation-widget.js"></script></body></html>'''
    requests, frames, errors = [], {}, []
    failures = {"editor": 1}
    stored = {"html": DECK, "revision": 1}

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
            source = playback.prepare_preview_source(data['html']) if is_preview else stored['html']
            doc = playback.prepare_presentation_document(source, mode=data.get('mode', 'present'), slide_index=data['slide_index'])
            frame_id = str(len(frames) + 1)
            frames[frame_id] = doc
            route.fulfill(json={"frame_url": f"/api/v1/llm/widgets/frame/{frame_id}", "channel_id": doc['channel_id'], "slide_count": doc['slide_count']})
        elif path.startswith("/api/v1/llm/widgets/frame/"):
            doc = frames[path.rsplit('/', 1)[-1]]
            route.fulfill(body=doc['html'], content_type="text/html", headers={"Content-Security-Policy": "sandbox allow-scripts; " + doc['csp']})
        elif path.endswith('/editor'):
            if failures['editor']:
                failures['editor'] -= 1
                route.fulfill(status=503, json={})
                return
            if route.request.method == 'PUT':
                data = route.request.post_data_json
                if data['expected_revision'] != stored['revision']:
                    route.fulfill(status=409, json={})
                    return
                stored['html'] = data['html']
                stored['revision'] += 1
            route.fulfill(json={"html": stored['html'], "title": "Example", "presentation_id": "saved", "file_id": "pptx", "slide_count": 4, "canvas_revision": stored['revision'], "render_revision": 0})
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
            # The specialist remains visible across draft renders. Only completion
            # loads the saved deck; no provisional iframe executes while it runs.
            page.evaluate("slidePresentationWidget.handleSlidePresentationEvent({event:'status',data:{phase:'generating',run_id:'run-1'}},'test')")
            page.evaluate("slidePresentationWidget.handleSlidePresentationEvent({event:'activity',data:{run_id:'run-1',event:'reasoning_delta',content:'Planning the narrative'}},'test')")
            page.evaluate("slidePresentationWidget.handleSlidePresentationEvent({event:'activity',data:{run_id:'run-1',event:'message_delta',content:'Drafting the presentation'}},'test')")
            page.evaluate("slidePresentationWidget.handleSlidePresentationEvent({event:'activity',data:{run_id:'run-1',event:'tool_call',raw:{payload:{d:{name:'update_presentation'}}}}},'test')")
            activity = page.locator('#slide-presentation-PreviewActivity')
            expect(activity.locator('[data-kind=reasoning]')).to_have_text('Planning the narrative')
            expect(activity.locator('[data-kind=message]')).to_have_text('Drafting the presentation')
            expect(activity.locator('[data-kind=tool]')).to_have_text('update_presentation')
            page.evaluate("html => slidePresentationWidget.handleSlidePresentationEvent({event:'html_snapshot',data:{html}},'test')", DECK)
            page.evaluate("slidePresentationWidget.handleSlidePresentationEvent({event:'revision_ready',data:{presentation_id:'saved',revision:1}},'test')")
            assert not any(path.endswith(('/preview', '/editor')) for path in requests)
            assert page.locator('#slide-presentation-PreviewSlidesTrack iframe').count() == 0
            assert page.locator('#slide-presentation-GenSkeleton').count() == 0
            page.evaluate('slidePresentationWidget.hidePreviewPanel()')
            page.evaluate("slidePresentationWidget.handleSlidePresentationEvent({event:'activity',data:{run_id:'run-1',event:'message_delta',content:'Reviewing the draft'}},'test')")
            page.evaluate('slidePresentationWidget.toggleSlidePresentationPreview()')
            expect(activity.locator('[data-kind=message]').last).to_have_text('Reviewing the draft')
            activity.screenshot(path=str(tmp_path / 'specialist-activity.png'))
            page.evaluate("slidePresentationWidget.handleSlidePresentationEvent({event:'complete',data:{run_id:'run-1',presentation_id:'saved',file_id:'pptx',slide_count:4}},'test')")
            expect(page.locator('#slide-presentation-PreviewUpdateRetry')).to_be_visible()
            expect(activity).to_be_visible()
            expect(activity.locator('.subagent-transcript-chat')).to_have_attribute('data-is-streaming', 'false')
            page.locator('#slide-presentation-PreviewUpdateRetry').click()
            preview = page.frame_locator('#slide-presentation-PreviewSlidesTrack iframe.interactive')
            page.locator('#slide-presentation-PreviewThumbnails button').first.wait_for()
            expect(activity).to_be_hidden()
            assert activity.locator('.subagent-transcript-panel').count() == 1
            page.locator('#slide-presentation-PreviewThumbnails button').first.click()
            preview.locator('#counter').click()
            assert preview.locator('#counter').inner_text() == '1'
            selector = page.locator('#slide-presentation-PreviewView')

            def select_view(view):
                selector.click()
                page.get_by_role('menuitemradio', name={
                    'preview': 'Preview', 'activity': 'Specialist chat', 'html': 'HTML source',
                }[view], exact=True).click()
                expect(selector).to_be_focused()

            assert page.locator('.slide-presentation-preview-panel-header select').count() == 0
            assert selector.inner_text().strip() == ''
            assert selector.locator('svg').count() == 1
            assert page.locator('#slide-presentation-PreviewEdit svg').evaluate("element => element.innerHTML === Icons.createSvgElement(Icons.edit).innerHTML")
            select_view('activity')
            expect(activity).to_be_visible()
            expect(activity.locator('[data-kind=message]').last).to_have_text('Reviewing the draft')
            expect(preview.locator('section.slide').first).to_have_attribute('data-running', 'false')
            select_view('html')
            source = page.locator('#slide-presentation-SourceHtml')
            expect(source).to_have_value(DECK)
            assert page.locator('#slide-presentation-PreviewPresent').inner_text().strip() == ''
            assert page.locator('#slide-presentation-PreviewPresent').get_attribute('aria-label') == 'Present'
            draft = DECK.replace('Second slide', 'Revised second slide')
            source.fill(draft)
            select_view('preview')
            assert preview.locator('#counter').inner_text() == '1'
            select_view('html')
            expect(source).to_have_value(draft)
            source.press('Control+s')
            expect(page.locator('#slide-presentation-SourceStatus')).to_have_text('Saved')
            assert stored['html'] == draft and stored['revision'] == 2
            selector.click()
            selected_view = page.get_by_role('menuitemradio', name='HTML source', exact=True)
            expect(selected_view).to_have_attribute('aria-checked', 'true')
            expect(selected_view).to_be_focused()
            selected_view.press('Home')
            expect(page.get_by_role('menuitemradio', name='Preview', exact=True)).to_be_focused()
            page.keyboard.press('Escape')
            expect(selector).to_be_focused()
            expect(selector).to_have_attribute('aria-expanded', 'false')
            # Failed revision checks preserve the user's code for recovery.
            stored['revision'] = 3
            source.fill(draft.replace('Revised second slide', 'Conflicting edit'))
            page.locator('#slide-presentation-SourceSave').click()
            expect(page.locator('#slide-presentation-SourceStatus')).to_contain_text('changed elsewhere')
            assert 'Conflicting edit' in source.input_value()
            assert 'Conflicting edit' not in stored['html']
            page.locator('#slide-presentation-SourceReload').click()
            expect(source).to_have_value(draft)
            page.locator('#slide-presentation-PreviewSource').screenshot(path=str(tmp_path / 'presentation-html-editor.png'))
            panel = page.locator('#slide-presentation-PreviewPanel')
            panel.evaluate("element => element.style.setProperty('width', '320px', 'important')")
            bounds = selector.bounding_box()
            panel_bounds = panel.bounding_box()
            assert bounds['x'] >= panel_bounds['x']
            assert bounds['x'] + bounds['width'] <= panel_bounds['x'] + panel_bounds['width']
            page.locator('.slide-presentation-preview-panel-header').screenshot(path=str(tmp_path / 'presentation-view-header.png'))
            selector.click()
            menu = page.get_by_role('menu', name='Presentation view')
            menu_bounds = menu.bounding_box()
            assert menu_bounds['x'] >= 0
            assert menu_bounds['x'] + menu_bounds['width'] <= 1400
            menu.screenshot(path=str(tmp_path / 'presentation-view-menu.png'))
            # A second click toggles the menu; clicking outside also closes it.
            selector.click()
            expect(menu).to_have_count(0)
            selector.click()
            source.click()
            expect(menu).to_have_count(0)
            download = page.locator('#slide-presentation-PreviewDownloadBtn')
            assert download.inner_text().strip() == ''
            assert download.locator('svg').count() == 1
            download.click()
            expect(page.get_by_role('menu', name='Download format').get_by_role('menuitem')).to_have_count(4)
            page.get_by_role('menu', name='Download format').screenshot(path=str(tmp_path / 'presentation-download-menu.png'))
            page.evaluate("() => {window.downloads=[];window.chatDownloadControls.downloadBlobFromUrl=async (...args)=>window.downloads.push(args.slice(0,2));}")
            page.get_by_role('menuitem', name='HTML source').click()
            assert page.evaluate('window.downloads') == [['/api/v1/files/download?file_id=saved', 'Presentation.html']]
            expect(download).to_be_focused()
            assert download.inner_text().strip() == ''
            assert download.locator('svg').count() == 1
            panel.evaluate("element => element.style.removeProperty('width')")
            select_view('activity')
            expect(activity.locator('[data-kind=message]').last).to_have_text('Reviewing the draft')
            select_view('preview')
            expect(preview.locator('h1')).to_have_text('Revised second slide')
            preview.locator('#counter').click()
            expect(preview.locator('#reveal')).to_have_text('Complete explanation')
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
            # The completed deck uses the single-slide runtime for Present.
            page.evaluate('slidePresentationWidget.openSlideshow()')
            draft_slideshow = page.frame_locator('#slide-presentation-SlideshowOverlay iframe.interactive')
            expect(draft_slideshow.locator('#counter')).to_be_visible()
            expect(draft_slideshow.locator('h1')).to_be_hidden()
            page.evaluate('slidePresentationWidget.closeSlideshow()')
            assert page.locator('body').get_attribute('data-escaped') is None
            # Reset destroys live activity and rejects late events from that run.
            page.evaluate("slidePresentationWidget.handleSlidePresentationEvent({event:'status',data:{phase:'generating',run_id:'abandoned'}},'test')")
            page.evaluate("slidePresentationWidget.reset()")
            page.evaluate("slidePresentationWidget.handleSlidePresentationEvent({event:'activity',data:{run_id:'abandoned',event:'message_delta',content:'Late output'}},'test')")
            page.evaluate("slidePresentationWidget.handleSlidePresentationEvent({event:'complete',data:{run_id:'abandoned',presentation_id:'saved',file_id:'pptx'}},'test')")
            assert page.locator('#slide-presentation-PreviewSlidesTrack iframe').count() == 0
            assert activity.locator('.subagent-transcript-panel').count() == 0
            page.evaluate("slidePresentationWidget.openExistingPresentationPreview({presentationId:'saved',fileId:'pptx'})")
            preview.locator('#counter').click()
            assert preview.locator('#counter').inner_text() == '1'
            page.evaluate('slidePresentationWidget.openSlideshow()')
            slideshow = page.frame_locator('#slide-presentation-SlideshowOverlay iframe.interactive')
            expect(slideshow.locator('#counter')).to_be_visible()
            expect(slideshow.locator('h1')).to_be_hidden()
            expect(slideshow.locator('#reveal')).to_be_hidden()
            expect(page.locator('#slide-presentation-SsPrev')).to_be_disabled()
            # Keys inside inputs belong to the input; body keys consume steps.
            slideshow.locator('#slider').focus()
            page.keyboard.press('ArrowRight')
            expect(slideshow.locator('#slider')).to_have_value('6')
            expect(slideshow.locator('#reveal')).to_be_hidden()
            slideshow.locator('section.slide').first.focus()
            page.keyboard.press('ArrowRight')
            expect(slideshow.locator('#reveal')).to_have_text('First detail')
            expect(page.locator('#slide-presentation-SsCurrent')).to_have_text('1')
            expect(page.locator('#slide-presentation-SsStep')).to_have_text('Step 1 of 2')
            expect(page.locator('#slide-presentation-SsPrev')).to_be_enabled()
            page.keyboard.press('Space')
            expect(slideshow.locator('#reveal')).to_have_text('Complete explanation')
            page.keyboard.press('ArrowRight')
            expect(slideshow.locator('h1')).to_be_visible()
            page.keyboard.press('ArrowLeft')
            expect(slideshow.locator('#reveal')).to_have_text('Complete explanation')
            page.keyboard.press('Shift+Space')
            expect(slideshow.locator('#reveal')).to_have_text('First detail')
            # Actual host buttons use the same protocol and can reverse to step 0.
            page.locator('#slide-presentation-SsPrev').evaluate('(button) => button.click()')
            expect(slideshow.locator('#reveal')).to_be_hidden()
            page.locator('#slide-presentation-SsNext').evaluate('(button) => button.click()')
            expect(slideshow.locator('#reveal')).to_have_text('First detail')
            # Host-focused keys also use steps, not optimistic slide jumps.
            page.locator('#slide-presentation-SlideshowOverlay').focus()
            page.keyboard.press('Space')
            expect(page.locator('#slide-presentation-SsStep')).to_have_text('Step 2 of 2')
            page.set_viewport_size({'width': 320, 'height': 640})
            page.locator('#slide-presentation-SsPrev').focus()
            controls = page.locator('#slide-presentation-SlideshowControls')
            bounds = controls.bounding_box()
            assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 320
            controls.screenshot(path=str(tmp_path / 'presentation-step-controls.png'))
            page.locator('#slide-presentation-SlideshowOverlay').focus()
            page.keyboard.press('End')
            expect(page.locator('#slide-presentation-SsCurrent')).to_have_text('4')
            expect(page.locator('#slide-presentation-SsNext')).to_be_enabled()
            page.locator('#slide-presentation-SsNext').evaluate('(button) => button.click()')
            expect(slideshow.locator('section.slide').last).to_have_attribute('data-final-step', '1')
            expect(page.locator('#slide-presentation-SsNext')).to_be_disabled()
            page.keyboard.press('Home')
            expect(slideshow.locator('#reveal')).to_be_hidden()
            page.evaluate('slidePresentationWidget.closeSlideshow()')
            assert preview.locator('#counter').inner_text() == '1'
            assert not any('/slides/' in path or '/draft-slides/' in path for path in requests)
            assert not errors, json.dumps(errors)
            screenshot = tmp_path / 'interactive-sidebar.png'
            page.screenshot(path=str(screenshot))
            print(f'Sidebar screenshot: {screenshot}')
            # Reopening the chat rebuilds result cards from persisted tool metadata.
            page.set_viewport_size({'width': 1400, 'height': 1000})
            page.reload()
            saved_meta = {
                'slide_presentation': True, 'presentation_id': 'saved', 'file_id': 'pptx',
                'title': 'Saved deck', 'slide_count': 4,
                'slide_presentation_activity': {
                    'schema_version': 1, 'run_id': 'persisted-run', 'events': [
                        {'event': 'reasoning_delta', 'content': 'Planning the saved narrative'},
                        {'event': 'tool_call', 'raw': {'payload': {'d': {'id': 'edit-1', 'name': 'update_presentation'}}}},
                        {'event': 'tool_delta', 'raw': {'t': 't_cd', 'd': {'id': 'edit-1', 'name': 'update_presentation', 'delta': '{"type":"view"}'}}},
                        {'event': 'message_delta', 'content': 'Reviewed the saved chart'},
                        {'event': 'complete', 'result': 'Reviewed the saved chart'},
                    ],
                },
            }
            page.evaluate("""meta => {
              const message=document.createElement('div');message.id='a-restored';document.body.prepend(message);
              slidePresentationWidget.renderSlidePresentationResultBlock('restored', meta);
            }""", saved_meta)
            assert activity.locator('.subagent-transcript-panel').count() == 0
            page.locator('#a-restored .slide-presentation-completion-view-btn').click()
            preview.locator('#counter').wait_for()
            select_view('activity')
            expect(activity.locator('[data-kind=reasoning]')).to_have_text('Planning the saved narrative')
            expect(activity.locator('[data-kind=tool]')).to_have_text('update_presentation')
            expect(activity.locator('[data-kind=tool-delta]')).to_have_text('{"type":"view"}')
            expect(activity.locator('[data-kind=message]')).to_have_text('Reviewed the saved chart')
            expect(activity.locator('.subagent-transcript-chat')).to_have_attribute('data-is-streaming', 'false')
            activity.screenshot(path=str(tmp_path / 'restored-specialist-chat.png'))
            # Opening the same deck via an attachment also finds its saved run.
            page.evaluate('slidePresentationWidget.reset()')
            page.evaluate("slidePresentationWidget.openExistingPresentationPreview({presentationId:'saved',fileId:'pptx'})")
            select_view('activity')
            expect(activity.locator('[data-kind=message]')).to_have_text('Reviewed the saved chart')
            # A different/legacy deck must not inherit this transcript.
            page.evaluate("slidePresentationWidget.openExistingPresentationPreview({presentationId:'other',fileId:'other-pptx'})")
            selector.click()
            expect(page.get_by_role('menuitemradio', name='Specialist chat')).to_be_disabled()
            page.keyboard.press('Escape')
            assert activity.locator('.subagent-transcript-panel').count() == 0
            assert not errors, json.dumps(errors)

        finally:
            browser.close()


@pytest.mark.parametrize('mode', ['present', 'preview', 'editor', 'render'])
def test_step_states_and_managed_animation_in_real_browser(mode):
    sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright
    source = DECK.replace('duration:250', 'duration:10000')
    # Registration after async initialization must also affect the exported state.
    source = source.replace('let n=0;', '''api.waitUntil(new Promise(resolve => setTimeout(() => {
api.registerSteps(1, {count:3, exportStep:2, render({slide,step}) {slide.dataset.exportStep=step;}});
resolve();
}, 20))); let n=0;''')
    document = playback.prepare_presentation_document(source, mode=mode)
    with sync_playwright() as browser_api:
        if not Path(browser_api.chromium.executable_path).exists():
            pytest.skip('Install Playwright Chromium for step integration tests')
        browser = browser_api.chromium.launch()
        page = browser.new_page(viewport={'width': 1920, 'height': 1080})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/*', lambda route: route.fulfill(body=document['html'], content_type='text/html'))
        try:
            page.goto('http://steps.test/')
            page.evaluate('window.omlorixPresentationRenderReady')
            assert page.locator('html').get_attribute('data-omlorix-render-ready') == 'true'
            if mode == 'present':
                assert page.locator('#reveal').is_hidden()
                page.evaluate('OmlorixPresentation.next(); OmlorixPresentation.next();')
                assert page.locator('#reveal').inner_text() == 'Complete explanation'
                assert page.evaluate("document.querySelector('#reveal').getAnimations().length") == 1
                page.emulate_media(reduced_motion='reduce')
                # CSP forbids the eval-based wait_for_function helper.
                page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                assert page.evaluate("document.querySelector('#reveal').getAnimations().length") == 0
                page.evaluate('OmlorixPresentation.previous()')
                assert page.locator('#reveal').inner_text() == 'First detail'
                assert page.evaluate("document.querySelector('#reveal').getAnimations().length") == 0
            else:
                assert not page.locator('#reveal').evaluate('(element) => element.hidden')
                assert page.locator('#reveal').inner_text() == 'Complete explanation'
                assert page.locator('section.slide').nth(1).get_attribute('data-export-step') == '2'
                assert page.evaluate("document.querySelector('#reveal').getAnimations().length") == 0
            assert not errors, errors
        finally:
            browser.close()


def test_isolated_editor_uses_shared_header_and_download_menu(tmp_path):
    sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright
    from playwright.sync_api import expect
    from app.tools.slide_presentation.editor_frame import EDITOR_PROXY_HTML, EDITOR_PROXY_CSP

    frontend = Path(__file__).resolve().parents[3] / 'frontend'
    index = BeautifulSoup((frontend / 'index.html').read_text(), 'html.parser')
    styles = ''.join(str(link) for link in index.select('link[data-slide-presentation-editor-stylesheet]'))
    scripts = ''.join(str(script) for script in index.select('script[data-slide-presentation-editor-script]'))
    shell = '''<!doctype html><html data-mode="light"><head><meta charset="utf-8">''' + styles + '''
    <style>html,body,#slide-presentation-EditorHost{margin:0;width:100%;height:100%;overflow:hidden}.slide-presentation-editor-frame{width:100%;height:100%;border:0}</style>
    </head><body><div id="slide-presentation-EditorHost"></div>
    <script>window.getTranslation=(key,fallback)=>fallback;</script>
    <script src="/js/common/icons.js"></script>''' + scripts + '''
    <script src="/js/chat/slide-presentation-editor.js"></script></body></html>'''
    prepared = playback.prepare_presentation_document(DECK, mode='editor')
    errors = []

    def route_request(route):
        path = urlsplit(route.request.url).path
        if path == '/':
            route.fulfill(body=shell, content_type='text/html')
        elif path == '/api/v1/presentations/editor/proxy':
            route.fulfill(body=EDITOR_PROXY_HTML, content_type='text/html', headers={'Content-Security-Policy': EDITOR_PROXY_CSP})
        elif path.startswith(('/js/', '/css/')):
            route.fulfill(body=(frontend / path[1:]).read_text(), content_type='text/javascript' if path.startswith('/js/') else 'text/css')
        else:
            route.fulfill(status=404, body='Not found')

    with sync_playwright() as browser_api:
        if not Path(browser_api.chromium.executable_path).exists():
            pytest.skip('Install Playwright Chromium for the browser smoke test')
        browser = browser_api.chromium.launch()
        page = browser.new_page(viewport={'width': 1400, 'height': 900})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/*', route_request)
        try:
            page.goto('http://localhost/')
            page.evaluate('''async prepared => {
              window.calls=[];window.revision=1;window.editorReady=false;
              await slidePresentationNativeEditor.open({
                payload: {html:prepared.source,title:'Example',canvas_revision:1,render_revision:1},
                prepare:async()=>({html:prepared.source,runtime:prepared.runtime,csp:prepared.csp}),
                save:async payload=>{calls.push(['save',payload.title]);return {canvas_revision:++revision,render_revision:1};},
                render:async()=>{calls.push(['render']);return {canvas_revision:revision,render_revision:revision};},
                export:async payload=>{calls.push(['export',payload.format]);},
                onReady:()=>{window.editorReady=true;},onClose:()=>{calls.push(['close']);},
              });
            }''', prepared)
            page.wait_for_function('window.editorReady')
            editor = page.frame_locator('.slide-presentation-editor-frame').frame_locator('iframe')
            header = editor.locator('#topbar')
            assert header.locator('button:not(.om-button), select').count() == 0
            expect(header.locator('#btnUndo')).to_be_disabled()
            download = header.locator('#btnExport')
            assert download.inner_text().strip() == ''
            assert download.locator('svg').count() == 1
            assert download.evaluate('element => getComputedStyle(element).height') == '36px'
            header.screenshot(path=str(tmp_path / 'editor-header.png'))
            download.click()
            menu = editor.get_by_role('menu', name='Download format')
            expect(menu.get_by_role('menuitem')).to_have_count(4)
            expect(menu.get_by_role('menuitem', name='PPTX', exact=True)).to_be_focused()
            menu.get_by_role('menuitem', name='PPTX', exact=True).press('End')
            expect(menu.get_by_role('menuitem', name='HTML source')).to_be_focused()
            menu.get_by_role('menuitem', name='HTML source').press('Escape')
            expect(download).to_be_focused()
            expect(menu).to_have_count(0)
            assert ['close'] not in page.evaluate('window.calls')
            header.locator('#deckTitle').fill('Updated title')
            download.click()
            menu.screenshot(path=str(tmp_path / 'editor-download-menu.png'))
            menu.get_by_role('menuitem', name='HTML source').click()
            expect(download).to_be_enabled()
            expect(download).to_be_focused()
            assert page.evaluate('window.calls') == [['save', 'Updated title'], ['export', 'html']]
            assert download.inner_text().strip() == ''
            for label, value in [('PPTX', 'pptx'), ('PDF', 'pdf'), ('Images', 'slides_zip')]:
                download.click()
                menu.get_by_role('menuitem', name=label, exact=True).click()
                expect(download).to_be_enabled()
                page.wait_for_function('format => calls.some(call => call[0] === "export" && call[1] === format)', arg=value)
            download.click()
            header.locator('#deckTitle').click()
            expect(menu).to_have_count(0)
            assert not errors
        finally:
            browser.close()
