//go:build !windows

package main

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
)

func preflightProxyServiceInstall(_ options) error { return nil }

// stableProxyServiceExecutablePath uses a product-owned per-user data path.
// launchd and systemd user services run with the same privilege as this owner,
// so a root-owned system path would add prompts without adding a boundary.
func stableProxyServiceExecutablePath(_ options) (string, error) {
	switch runtime.GOOS {
	case "darwin":
		return filepath.Join(userHome(), "Library", "Application Support", "Omlorix Server", "service", "omlorix-server"), nil
	default:
		return filepath.Join(userHome(), ".local", "lib", "omlorix-server", "omlorix-server"), nil
	}
}

func prepareStableProxyServiceDirectory(_ options, directory string) error {
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return fmt.Errorf("could not create the stable proxy service directory: %w", err)
	}
	if err := os.Chmod(directory, 0o700); err != nil {
		return fmt.Errorf("could not protect the stable proxy service directory: %w", err)
	}
	return nil
}

func finalizeStableProxyServiceExecutable(_ options, executable string) error {
	if err := os.Chmod(executable, 0o700); err != nil {
		return fmt.Errorf("could not protect the stable proxy service executable: %w", err)
	}
	return nil
}

func replaceStableProxyServiceExecutable(source string, destination string) error {
	return os.Rename(source, destination)
}

func removeStableProxyServiceFile(path string) error { return os.Remove(path) }

func nativeProxyServiceInstalled(opts options) bool {
	_, err := os.Stat(proxyServiceDefinitionPath(opts))
	return err == nil
}
