package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// launcherMetadata intentionally uses the Launcher's existing file and field
// so operators can alternate between GUI and CLI without losing downgrade
// diagnostics.
type launcherMetadata struct {
	Version                        int    `json:"version"`
	HighestSuccessfulServerVersion string `json:"highestSuccessfulServerVersion"`
	raw                            map[string]json.RawMessage
}

func launcherMetadataPath(opts options) string {
	return filepath.Join(opts.home, ".launcher-metadata.json")
}

func readLauncherMetadataForCLI(opts options) launcherMetadata {
	metadata := launcherMetadata{Version: 1, raw: map[string]json.RawMessage{}}
	raw, err := os.ReadFile(launcherMetadataPath(opts))
	if err == nil {
		if json.Unmarshal(raw, &metadata.raw) == nil {
			if metadata.raw == nil {
				metadata.raw = map[string]json.RawMessage{}
			}
			_ = json.Unmarshal(metadata.raw["version"], &metadata.Version)
			_ = json.Unmarshal(metadata.raw["highestSuccessfulServerVersion"], &metadata.HighestSuccessfulServerVersion)
		}
	}
	metadata.Version = 1
	return metadata
}

func trackableServerVersionForCLI(value string) string {
	value = strings.TrimPrefix(strings.TrimSpace(value), "v")
	if !semanticVersionPattern.MatchString(value) {
		return ""
	}
	return value
}

func recordSuccessfulServerVersionForCLI(opts options, version string) error {
	candidate := trackableServerVersionForCLI(version)
	if candidate == "" {
		return nil
	}
	metadata := readLauncherMetadataForCLI(opts)
	if current := trackableServerVersionForCLI(metadata.HighestSuccessfulServerVersion); current != "" && compareVersions(current, candidate) > 0 {
		candidate = current
	}
	metadata.HighestSuccessfulServerVersion = candidate
	versionRaw, err := json.Marshal(metadata.Version)
	if err != nil {
		return err
	}
	highestRaw, err := json.Marshal(metadata.HighestSuccessfulServerVersion)
	if err != nil {
		return err
	}
	metadata.raw["version"] = versionRaw
	metadata.raw["highestSuccessfulServerVersion"] = highestRaw
	raw, err := json.MarshalIndent(metadata.raw, "", "  ")
	if err != nil {
		return err
	}
	return atomicWriteFile(launcherMetadataPath(opts), append(raw, '\n'), 0o600)
}

func possibleDatabaseDowngradeErrorForCLI(opts options, version string, cause error) error {
	current := trackableServerVersionForCLI(version)
	highest := trackableServerVersionForCLI(readLauncherMetadataForCLI(opts).HighestSuccessfulServerVersion)
	if current == "" || highest == "" || compareVersions(current, highest) >= 0 {
		return cause
	}
	return fmt.Errorf("Omlorix %s did not become ready after this server previously ran %s; database migrations from the newer release may prevent a downgrade: %w", current, highest, cause)
}
