const electronToGoArchitecture = {
  x64: 'amd64',
  arm64: 'arm64',
};
const knownElectronArchitectures = new Set([
  ...Object.keys(electronToGoArchitecture),
  'ia32',
  'armv7l',
]);

/**
 * Resolve one Electron target architecture to the matching Go architecture.
 * A single app resources directory cannot safely contain binaries for multiple
 * architectures, so multi-architecture requests fail instead of silently
 * embedding a host-only executable.
 */
export function resolveDesktopArchitecture(argumentsList, hostArchitecture) {
  const requested = [];
  for (let index = 0; index < argumentsList.length; index += 1) {
    const argument = argumentsList[index];
    if (knownElectronArchitectures.has(argument)) requested.push(argument);
    if (/^--(x64|arm64|ia32|armv7l)$/.test(argument)) requested.push(argument.slice(2));
    if (argument === '--universal' || argument === 'universal') requested.push('universal');
    if (argument === '--arch' && argumentsList[index + 1]) requested.push(argumentsList[++index]);
    if (argument.startsWith('--arch=')) requested.push(argument.slice('--arch='.length));
  }

  const architectures = [...new Set(requested)];
  if (architectures.length > 1) {
    throw new Error(`Desktop packaging supports one architecture at a time, received: ${architectures.join(', ')}`);
  }
  const electronArchitecture = architectures[0] || hostArchitecture;
  const goArchitecture = electronToGoArchitecture[electronArchitecture];
  if (!goArchitecture) {
    throw new Error(`Unsupported desktop architecture: ${electronArchitecture}`);
  }
  return { electronArchitecture, goArchitecture };
}
