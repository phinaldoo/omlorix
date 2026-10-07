"""Serve production rendering assets and proxy for the reproducible visual proof.

Run from the repository root: python3 docs/pr-evidence/live-visualizations/serve.py
Requires backend dependencies; state uses a disposable SQLite fixture database.
No account, running PostgreSQL server, or AI provider is needed.
"""
import importlib.util
import json
import os
from datetime import datetime, timezone
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from examples import EXAMPLES, example_content

ROOT = Path(__file__).resolve().parents[3]
PROOF = Path(__file__).resolve().parent
security_hits = []
exported_html = b''

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded

proxy = module('proof_proxy', 'backend/app/files/html_preview.py')
visualization = module('proof_visualization', 'backend/app/tools/visualization/utils.py')

# State proof uses the production service and SQLite in a disposable fixture DB.
# The fixed fixture identity does not stand in for an authenticated app session.
sys.path.insert(0, str(ROOT / 'backend'))
from cryptography.fernet import Fernet
os.environ.setdefault('ENCRYPTION_KEY', Fernet.generate_key().decode())
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.chats.models import Chats, ChatMessages
from app.chats.schemas import VisualizationStateRequest
from app.chats.visualization_state import visualization_state
from fastapi import HTTPException
engine = create_engine('sqlite:////tmp/omlorix-visual-state-proof.sqlite', execution_options={'schema_translate_map': {'app': None}}, connect_args={'check_same_thread': False})
Base.metadata.create_all(engine, tables=[Chats.__table__, ChatMessages.__table__])
StateSession = sessionmaker(bind=engine)
with StateSession() as db:
    now = datetime.now(timezone.utc)
    if not db.get(Chats, 'proof'):
        db.add(Chats(id='proof', user_id='proof', title='Visual proof', meta={}, created_at=now, last_updated_at=now))
    for name in EXAMPLES:
        content = json.dumps([{'type': 'widget', 'content': example_content(name), 'meta': {'widget_type': 'visualization', 'tool_call_id': name, 'visualization': {'title': EXAMPLES[name]['title']}}}])
        row = db.get(ChatMessages, 'proof-' + name)
        if row: row.content = content
        else: db.add(ChatMessages(id='proof-' + name, chat_id='proof', model_id='fixture', role='assistant', content=content, created_at=now))
    db.commit()

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'frontend'), **kwargs)

    def do_POST(self):
        global exported_html
        size = int(self.headers.get('Content-Length', '0'))
        if self.path != '/__proof__/export' or not 0 < size <= 2 * 1024 * 1024:
            self.send_error(400)
            return
        exported_html = self.rfile.read(size)
        self.send_response(204)
        self.end_headers()

    def state_response(self, payload=None):
        try:
            parts = urlsplit(self.path).path.split('/')
            with StateSession() as db:
                result = visualization_state('proof', parts[5], parts[7], db, payload)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(result.model_dump_json().encode())
        except HTTPException as error:
            self.send_error(error.status_code)

    def do_PUT(self):
        if not self.path.startswith('/api/v1/chats/messages/proof-'):
            self.send_error(404)
            return
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.state_response(VisualizationStateRequest.model_validate(payload))

    def do_GET(self):
        path = self.path.split('?')[0]
        if path.startswith('/api/v1/chats/messages/proof-'):
            self.state_response()
        elif path.startswith('/__proof__/blocked'):
            security_hits.append(path)
            self.send_response(204)
            self.end_headers()
        elif path == '/__proof__/security-hits':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(security_hits).encode())
        elif path == '/__proof__/export':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(exported_html)
        elif path == '/api/v1/files/canvas/visualization-preview-proxy':
            payload = proxy.get_canvas_html_preview_proxy_payload(visualization=True)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            for name, value in payload['headers'].items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(payload['html'].encode())
        elif path == '/__proof__/payload':
            name = parse_qs(urlsplit(self.path).query).get('example', ['parallelism'])[0]
            if name not in EXAMPLES:
                self.send_error(404)
                return
            example = EXAMPLES[name]
            payload = visualization.create_visualization_payload(
                title=example['title'], mode='wide', capabilities={'chat_followup': name == 'mockup'},
                summary=example['summary'], content=example_content(name),
            )
            payload['proof'] = example
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
        elif path in ('/', '/__proof__/', '/__proof__/proof.css', '/__proof__/proof.js', '/__proof__/verify.js', '/__proof__/verify-examples.js', '/__proof__/verify-state.js'):
            file = PROOF / (path.rsplit('/', 1)[-1] or 'index.html')
            self.send_response(200)
            self.send_header('Content-Type', {'.css': 'text/css', '.js': 'text/javascript', '.html': 'text/html'}[file.suffix] + '; charset=utf-8')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'self'")
            self.end_headers()
            self.wfile.write(file.read_bytes())
        else:
            super().do_GET()

if __name__ == '__main__':
    print('Visual proof: http://127.0.0.1:8975', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8975), Handler).serve_forever()
