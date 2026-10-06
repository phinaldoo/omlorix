"""Serve production rendering assets and proxy for the reproducible visual proof.

Run from the repository root: python3 docs/pr-evidence/live-visualizations/serve.py
No database, account, AI provider, or application dependencies are needed.
"""
import importlib.util
import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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

    def do_GET(self):
        path = self.path.split('?')[0]
        if path.startswith('/__proof__/blocked'):
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
            payload = visualization.create_visualization_payload(
                title='The shape of a faster response', mode='wide',
                summary='Illustrative model, not a benchmark. Increasing parallel workers shortens processing time until coordination overhead dominates. The table provides the values shown in the chart.',
                content=(PROOF / 'parallelism.html').read_text(),
            )
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
        elif path in ('/', '/__proof__/', '/__proof__/proof.css', '/__proof__/proof.js', '/__proof__/verify.js'):
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
