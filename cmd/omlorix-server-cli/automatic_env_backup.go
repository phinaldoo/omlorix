package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// automaticEnvBackupConfig is intentionally stored beside the selected .env.
// That lets every CLI configuration write refresh the recovery copy even when
// an operator supplies a custom --env-file path.
type automaticEnvBackupConfig struct {
	Target      string `json:"target"`
	LastSavedAt string `json:"lastSavedAt"`
	Fingerprint string `json:"fingerprint"`
	LastError   string `json:"lastError,omitempty"`
	ServerHome  string `json:"serverHome,omitempty"`
}

func automaticEnvBackupConfigPath(envFile string) string {
	return filepath.Join(filepath.Dir(envFile), ".omlorix-server-env-backup.json")
}

func envContentFingerprint(raw []byte) string {
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}

// completeRecoveryEnv keeps the external recovery format self-contained while
// the live Compose .env remains free of management-only settings. The legacy
// update-channel key is intentionally reused only in recovery snapshots so old
// and new backups share one backward-compatible interchange format.
func completeRecoveryEnv(serverHome, envFile string, raw []byte) ([]byte, error) {
	settings, err := readServerSettings(options{home: firstNonBlank(serverHome, filepath.Dir(envFile)), envFile: envFile})
	if err != nil {
		return nil, err
	}
	keys := make(map[string]bool, len(managedProxySettingsEnvKeys)+len(retiredEnvKeys)+1)
	keys["OMLORIX_UPDATE_CHANNEL"] = true
	for _, key := range managedProxySettingsEnvKeys {
		keys[key] = true
	}
	for key := range retiredEnvKeys {
		keys[key] = true
	}
	clean := removeEnvKeysFromContent(string(raw), keys)
	projected := applyServerSettingsToEnv(map[string]string{}, settings)
	updates := make(map[string]string, len(managedProxySettingsEnvKeys)+1)
	updates["OMLORIX_UPDATE_CHANNEL"] = settings.UpdateChannel
	for _, key := range managedProxySettingsEnvKeys {
		updates[key] = projected[key]
	}
	return []byte(strings.TrimRight(updateEnvContent(clean, updates), "\r\n") + "\n"), nil
}

func readAutomaticEnvBackupConfig(envFile string) (automaticEnvBackupConfig, error) {
	raw, err := os.ReadFile(automaticEnvBackupConfigPath(envFile))
	if errors.Is(err, os.ErrNotExist) {
		// Launcher versions before the shared record kept these fields inside
		// onboarding state. Read that format only when no canonical record exists;
		// an intentionally empty canonical record therefore disables stale state.
		legacyRaw, legacyErr := os.ReadFile(filepath.Join(filepath.Dir(envFile), ".launcher-setup.json"))
		if errors.Is(legacyErr, os.ErrNotExist) {
			return automaticEnvBackupConfig{}, nil
		}
		if legacyErr != nil {
			return automaticEnvBackupConfig{}, legacyErr
		}
		var legacy struct {
			Target      string `json:"backupFilePath"`
			LastSavedAt string `json:"backupSavedAt"`
		}
		if json.Unmarshal(legacyRaw, &legacy) != nil {
			return automaticEnvBackupConfig{}, nil
		}
		config := automaticEnvBackupConfig{Target: legacy.Target, LastSavedAt: legacy.LastSavedAt}
		if backupRaw, backupErr := os.ReadFile(config.Target); backupErr == nil {
			config.Fingerprint = envContentFingerprint(backupRaw)
		}
		return config, nil
	}
	if err != nil {
		return automaticEnvBackupConfig{}, err
	}
	var config automaticEnvBackupConfig
	if err := json.Unmarshal(raw, &config); err != nil {
		return automaticEnvBackupConfig{}, errors.New("automatic .env backup settings are invalid")
	}
	return config, nil
}

func writeAutomaticEnvBackupConfig(envFile string, config automaticEnvBackupConfig) error {
	raw, err := json.MarshalIndent(config, "", "  ")
	if err != nil {
		return err
	}
	return atomicWriteFile(automaticEnvBackupConfigPath(envFile), append(raw, '\n'), 0o600)
}

// validateAutomaticEnvBackupTarget prevents a recovery copy from living inside
// the deployment directory that it is expected to recover.
func validateAutomaticEnvBackupTarget(serverHome, target string) (string, error) {
	absolute, err := filepath.Abs(strings.TrimSpace(target))
	if err != nil || strings.TrimSpace(target) == "" {
		return "", errors.New("choose a file path for the automatic .env backup")
	}
	absoluteHome, err := filepath.Abs(serverHome)
	if err != nil {
		return "", err
	}
	relative, err := filepath.Rel(filepath.Clean(absoluteHome), absolute)
	if err != nil {
		return "", err
	}
	if relative == "." || (!filepath.IsAbs(relative) && relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator))) {
		return "", errors.New("store the automatic .env backup outside the Omlorix server folder")
	}
	return absolute, nil
}

// A failed external refresh must not roll back a valid live configuration
// write. Match the Launcher by retaining the live change and surfacing a loud
// warning that can be inspected with `secrets backup-status`.
func refreshAutomaticEnvBackupAfterWrite(envFile string) {
	if err := refreshAutomaticEnvBackup(envFile); err != nil {
		fmt.Fprintf(os.Stderr, "Warning: the live .env was saved, but its automatic recovery copy could not be refreshed: %s\n", err)
	}
}

func configureAutomaticEnvBackup(opts options, target string) error {
	absolute, err := validateAutomaticEnvBackupTarget(opts.home, target)
	if err != nil {
		return err
	}
	raw, err := os.ReadFile(opts.envFile)
	if err != nil {
		return err
	}
	recoveryRaw, err := completeRecoveryEnv(opts.home, opts.envFile, raw)
	if err != nil {
		return err
	}
	if err := atomicWriteFile(absolute, recoveryRaw, 0o600); err != nil {
		return err
	}
	config := automaticEnvBackupConfig{
		Target: absolute, LastSavedAt: time.Now().UTC().Format(time.RFC3339),
		Fingerprint: envContentFingerprint(recoveryRaw), ServerHome: opts.home,
	}
	if err := writeAutomaticEnvBackupConfig(opts.envFile, config); err != nil {
		return err
	}
	fmt.Printf("Automatic .env backup, including secrets, saved and configured at %s\n", absolute)
	return nil
}

func refreshAutomaticEnvBackup(envFile string) error {
	config, err := readAutomaticEnvBackupConfig(envFile)
	if err != nil || strings.TrimSpace(config.Target) == "" {
		return err
	}
	raw, err := os.ReadFile(envFile)
	if err == nil {
		raw, err = completeRecoveryEnv(config.ServerHome, envFile, raw)
	}
	if err == nil {
		err = atomicWriteFile(config.Target, raw, 0o600)
	}
	if err != nil {
		config.LastError = err.Error()
		_ = writeAutomaticEnvBackupConfig(envFile, config)
		return err
	}
	config.LastSavedAt = time.Now().UTC().Format(time.RFC3339)
	config.Fingerprint = envContentFingerprint(raw)
	config.LastError = ""
	return writeAutomaticEnvBackupConfig(envFile, config)
}

func commandAutomaticEnvBackupStatus(opts options) error {
	config, err := readAutomaticEnvBackupConfig(opts.envFile)
	if err != nil {
		return err
	}
	configured := strings.TrimSpace(config.Target) != ""
	current := false
	if configured {
		liveRaw, liveErr := os.ReadFile(opts.envFile)
		if liveErr == nil {
			liveRaw, liveErr = completeRecoveryEnv(opts.home, opts.envFile, liveRaw)
		}
		backupRaw, backupErr := os.ReadFile(config.Target)
		current = liveErr == nil && backupErr == nil && envContentFingerprint(liveRaw) == envContentFingerprint(backupRaw)
	}
	payload := map[string]any{
		"configured": configured, "current": current, "target": config.Target,
		"last_saved_at": config.LastSavedAt, "last_error": config.LastError,
	}
	if opts.jsonOutput {
		return printJSON(payload)
	}
	fmt.Printf("Automatic .env backup: %s\n", boolChoice(configured, "configured", "not configured"))
	if configured {
		fmt.Printf("Target: %s\nCurrent: %t\nLast saved: %s\n", config.Target, current, firstNonBlank(config.LastSavedAt, "never"))
		if config.LastError != "" {
			fmt.Printf("Last error: %s\n", config.LastError)
		}
	}
	return nil
}
