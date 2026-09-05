"""Live presentation sources execute only in isolated browser documents."""
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import asyncio
import json

from bs4 import BeautifulSoup
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import DATABASE_SCHEMA
from app.tools import widget_frames
from app.tools.slide_presentation import frame_storage, playback, router
from app.tools.slide_presentation.models import PresentationPlaybackDocument
from app.files.storage.local import LocalUserFileStorageAdapter
from app.tools.slide_presentation.sanitizer import sanitize_slide_presentation_html, validate_slide_presentation_html
from app.tools.slide_presentation.schemas import SlidePresentationPlaybackRequest

DECK = '''<!DOCTYPE html><html lang="en" data-omlorix-interactive="1"><head>
<meta name="omlorix-connect-src" content="https://api.example.org https://127.0.0.1 https://app.example.org">
<meta name="omlorix-frame-src" content="https://embed.example.org">
<style>.slide{width:1920px;height:1080px;position:relative;overflow:hidden;box-sizing:border-box}button{transition:opacity 1s}</style>
</head><body><section class="slide" data-slide-index="1" data-slide-title="Quiz">
<button id="answer">Answer</button><input type="range" min="0" max="10" value="5">
<iframe title="Example" src="https://embed.example.org/page" sandbox="allow-same-origin allow-scripts"></iframe>
<template data-slide-snapshot><h1>Quiz summary</h1><p>The answer is 42.</p></template>
</section><script>window.quizValue = 42;</script></body></html>'''


@pytest.fixture(autouse=True)
def isolated_playback_storage(tmp_path, monkeypatch):
    engine = create_engine("sqlite://").execution_options(schema_translate_map={DATABASE_SCHEMA: None})
    PresentationPlaybackDocument.__table__.create(engine)
    sessions = sessionmaker(bind=engine)
    adapter = LocalUserFileStorageAdapter(tmp_path / "objects")
    monkeypatch.setattr(frame_storage, "SessionLocal", sessions)
    monkeypatch.setattr(frame_storage, "get_local_user_files_base_dir", lambda: tmp_path)
    monkeypatch.setattr(frame_storage, "get_user_file_storage_config", lambda: SimpleNamespace(provider="local"))
    monkeypatch.setattr(frame_storage, "get_user_file_storage_adapter_for_provider", lambda provider: adapter)
    monkeypatch.setattr(widget_frames, "get_redis_client", lambda: None)
    widget_frames._WIDGET_FRAME_CACHE.clear()
    yield sessions, adapter
    widget_frames._WIDGET_FRAME_CACHE.clear()
    engine.dispose()


def test_interactive_source_round_trip_preserves_code_controls_and_snapshot():
    cleaned = sanitize_slide_presentation_html(DECK)
    assert BeautifulSoup(sanitize_slide_presentation_html(cleaned), "html.parser").html == BeautifulSoup(cleaned, "html.parser").html
    assert 'window.quizValue = 42' in cleaned
    assert '<button' in cleaned and '<input' in cleaned
    assert 'data-slide-snapshot' in cleaned
    assert 'allow-same-origin' not in cleaned
    assert "script-src 'none'" in cleaned
    assert validate_slide_presentation_html(cleaned) == 1


def test_export_preserves_live_code_controls_and_network_without_snapshot_projection():
    rendered = playback.build_slide_render_document(DECK)
    doc = BeautifulSoup(rendered, 'html.parser')
    assert 'window.quizValue = 42' in rendered
    assert 'startPresentation' in rendered
    assert doc.button and not doc.button.has_attr('disabled')
    assert doc.iframe['data-omlorix-embed-src'] == 'https://embed.example.org/page'
    assert doc.html['data-omlorix-mode'] == 'render'
    assert "script-src 'unsafe-inline'" in rendered
    assert 'connect-src https://api.example.org' in rendered
    assert 'animation:none!important' not in rendered
    assert validate_slide_presentation_html(rendered) == 1


def test_declared_live_images_survive_but_metadata_cannot_activate_navigation():
    source = DECK.replace('<head>', '''<head>
<meta name="omlorix-img-src" content="https://images.example.org" http-equiv="refresh" onload="alert(1)">
''').replace('<button', '''<img id="live-image" src="https://images.example.org/chart.png">
<img id="undeclared" src="https://other.example.org/chart.png"><button''')
    doc = BeautifulSoup(sanitize_slide_presentation_html(source), 'html.parser')
    meta = doc.find('meta', attrs={'name': 'omlorix-img-src'})
    assert meta.attrs == {'name': 'omlorix-img-src', 'content': 'https://images.example.org'}
    assert doc.find(id='live-image')['src'] == 'https://images.example.org/chart.png'
    assert not doc.find(id='undeclared').has_attr('src')
    assert 'images.example.org' in playback.build_slide_render_document(source)


@pytest.mark.parametrize('origin', [
    'https://localhost', 'https://localhost.', 'https://127.1', 'https://127.0.0.1',
    'https://[::1]', 'https://10.0.0.1', 'https://192.168.1.2', 'https://api.local',
    'https://*.example.org', 'https://user:pass@api.example.org', 'https://api.example.org:8443',
    'https://app.example.org', 'https://api.example.org/path', "https://x.org;script-src *", 'http://api.example.org',
])
def test_network_policy_rejects_unsafe_or_non_origin_declarations(origin):
    assert playback.public_https_origin(origin, app_origin='https://app.example.org') is None


def test_frame_uses_expiring_quota_store_and_independent_response_csp(monkeypatch):
    monkeypatch.setattr(widget_frames, 'get_redis_client', lambda: None)
    result = playback.create_playback_frame(user_id='playback-test', html=DECK, app_origin='https://app.example.org')
    frame = widget_frames.get_widget_frame_payload(result['frame_id'])
    csp = frame['headers']['Content-Security-Policy']
    assert 'sandbox allow-scripts' in csp and 'allow-same-origin' not in csp
    assert "connect-src https://api.example.org;" in csp
    assert 'app.example.org' not in csp
    assert "script-src-attr 'none'" in csp
    assert frame['headers']['Cache-Control'] == 'no-store, private'
    html = frame['path'].read_text(encoding='utf-8')
    frame['path'].unlink()
    assert result['channel_id'] in html
    doc = BeautifulSoup(html, 'html.parser')
    assert not doc.iframe.has_attr('src')
    assert doc.iframe['data-omlorix-embed-src'] == 'https://embed.example.org/page'
    assert 'startPresentation' in doc.head.script.string
    assert doc.find_all('script')[-1].string == 'window.quizValue = 42;'
    assert playback.public_https_origin('https://API.example.org:443/') == 'https://api.example.org'


def test_large_deck_stores_only_reference_and_streams_immutable_document(isolated_playback_storage):
    from app.llm.router import get_widget_frame_route

    large = DECK.replace('<button', '<img src="data:image/png;base64,' + 'A' * (26 * 1024 * 1024) + '"><button')
    result = playback.create_playback_frame(user_id='large-deck', html=large, app_origin='https://app.example.org')
    metadata = widget_frames._load_widget_frame(result['frame_id'])
    assert 'html' not in metadata
    assert len(json.dumps(metadata)) < 4096
    response = get_widget_frame_route(result['frame_id'])
    assert response.path.stat().st_size > 25 * 1024 * 1024
    assert response.headers['cache-control'] == 'no-store, private'
    sizes = []

    async def send(message):
        if message['type'] == 'http.response.body':
            sizes.append(len(message['body']))

    size = response.path.stat().st_size
    asyncio.run(response({'type': 'http', 'method': 'GET', 'headers': []}, None, send))
    assert sum(sizes) == size
    assert max(sizes) <= response.chunk_size
    assert not response.path.exists(), 'materialized copy is removed after serving'
    # A second revision cannot replace the first session's stored document.
    second = playback.create_playback_frame(user_id='large-deck', html=DECK.replace('42', '99'), app_origin='https://app.example.org')
    assert widget_frames._load_widget_frame(second['frame_id'])['document'] != metadata['document']
    first = widget_frames.get_widget_frame_payload(result['frame_id'])['path']
    assert first.stat().st_size == size
    first.unlink()


def test_expiry_denies_reads_and_cleanup_survives_lost_tokens(isolated_playback_storage, monkeypatch):
    sessions, adapter = isolated_playback_storage
    result = playback.create_playback_frame(user_id='viewer', html=DECK, app_origin='https://app.example.org')
    metadata = widget_frames._load_widget_frame(result['frame_id'])
    monkeypatch.setattr(widget_frames.time, 'time', lambda: metadata['expires_at'] + 1)
    with pytest.raises(HTTPException) as error:
        widget_frames.get_widget_frame_payload(result['frame_id'])
    assert error.value.status_code == 404
    widget_frames._WIDGET_FRAME_CACHE.clear()
    assert frame_storage.cleanup_expired_documents() == 0
    with sessions() as db:
        db.get(PresentationPlaybackDocument, result['frame_id']).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert frame_storage.cleanup_expired_documents() == 1
    assert not adapter.exists(metadata['document']['storage_key'])
    assert frame_storage.cleanup_expired_documents() == 0


def test_remote_storage_reference_and_failed_publication_cleanup(isolated_playback_storage, monkeypatch):
    sessions, adapter = isolated_playback_storage
    providers = []
    monkeypatch.setattr(frame_storage, 'get_user_file_storage_config', lambda: SimpleNamespace(provider='s3'))
    monkeypatch.setattr(frame_storage, 'get_user_file_storage_adapter_for_provider', lambda provider: providers.append(provider) or adapter)
    result = playback.create_playback_frame(user_id='viewer', html=DECK, app_origin='https://app.example.org')
    frame = widget_frames.get_widget_frame_payload(result['frame_id'])
    assert 'window.quizValue = 42' in frame['path'].read_text()
    frame['path'].unlink()
    assert providers == ['s3', 's3']
    # Publication failure must not leave another stored document or ledger row.
    def unavailable(*args, **kwargs):
        raise HTTPException(503)
    monkeypatch.setattr(widget_frames, '_store_widget_frame', unavailable)
    with pytest.raises(HTTPException):
        playback.create_playback_frame(user_id='viewer', html=DECK, app_origin='https://app.example.org')
    with sessions() as db:
        assert db.query(PresentationPlaybackDocument).count() == 1
    assert len(list(adapter.base_path.rglob('*.html'))) == 1


def test_cleanup_retries_failed_deletes_and_tolerates_missing_objects(isolated_playback_storage, monkeypatch):
    sessions, adapter = isolated_playback_storage
    result = playback.create_playback_frame(user_id='viewer', html=DECK, app_origin='https://app.example.org')
    def expire():
        with sessions() as db:
            db.get(PresentationPlaybackDocument, result['frame_id']).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
    expire()
    def failed_delete(key):
        raise RuntimeError('storage unavailable')
    monkeypatch.setattr(adapter, 'delete_file', failed_delete)
    assert frame_storage.cleanup_expired_documents() == 0
    with sessions() as db:
        assert db.get(PresentationPlaybackDocument, result['frame_id']) is not None
    expire()
    class MissingObject(Exception):
        status_code = 404
    def missing_delete(key):
        raise MissingObject()
    monkeypatch.setattr(adapter, 'delete_file', missing_delete)
    assert frame_storage.cleanup_expired_documents() == 1


def test_playback_cleanup_migration_round_trip(monkeypatch):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect

    migration = importlib.import_module('alembic_main.versions.playback_documents_20260905')
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        monkeypatch.setattr(migration, 'op', Operations(MigrationContext.configure(
            connection, opts={'version_table_schema': 'main'},
        )))
        migration.upgrade()
        inspector = inspect(connection)
        assert inspector.has_table('presentation_playback_documents')
        assert inspector.get_indexes('presentation_playback_documents')[0]['column_names'] == ['expires_at']
        migration.downgrade()
        assert not inspect(connection).has_table('presentation_playback_documents')
    engine.dispose()


def test_playback_route_checks_source_ownership_before_creating_frame(monkeypatch):
    def denied(*args):
        raise HTTPException(404, 'Presentation not found')
    monkeypatch.setattr(router, '_owned_editor_records', denied)
    with pytest.raises(HTTPException) as error:
        router.create_presentation_playback('other-deck', SlidePresentationPlaybackRequest(),
            SimpleNamespace(base_url='https://app.example.org'), user=SimpleNamespace(id='viewer'), db=None, db_log=None)
    assert error.value.status_code == 404


def test_external_render_refreshes_the_source_loaded_before_worker_completion(monkeypatch):
    from app.tools import helper
    from app.workers import rendering, tool_jobs
    from app.tools.slide_presentation.schemas import SlidePresentationEditorRenderRequest

    source = SimpleNamespace(meta={"canvas_revision": 3, "presentation_render_revision": 2})
    presentation = SimpleNamespace(file_id="old-export", title="Tesla", slide_count=1)
    monkeypatch.setattr(router, '_owned_editor_records', lambda *args: (presentation, source, dict(source.meta)))
    monkeypatch.setattr(router, 'get_file', lambda *args: source)
    monkeypatch.setattr(router, 'get_audit_request_ip', lambda request: None)
    monkeypatch.setattr(helper, 'enforce_tool_rate_limit_or_raise', lambda *args, **kwargs: None)
    monkeypatch.setattr(tool_jobs, 'external_rendering_enabled', lambda: True)
    monkeypatch.setattr(rendering, 'enqueue_presentation_rerender', lambda **kwargs: SimpleNamespace(id='job'))
    monkeypatch.setattr(rendering, 'wait_for_rendering_job', lambda job: {'result': {'file_id': 'new-export', 'slide_count': 1}})
    def refresh():
        source.meta = {"canvas_revision": 3, "presentation_render_revision": 3, "presentation_render_status": "ready"}
    result = router.render_presentation_editor_source(
        'deck', SlidePresentationEditorRenderRequest(expected_revision=3),
        request=SimpleNamespace(headers={}), user=SimpleNamespace(id='viewer', group_id=None),
        db=SimpleNamespace(expire_all=refresh), db_log=None,
    )
    assert result.render_revision == 3
    assert result.render_status == 'ready'
    assert result.file_id == 'new-export'


def test_editor_preparation_validates_owned_unsaved_source_and_execution_policy(monkeypatch):
    from app.tools.slide_presentation.schemas import SlidePresentationEditorPrepareRequest

    checked = []
    monkeypatch.setattr(router, '_owned_editor_records', lambda *args: checked.append(args))
    result = router.prepare_presentation_editor('deck', SlidePresentationEditorPrepareRequest(html=DECK),
        SimpleNamespace(base_url='https://app.example.org'), user=SimpleNamespace(id='owner'), db=None)
    assert checked == [(None, 'owner', 'deck')]
    assert 'window.quizValue = 42' in result.html
    assert '"mode": "editor"' in result.runtime
    assert 'https://app.example.org' not in result.csp
    assert "script-src 'unsafe-inline'" in result.csp
    assert validate_slide_presentation_html(result.html) == 1
    assert 'startPresentation' not in result.html  # Runtime never contaminates saved source.

    with pytest.raises(HTTPException) as invalid:
        router.prepare_presentation_editor('deck', SlidePresentationEditorPrepareRequest(html='<html>invalid</html>'),
            SimpleNamespace(base_url='https://app.example.org'), user=SimpleNamespace(id='owner'), db=None)
    assert invalid.value.status_code == 400

    def denied(*args):
        raise HTTPException(404, 'Presentation not found')
    monkeypatch.setattr(router, '_owned_editor_records', denied)
    with pytest.raises(HTTPException) as error:
        router.prepare_presentation_editor('other', SlidePresentationEditorPrepareRequest(html=DECK),
            SimpleNamespace(base_url='https://app.example.org'), user=SimpleNamespace(id='owner'), db=None)
    assert error.value.status_code == 404
