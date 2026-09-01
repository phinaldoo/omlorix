package main

import (
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCodeExecutionUsesCanonicalGatewayHealthRoutes(t *testing.T) {
	if codeExecutionHealthPath != "/health" {
		t.Fatalf("health path = %q", codeExecutionHealthPath)
	}
	if codeExecutionHealthDetailsPath != "/health/details" {
		t.Fatalf("health details path = %q", codeExecutionHealthDetailsPath)
	}

	previousTransport := http.DefaultTransport
	t.Cleanup(func() { http.DefaultTransport = previousTransport })
	http.DefaultTransport = roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.Path != codeExecutionHealthDetailsPath {
			t.Fatalf("request path = %q", request.URL.Path)
		}
		if request.Header.Get("Authorization") != "Bearer private-key" {
			t.Fatalf("Authorization = %q", request.Header.Get("Authorization"))
		}
		return &http.Response{
			StatusCode: http.StatusOK,
			Header:     make(http.Header),
			Body:       io.NopCloser(strings.NewReader(`{"status":"healthy"}`)),
			Request:    request,
		}, nil
	})

	response := requestCodeExecutionHealth(8123, "private-key", codeExecutionHealthDetailsPath)
	if !response.OK || jsonMapString(response.Data, "status") != "healthy" {
		t.Fatalf("health response = %+v", response)
	}
}

func TestRequestCodeExecutionHealthReadsDetailedMetrics(t *testing.T) {
	data := decodeCodeExecutionHealth(strings.NewReader(`{"status":"healthy","sandbox_image_available":true,"sandbox_runtime":"runsc","metrics":{"active_executions":2,"active_renders":1}}`))
	if jsonMapString(data, "status") != "healthy" {
		t.Fatalf("health response = %+v", data)
	}
	metrics, _ := data["metrics"].(map[string]any)
	if jsonMapInt(metrics, "active_executions") != 2 || !jsonMapBool(data, "sandbox_image_available") {
		t.Fatalf("health details = %+v", data)
	}
}

func TestSerializeCodeExecutionEnvUsesPrivateSecureDefaults(t *testing.T) {
	instance := codeExecutionInstance{
		ID: "analysis-a1b2c3d4", Name: "Analysis", Version: "1.2.3", Port: 8123,
		Memory: "1g", MaxConcurrent: 7, SessionTimeout: 900, NetworkAccess: false, AllowPip: false,
	}
	raw := serializeCodeExecutionEnv(instance, "secret-token")
	required := []string{
		"GATEWAY_HOST_BIND=127.0.0.1",
		"GATEWAY_DOCKER_HOST=tcp://docker-proxy:2375",
		"API_KEYS=launcher:secret-token",
		"REDIS_HEALTH_CHECK_INTERVAL=30",
		"SANDBOX_NETWORK_MODE=none",
		"SANDBOX_READ_ONLY_ROOTFS=true",
		"SANDBOX_TMP_ROOT_SIZE=512m",
		"ALLOW_SANDBOX_ENV_INJECTION=false",
		"MAX_EXECUTIONS_PER_SESSION=100",
		"RENDER_MAX_OUTPUT_BYTES=220000000",
		"CODE_EXECUTION_NETWORK_ALIAS=codeexec-analysis-a1b2c3d4",
	}
	for _, value := range required {
		if !strings.Contains(raw, value) {
			t.Fatalf("secure Code Execution env is missing %q:\n%s", value, raw)
		}
	}
}

func TestCodeExecutionRegistryRoundTrip(t *testing.T) {
	opts := options{home: t.TempDir()}
	registry := codeExecutionRegistry{
		Instances: []codeExecutionInstance{{ID: "worker-1234abcd", Name: "Worker", Version: "1.0.0", Port: 8000}},
	}
	if err := writeCodeExecutionRegistry(opts, registry); err != nil {
		t.Fatal(err)
	}
	loaded, err := readCodeExecutionRegistry(opts)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.Version != codeExecutionRegistryVersion || len(loaded.Instances) != 1 || loaded.Instances[0].ID != "worker-1234abcd" {
		t.Fatalf("registry round trip = %+v", loaded)
	}
	info, err := os.Stat(filepath.Join(opts.home, "code-execution", "instances.json"))
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm()&0o077 != 0 {
		t.Fatalf("registry permissions = %o, want private", info.Mode().Perm())
	}
}

func TestCodeExecutionRuntimeBundleRefreshesExistingInstances(t *testing.T) {
	home := t.TempDir()
	opts := options{home: home}
	bundle := filepath.Join(home, "electron", "code-execution")
	instance := filepath.Join(home, "code-execution", "instances", "worker-ab12cd34")
	if err := os.MkdirAll(bundle, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(instance, 0o700); err != nil {
		t.Fatal(err)
	}
	for _, file := range codeExecutionInstanceBundleFiles {
		if err := os.WriteFile(filepath.Join(bundle, file), []byte("current "+file+"\n"), 0o644); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(instance, file), []byte("stale\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	secretPath := filepath.Join(instance, ".env")
	if err := os.WriteFile(secretPath, []byte("API_KEYS=launcher:keep-me\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := syncCodeExecutionInstanceBundle(opts, instance); err != nil {
		t.Fatal(err)
	}
	for _, file := range codeExecutionInstanceBundleFiles {
		raw, err := os.ReadFile(filepath.Join(instance, file))
		if err != nil || string(raw) != "current "+file+"\n" {
			t.Fatalf("runtime file %s = %q, err=%v", file, raw, err)
		}
	}
	secret, err := os.ReadFile(secretPath)
	if err != nil || string(secret) != "API_KEYS=launcher:keep-me\n" {
		t.Fatalf("instance environment changed: %q, err=%v", secret, err)
	}
}

func TestSlugifyProducesDockerSafeIdentifier(t *testing.T) {
	if got := slugify("  Résumé / Python Runner!  "); got != "r-sum-python-runner" {
		t.Fatalf("slugify() = %q", got)
	}
	if got := slugify("!!!"); got != "execution" {
		t.Fatalf("empty slug fallback = %q", got)
	}
}

func TestParseCodeExecutionVersionsKeepsPublishedStableReleases(t *testing.T) {
	releases := []codeExecutionRelease{
		{TagName: "v2.0.0"},
		{TagName: "2.0.0"},
		{TagName: "v2.1.0-beta.1", Prerelease: true},
		{TagName: "v1.9.0"},
		{TagName: "invalid"},
		{TagName: "v1.8.0", Draft: true},
	}
	versions := parseCodeExecutionVersions(releases)
	if strings.Join(versions, ",") != "2.0.0,1.9.0" {
		t.Fatalf("versions = %v", versions)
	}
}

func TestCodeExecutionUpdateRestoresPreviousVersionAfterStartFailure(t *testing.T) {
	home := t.TempDir()
	opts := options{home: home, timeout: readinessTimeout}
	instance := codeExecutionInstance{
		ID: "worker-1234abcd", Name: "Worker", Version: "1.0.0", Port: 8123,
		Memory: "512m", MaxConcurrent: 10, SessionTimeout: 1200, ImageSource: "release",
	}
	if err := writeCodeExecutionRegistry(opts, codeExecutionRegistry{Instances: []codeExecutionInstance{instance}}); err != nil {
		t.Fatal(err)
	}
	instanceHome, err := codeExecutionInstanceHome(opts, instance.ID)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(instanceHome, 0o700); err != nil {
		t.Fatal(err)
	}
	envPath := filepath.Join(instanceHome, ".env")
	if err := os.WriteFile(envPath, []byte(serializeCodeExecutionEnv(instance, "private-key")), 0o600); err != nil {
		t.Fatal(err)
	}

	startCalls := 0
	fakeStart := func(callOpts options, id string) error {
		startCalls++
		_, current, readErr := registeredCodeExecution(callOpts, id)
		if readErr != nil {
			return readErr
		}
		if startCalls == 1 {
			if current.Version != "2.0.0" {
				t.Fatalf("first start used version %q", current.Version)
			}
			return errors.New("image pull failed")
		}
		if current.Version != "1.0.0" {
			t.Fatalf("rollback start used version %q", current.Version)
		}
		return nil
	}
	err = updateCodeExecutionWith(
		opts,
		instance.ID,
		func() (string, error) { return "2.0.0", nil },
		fakeStart,
	)
	if err == nil || !strings.Contains(err.Error(), "restored to 1.0.0") {
		t.Fatalf("update rollback result = %v", err)
	}
	if startCalls != 2 {
		t.Fatalf("start calls = %d, want update and rollback starts", startCalls)
	}
	_, restored, readErr := registeredCodeExecution(opts, instance.ID)
	if readErr != nil {
		t.Fatal(readErr)
	}
	if restored.Version != "1.0.0" {
		t.Fatalf("registry version after rollback = %q", restored.Version)
	}
	env, _ := readEnv(envPath)
	if env["CODE_EXECUTION_VERSION"] != "1.0.0" {
		t.Fatalf("environment version after rollback = %q", env["CODE_EXECUTION_VERSION"])
	}
}
