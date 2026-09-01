package main

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
)

func TestPackagedServerFileContractReferencesExistingSources(t *testing.T) {
	projectRoot := filepath.Clean(filepath.Join("..", ".."))
	for _, relative := range serverFiles {
		if _, err := os.Stat(filepath.Join(projectRoot, filepath.FromSlash(relative))); err != nil {
			t.Fatalf("server file %q is missing from the source tree: %v", relative, err)
		}
	}
}

func TestEnsureServerHomeCopiesGrafanaProvisioningFiles(t *testing.T) {
	projectRoot := filepath.Clean(filepath.Join("..", ".."))
	home := t.TempDir()
	opts := options{
		home:       home,
		envFile:    filepath.Join(home, ".env"),
		sourceRoot: projectRoot,
	}

	if err := ensureServerHome(opts); err != nil {
		t.Fatalf("ensureServerHome() failed: %v", err)
	}
	for _, relative := range grafanaProvisioningFiles {
		want, err := os.ReadFile(filepath.Join(projectRoot, filepath.FromSlash(relative)))
		if err != nil {
			t.Fatal(err)
		}
		got, err := os.ReadFile(filepath.Join(home, filepath.FromSlash(relative)))
		if err != nil {
			t.Fatalf("fresh home is missing %q: %v", relative, err)
		}
		if !reflect.DeepEqual(got, want) {
			t.Fatalf("fresh-home file %q does not match its packaged source", relative)
		}
	}
}

func TestFindSourceRootRejectsServerHomeWorkingDirectory(t *testing.T) {
	home := t.TempDir()
	previousWorkingDirectory, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(home); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.Chdir(previousWorkingDirectory); err != nil {
			t.Errorf("restore working directory: %v", err)
		}
	})

	examplePath := filepath.Join(home, ".env.example")
	if err := os.WriteFile(examplePath, []byte("stale env example\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	opts := options{home: home, envFile: filepath.Join(home, ".env")}
	if got := findSourceRoot(opts); got != "" {
		t.Fatalf("findSourceRoot() = %q, want embedded-asset selection", got)
	}

	homeAlias := home + "-alias"
	if err := os.Symlink(home, homeAlias); err == nil {
		t.Cleanup(func() { _ = os.Remove(homeAlias) })
		opts.sourceRoot = homeAlias
		if got := findSourceRoot(opts); got != "" {
			t.Fatalf("findSourceRoot() accepted server-home alias %q", got)
		}
	}

	trustedSource := t.TempDir()
	if err := os.WriteFile(filepath.Join(trustedSource, ".env.example"), []byte("trusted source\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	opts.sourceRoot = trustedSource
	if got := findSourceRoot(opts); got != trustedSource {
		t.Fatalf("findSourceRoot() = %q, want distinct source %q", got, trustedSource)
	}
}

func TestCopyFileOntoItselfPreservesCompleteContents(t *testing.T) {
	file := filepath.Join(t.TempDir(), "deployment-asset.yml")
	want := []byte("services:\n  frontend:\n    image: example\n")
	if err := os.WriteFile(file, want, 0o644); err != nil {
		t.Fatal(err)
	}

	if err := copyFile(file, file); err != nil {
		t.Fatalf("copyFile() failed: %v", err)
	}
	got, err := os.ReadFile(file)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("same-path copy changed asset: got %q, want %q", got, want)
	}
}

func TestDefaultLocalRedisURLUsesComposeService(t *testing.T) {
	got := defaultLocalRedisURL(map[string]string{"DEV_REDIS_HOST_PORT": "6385"}, "operator#redis:secret@word")
	want := "redis://:operator%23redis%3Asecret%40word@redis:6379/0"
	if got != want {
		t.Fatalf("defaultLocalRedisURL() = %q, want %q", got, want)
	}
}

func TestLogTimeBoundMatchesLauncherContract(t *testing.T) {
	valid := []string{
		"", " 5m ", "1h30m", "1.5h", "1us", "1µs", "1μs", "+5m", "-5m", ".5h", "1.h", "0s",
		"0", "+0", "-0", "1234567890", "123.123456789", "9223372036854775807",
		"2026-08-23", "2024-02-29", "0100-01-01", "2026-08-23T10",
		"2026-08-23T10:30", "2026-08-23T10:30:00.123456789Z",
		"2026-08-23Z", "2026-08-23+02:00",
		"2026-08-23T10:30:00+23:59", strings.Repeat("1s", 64),
	}
	for _, value := range valid {
		normalized, err := normalizeLogTimeBound(value)
		if err != nil {
			t.Errorf("normalizeLogTimeBound(%q) rejected Launcher-compatible input: %v", value, err)
		}
		if normalized != strings.TrimSpace(value) {
			t.Errorf("normalizeLogTimeBound(%q) = %q, want trimmed input", value, normalized)
		}
	}

	invalid := []string{
		"last Tuesday", "1Μs", "5M", "1d", "1ſ", "9223372036854775808ns",
		"123.1234567890", "9223372036854775808", "9999999999999999999", "2023-02-29", "0099-01-01",
		"2026-08-23 10:30", "2026-08-23+02", "2026-08-23+0200",
		"2026-08-23T23:59:60Z",
		"2026-08-23T24:00:00Z", "2026-08-23T23:60:00Z",
		"2026-08-23T10:30:00+24:00", "2026-08-23t10:30:00z",
		strings.Repeat("1s", 63) + "1ms",
	}
	for _, value := range invalid {
		if _, err := normalizeLogTimeBound(value); err == nil {
			t.Errorf("normalizeLogTimeBound(%q) accepted Launcher-incompatible input", value)
		}
	}
}

func TestParseOptionsRejectsInvalidLogTimeBound(t *testing.T) {
	if _, err := parseOptions([]string{"--since", "last Tuesday", "logs"}); err == nil {
		t.Fatal("parseOptions() accepted an invalid log time bound")
	} else if !strings.Contains(err.Error(), "valid log time bound") {
		t.Fatalf("parseOptions() error = %q, want log time guidance", err)
	}
}

func TestPrintMigrationFailureLogsUsesBoundedComposeLogs(t *testing.T) {
	tmpDir := t.TempDir()
	opts := options{home: tmpDir, envFile: filepath.Join(tmpDir, ".env")}
	var output strings.Builder
	var receivedName string
	var receivedArgs []string
	var receivedDir string

	printMigrationFailureLogs(
		opts,
		&output,
		func(name string, args []string, cwd string) (string, error) {
			receivedName = name
			receivedArgs = append([]string(nil), args...)
			receivedDir = cwd
			return "migrate-1 | database migration failed\n", nil
		},
	)

	if receivedName != dockerExecutable() {
		t.Fatalf("capture command = %q, want %q", receivedName, dockerExecutable())
	}
	if receivedDir != tmpDir {
		t.Fatalf("capture cwd = %q, want %q", receivedDir, tmpDir)
	}
	wantSuffix := []string{"logs", "--tail", "120", "--no-color", "migrate"}
	if len(receivedArgs) < len(wantSuffix) || !reflect.DeepEqual(receivedArgs[len(receivedArgs)-len(wantSuffix):], wantSuffix) {
		t.Fatalf("capture args = %q, want suffix %q", receivedArgs, wantSuffix)
	}
	if got := output.String(); !strings.Contains(got, "Recent migration logs") || !strings.Contains(got, "database migration failed") {
		t.Fatalf("diagnostic output = %q", got)
	}
}

func TestEnsureGeneratedEnvSynchronizesRotatedBundledRedisPassword(t *testing.T) {
	tmpDir := t.TempDir()
	envFile := filepath.Join(tmpDir, ".env")
	raw := strings.Join([]string{
		"REDIS_ENABLED=true",
		"OMLORIX_USE_BUNDLED_REDIS=true",
		"REDIS_PASSWORD=operator#redis:secret@word",
		"REDIS_URL=redis://:old-password@redis:6379/0",
	}, "\n") + "\n"
	if err := os.WriteFile(envFile, []byte(raw), 0o600); err != nil {
		t.Fatalf("setup temp .env failed: %v", err)
	}

	if err := ensureGeneratedEnv(options{home: tmpDir, envFile: envFile}); err != nil {
		t.Fatalf("ensureGeneratedEnv() failed: %v", err)
	}
	env, _ := readEnv(envFile)
	decodedJWTSecret, decodeErr := base64.StdEncoding.DecodeString(env["JWT_SECRET_KEY"])
	if decodeErr != nil || len(decodedJWTSecret) != jwtSecretMinBytes {
		t.Fatalf("generated JWT_SECRET_KEY decoded length = %d, error = %v", len(decodedJWTSecret), decodeErr)
	}
	if got, want := env["REDIS_PASSWORD"], "operator#redis:secret@word"; got != want {
		t.Fatalf("REDIS_PASSWORD = %q, want %q", got, want)
	}
	if got, want := env["REDIS_URL"], "redis://:operator%23redis%3Asecret%40word@redis:6379/0"; got != want {
		t.Fatalf("REDIS_URL = %q, want %q", got, want)
	}
}

func TestEnsureGeneratedEnvPreservesExternalRedisURL(t *testing.T) {
	tmpDir := t.TempDir()
	envFile := filepath.Join(tmpDir, ".env")
	raw := strings.Join([]string{
		"REDIS_ENABLED=true",
		"OMLORIX_USE_BUNDLED_REDIS=false",
		"REDIS_PASSWORD=unused-bundled-password",
		"REDIS_URL=rediss://:external-secret@redis.example.com:6380/0",
	}, "\n") + "\n"
	if err := os.WriteFile(envFile, []byte(raw), 0o600); err != nil {
		t.Fatalf("setup temp .env failed: %v", err)
	}

	if err := ensureGeneratedEnv(options{home: tmpDir, envFile: envFile}); err != nil {
		t.Fatalf("ensureGeneratedEnv() failed: %v", err)
	}
	env, _ := readEnv(envFile)
	if got, want := env["REDIS_URL"], "rediss://:external-secret@redis.example.com:6380/0"; got != want {
		t.Fatalf("REDIS_URL = %q, want %q", got, want)
	}
}

func TestEnsureGeneratedEnvRepairsLegacyPgBouncerRouting(t *testing.T) {
	tmpDir := t.TempDir()
	envFile := filepath.Join(tmpDir, ".env")
	raw := strings.Join([]string{
		"OMLORIX_USE_BUNDLED_DB=true",
		"OMLORIX_USE_PGBOUNCER=true",
		"DATABASE_URL=postgresql://external.example/other",
		"DATABASE_HOST_OVERRIDE=postgres",
		"DATABASE_PORT_OVERRIDE=7432",
	}, "\n") + "\n"
	if err := os.WriteFile(envFile, []byte(raw), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := ensureGeneratedEnv(options{home: tmpDir, envFile: envFile}); err != nil {
		t.Fatalf("ensureGeneratedEnv() failed: %v", err)
	}
	env, _ := readEnv(envFile)
	if env["DATABASE_URL"] != "" || env["DATABASE_HOST_OVERRIDE"] != "pgbouncer" || env["DATABASE_PORT_OVERRIDE"] != "5432" {
		t.Fatalf("legacy PgBouncer routing was not repaired: %#v", env)
	}
	if env["DATABASE_MIGRATION_HOST_OVERRIDE"] != "postgres" || env["DATABASE_MIGRATION_PORT_OVERRIDE"] != "5432" {
		t.Fatalf("migration route was not kept direct: %#v", env)
	}
}

func TestShouldResetGrafanaAdminUserRejectsDefaultAdmin(t *testing.T) {
	cases := map[string]bool{
		"":             true,
		"CHANGE_ME":    true,
		"admin":        true,
		"omlorix-admin": false,
	}

	for input, want := range cases {
		if got := shouldResetGrafanaAdminUser(input); got != want {
			t.Fatalf("shouldResetGrafanaAdminUser(%q) = %v, want %v", input, got, want)
		}
	}
}

func TestValidateSensitiveEnvRejectsPlaceholderRedisURL(t *testing.T) {
	opts := options{envFile: ".env"}
	env := map[string]string{
		"MODE":              "production",
		"JWT_SECRET_KEY":    strings.Repeat("j", 64),
		"ENCRYPTION_KEY":    "fernet-key",
		"DATABASE_PASSWORD": "db-secret",
		"REDIS_PASSWORD":    "redis-secret",
		"REDIS_URL":         "redis://:CHANGE_ME@localhost:6379/0",
	}
	toggles := envToggles{useBundledDB: true, useBundledRedis: true}

	err := validateSensitiveEnv(opts, env, toggles)
	if err == nil {
		t.Fatal("validateSensitiveEnv() returned nil, want placeholder REDIS_URL error")
	}
}

func TestReadEnvTogglesDefaults(t *testing.T) {
	tmpDir := t.TempDir()
	envFile := filepath.Join(tmpDir, ".env")
	if err := os.WriteFile(envFile, []byte(""), 0o600); err != nil {
		t.Fatalf("setup temp .env failed: %v", err)
	}
	opts := options{envFile: envFile}
	toggles := readEnvToggles(opts)
	if !toggles.useBundledDB {
		t.Fatal("readEnvToggles() useBundledDB = false, want true")
	}
	if !toggles.useBundledRedis {
		t.Fatal("readEnvToggles() useBundledRedis = false, want true")
	}
	if toggles.usePgbouncer {
		t.Fatal("readEnvToggles() usePgbouncer = true, want false")
	}
	if toggles.useBundledStorage {
		t.Fatal("readEnvToggles() useBundledStorage = true, want false")
	}
	if toggles.observability {
		t.Fatal("readEnvToggles() observability = true, want false")
	}
}

func TestEnvTruthyExplicitValues(t *testing.T) {
	cases := []struct {
		value    string
		expected bool
	}{
		{"1", true},
		{"true", true},
		{"yes", true},
		{"on", true},
		{"TRUE", true},
		{"0", false},
		{"false", false},
		{"no", false},
		{"off", false},
		{"FALSE", false},
		{"", false},
		{"maybe", false},
	}
	for _, c := range cases {
		got := envTruthy(c.value, false)
		if got != c.expected {
			t.Fatalf("envTruthy(%q, false) = %v, want %v", c.value, got, c.expected)
		}
	}
}

func TestBuildComposeProfiles(t *testing.T) {
	cases := []struct {
		name     string
		toggles  envToggles
		expected []string
	}{
		{"all-off", envToggles{}, nil},
		{"bundled-db-only", envToggles{useBundledDB: true}, []string{"bundled-db"}},
		{"redis-off", envToggles{useBundledDB: true, useBundledRedis: true}, []string{"bundled-db"}},
		{"bundled-db+redis", envToggles{useBundledDB: true, redisEnabled: true, useBundledRedis: true}, []string{"bundled-db", "redis-enabled", "bundled-redis"}},
		{"single-server-plus", envToggles{useBundledDB: true, redisEnabled: true, useBundledRedis: true, usePgbouncer: true, useBundledStorage: true}, []string{"bundled-db", "redis-enabled", "bundled-redis", "pgbouncer", "bundled-storage"}},
	}
	for _, c := range cases {
		got := buildComposeProfiles(c.toggles)
		if !reflect.DeepEqual(got, c.expected) {
			t.Fatalf("buildComposeProfiles(%s) = %v, want %v", c.name, got, c.expected)
		}
	}
}

func TestComposeFileListDefault(t *testing.T) {
	tmpDir := t.TempDir()
	envFile := filepath.Join(tmpDir, ".env")
	if err := os.WriteFile(envFile, []byte(""), 0o600); err != nil {
		t.Fatalf("setup temp .env failed: %v", err)
	}
	opts := options{envFile: envFile}
	files := composeFileList(opts)
	if !contains(files, "docker-compose.server.yml") {
		t.Fatalf("composeFileList() missing docker-compose.server.yml: %v", files)
	}
	if !contains(files, "docker-compose.frontend-port.yml") {
		t.Fatalf("composeFileList() missing docker-compose.frontend-port.yml: %v", files)
	}
}

func TestComposeFileListIncludesObservability(t *testing.T) {
	tmpDir := t.TempDir()
	envFile := filepath.Join(tmpDir, ".env")
	if err := os.WriteFile(envFile, []byte("OTEL_ENABLED=true\n"), 0o600); err != nil {
		t.Fatalf("setup temp .env failed: %v", err)
	}
	opts := options{envFile: envFile}
	files := composeFileList(opts)
	if !contains(files, "docker-compose.observability.yml") {
		t.Fatalf("composeFileList() missing docker-compose.observability.yml: %v", files)
	}
}

func TestComposeFileListEnablesHostMetricsOnlyOnLinux(t *testing.T) {
	tmpDir := t.TempDir()
	envFile := filepath.Join(tmpDir, ".env")
	if err := os.WriteFile(envFile, []byte("OTEL_ENABLED=true\n"), 0o600); err != nil {
		t.Fatalf("setup temp .env failed: %v", err)
	}
	opts := options{envFile: envFile}
	linuxFiles := composeFileListForPlatform(opts, "linux")
	if !contains(linuxFiles, "docker-compose.observability-linux.yml") {
		t.Fatalf("Linux Compose files omit the host-metrics overlay: %v", linuxFiles)
	}
	for _, goos := range []string{"darwin", "windows"} {
		files := composeFileListForPlatform(opts, goos)
		if contains(files, "docker-compose.observability-linux.yml") {
			t.Fatalf("%s Compose files include the Linux host-metrics overlay: %v", goos, files)
		}
	}
}

func TestMissingRequiredFilesIncludesGrafanaProvisioning(t *testing.T) {
	home := t.TempDir()
	envFile := filepath.Join(home, ".env")
	if err := os.WriteFile(envFile, []byte("OTEL_ENABLED=true\n"), 0o600); err != nil {
		t.Fatalf("setup temp .env failed: %v", err)
	}
	opts := options{home: home, envFile: envFile}
	for _, relative := range composeFileList(opts) {
		path := filepath.Join(home, filepath.FromSlash(relative))
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, nil, 0o644); err != nil {
			t.Fatal(err)
		}
	}

	missing := missingRequiredFiles(opts)
	want := append([]string{}, grafanaProvisioningFiles...)
	sort.Strings(want)
	if !reflect.DeepEqual(missing, want) {
		t.Fatalf("missingRequiredFiles() = %v, want %v", missing, want)
	}
}

func TestDoctorFailsForMissingGrafanaProvisioning(t *testing.T) {
	failures := doctorFailures(serverStatus{
		Docker: dockerStatus{
			Installed: true,
			Running:   true,
			Compose:   true,
		},
		Configuration: "valid",
		MissingFiles:  []string{grafanaProvisioningFiles[0]},
	})
	if !reflect.DeepEqual(failures, []string{"required Compose files are missing"}) {
		t.Fatalf("doctorFailures() = %v", failures)
	}
}

func TestComposeFileListIncludesDevPortsForBundledDevMode(t *testing.T) {
	tmpDir := t.TempDir()
	envFile := filepath.Join(tmpDir, ".env")
	if err := os.WriteFile(envFile, []byte("MODE=dev\nOMLORIX_USE_BUNDLED_DB=true\nOMLORIX_USE_BUNDLED_REDIS=true\n"), 0o600); err != nil {
		t.Fatalf("setup temp .env failed: %v", err)
	}
	opts := options{envFile: envFile}
	files := composeFileList(opts)
	if !contains(files, "docker-compose.dev-ports.yml") {
		t.Fatalf("composeFileList() missing docker-compose.dev-ports.yml: %v", files)
	}
}

func TestComposeFileListSkipsDevPortsForManagedCloudDevMode(t *testing.T) {
	tmpDir := t.TempDir()
	envFile := filepath.Join(tmpDir, ".env")
	if err := os.WriteFile(envFile, []byte("MODE=dev\nOMLORIX_USE_BUNDLED_DB=false\nOMLORIX_USE_BUNDLED_REDIS=false\n"), 0o600); err != nil {
		t.Fatalf("setup temp .env failed: %v", err)
	}
	opts := options{envFile: envFile}
	files := composeFileList(opts)
	if contains(files, "docker-compose.dev-ports.yml") {
		t.Fatalf("composeFileList() should skip docker-compose.dev-ports.yml for managed cloud: %v", files)
	}
}

func contains(slice []string, item string) bool {
	for _, s := range slice {
		if s == item {
			return true
		}
	}
	return false
}

func TestNormalizeUpdateChannel(t *testing.T) {
	if got := normalizeUpdateChannel("BETA"); got != "beta" {
		t.Fatalf("normalizeUpdateChannel(BETA) = %q, want beta", got)
	}
	if got := normalizeUpdateChannel("nightly"); got != "stable" {
		t.Fatalf("normalizeUpdateChannel(nightly) = %q, want stable", got)
	}
}

func TestValidateUpdateChannelRejectsTypos(t *testing.T) {
	if _, err := validateUpdateChannel("nightly"); err == nil {
		t.Fatal("invalid update channel was silently accepted")
	}
	if got, err := validateUpdateChannel(" BETA "); err != nil || got != "beta" {
		t.Fatalf("valid beta channel = %q, %v", got, err)
	}
}

func TestTopLevelVersionFlagDoesNotConsumeAValue(t *testing.T) {
	opts, err := parseOptions([]string{"--version"})
	if err != nil || opts.command != "version" {
		t.Fatalf("top-level --version = %+v, %v", opts, err)
	}
	update, err := parseOptions([]string{"update", "--version", "1.2.3"})
	if err != nil || update.version != "1.2.3" {
		t.Fatalf("update --version = %+v, %v", update, err)
	}
}

func TestComposeProjectNameIsStableAndHomeSpecific(t *testing.T) {
	home := filepath.Join(t.TempDir(), "server")
	if composeProjectNameForHome(home) != composeProjectNameForHome(home) {
		t.Fatal("project name was not stable")
	}
	if composeProjectNameForHome(home) == composeProjectNameForHome(home+"-other") {
		t.Fatal("different homes received the same project name")
	}
}
