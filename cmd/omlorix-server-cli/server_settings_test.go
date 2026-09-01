package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestMigrateLegacyServerSettings(t *testing.T) {
	home := t.TempDir()
	opts := options{home: home, envFile: filepath.Join(home, ".env")}
	legacy := strings.Join([]string{
		"OMLORIX_VERSION=1.2.2",
		"OMLORIX_UPDATE_CHANNEL=beta",
		"OMLORIX_BACKEND_IMAGE_REPOSITORY=registry.example/backend",
		"OMLORIX_FRONTEND_IMAGE_REPOSITORY=registry.example/frontend",
		"FILE_SCANNER_COMMAND=clamscan --no-summary",
		"OMLORIX_GITHUB_TOKEN=retired-release-token",
		"OMLORIX_LAUNCHER_PROXY_ENABLED=true",
		"OMLORIX_LAUNCHER_PROXY_AUTOSTART=false",
		"OMLORIX_LAUNCHER_PROXY_BIND=127.0.0.1",
		"OMLORIX_LAUNCHER_PROXY_HTTP_PORT=9081",
		"OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE=legacy-passphrase",
		"",
	}, "\n")
	if err := os.WriteFile(opts.envFile, []byte(legacy), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := migrateLegacyServerSettings(opts); err != nil {
		t.Fatal(err)
	}

	settings, err := readServerSettings(opts)
	if err != nil {
		t.Fatal(err)
	}
	if settings.UpdateChannel != "beta" {
		t.Fatalf("update channel = %q, want beta", settings.UpdateChannel)
	}
	if !settings.Proxy.Enabled || settings.Proxy.Autostart || settings.Proxy.BindHost != "127.0.0.1" ||
		settings.Proxy.HTTPPort != "9081" || settings.Proxy.TLSKeyPassphrase != "legacy-passphrase" {
		t.Fatalf("proxy settings were not migrated: %#v", settings.Proxy)
	}
	migrated, err := os.ReadFile(opts.envFile)
	if err != nil {
		t.Fatal(err)
	}
	for _, retired := range []string{
		"OMLORIX_UPDATE_CHANNEL",
		"OMLORIX_BACKEND_IMAGE_REPOSITORY",
		"OMLORIX_FRONTEND_IMAGE_REPOSITORY",
		"FILE_SCANNER_COMMAND",
		"OMLORIX_GITHUB_TOKEN",
		"OMLORIX_LAUNCHER_PROXY_ENABLED",
		"OMLORIX_LAUNCHER_PROXY_AUTOSTART",
		"OMLORIX_LAUNCHER_PROXY_BIND",
		"OMLORIX_LAUNCHER_PROXY_HTTP_PORT",
		"OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE",
	} {
		if strings.Contains(string(migrated), retired) {
			t.Fatalf("migrated environment still contains %s", retired)
		}
	}
	backups, err := filepath.Glob(filepath.Join(home, ".env.backups", "*.bak"))
	if err != nil {
		t.Fatal(err)
	}
	if len(backups) != 1 {
		t.Fatalf("migration backups = %d, want 1", len(backups))
	}
	backupRaw, err := os.ReadFile(backups[0])
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(backupRaw), "OMLORIX_GITHUB_TOKEN") {
		t.Fatalf("migration backup retained the retired value:\n%s", backupRaw)
	}
}

func TestExistingServerSettingsWinDuringLegacyMigration(t *testing.T) {
	home := t.TempDir()
	opts := options{home: home, envFile: filepath.Join(home, ".env")}
	if err := os.WriteFile(opts.envFile, []byte("OMLORIX_UPDATE_CHANNEL=beta\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := writeServerSettings(opts, serverSettings{UpdateChannel: "stable"}); err != nil {
		t.Fatal(err)
	}

	if err := migrateLegacyServerSettings(opts); err != nil {
		t.Fatal(err)
	}

	settings, err := readServerSettings(opts)
	if err != nil {
		t.Fatal(err)
	}
	if settings.UpdateChannel != "stable" {
		t.Fatalf("update channel = %q, want existing stable preference", settings.UpdateChannel)
	}
}

func TestConfigureProxyPersistsHostStateOutsideEnvironment(t *testing.T) {
	home := t.TempDir()
	opts := options{home: home, envFile: filepath.Join(home, ".env")}
	if err := os.WriteFile(opts.envFile, []byte("FRONTEND_HTTP_HOST_BIND=0.0.0.0\nFRONTEND_HTTP_HOST_PORT=8080\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := writeServerSettings(opts, defaultServerSettings()); err != nil {
		t.Fatal(err)
	}
	if err := writeManagedProxyEnabled(opts, true); err != nil {
		t.Fatal(err)
	}
	if err := configureProxySettings(opts, []string{
		"autostart=false",
		"bind=127.0.0.1",
		"public-hostname=chat.example.test",
		"http-port=9081",
	}); err != nil {
		t.Fatal(err)
	}

	settings, err := readServerSettings(opts)
	if err != nil {
		t.Fatal(err)
	}
	if !settings.Proxy.Enabled || settings.Proxy.Autostart || settings.Proxy.HTTPPort != "9081" ||
		settings.Proxy.PublicHostname != "chat.example.test" {
		t.Fatalf("unexpected proxy settings: %#v", settings.Proxy)
	}
	env, raw := readEnv(opts.envFile)
	for _, key := range managedProxySettingsEnvKeys {
		if _, present := env[key]; present {
			t.Fatalf("host proxy setting %s leaked into .env:\n%s", key, raw)
		}
	}
	if env["FRONTEND_HTTP_HOST_BIND"] != "127.0.0.1" || len(env["OMLORIX_LAUNCHER_PROXY_SECRET"]) != 64 {
		t.Fatalf("container proxy boundary was not prepared: %#v", env)
	}
}

func TestConfigureProxyRequiresDedicatedEnabledLifecycleCommands(t *testing.T) {
	home := t.TempDir()
	opts := options{home: home, envFile: filepath.Join(home, ".env")}
	if err := os.WriteFile(opts.envFile, []byte("FRONTEND_HTTP_HOST_BIND=0.0.0.0\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	err := configureProxySettings(opts, []string{"enabled=true"})
	if err == nil || !strings.Contains(err.Error(), "proxy enable") {
		t.Fatalf("direct enabled-state configuration was not rejected: %v", err)
	}
	settings, readErr := readServerSettings(opts)
	if readErr != nil {
		t.Fatal(readErr)
	}
	if settings.Proxy.Enabled {
		t.Fatal("rejected configuration changed the enabled state")
	}
	env, _ := readEnv(opts.envFile)
	if env["FRONTEND_HTTP_HOST_BIND"] != "0.0.0.0" {
		t.Fatalf("rejected configuration changed the frontend bind: %#v", env)
	}
}
