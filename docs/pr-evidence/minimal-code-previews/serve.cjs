// Isolated browser proof using the same renderer and CSS as index.html.
// Run: node docs/pr-evidence/minimal-code-previews/serve.cjs
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const proxy = JSON.parse(execFileSync('python3', ['-c', "import runpy,json; print(json.dumps(runpy.run_path('backend/app/files/html_preview.py')['get_canvas_html_preview_proxy_payload']()))"], { cwd: path.resolve(__dirname, '../../..') }));
const root = path.resolve(__dirname, '../../../frontend');
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf', '.svg': 'image/svg+xml' };
http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    if (pathname === '/api/v1/files/canvas/html-preview-proxy') {
        res.writeHead(200, { 'Content-Type': 'text/html', ...proxy.headers });
        res.end(proxy.html); return;
    }
    if (pathname === '/proof-checks.js') {
        res.setHeader('Content-Type', 'text/javascript');
        res.end(fs.readFileSync(path.join(__dirname, 'checks.js'))); return;
    }
    const file = pathname === '/' ? path.join(__dirname, 'index.html') : path.resolve(root, '.' + decodeURIComponent(pathname));
    if (pathname !== '/' && !file.startsWith(root + path.sep)) { res.writeHead(403).end(); return; }
    try {
        res.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream');
        res.end(fs.readFileSync(file));
    } catch { res.writeHead(404).end(); }
}).listen(8765, '127.0.0.1', () => console.log('Preview proof: http://localhost:8765'));
