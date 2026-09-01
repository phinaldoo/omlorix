import { cpSync, mkdirSync, readFileSync, rmSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptDirectory, '..', '..');
const assetDirectory = path.join(repositoryRoot, 'cmd', 'omlorix-server-cli', 'assets');
const serverFileManifest = JSON.parse(readFileSync(
  path.join(repositoryRoot, 'cmd', 'omlorix-server-cli', 'server-files.json'),
  'utf8',
));

const assets = [...serverFileManifest.common, ...serverFileManifest.cliOnly];

/** Rebuild the embedded CLI asset tree without requiring a POSIX shell. */
export function prepareCliAssets() {
  rmSync(assetDirectory, { recursive: true, force: true });
  for (const relativePath of assets) {
    const targetPath = path.join(assetDirectory, relativePath);
    mkdirSync(path.dirname(targetPath), { recursive: true });
    cpSync(path.join(repositoryRoot, relativePath), targetPath);
  }
  return assetDirectory;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  console.log(`Prepared CLI assets in ${prepareCliAssets()}`);
}
