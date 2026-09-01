package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const serverSettingsVersion = 2

var managedProxySettingsEnvKeys = []string{
	"OMLORIX_LAUNCHER_PROXY_ENABLED",
	"OMLORIX_LAUNCHER_PROXY_AUTOSTART",
	"OMLORIX_LAUNCHER_PROXY_BIND",
	"OMLORIX_LAUNCHER_PROXY_PUBLIC_HOSTNAME",
	"OMLORIX_LAUNCHER_PROXY_HTTP_PORT",
	"OMLORIX_LAUNCHER_PROXY_HTTPS_ENABLED",
	"OMLORIX_LAUNCHER_PROXY_HTTPS_PORT",
	"OMLORIX_LAUNCHER_PROXY_REDIRECT_HTTP_TO_HTTPS",
	"OMLORIX_LAUNCHER_PROXY_TLS_CERT_PATH",
	"OMLORIX_LAUNCHER_PROXY_TLS_KEY_PATH",
	"OMLORIX_LAUNCHER_PROXY_TLS_CA_PATH",
	"OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE",
}

func isManagedProxySettingsEnvKey(key string) bool {
	for _, candidate := range managedProxySettingsEnvKeys {
		if key == candidate {
			return true
		}
	}
	return false
}

// managedProxySettings configures the native host listener. The generated
// launcher secret deliberately remains in .env because nginx and the backend
// containers consume it to authenticate the host-proxy hop.
type managedProxySettings struct {
	Enabled             bool   `json:"enabled"`
	Autostart           bool   `json:"autostart"`
	BindHost            string `json:"bindHost"`
	PublicHostname      string `json:"publicHostname"`
	HTTPPort            string `json:"httpPort"`
	HTTPSEnabled        bool   `json:"httpsEnabled"`
	HTTPSPort           string `json:"httpsPort"`
	RedirectHTTPToHTTPS bool   `json:"redirectHttpToHttps"`
	TLSCertPath         string `json:"tlsCertPath"`
	TLSKeyPath          string `json:"tlsKeyPath"`
	TLSCAPath           string `json:"tlsCaPath"`
	TLSKeyPassphrase    string `json:"tlsKeyPassphrase"`
}

// serverSettings contains permission-restricted management state shared by the
// Electron Launcher and standalone CLI. These values intentionally live
// outside .env because Compose and the Omlorix containers do not consume them.
type serverSettings struct {
	SchemaVersion int                  `json:"schemaVersion"`
	UpdateChannel string               `json:"updateChannel"`
	Proxy         managedProxySettings `json:"proxy"`
}

func defaultServerSettings() serverSettings {
	return serverSettings{
		SchemaVersion: serverSettingsVersion,
		UpdateChannel: "stable",
		Proxy: managedProxySettings{
			Autostart: true,
			BindHost:  "0.0.0.0",
			HTTPPort:  defaultProxyHTTPPort,
			HTTPSPort: defaultProxyHTTPSPort,
		},
	}
}

func proxySettingsFromEnv(env map[string]string) managedProxySettings {
	autostart := true
	if value := strings.TrimSpace(env["OMLORIX_LAUNCHER_PROXY_AUTOSTART"]); value != "" {
		autostart = envTruthy(value, true)
	}
	return managedProxySettings{
		Enabled:             envTruthy(env["OMLORIX_LAUNCHER_PROXY_ENABLED"], false),
		Autostart:           autostart,
		BindHost:            firstNonBlank(env["OMLORIX_LAUNCHER_PROXY_BIND"], "0.0.0.0"),
		PublicHostname:      strings.TrimSpace(env["OMLORIX_LAUNCHER_PROXY_PUBLIC_HOSTNAME"]),
		HTTPPort:            firstNonBlank(env["OMLORIX_LAUNCHER_PROXY_HTTP_PORT"], defaultProxyHTTPPort),
		HTTPSEnabled:        envTruthy(env["OMLORIX_LAUNCHER_PROXY_HTTPS_ENABLED"], false),
		HTTPSPort:           firstNonBlank(env["OMLORIX_LAUNCHER_PROXY_HTTPS_PORT"], defaultProxyHTTPSPort),
		RedirectHTTPToHTTPS: envTruthy(env["OMLORIX_LAUNCHER_PROXY_REDIRECT_HTTP_TO_HTTPS"], false),
		TLSCertPath:         strings.TrimSpace(env["OMLORIX_LAUNCHER_PROXY_TLS_CERT_PATH"]),
		TLSKeyPath:          strings.TrimSpace(env["OMLORIX_LAUNCHER_PROXY_TLS_KEY_PATH"]),
		TLSCAPath:           strings.TrimSpace(env["OMLORIX_LAUNCHER_PROXY_TLS_CA_PATH"]),
		TLSKeyPassphrase:    env["OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE"],
	}
}

func applyServerSettingsToEnv(env map[string]string, settings serverSettings) map[string]string {
	env["OMLORIX_LAUNCHER_PROXY_ENABLED"] = fmt.Sprintf("%t", settings.Proxy.Enabled)
	env["OMLORIX_LAUNCHER_PROXY_AUTOSTART"] = fmt.Sprintf("%t", settings.Proxy.Autostart)
	env["OMLORIX_LAUNCHER_PROXY_BIND"] = settings.Proxy.BindHost
	env["OMLORIX_LAUNCHER_PROXY_PUBLIC_HOSTNAME"] = settings.Proxy.PublicHostname
	env["OMLORIX_LAUNCHER_PROXY_HTTP_PORT"] = settings.Proxy.HTTPPort
	env["OMLORIX_LAUNCHER_PROXY_HTTPS_ENABLED"] = fmt.Sprintf("%t", settings.Proxy.HTTPSEnabled)
	env["OMLORIX_LAUNCHER_PROXY_HTTPS_PORT"] = settings.Proxy.HTTPSPort
	env["OMLORIX_LAUNCHER_PROXY_REDIRECT_HTTP_TO_HTTPS"] = fmt.Sprintf("%t", settings.Proxy.RedirectHTTPToHTTPS)
	env["OMLORIX_LAUNCHER_PROXY_TLS_CERT_PATH"] = settings.Proxy.TLSCertPath
	env["OMLORIX_LAUNCHER_PROXY_TLS_KEY_PATH"] = settings.Proxy.TLSKeyPath
	env["OMLORIX_LAUNCHER_PROXY_TLS_CA_PATH"] = settings.Proxy.TLSCAPath
	env["OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE"] = settings.Proxy.TLSKeyPassphrase
	return env
}

func readManagedEnvironment(opts options) (map[string]string, string, error) {
	env, raw := readEnv(opts.envFile)
	settings, err := readServerSettings(opts)
	if err != nil {
		return env, raw, err
	}
	return applyServerSettingsToEnv(env, settings), raw, nil
}

func serverSettingsPath(opts options) string {
	return filepath.Join(opts.home, "server-settings.json")
}

// readServerSettings validates persisted management settings instead of
// silently replacing malformed operator state with defaults.
func readServerSettings(opts options) (serverSettings, error) {
	settings := defaultServerSettings()
	raw, err := os.ReadFile(serverSettingsPath(opts))
	if errors.Is(err, os.ErrNotExist) {
		return settings, nil
	}
	if err != nil {
		return settings, err
	}
	if err := json.Unmarshal(raw, &settings); err != nil {
		return defaultServerSettings(), errors.New("server settings are invalid")
	}
	channel, err := validateUpdateChannel(settings.UpdateChannel)
	if err != nil || channel == "" {
		return defaultServerSettings(), errors.New("server settings contain an invalid update channel")
	}
	settings.SchemaVersion = serverSettingsVersion
	settings.UpdateChannel = channel
	return settings, nil
}

// writeServerSettings uses the CLI's atomic file helper so the Launcher and
// CLI can safely alternate ownership of the same small JSON document.
func writeServerSettings(opts options, settings serverSettings) error {
	channel, err := validateUpdateChannel(settings.UpdateChannel)
	if err != nil || channel == "" {
		return errors.New("update channel must be stable or beta")
	}
	settings.SchemaVersion = serverSettingsVersion
	settings.UpdateChannel = channel
	if settings.Proxy == (managedProxySettings{}) {
		settings.Proxy = defaultServerSettings().Proxy
	}
	settings.Proxy.BindHost = firstNonBlank(settings.Proxy.BindHost, "0.0.0.0")
	settings.Proxy.HTTPPort = firstNonBlank(settings.Proxy.HTTPPort, defaultProxyHTTPPort)
	settings.Proxy.HTTPSPort = firstNonBlank(settings.Proxy.HTTPSPort, defaultProxyHTTPSPort)
	settings.Proxy.PublicHostname = strings.TrimSpace(settings.Proxy.PublicHostname)
	settings.Proxy.TLSCertPath = strings.TrimSpace(settings.Proxy.TLSCertPath)
	settings.Proxy.TLSKeyPath = strings.TrimSpace(settings.Proxy.TLSKeyPath)
	settings.Proxy.TLSCAPath = strings.TrimSpace(settings.Proxy.TLSCAPath)
	raw, err := json.MarshalIndent(settings, "", "  ")
	if err != nil {
		return err
	}
	if err := atomicWriteFile(serverSettingsPath(opts), append(raw, '\n'), 0o600); err != nil {
		return err
	}
	refreshAutomaticEnvBackupAfterWrite(opts.envFile)
	return nil
}

func writeUpdateChannel(opts options, channelInput string) error {
	channel, err := validateUpdateChannel(channelInput)
	if err != nil || channel == "" {
		return errors.New("update channel must be stable or beta")
	}
	settings, err := readServerSettings(opts)
	if err != nil {
		return err
	}
	settings.UpdateChannel = channel
	return writeServerSettings(opts, settings)
}

// createSettingsMigrationEnvBackup preserves the recoverable pre-migration
// dotenv state while excluding settings that have been permanently retired.
func createSettingsMigrationEnvBackup(opts options, raw string) (string, error) {
	backupDirectory := filepath.Join(opts.home, ".env.backups")
	if err := os.MkdirAll(backupDirectory, 0o700); err != nil {
		return "", err
	}
	stamp := time.Now().UTC().Format("20060102-150405.000000000")
	backupPath := filepath.Join(backupDirectory, fmt.Sprintf(".env.%s-settings-migration.bak", stamp))
	file, err := os.OpenFile(backupPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return "", err
	}
	backupRaw := removeEnvKeysFromContent(raw, retiredEnvKeys)
	if _, err := file.WriteString(backupRaw); err != nil {
		_ = file.Close()
		return "", err
	}
	if err := file.Close(); err != nil {
		return "", err
	}
	return backupPath, nil
}

// migrateLegacyServerSettings moves former dotenv update/proxy state into the
// shared settings document and removes repository overrides that no longer
// affect the fixed official release images.
func migrateLegacyServerSettings(opts options) error {
	env, raw := readEnv(opts.envFile)
	legacyKeys := map[string]bool{
		"OMLORIX_UPDATE_CHANNEL":            true,
		"OMLORIX_BACKEND_IMAGE_REPOSITORY":  true,
		"OMLORIX_FRONTEND_IMAGE_REPOSITORY": true,
		"FILE_SCANNER_COMMAND":             true,
	}
	for key := range retiredEnvKeys {
		legacyKeys[key] = true
	}
	for _, key := range managedProxySettingsEnvKeys {
		legacyKeys[key] = true
	}
	hasLegacyKeys := false
	for key := range legacyKeys {
		if _, present := env[key]; present {
			hasLegacyKeys = true
			break
		}
	}

	settingsFileExists := true
	storedHasProxy := false
	storedSchemaVersion := 0
	if _, err := os.Stat(serverSettingsPath(opts)); errors.Is(err, os.ErrNotExist) {
		settingsFileExists = false
	} else if err != nil {
		return err
	} else {
		rawSettings, readErr := os.ReadFile(serverSettingsPath(opts))
		if readErr != nil {
			return readErr
		}
		var shape struct {
			SchemaVersion int             `json:"schemaVersion"`
			Proxy         json.RawMessage `json:"proxy"`
		}
		if json.Unmarshal(rawSettings, &shape) != nil {
			return errors.New("server settings are invalid")
		}
		storedSchemaVersion = shape.SchemaVersion
		storedHasProxy = len(shape.Proxy) > 0 && string(shape.Proxy) != "null"
	}
	settings, err := readServerSettings(opts)
	if err != nil {
		return err
	}
	if !settingsFileExists {
		if legacyChannel := strings.TrimSpace(env["OMLORIX_UPDATE_CHANNEL"]); legacyChannel != "" {
			channel, validationErr := validateUpdateChannel(legacyChannel)
			if validationErr != nil {
				return fmt.Errorf("invalid OMLORIX_UPDATE_CHANNEL in %s: %w", opts.envFile, validationErr)
			}
			settings.UpdateChannel = channel
		}
	}
	legacyProxyPresent := false
	for _, key := range managedProxySettingsEnvKeys {
		if _, present := env[key]; present {
			legacyProxyPresent = true
			break
		}
	}
	if legacyProxyPresent && !storedHasProxy {
		settings.Proxy = proxySettingsFromEnv(env)
	}

	if hasLegacyKeys {
		if _, err := createSettingsMigrationEnvBackup(opts, raw); err != nil {
			return fmt.Errorf("could not back up environment before settings migration: %w", err)
		}
	}
	if !settingsFileExists || storedSchemaVersion < serverSettingsVersion || legacyProxyPresent {
		if err := writeServerSettings(opts, settings); err != nil {
			return err
		}
	}
	if !hasLegacyKeys {
		return nil
	}

	migrated := strings.TrimSpace(removeEnvKeysFromContent(raw, legacyKeys)) + "\n"
	if err := atomicWriteFile(opts.envFile, []byte(migrated), 0o600); err != nil {
		return err
	}
	refreshAutomaticEnvBackupAfterWrite(opts.envFile)
	return nil
}

// commandUpdateChannel provides the CLI equivalent of the Launcher's channel
// selector without exposing a management preference as container environment.
func commandUpdateChannel(opts options) error {
	if err := ensureServerHome(opts); err != nil {
		return err
	}
	if len(opts.arguments) > 1 {
		return errors.New("usage: omlorix-server update-channel [stable|beta]")
	}
	if len(opts.arguments) == 1 {
		if err := writeUpdateChannel(opts, opts.arguments[0]); err != nil {
			return err
		}
	}
	settings, err := readServerSettings(opts)
	if err != nil {
		return err
	}
	if opts.jsonOutput {
		return printJSON(map[string]string{"update_channel": settings.UpdateChannel})
	}
	fmt.Println(settings.UpdateChannel)
	return nil
}
