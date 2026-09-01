//go:build windows

package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"golang.org/x/sys/windows"
)

const windowsProxyServiceName = "OmlorixServerProxy"

// preflightProxyServiceInstall fails before a working detached proxy is
// stopped when the current Windows token cannot perform the privileged install.
func preflightProxyServiceInstall(opts options) error {
	token := windows.GetCurrentProcessToken()
	if !token.IsElevated() {
		return errors.New("installing the Windows proxy service requires an elevated Administrator terminal")
	}
	destination, err := stableProxyServiceExecutablePath(opts)
	if err != nil {
		return err
	}
	return prepareStableProxyServiceDirectory(opts, filepath.Dir(destination))
}

// stableProxyServiceExecutablePath deliberately resolves Program Files through
// the Windows Known Folder API instead of an inherited environment variable.
// An untrusted user environment must never choose a LocalSystem binary path.
func stableProxyServiceExecutablePath(_ options) (string, error) {
	programFiles, err := windows.KnownFolderPath(windows.FOLDERID_ProgramFiles, 0)
	if err != nil {
		return "", fmt.Errorf("could not resolve the protected Windows program directory: %w", err)
	}
	return filepath.Join(programFiles, "Omlorix Server", "service", "omlorix-server.exe"), nil
}

func prepareStableProxyServiceDirectory(opts options, directory string) error {
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return fmt.Errorf("could not create the protected proxy service directory: %w", err)
	}
	programFiles, err := windows.KnownFolderPath(windows.FOLDERID_ProgramFiles, 0)
	if err != nil {
		return fmt.Errorf("could not resolve the protected Windows program directory: %w", err)
	}
	if err := rejectWindowsReparseTree(programFiles, directory); err != nil {
		return err
	}
	// Use SID forms so ACL creation does not depend on the Windows display
	// language. Standard users receive read/execute only; administrators and
	// LocalSystem retain the access required to update and run the service.
	output, err := runCapture("icacls.exe", []string{
		directory,
		"/inheritance:r",
		"/grant:r",
		"*S-1-5-18:(OI)(CI)F",
		"*S-1-5-32-544:(OI)(CI)F",
		"*S-1-5-32-545:(OI)(CI)RX",
	}, opts.home)
	if err != nil {
		return fmt.Errorf("could not protect the proxy service directory ACL: %s", strings.TrimSpace(output))
	}
	return nil
}

// rejectWindowsReparseTree checks every product-owned component beneath the
// trusted Program Files root. Checking only the leaf would still permit an
// attacker-created parent junction to redirect the privileged copy elsewhere.
func rejectWindowsReparseTree(root string, target string) error {
	relative, err := filepath.Rel(root, target)
	if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(os.PathSeparator)) {
		return errors.New("proxy service path must remain beneath Program Files")
	}
	current := root
	for _, component := range strings.Split(relative, string(os.PathSeparator)) {
		if component == "" || component == "." {
			continue
		}
		current = filepath.Join(current, component)
		if err := rejectWindowsReparsePoint(current); err != nil {
			return err
		}
	}
	return nil
}

func finalizeStableProxyServiceExecutable(opts options, executable string) error {
	if err := rejectWindowsReparsePoint(executable); err != nil {
		return err
	}
	output, err := runCapture("icacls.exe", []string{
		executable,
		"/inheritance:r",
		"/grant:r",
		"*S-1-5-18:F",
		"*S-1-5-32-544:F",
		"*S-1-5-32-545:RX",
	}, opts.home)
	if err != nil {
		return fmt.Errorf("could not protect the proxy service executable ACL: %s", strings.TrimSpace(output))
	}
	return nil
}

func replaceStableProxyServiceExecutable(source string, destination string) error {
	sourcePointer, err := windows.UTF16PtrFromString(source)
	if err != nil {
		return err
	}
	destinationPointer, err := windows.UTF16PtrFromString(destination)
	if err != nil {
		return err
	}
	var replaceErr error
	for attempt := 0; attempt < 50; attempt++ {
		replaceErr = windows.MoveFileEx(
			sourcePointer,
			destinationPointer,
			windows.MOVEFILE_REPLACE_EXISTING|windows.MOVEFILE_WRITE_THROUGH,
		)
		if replaceErr == nil {
			return nil
		}
		// SCM can report the service stopped just before Windows releases the
		// executable image. A bounded retry makes service refresh reliable while
		// preserving atomic replacement and never falling back to in-place writes.
		time.Sleep(100 * time.Millisecond)
	}
	return replaceErr
}

func removeStableProxyServiceFile(path string) error {
	var removeErr error
	for attempt := 0; attempt < 50; attempt++ {
		removeErr = os.Remove(path)
		if removeErr == nil || errors.Is(removeErr, os.ErrNotExist) {
			return removeErr
		}
		time.Sleep(100 * time.Millisecond)
	}
	return removeErr
}

func rejectWindowsReparsePoint(path string) error {
	pointer, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return err
	}
	attributes, err := windows.GetFileAttributes(pointer)
	if err != nil {
		return err
	}
	if attributes&windows.FILE_ATTRIBUTE_REPARSE_POINT != 0 {
		return errors.New("proxy service paths must not contain Windows reparse points")
	}
	return nil
}

func nativeProxyServiceInstalled(opts options) bool {
	_, err := runCapture("sc.exe", []string{"query", windowsProxyServiceName}, opts.home)
	return err == nil
}
