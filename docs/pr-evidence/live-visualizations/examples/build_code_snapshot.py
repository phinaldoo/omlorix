"""Rebuild the treemap's measured repository snapshot, without reading secrets."""
import io
import json
from pathlib import Path
import subprocess
import tarfile

REF = '36df43ebc13eda3cd72227d6437d0b23dfe39db8'
SCOPES = ('backend/app', 'backend/tests', 'frontend/js')


def git(*args):
    return subprocess.check_output(['git', *args])


if __name__ == '__main__':
    touches = {}
    history = git('log', '-100', '--format=', '--name-only', REF).decode().splitlines()
    for path in history:
        if path:
            touches[path] = touches.get(path, 0) + 1
    rows = []
    with tarfile.open(fileobj=io.BytesIO(git('archive', REF, *SCOPES))) as archive:
        for entry in archive:
            path = entry.name
            if not entry.isfile() or '/vendor/' in path or not path.endswith(('.py', '.js')):
                continue
            rows.append([path, len(archive.extractfile(entry).read().splitlines()), touches.get(path, 0)])
    payload = {'commit': REF, 'historyCommits': 100, 'files': sorted(rows)}
    Path(__file__).with_name('code-snapshot.json').write_text(json.dumps(payload, separators=(',', ':')) + '\n')
    print(f'{len(rows)} source files, {sum(row[1] for row in rows):,} lines')
