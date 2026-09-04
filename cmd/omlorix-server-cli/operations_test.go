package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestParseOptionsSupportsCommandArgumentsAndAutomationFlags(t *testing.T) {
	opts, err := parseOptions([]string{
		"--home", "/tmp/omlorix-test",
		"--json",
		"--timeout", "90s",
		"config", "set", "REDIS_ENABLED", "false",
	})
	if err != nil {
		t.Fatalf("parseOptions() error = %v", err)
	}
	if opts.command != "config" {
		t.Fatalf("command = %q, want config", opts.command)
	}
	if !reflect.DeepEqual(opts.arguments, []string{"set", "REDIS_ENABLED", "false"}) {
		t.Fatalf("arguments = %v", opts.arguments)
	}
	if !opts.jsonOutput || opts.timeout != 90*time.Second {
		t.Fatalf("automation flags were not parsed: %+v", opts)
	}
}

func TestParseOptionsEnforcesSharedLogLineLimits(t *testing.T) {
	limits := serverManagement.Logs
	opts, err := parseOptions([]string{"logs", "--lines", fmt.Sprint(limits.MaximumLines)})
	if err != nil {
		t.Fatalf("parseOptions() rejected maximum line count: %v", err)
	}
	if opts.lines != limits.MaximumLines {
		t.Fatalf("lines = %d, want %d", opts.lines, limits.MaximumLines)
	}

	for _, lines := range []int{limits.MinimumLines - 1, limits.MaximumLines + 1} {
		if _, err := parseOptions([]string{"logs", "--lines", fmt.Sprint(lines)}); err == nil {
			t.Fatalf("parseOptions() accepted out-of-range line count %d", lines)
		} else if !strings.Contains(err.Error(), fmt.Sprintf("%d to %d", limits.MinimumLines, limits.MaximumLines)) {
			t.Fatalf("out-of-range error = %q, want shared bounds", err)
		}
	}
}

func TestNormalizeLogLineCountCapsInternalRequests(t *testing.T) {
	limits := serverManagement.Logs
	got, err := normalizeLogLineCount(limits.MaximumLines + 1)
	if err != nil {
		t.Fatalf("normalizeLogLineCount() error = %v", err)
	}
	if got != limits.MaximumLines {
		t.Fatalf("normalizeLogLineCount() = %d, want %d", got, limits.MaximumLines)
	}
}

func TestParseOptionsSupportsExplicitBackupDownloadPath(t *testing.T) {
	opts, err := parseOptions([]string{
		"backup", "download", "job-1", "--output", "./backup.tar.zst", "--json",
	})
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(opts.arguments, []string{"download", "job-1"}) || opts.output != "./backup.tar.zst" || !opts.jsonOutput {
		t.Fatalf("backup download flags parsed incorrectly: %+v", opts)
	}
}

func TestBackupDownloadCommitsCompleteArchiveWithoutOverwrite(t *testing.T) {
	target := filepath.Join(t.TempDir(), "backup.tar.zst")
	opts := options{home: t.TempDir()}
	want := []byte("complete backup archive")
	result, err := downloadBackupArtifact(
		opts,
		"job-1",
		target,
		func(args []string, cwd string, writer io.Writer) (string, error) {
			if !strings.Contains(strings.Join(args, " "), "app.backups.cli download job-1") {
				t.Fatalf("unexpected backend command: %v", args)
			}
			_, writeErr := writer.Write(want)
			return "", writeErr
		},
	)
	if err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(target)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, want) || !result.OK || result.JobID != "job-1" || result.Bytes != int64(len(want)) {
		t.Fatalf("unexpected download result=%+v contents=%q", result, got)
	}
	if info, statErr := os.Stat(target); statErr != nil || info.Mode().Perm() != 0o600 {
		t.Fatalf("download permissions are not private: info=%v error=%v", info, statErr)
	}

	_, err = downloadBackupArtifact(opts, "job-1", target, func(_ []string, _ string, _ io.Writer) (string, error) {
		t.Fatal("an existing destination must be rejected before streaming")
		return "", nil
	})
	if structuredCLIError(err).Code != "destination_exists" {
		t.Fatalf("collision error = %v", err)
	}
	if got, readErr := os.ReadFile(target); readErr != nil || !bytes.Equal(got, want) {
		t.Fatalf("collision changed destination: %q, error = %v", got, readErr)
	}
}

func TestBackupDownloadRemovesPartialFileAfterInterruptedStream(t *testing.T) {
	directory := t.TempDir()
	target := filepath.Join(directory, "backup.tar.zst.enc")
	_, err := downloadBackupArtifact(
		options{home: t.TempDir()},
		"job-2",
		target,
		func(_ []string, _ string, writer io.Writer) (string, error) {
			_, _ = writer.Write([]byte("partial secret archive"))
			return "Error: Backup job is not complete", errors.New("interrupted")
		},
	)
	if structuredCLIError(err).Code != "backup_not_complete" {
		t.Fatalf("interrupted download error = %v", err)
	}
	if _, statErr := os.Lstat(target); !errors.Is(statErr, os.ErrNotExist) {
		t.Fatalf("partial destination exists: %v", statErr)
	}
	entries, readErr := os.ReadDir(directory)
	if readErr != nil {
		t.Fatal(readErr)
	}
	if len(entries) != 0 {
		t.Fatalf("temporary download was not removed: %v", entries)
	}
}

func TestParseOptionsHonorsExplicitJSONBoolean(t *testing.T) {
	opts, err := parseOptions([]string{"storage", "probe", "--json=false"})
	if err != nil {
		t.Fatal(err)
	}
	if opts.jsonOutput {
		t.Fatal("--json=false unexpectedly enabled JSON output")
	}
	if !jsonOutputRequested([]string{"storage", "probe", "--json=true"}) {
		t.Fatal("--json=true was not detected after a preceding parse failure")
	}
}

func TestJSONBackendServiceNotRunningEmitsOneStructuredError(t *testing.T) {
	opts := options{home: t.TempDir(), jsonOutput: true}
	var backendOutput bytes.Buffer
	exitErr := errors.New("exit status 1")
	err := runJSONBackendCommand(
		opts,
		[]string{"compose", "exec", "-T", "fastapi"},
		&backendOutput,
		func(_ string, _ []string, _ string) (string, string, error) {
			return "", "service \"fastapi\" is not running\n", exitErr
		},
	)
	if err == nil {
		t.Fatal("stopped backend unexpectedly succeeded")
	}
	if backendOutput.Len() != 0 {
		t.Fatalf("backend failure leaked unstructured output: %q", backendOutput.String())
	}

	var rendered bytes.Buffer
	if writeErr := writeCLIErrorJSON(&rendered, err); writeErr != nil {
		t.Fatal(writeErr)
	}
	var payload cliErrorResponse
	if decodeErr := json.Unmarshal(rendered.Bytes(), &payload); decodeErr != nil {
		t.Fatalf("error output is not JSON: %v; output=%q", decodeErr, rendered.String())
	}
	if payload.OK || payload.Error.Code != "service_not_running" || payload.Error.Message != "The fastapi service is not running." {
		t.Fatalf("unexpected error payload: %+v", payload)
	}
	if strings.Contains(rendered.String(), "exit status 1") || strings.Count(rendered.String(), `"error"`) != 1 {
		t.Fatalf("error output was duplicated or leaked the process error: %q", rendered.String())
	}
}

func TestJSONBackendSuccessValidatesAndNormalizesOneDocument(t *testing.T) {
	opts := options{home: t.TempDir(), jsonOutput: true}
	var output bytes.Buffer
	err := runJSONBackendCommand(
		opts,
		[]string{"compose", "exec", "-T", "fastapi"},
		&output,
		func(_ string, _ []string, _ string) (string, string, error) {
			return `{"items":[],"total":0}`, "ignored backend warning\n", nil
		},
	)
	if err != nil {
		t.Fatal(err)
	}
	var payload map[string]any
	if decodeErr := json.Unmarshal(output.Bytes(), &payload); decodeErr != nil {
		t.Fatalf("success output is not JSON: %v; output=%q", decodeErr, output.String())
	}
	if _, found := payload["items"]; !found || strings.Contains(output.String(), "warning") {
		t.Fatalf("success output was changed or polluted: %q", output.String())
	}
}

func TestJSONBackendClassifiesOperatorSafeFailureCategories(t *testing.T) {
	tests := []struct {
		output string
		code   string
	}{
		{output: "Backup job not found", code: "not_found"},
		{output: "Cannot connect to the Docker daemon. Is the docker daemon running?", code: "docker_not_running"},
		{output: "dial tcp 127.0.0.1: connection refused", code: "transport_error"},
		{output: "remote returned unauthorized", code: "authentication_failed"},
		{output: "unexpected internal detail secret=value", code: "backend_command_failed"},
	}
	for _, test := range tests {
		t.Run(test.code, func(t *testing.T) {
			details := structuredCLIError(classifyBackendCommandError("", test.output, exitErrForTest{}))
			if details.Code != test.code {
				t.Fatalf("code = %q, want %q", details.Code, test.code)
			}
			if strings.Contains(details.Message, "secret=value") {
				t.Fatalf("operator-safe message leaked backend detail: %q", details.Message)
			}
		})
	}
}

type exitErrForTest struct{}

func (exitErrForTest) Error() string { return "exit status 1" }

func TestParseOptionsRejectsUnexpectedLifecycleArguments(t *testing.T) {
	if _, err := parseOptions([]string{"start", "typo"}); err == nil {
		t.Fatal("unexpected start argument was accepted")
	}
}

func TestParseOptionsHonorsExplicitAutomaticUpdateSafetyBooleans(t *testing.T) {
	opts, err := parseOptions([]string{
		"auto-update", "enable", "--skip-backup=false", "--allow-unhealthy=false",
	})
	if err != nil {
		t.Fatalf("parseOptions() error = %v", err)
	}
	if opts.skipBackup || opts.allowUnhealthy || !opts.backupBeforeUpdateSet || !opts.onlyWhenHealthySet {
		t.Fatalf("explicit false safety flags parsed incorrectly: %+v", opts)
	}

	opts, err = parseOptions([]string{
		"auto-update", "enable", "--backup-before-update=false", "--only-when-healthy=false",
	})
	if err != nil {
		t.Fatalf("parseOptions() inverse flags error = %v", err)
	}
	if !opts.skipBackup || !opts.allowUnhealthy {
		t.Fatalf("explicit false inverse flags parsed incorrectly: %+v", opts)
	}

	for _, flag := range []string{"--skip-backup=maybe", "--allow-unhealthy=0"} {
		if _, err := parseOptions([]string{"auto-update", "enable", flag}); err == nil {
			t.Fatalf("invalid explicit boolean %q was accepted", flag)
		}
	}

	opts, err = parseOptions([]string{
		"auto-update", "enable", "--destination", "remote-store", "--no-encrypted=false",
	})
	if err != nil {
		t.Fatalf("parseOptions() backup policy error = %v", err)
	}
	if opts.destination != "remote-store" || !opts.destinationSet || opts.noEncrypted || !opts.backupEncryptionSet {
		t.Fatalf("automatic-update backup policy parsed incorrectly: %+v", opts)
	}
}

func TestReadEnvTogglesSupportsRedisOff(t *testing.T) {
	directory := t.TempDir()
	envFile := filepath.Join(directory, ".env")
	if err := os.WriteFile(envFile, []byte("REDIS_ENABLED=false\nOMLORIX_USE_BUNDLED_REDIS=true\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	toggles := readEnvToggles(options{envFile: envFile})
	if toggles.redisEnabled || toggles.useBundledRedis {
		t.Fatalf("Redis Off was not canonicalized: %+v", toggles)
	}
	if contains(buildComposeProfiles(toggles), "redis-enabled") || contains(buildComposeProfiles(toggles), "bundled-redis") {
		t.Fatalf("Redis profiles enabled while Redis is off: %v", buildComposeProfiles(toggles))
	}
}

func TestManagedCloudSelectionKeepsServerComposeForBundledStorage(t *testing.T) {
	directory := t.TempDir()
	envFile := filepath.Join(directory, ".env")
	raw := strings.Join([]string{
		"OMLORIX_USE_BUNDLED_DB=false",
		"REDIS_ENABLED=false",
		"OMLORIX_USE_BUNDLED_REDIS=false",
		"OMLORIX_USE_PGBOUNCER=false",
		"OMLORIX_USE_BUNDLED_STORAGE=true",
	}, "\n") + "\n"
	if err := os.WriteFile(envFile, []byte(raw), 0o600); err != nil {
		t.Fatal(err)
	}
	files := composeFileList(options{envFile: envFile})
	if !contains(files, "docker-compose.server.yml") || contains(files, "docker-compose.managed-cloud.yml") {
		t.Fatalf("bundled storage selected incorrect topology: %v", files)
	}
}

func TestParseComposeServicesSupportsArrayAndJSONLines(t *testing.T) {
	array := `[{"Service":"frontend","State":"running","Health":"healthy"},{"Service":"fastapi","State":"running"}]`
	services, err := parseComposeServices(array)
	if err != nil || len(services) != 2 {
		t.Fatalf("array parse = %v, %v", services, err)
	}
	lines := "{\"Service\":\"redis\",\"State\":\"running\"}\n{\"Service\":\"postgres\",\"State\":\"exited\"}\n"
	services, err = parseComposeServices(lines)
	if err != nil || len(services) != 2 || services[0].Name != "postgres" {
		t.Fatalf("JSON-lines parse = %v, %v", services, err)
	}
}

func TestFullStackReadinessDoesNotReturnOnEndpointAlone(t *testing.T) {
	snapshots := []struct {
		stack    stackStatus
		endpoint endpointStatus
	}{
		{
			stack:    stackStatus{Total: 2, Running: 2, HealthIssues: 1},
			endpoint: endpointStatus{ReadyURL: "http://localhost:8080/ready", Reachable: true},
		},
		{
			stack:    stackStatus{Total: 2, Running: 2},
			endpoint: endpointStatus{ReadyURL: "http://localhost:8080/ready", Reachable: true},
		},
	}
	calls := 0
	err := waitForServerHealthyWithInspector(options{}, 100*time.Millisecond, time.Millisecond, func(options) (stackStatus, endpointStatus) {
		index := calls
		if index >= len(snapshots) {
			index = len(snapshots) - 1
		}
		calls++
		return snapshots[index].stack, snapshots[index].endpoint
	})
	if err != nil {
		t.Fatalf("full-stack readiness failed: %v", err)
	}
	if calls != 2 {
		t.Fatalf("readiness inspections = %d, want 2", calls)
	}
	if serverStackHealthy(snapshots[0].stack, snapshots[0].endpoint) {
		t.Fatal("endpoint-only readiness was treated as full-stack health")
	}
}

func TestEndpointProbeRequiresInitializedHomeAndOwnedFrontend(t *testing.T) {
	stack := stackStatus{Services: []serviceStatus{{
		Name: "frontend", State: "running", Expected: true,
	}}}
	if shouldProbeServerEndpoint(false, nil, stack) {
		t.Fatal("uninitialized home was allowed to probe an unrelated endpoint")
	}
	if shouldProbeServerEndpoint(true, []string{"docker-compose.server.yml"}, stack) {
		t.Fatal("incomplete home was allowed to probe an endpoint")
	}
	if !shouldProbeServerEndpoint(true, nil, stack) {
		t.Fatal("initialized home with its owned frontend was not allowed to probe")
	}
	stack.Services[0].State = "exited"
	if shouldProbeServerEndpoint(true, nil, stack) {
		t.Fatal("stopped frontend was allowed to attribute an endpoint")
	}
}

func TestMergeExpectedServicesCreatesMissingRows(t *testing.T) {
	runtime := []serviceStatus{{Name: "frontend", State: "running"}}
	merged := mergeExpectedServices([]string{"frontend", "fastapi"}, runtime)
	if len(merged) != 2 || !merged[0].Missing || merged[0].Name != "fastapi" {
		t.Fatalf("missing expected services were not represented: %+v", merged)
	}
}

func TestOneShotServicesDoNotMakeHealthyStackIncomplete(t *testing.T) {
	services := mergeExpectedServices(
		[]string{"fastapi", "frontend"},
		[]serviceStatus{
			{Name: "fastapi", State: "running", Health: "healthy"},
			{Name: "frontend", State: "running", Health: "healthy"},
			{Name: "migrate", State: "exited", Status: "Exited (0)"},
		},
	)
	stack := summarizeStackServices(services, true)
	if stack.Total != 2 || stack.Running != 2 || stack.NotRunning != 0 || stack.Missing != 0 {
		t.Fatalf("one-shot service affected health denominator: %+v", stack)
	}
}

func TestOfflineMigrationDrainsEveryApplicationService(t *testing.T) {
	services := offlineApplicationServiceNames()
	expectedServices := []string{"frontend", "email_worker"}
	expectedServices = append(expectedServices, dedicatedWorkerServiceNames...)
	expectedServices = append(
		expectedServices,
		"automation_scheduler",
		"automation_worker",
		"fastapi",
	)
	for _, expected := range expectedServices {
		if !contains(services, expected) {
			t.Fatalf("offline migration does not drain %s: %v", expected, services)
		}
	}
	if got, want := offlineMigrationDrainCommand(), []string{"down", "--remove-orphans"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("offline drain command = %v, want %v", got, want)
	}
	if got, want := offlineMigrationResetCommand(), []string{"rm", "-sf", "migrate"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("offline reset command = %v, want %v", got, want)
	}
	if got, want := offlineMigrationRunCommand(), []string{"up", "-d", "--force-recreate", "migrate"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("offline migration command = %v, want %v", got, want)
	}
}

func TestRestoreApplicationContainerIDsIncludeOrphansAndOneOffs(t *testing.T) {
	raw := strings.Join([]string{
		`{"ID":"aaaaaaaaaaaa","Service":"postgres","State":"running","Labels":"com.docker.compose.oneoff=False"}`,
		`{"ID":"bbbbbbbbbbbb","Service":"fastapi","State":"running"}`,
		`{"ID":"cccccccccccc","Service":"removed_worker","State":"restarting"}`,
		`{"ID":"dddddddddddd","Service":"frontend","State":"exited"}`,
		`{"ID":"eeeeeeeeeeee","Service":"postgres","State":"running","Labels":{"com.docker.compose.oneoff":"True"}}`,
	}, "\n")

	ids, err := restoreApplicationContainerIDs(raw)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"bbbbbbbbbbbb", "cccccccccccc", "eeeeeeeeeeee"}
	if !reflect.DeepEqual(ids, want) {
		t.Fatalf("restore application container IDs = %v, want %v", ids, want)
	}

	if _, err := restoreApplicationContainerIDs(
		`{"ID":"--dangerous","Service":"old_worker","State":"running"}`,
	); err == nil {
		t.Fatal("invalid container ID was accepted")
	}
	if _, err := restoreApplicationContainerIDs(
		`{"ID":"aaaaaaaaaaaa","Service":"postgres","State":"running","Labels":"com.docker.compose.oneoff=maybe"}`,
	); err == nil {
		t.Fatal("malformed infrastructure one-off label was accepted")
	}
}

func TestStopRemainingRestoreApplicationContainersStopsDiscoveredIDs(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("test helper uses a POSIX shell")
	}
	home := t.TempDir()
	envFile := filepath.Join(home, ".env")
	logPath := filepath.Join(home, "docker.log")
	if err := os.WriteFile(envFile, []byte("OMLORIX_VERSION=test\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	fakeDocker := filepath.Join(home, "docker")
	script := `#!/bin/sh
printf '%s\n' "$*" >> "$DOCKER_LOG"
case " $* " in
  *" ps --all --orphans --format json "*)
    printf '%s\n' '{"ID":"aaaaaaaaaaaa","Service":"redis","State":"running","Labels":"com.docker.compose.oneoff=False"}'
    printf '%s\n' '{"ID":"bbbbbbbbbbbb","Service":"old_worker","State":"running"}'
    ;;
esac
exit 0
`
	if err := os.WriteFile(fakeDocker, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("DOCKER_BIN", fakeDocker)
	t.Setenv("DOCKER_LOG", logPath)

	if err := stopRemainingRestoreApplicationContainers(
		options{home: home, envFile: envFile},
	); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(logPath)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	if len(lines) != 2 || !strings.HasSuffix(lines[1], "stop --time 60 bbbbbbbbbbbb") {
		t.Fatalf("restore container fence commands = %v", lines)
	}
}

func TestUpdateRollbackDrainsProjectBeforeStartingPreviousImage(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("test helper uses a POSIX shell")
	}
	home := t.TempDir()
	envFile := filepath.Join(home, ".env")
	logPath := filepath.Join(home, "docker.log")
	if err := os.WriteFile(envFile, []byte("OMLORIX_VERSION=next\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	fakeDocker := filepath.Join(home, "docker")
	if err := os.WriteFile(
		fakeDocker,
		[]byte("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\nexit 0\n"),
		0o700,
	); err != nil {
		t.Fatal(err)
	}
	t.Setenv("DOCKER_BIN", fakeDocker)
	t.Setenv("DOCKER_LOG", logPath)
	opts := options{home: home, envFile: envFile}

	err := rollbackUpdate(opts, "previous", "stable", "next", errors.New("failed"), true, false)
	if err == nil {
		t.Fatal("rollback unexpectedly reported update success")
	}
	raw, readErr := os.ReadFile(logPath)
	if readErr != nil {
		t.Fatal(readErr)
	}
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	if len(lines) != 3 {
		t.Fatalf("rollback docker calls = %v", lines)
	}
	if !strings.HasSuffix(lines[0], "down --remove-orphans") {
		t.Fatalf("rollback did not drain first: %v", lines)
	}
	if !strings.HasSuffix(lines[1], "pull") || !strings.HasSuffix(lines[2], "up -d --force-recreate --remove-orphans") {
		t.Fatalf("rollback ordering = %v", lines)
	}
}

func TestUpdateRollbackLeavesStackOfflineWhenDrainFails(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("test helper uses a POSIX shell")
	}
	home := t.TempDir()
	envFile := filepath.Join(home, ".env")
	logPath := filepath.Join(home, "docker.log")
	if err := os.WriteFile(envFile, []byte("OMLORIX_VERSION=next\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	fakeDocker := filepath.Join(home, "docker")
	script := "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\nexit 7\n"
	if err := os.WriteFile(fakeDocker, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("DOCKER_BIN", fakeDocker)
	t.Setenv("DOCKER_LOG", logPath)

	err := rollbackUpdate(
		options{home: home, envFile: envFile},
		"previous",
		"stable",
		"next",
		errors.New("failed"),
		true,
		false,
	)
	if err == nil || !strings.Contains(err.Error(), "left offline") {
		t.Fatalf("unsafe rollback result = %v", err)
	}
	raw, readErr := os.ReadFile(logPath)
	if readErr != nil {
		t.Fatal(readErr)
	}
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	if len(lines) != 1 || !strings.HasSuffix(lines[0], "down --remove-orphans") {
		t.Fatalf("rollback started services after failed drain: %v", lines)
	}
}

func TestUpdateFailureAfterMigrationKeepsTargetSelectedAndOffline(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("test helper uses a POSIX shell")
	}
	home := t.TempDir()
	envFile := filepath.Join(home, ".env")
	logPath := filepath.Join(home, "docker.log")
	if err := os.WriteFile(envFile, []byte("OMLORIX_VERSION=next\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	fakeDocker := filepath.Join(home, "docker")
	if err := os.WriteFile(
		fakeDocker,
		[]byte("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\nexit 0\n"),
		0o700,
	); err != nil {
		t.Fatal(err)
	}
	t.Setenv("DOCKER_BIN", fakeDocker)
	t.Setenv("DOCKER_LOG", logPath)
	opts := options{home: home, envFile: envFile}

	err := rollbackUpdate(opts, "previous", "stable", "next", errors.New("migration failed"), true, true)
	if err == nil || !strings.Contains(err.Error(), "target release remains selected") {
		t.Fatalf("post-migration failure result = %v", err)
	}
	rawEnv, readErr := os.ReadFile(envFile)
	if readErr != nil {
		t.Fatal(readErr)
	}
	if !strings.Contains(string(rawEnv), "OMLORIX_VERSION=next") || strings.Contains(string(rawEnv), "previous") {
		t.Fatalf("post-migration failure changed target selection: %s", rawEnv)
	}
	rawLog, readErr := os.ReadFile(logPath)
	if readErr != nil {
		t.Fatal(readErr)
	}
	lines := strings.Split(strings.TrimSpace(string(rawLog)), "\n")
	if len(lines) != 1 || !strings.HasSuffix(lines[0], "down --remove-orphans") {
		t.Fatalf("post-migration failure started an image or failed to drain: %v", lines)
	}
}

func TestComposeOwnershipRejectsAnotherInstallation(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("test helper uses a POSIX shell")
	}
	home := t.TempDir()
	envFile := filepath.Join(home, ".env")
	if err := os.WriteFile(envFile, []byte("COMPOSE_PROJECT_NAME=omlorix-test\nOMLORIX_INSTALLATION_ID=expected-home\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	fakeDocker := filepath.Join(home, "docker")
	script := "#!/bin/sh\nif [ \"$1\" = ps ]; then echo foreign-container; exit 0; fi\nif [ \"$1\" = inspect ]; then echo another-home; exit 0; fi\nexit 1\n"
	if err := os.WriteFile(fakeDocker, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("DOCKER_BIN", fakeDocker)
	err := validateComposeOwnership(options{home: home, envFile: envFile})
	if err == nil || !strings.Contains(err.Error(), "belongs to another") {
		t.Fatalf("ownership validation result = %v", err)
	}
}

func TestCLIConfigValidationMatchesLauncherTypesAndFernetFormat(t *testing.T) {
	opts := options{home: t.TempDir()}
	if err := os.WriteFile(filepath.Join(opts.home, ".env.example"), []byte("FRONTEND_HTTP_HOST_PORT=8080\nREDIS_ENABLED=true\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := validateConfigValue(opts, "FRONTEND_HTTP_HOST_PORT", "banana"); err == nil {
		t.Fatal("invalid port was accepted")
	}
	if err := validateConfigValue(opts, "REDIS_ENABLED", "sometimes"); err == nil {
		t.Fatal("invalid boolean was accepted")
	}
	if err := validateConfigValue(opts, "PGBOUNCER_POOL_MODE", "statement"); err == nil {
		t.Fatal("statement pooling was accepted for the transactional application")
	}
	if err := validateConfigValue(opts, "PGBOUNCER_POOL_MODE", "transaction"); err != nil {
		t.Fatalf("transaction pooling was rejected: %v", err)
	}
	if err := validateConfigValue(opts, "JWT_SECRET_KEY", strings.Repeat("x", 63)); err == nil {
		t.Fatal("63-byte JWT secret was accepted")
	}
	multibyteJWTSecret := strings.Repeat("é", 32)
	if len(multibyteJWTSecret) != jwtSecretMinBytes {
		t.Fatalf("test JWT secret byte length = %d, want %d", len(multibyteJWTSecret), jwtSecretMinBytes)
	}
	if err := validateConfigValue(opts, "JWT_SECRET_KEY", multibyteJWTSecret); err != nil {
		t.Fatalf("64-byte multibyte JWT secret rejected: %v", err)
	}
	fernet := base64.URLEncoding.EncodeToString(make([]byte, 32))
	if err := validateConfigValue(opts, "ENCRYPTION_KEY", fernet); err != nil {
		t.Fatalf("valid Fernet key rejected: %v", err)
	}
	if err := validateConfigValue(opts, "ENCRYPTION_KEY", "not-a-fernet-key"); err == nil {
		t.Fatal("invalid Fernet key was accepted")
	}
}

func TestCLIConfigWriteRejectsAuditIPSaltThatReusesJWTSecret(t *testing.T) {
	home := t.TempDir()
	envFile := filepath.Join(home, ".env")
	jwtSecret := strings.Repeat("j", 64)
	original := "JWT_SECRET_KEY=" + jwtSecret + "\nLOG_IP_HASH_SALT=" + strings.Repeat("i", 32) + "\n"
	if err := os.WriteFile(envFile, []byte(original), 0o600); err != nil {
		t.Fatal(err)
	}

	// config set ultimately writes through writeEnv. Surrounding whitespace is
	// significant here because the backend normalizes both values before use.
	err := writeEnv(envFile, map[string]string{"LOG_IP_HASH_SALT": "  " + jwtSecret + "  "})
	if err == nil || !strings.Contains(err.Error(), "must differ from JWT_SECRET_KEY") {
		t.Fatalf("reused JWT secret validation result = %v", err)
	}
	raw, readErr := os.ReadFile(envFile)
	if readErr != nil {
		t.Fatal(readErr)
	}
	if string(raw) != original {
		t.Fatalf("invalid configuration was written:\n%s", raw)
	}
}

func TestScheduledUpdateNextRunUsesSelectedWeekdays(t *testing.T) {
	after := time.Date(2026, time.August, 7, 4, 0, 0, 0, time.UTC) // Friday
	next := nextScheduledUpdate(scheduledUpdateSettings{Enabled: true, Time: "03:00", Weekdays: []int{0, 6}}, after)
	if next == nil || next.Weekday() != time.Saturday {
		t.Fatalf("next weekend run = %v", next)
	}
}

func TestScheduledSettingsRequireWeekdaysWhenSwitchingToCustom(t *testing.T) {
	current := defaultScheduledUpdateStore().Settings
	if _, err := scheduledSettingsFromOptions(options{schedule: "custom", scheduleSet: true}, current); err == nil || !strings.Contains(err.Error(), "same command") {
		t.Fatalf("daily-to-custom transition result = %v", err)
	}

	configured, err := scheduledSettingsFromOptions(options{
		schedule: "custom", scheduleSet: true, weekdays: "1,3,5", weekdaysSet: true,
	}, current)
	if err != nil {
		t.Fatalf("explicit custom schedule rejected: %v", err)
	}
	if !reflect.DeepEqual(configured.Weekdays, []int{1, 3, 5}) {
		t.Fatalf("custom weekdays = %v", configured.Weekdays)
	}

	configured, err = scheduledSettingsFromOptions(options{schedule: "custom", scheduleSet: true}, scheduledUpdateSettings{
		Schedule: "custom", Time: "03:00", Weekdays: []int{2, 4},
	})
	if err != nil || !reflect.DeepEqual(configured.Weekdays, []int{2, 4}) {
		t.Fatalf("existing custom weekdays were not retained: settings=%+v err=%v", configured, err)
	}
}

func TestScheduledUpdateCarriesReviewedBackupPolicyToUpdate(t *testing.T) {
	current := defaultScheduledUpdateStore().Settings
	configured, err := scheduledSettingsFromOptions(options{
		destination:         " remote-store ",
		destinationSet:      true,
		noEncrypted:         true,
		backupEncryptionSet: true,
	}, current)
	if err != nil {
		t.Fatalf("scheduled backup policy rejected: %v", err)
	}
	if configured.BackupDestinationID != "remote-store" || configured.BackupEncryptionEnabled {
		t.Fatalf("scheduled backup policy = %+v", configured)
	}

	updateOpts := scheduledUpdateOptions(options{}, configured)
	if updateOpts.skipBackup || updateOpts.destination != "remote-store" || !updateOpts.noEncrypted {
		t.Fatalf("scheduled update options = %+v", updateOpts)
	}

	_, err = scheduledSettingsFromOptions(options{
		destination:    strings.Repeat("x", 256),
		destinationSet: true,
	}, current)
	if err == nil || !strings.Contains(err.Error(), "255") {
		t.Fatalf("oversized destination result = %v", err)
	}
}

func TestAutoUpdateEnablePersistsReviewedBackupPolicyInServerHome(t *testing.T) {
	home := t.TempDir()
	opts := options{
		home:                home,
		arguments:           []string{"enable"},
		destination:         "remote-store",
		destinationSet:      true,
		noEncrypted:         true,
		backupEncryptionSet: true,
	}
	if err := commandAutoUpdate(opts); err != nil {
		t.Fatalf("auto-update enable failed: %v", err)
	}

	store, err := readScheduledUpdates(opts)
	if err != nil {
		t.Fatal(err)
	}
	if !store.Settings.Enabled || store.Settings.BackupDestinationID != "remote-store" || store.Settings.BackupEncryptionEnabled {
		t.Fatalf("persisted automatic-update backup policy = %+v", store.Settings)
	}
}

func TestScheduledUpdateStoreDefaultsMissingEncryptionPolicyToEnabled(t *testing.T) {
	home := t.TempDir()
	opts := options{home: home}
	raw := []byte(`{"settings":{"enabled":true,"channel":"stable","schedule":"daily","weekdays":[0,1,2,3,4,5,6],"time":"03:00","backupBeforeUpdate":true},"status":{}}`)
	if err := os.WriteFile(scheduledUpdatePath(opts), raw, 0o600); err != nil {
		t.Fatal(err)
	}

	store, err := readScheduledUpdates(opts)
	if err != nil {
		t.Fatal(err)
	}
	if !store.Settings.BackupEncryptionEnabled || store.Settings.BackupDestinationID != "" {
		t.Fatalf("legacy backup policy defaults = %+v", store.Settings)
	}
}

func TestScheduledUpdateDaemonCatchesPollingJitterOnce(t *testing.T) {
	settings := scheduledUpdateSettings{Enabled: true, Time: "03:00", Weekdays: []int{6}}
	now := time.Date(2026, time.August, 8, 3, 0, 40, 0, time.UTC) // Saturday
	window, due := scheduledUpdateWindowDue(settings, scheduledUpdateStatus{}, now)
	if !due || window != "2026-08-08T03:00" {
		t.Fatalf("scheduled window = %q, due = %t", window, due)
	}
	if _, repeated := scheduledUpdateWindowDue(settings, scheduledUpdateStatus{LastWindowKey: window}, now); repeated {
		t.Fatal("an attempted scheduled window should not be replayed")
	}
}

func TestManualScheduledUpdateReturnsActiveOperationLockError(t *testing.T) {
	home := t.TempDir()
	opts := options{home: home, envFile: filepath.Join(home, ".env")}
	release, err := acquireOperationLock(options{home: home, command: "update"})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(release)

	store := defaultScheduledUpdateStore()
	err = runScheduledUpdate(opts, &store, true)
	if err == nil || !strings.Contains(err.Error(), "another Omlorix CLI operation is active") {
		t.Fatalf("manual automatic update lock result = %v", err)
	}
	if store.Status.State != "skipped" {
		t.Fatalf("manual automatic update status = %+v", store.Status)
	}
}

func TestAutomaticEnvBackupRefreshesAfterConfigurationWrites(t *testing.T) {
	home := t.TempDir()
	target := filepath.Join(t.TempDir(), "omlorix-recovery.env")
	opts := options{home: home, envFile: filepath.Join(home, ".env")}
	if err := os.WriteFile(opts.envFile, []byte("FRONTEND_HTTP_HOST_PORT=8080\nOMLORIX_GITHUB_TOKEN=retired-release-token\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := configureAutomaticEnvBackup(opts, target); err != nil {
		t.Fatal(err)
	}
	initialBackup, err := os.ReadFile(target)
	if err != nil {
		t.Fatal(err)
	}
	if parseEnvContent(string(initialBackup))["OMLORIX_UPDATE_CHANNEL"] != "stable" {
		t.Fatalf("automatic backup did not include the default update channel:\n%s", initialBackup)
	}
	if strings.Contains(string(initialBackup), "OMLORIX_GITHUB_TOKEN") {
		t.Fatalf("automatic backup retained a retired environment key:\n%s", initialBackup)
	}
	if live, _ := os.ReadFile(opts.envFile); strings.Contains(string(live), "OMLORIX_UPDATE_CHANNEL=") {
		t.Fatalf("recovery-only update channel leaked into live environment:\n%s", live)
	}
	if err := writeUpdateChannel(opts, "beta"); err != nil {
		t.Fatal(err)
	}
	if err := writeEnv(opts.envFile, map[string]string{"FRONTEND_HTTP_HOST_PORT": "8088"}); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(target)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), "FRONTEND_HTTP_HOST_PORT=8088") {
		t.Fatalf("automatic backup was not refreshed:\n%s", raw)
	}
	if parseEnvContent(string(raw))["OMLORIX_UPDATE_CHANNEL"] != "beta" {
		t.Fatalf("channel change did not refresh the automatic backup:\n%s", raw)
	}
	if live, _ := os.ReadFile(opts.envFile); strings.Contains(string(live), "OMLORIX_UPDATE_CHANNEL=") {
		t.Fatalf("channel change polluted live environment:\n%s", live)
	}
	if _, err := validateAutomaticEnvBackupTarget(home, filepath.Join(home, "unsafe.env")); err == nil {
		t.Fatal("backup target inside server home should be rejected")
	}
}

func TestAutomaticEnvBackupReadsLauncherRecordAndExplicitDisableWins(t *testing.T) {
	home := t.TempDir()
	envFile := filepath.Join(home, ".env")
	target := filepath.Join(t.TempDir(), "launcher-recovery.env")
	raw := []byte("MODE=dev\n")
	if err := os.WriteFile(envFile, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(target, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	legacy, _ := json.Marshal(map[string]string{
		"backupFilePath": target,
		"backupSavedAt":  "2026-08-09T12:00:00Z",
	})
	if err := os.WriteFile(filepath.Join(home, ".launcher-setup.json"), legacy, 0o600); err != nil {
		t.Fatal(err)
	}
	config, err := readAutomaticEnvBackupConfig(envFile)
	if err != nil || config.Target != target || config.Fingerprint != envContentFingerprint(raw) {
		t.Fatalf("legacy Launcher config was not discovered: %+v, %v", config, err)
	}
	if err := writeAutomaticEnvBackupConfig(envFile, automaticEnvBackupConfig{}); err != nil {
		t.Fatal(err)
	}
	disabled, err := readAutomaticEnvBackupConfig(envFile)
	if err != nil || disabled.Target != "" {
		t.Fatalf("explicit shared disable fell back to legacy state: %+v, %v", disabled, err)
	}
}

func TestConfigImportMergesOmittedValuesAndEnforcesTopology(t *testing.T) {
	home := t.TempDir()
	envFile := filepath.Join(home, ".env")
	current := strings.Join([]string{
		"# retained comment",
		"MODE=dev",
		"JWT_SECRET_KEY=" + strings.Repeat("j", 64),
		"OMLORIX_USE_BUNDLED_DB=true",
		"DATABASE_URL=postgresql://user:secret@database.example.com/omlorix",
		"OMLORIX_USE_PGBOUNCER=true",
		"OMLORIX_USE_BUNDLED_REDIS=true",
		"REDIS_ENABLED=true",
		"REDIS_PASSWORD=redis-secret",
		"REDIS_URL=redis://:stale@redis:6379/0",
		"OMLORIX_USE_BUNDLED_STORAGE=false",
		"FILE_STORAGE_PROVIDER=local",
		"PRESERVE_ME=yes",
	}, "\n") + "\n"
	if err := os.WriteFile(envFile, []byte(current), 0o600); err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(t.TempDir(), "partial.env")
	if err := os.WriteFile(source, []byte("OMLORIX_USE_BUNDLED_DB=false\nOMLORIX_USE_BUNDLED_STORAGE=true\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	opts := options{home: home, envFile: envFile}
	if err := importConfig(opts, source, false); err != nil {
		t.Fatal(err)
	}
	env, mergedRaw := readEnv(envFile)
	if env["PRESERVE_ME"] != "yes" || !strings.Contains(mergedRaw, "# retained comment") {
		t.Fatalf("partial import erased omitted configuration:\n%s", mergedRaw)
	}
	if env["OMLORIX_USE_PGBOUNCER"] != "false" || env["FILE_STORAGE_PROVIDER"] != "s3" {
		t.Fatalf("topology invariants were not applied: %#v", env)
	}
}

func TestTopologyInvariantsRouteBundledPgBouncerAndRestoreDirectPostgres(t *testing.T) {
	pooled := topologyInvariantUpdates(map[string]string{
		"OMLORIX_USE_BUNDLED_DB": "true",
		"OMLORIX_USE_PGBOUNCER":  "true",
	})
	if pooled["DATABASE_HOST_OVERRIDE"] != "pgbouncer" || pooled["DATABASE_PORT_OVERRIDE"] != "5432" {
		t.Fatalf("pooled database route = %#v", pooled)
	}
	if value, present := pooled["DATABASE_URL"]; !present || value != "" {
		t.Fatalf("pooled route retained a higher-precedence DATABASE_URL: %#v", pooled)
	}
	if pooled["DATABASE_MIGRATION_HOST_OVERRIDE"] != "postgres" || pooled["DATABASE_MIGRATION_PORT_OVERRIDE"] != "5432" {
		t.Fatalf("migration database route = %#v", pooled)
	}

	direct := topologyInvariantUpdates(map[string]string{
		"OMLORIX_USE_BUNDLED_DB":  "true",
		"OMLORIX_USE_PGBOUNCER":   "false",
		"DATABASE_HOST_OVERRIDE": "pgbouncer",
		"DATABASE_PORT_OVERRIDE": "7432",
	})
	if direct["DATABASE_HOST_OVERRIDE"] != "postgres" || direct["DATABASE_PORT_OVERRIDE"] != "5432" {
		t.Fatalf("direct database route = %#v", direct)
	}
}

func TestConfigImportPreservesLauncherHiddenKeysInMergeAndReplacementModes(t *testing.T) {
	for _, replace := range []bool{false, true} {
		t.Run(map[bool]string{false: "merge", true: "replacement"}[replace], func(t *testing.T) {
			home := t.TempDir()
			envFile := filepath.Join(home, ".env")
			current := strings.Join([]string{
				"MODE=dev",
				"JWT_SECRET_KEY=" + strings.Repeat("j", 64),
				"OMLORIX_USE_BUNDLED_STORAGE=true",
				"FILE_STORAGE_PROVIDER=s3",
				"OMLORIX_INSTALLATION_ID=trusted-installation",
				"OMLORIX_LAUNCHER_PROXY_SECRET=" + strings.Repeat("a", 64),
				"FRONTEND_HTTP_HOST_BIND=0.0.0.0",
				"FRONTEND_TRUSTED_UPSTREAMS=",
				"FRONTEND_TRUST_PROXY_HEADERS=false",
				"OMLORIX_GITHUB_TOKEN=current-retired-token",
				"PRESERVE_WHEN_MERGING=yes",
			}, "\n") + "\n"
			if err := os.WriteFile(envFile, []byte(current), 0o600); err != nil {
				t.Fatal(err)
			}
			settings := defaultServerSettings()
			settings.Proxy.Enabled = true
			if err := writeServerSettings(options{home: home, envFile: envFile}, settings); err != nil {
				t.Fatal(err)
			}
			source := filepath.Join(t.TempDir(), "import.env")
			imported := strings.Join([]string{
				"MODE=dev",
				"JWT_SECRET_KEY=" + strings.Repeat("j", 64),
				"OMLORIX_USE_BUNDLED_STORAGE=true",
				"FILE_STORAGE_PROVIDER=s3",
				"OMLORIX_INSTALLATION_ID=foreign-installation",
				"OMLORIX_LAUNCHER_PROXY_ENABLED=true",
				"OMLORIX_LAUNCHER_PROXY_SECRET=" + strings.Repeat("b", 64),
				"FRONTEND_HTTP_HOST_BIND=0.0.0.0",
				"FRONTEND_TRUSTED_UPSTREAMS=10.0.0.0/8",
				"FRONTEND_TRUST_PROXY_HEADERS=true",
				"OMLORIX_GITHUB_TOKEN=imported-retired-token",
				"ORDINARY_SETTING=imported",
			}, "\n") + "\n"
			if err := os.WriteFile(source, []byte(imported), 0o600); err != nil {
				t.Fatal(err)
			}

			if err := importConfig(options{home: home, envFile: envFile}, source, replace); err != nil {
				t.Fatal(err)
			}
			env, _ := readEnv(envFile)
			if env["OMLORIX_INSTALLATION_ID"] != "trusted-installation" ||
				env["OMLORIX_LAUNCHER_PROXY_SECRET"] != strings.Repeat("a", 64) ||
				env["FRONTEND_TRUSTED_UPSTREAMS"] != "" {
				t.Fatalf("launcher-owned values were replaced: %#v", env)
			}
			if env["ORDINARY_SETTING"] != "imported" {
				t.Fatalf("ordinary imported value = %q", env["ORDINARY_SETTING"])
			}
			if _, present := env["OMLORIX_GITHUB_TOKEN"]; present {
				t.Fatalf("replace=%v restored retired GitHub configuration: %#v", replace, env)
			}
			if env["FRONTEND_HTTP_HOST_BIND"] != "127.0.0.1" || env["FRONTEND_TRUST_PROXY_HEADERS"] != "true" {
				t.Fatalf("launcher proxy invariants were not derived: %#v", env)
			}
			if replace == (env["PRESERVE_WHEN_MERGING"] == "yes") {
				t.Fatalf("replace=%v produced incorrect omitted-key behavior: %#v", replace, env)
			}
		})
	}
}

func TestRetiredGitHubTokenIsLauncherHiddenAndCannotBeConfigured(t *testing.T) {
	if !retiredEnvKeys["OMLORIX_GITHUB_TOKEN"] || !launcherHiddenEnvKeys["OMLORIX_GITHUB_TOKEN"] {
		t.Fatal("retired GitHub token is not launcher-hidden")
	}

	home := t.TempDir()
	envFile := filepath.Join(home, ".env")
	if err := os.WriteFile(envFile, []byte("OMLORIX_GITHUB_TOKEN=legacy-token\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	err := commandConfig(options{
		home:       home,
		envFile:    envFile,
		sourceRoot: filepath.Clean(filepath.Join("..", "..")),
		arguments:  []string{"set", "OMLORIX_GITHUB_TOKEN", "new-token"},
	})
	if err == nil || !strings.Contains(err.Error(), "is retired and cannot be configured") {
		t.Fatalf("retired config set result = %v", err)
	}
	if raw, readErr := os.ReadFile(envFile); readErr != nil {
		t.Fatal(readErr)
	} else if strings.Contains(string(raw), "OMLORIX_GITHUB_TOKEN") {
		t.Fatalf("retired config remained in live environment:\n%s", raw)
	}
}

func TestConfigImportDoesNotCreateBackupOrMutateRuntime(t *testing.T) {
	home := t.TempDir()
	envFile := filepath.Join(home, ".env")
	if err := os.WriteFile(
		envFile,
		[]byte("MODE=dev\nJWT_SECRET_KEY="+strings.Repeat("j", 64)+"\nOMLORIX_USE_BUNDLED_STORAGE=true\nFILE_STORAGE_PROVIDER=s3\nCURRENT=value\n"),
		0o600,
	); err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(t.TempDir(), "direct.env")
	if err := os.WriteFile(source, []byte("CURRENT=imported\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := importConfig(options{home: home, envFile: envFile}, source, false); err != nil {
		t.Fatal(err)
	}
	env, _ := readEnv(envFile)
	if env["CURRENT"] != "imported" {
		t.Fatalf("imported value = %q", env["CURRENT"])
	}
	matches, err := filepath.Glob(envFile + ".backup-*")
	if err != nil {
		t.Fatal(err)
	}
	if len(matches) != 0 {
		t.Fatalf("direct import created backups: %v", matches)
	}
}

func TestCompleteEnvRecoveryRestoresLauncherOwnedValuesExactly(t *testing.T) {
	home := t.TempDir()
	envFile := filepath.Join(home, ".env")
	if err := os.WriteFile(envFile, []byte("MODE=dev\nOMLORIX_INSTALLATION_ID=current\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	fernet := base64.URLEncoding.EncodeToString(make([]byte, 32))
	recoveryRaw := strings.Join([]string{
		"MODE=dev",
		"OMLORIX_UPDATE_CHANNEL=beta",
		"JWT_SECRET_KEY=" + strings.Repeat("j", 64),
		"ENCRYPTION_KEY=" + fernet,
		"PASSWORD_RESET_IDENTIFIER_HASH_SALT=" + strings.Repeat("s", 32),
		"LOG_IP_HASH_SALT=" + strings.Repeat("i", 32),
		"OMLORIX_USE_BUNDLED_STORAGE=true",
		"FILE_STORAGE_PROVIDER=s3",
		"OMLORIX_INSTALLATION_ID=recovered-installation",
		"OMLORIX_LAUNCHER_PROXY_SECRET=" + strings.Repeat("p", 64),
		"OMLORIX_LAUNCHER_PROXY_ENABLED=true",
		"OMLORIX_LAUNCHER_PROXY_AUTOSTART=false",
		"OMLORIX_LAUNCHER_PROXY_BIND=127.0.0.1",
		"OMLORIX_LAUNCHER_PROXY_HTTP_PORT=9081",
		"OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE=recovered-passphrase",
		"FRONTEND_TRUSTED_UPSTREAMS=10.25.0.10/32",
		"FRONTEND_TRUST_PROXY_HEADERS=true",
		"OMLORIX_GITHUB_TOKEN=recovery-retired-token",
		"CUSTOM_RECOVERY_VALUE=exact",
		"# preserve this final comment",
	}, "\n")
	recoveryFile := filepath.Join(t.TempDir(), "complete.env")
	if err := os.WriteFile(recoveryFile, []byte(recoveryRaw), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := restoreCompleteEnv(options{home: home, envFile: envFile}, recoveryFile); err != nil {
		t.Fatal(err)
	}
	restoredRaw, err := os.ReadFile(envFile)
	if err != nil {
		t.Fatal(err)
	}
	for _, hostKey := range append([]string{"OMLORIX_UPDATE_CHANNEL"}, managedProxySettingsEnvKeys...) {
		if _, present := parseEnvContent(string(restoredRaw))[hostKey]; present {
			t.Fatalf("live environment retained management-only setting %s:\n%s", hostKey, restoredRaw)
		}
	}
	restored := parseEnvContent(string(restoredRaw))
	if _, present := restored["OMLORIX_GITHUB_TOKEN"]; present {
		t.Fatalf("complete recovery restored a retired environment key: %#v", restored)
	}
	if restored["OMLORIX_INSTALLATION_ID"] != "recovered-installation" ||
		restored["OMLORIX_LAUNCHER_PROXY_SECRET"] != strings.Repeat("p", 64) ||
		restored["FRONTEND_TRUSTED_UPSTREAMS"] != "10.25.0.10/32" {
		t.Fatalf("launcher-owned recovery values were not restored: %#v", restored)
	}
	settings, err := readServerSettings(options{home: home, envFile: envFile})
	if err != nil {
		t.Fatal(err)
	}
	if settings.UpdateChannel != "beta" || !settings.Proxy.Enabled || settings.Proxy.Autostart || settings.Proxy.BindHost != "127.0.0.1" ||
		settings.Proxy.HTTPPort != "9081" || settings.Proxy.TLSKeyPassphrase != "recovered-passphrase" {
		t.Fatalf("management recovery settings were not restored: %#v", settings)
	}
	matches, err := filepath.Glob(envFile + ".backup-*")
	if err != nil {
		t.Fatal(err)
	}
	if len(matches) != 0 {
		t.Fatalf("complete recovery created backups: %v", matches)
	}
}

func TestCompleteEnvRecoveryWithoutChannelPreservesCurrentSetting(t *testing.T) {
	home := t.TempDir()
	envFile := filepath.Join(home, ".env")
	opts := options{home: home, envFile: envFile}
	if err := os.WriteFile(envFile, []byte("MODE=dev\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	settings := defaultServerSettings()
	settings.UpdateChannel = "beta"
	if err := writeServerSettings(opts, settings); err != nil {
		t.Fatal(err)
	}
	fernet := base64.URLEncoding.EncodeToString(make([]byte, 32))
	recoveryRaw := strings.Join([]string{
		"MODE=dev",
		"JWT_SECRET_KEY=" + strings.Repeat("j", 64),
		"ENCRYPTION_KEY=" + fernet,
		"PASSWORD_RESET_IDENTIFIER_HASH_SALT=" + strings.Repeat("s", 32),
		"LOG_IP_HASH_SALT=" + strings.Repeat("i", 32),
		"OMLORIX_USE_BUNDLED_STORAGE=true",
		"FILE_STORAGE_PROVIDER=s3",
	}, "\n") + "\n"
	recoveryFile := filepath.Join(t.TempDir(), "channel-less.env")
	if err := os.WriteFile(recoveryFile, []byte(recoveryRaw), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := restoreCompleteEnv(opts, recoveryFile); err != nil {
		t.Fatal(err)
	}
	restoredSettings, err := readServerSettings(opts)
	if err != nil {
		t.Fatal(err)
	}
	if restoredSettings.UpdateChannel != "beta" {
		t.Fatalf("channel-less recovery changed update channel to %q", restoredSettings.UpdateChannel)
	}
}

func TestCompleteEnvRecoveryRejectsInvalidChannelBeforeWriting(t *testing.T) {
	home := t.TempDir()
	envFile := filepath.Join(home, ".env")
	opts := options{home: home, envFile: envFile}
	originalRaw := []byte("MODE=dev\nCUSTOM_CURRENT_VALUE=preserved\n")
	if err := os.WriteFile(envFile, originalRaw, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := writeServerSettings(opts, defaultServerSettings()); err != nil {
		t.Fatal(err)
	}
	fernet := base64.URLEncoding.EncodeToString(make([]byte, 32))
	recoveryRaw := strings.Join([]string{
		"MODE=dev",
		"OMLORIX_UPDATE_CHANNEL=nightly",
		"JWT_SECRET_KEY=" + strings.Repeat("j", 64),
		"ENCRYPTION_KEY=" + fernet,
		"PASSWORD_RESET_IDENTIFIER_HASH_SALT=" + strings.Repeat("s", 32),
		"LOG_IP_HASH_SALT=" + strings.Repeat("i", 32),
		"OMLORIX_USE_BUNDLED_STORAGE=true",
		"FILE_STORAGE_PROVIDER=s3",
	}, "\n") + "\n"
	recoveryFile := filepath.Join(t.TempDir(), "invalid-channel.env")
	if err := os.WriteFile(recoveryFile, []byte(recoveryRaw), 0o600); err != nil {
		t.Fatal(err)
	}

	err := restoreCompleteEnv(opts, recoveryFile)
	if err == nil || !strings.Contains(err.Error(), "stable or beta") {
		t.Fatalf("invalid recovery channel returned %v", err)
	}
	currentRaw, readErr := os.ReadFile(envFile)
	if readErr != nil {
		t.Fatal(readErr)
	}
	if string(currentRaw) != string(originalRaw) {
		t.Fatalf("invalid recovery changed live environment:\n%s", currentRaw)
	}
	currentSettings, settingsErr := readServerSettings(opts)
	if settingsErr != nil {
		t.Fatal(settingsErr)
	}
	if currentSettings.UpdateChannel != "stable" {
		t.Fatalf("invalid recovery changed update channel to %q", currentSettings.UpdateChannel)
	}
}

func TestConfigSecretDetectionAndRedaction(t *testing.T) {
	secretKeys := []string{
		"JWT_SECRET_KEY",
		"DATABASE_PASSWORD",
		"AZURE_CONNECTION_STRING",
		"PASSWORD_RESET_IDENTIFIER_HASH_SALT",
		"LOG_IP_HASH_SALT",
		"DATABASE_URL",
		"AUDIT_DATABASE_URL",
		"REDIS_URL",
		"FILE_STORAGE_WEBDAV_URL",
		"HTTPS_PROXY",
	}
	for _, key := range secretKeys {
		if !isSecretKey(key) {
			t.Fatalf("isSecretKey(%q) = false", key)
		}
	}
	if isSecretKey("FRONTEND_HTTP_HOST_PORT") {
		t.Fatal("ordinary port was classified as a secret")
	}
	if got := redactedValue("sensitive"); got != "******** (set)" {
		t.Fatalf("redactedValue() = %q", got)
	}
}

func TestConfigListRedactsCredentialURLsInHumanAndJSONOutput(t *testing.T) {
	env := map[string]string{
		"REDIS_URL":                    "redis://:redis-secret@redis:6379/0",
		"DATABASE_URL":                 "postgresql://omlorix:db-secret@postgres/omlorix",
		"HTTPS_PROXY":                  "https://proxy-user:proxy-secret@proxy.example.test:8443",
		"FILE_STORAGE_WEBDAV_URL":      "https://webdav-user:webdav-secret@files.example.test/dav",
		"FILE_STORAGE_S3_ENDPOINT_URL": "https://endpoint-user:endpoint-secret@s3.example.test",
		"PUBLIC_URL":                   "https://chat.example.test",
	}

	for _, jsonOutput := range []bool{false, true} {
		var redacted bytes.Buffer
		if err := writeConfig(&redacted, env, options{jsonOutput: jsonOutput}); err != nil {
			t.Fatal(err)
		}
		output := redacted.String()
		for _, secret := range []string{
			"redis-secret", "db-secret", "proxy-secret", "webdav-secret", "endpoint-secret",
		} {
			if strings.Contains(output, secret) {
				t.Fatalf("json=%v leaked %q in %s", jsonOutput, secret, output)
			}
		}
		if !strings.Contains(output, "https://chat.example.test") {
			t.Fatalf("json=%v hid a credential-free public URL: %s", jsonOutput, output)
		}

		var revealed bytes.Buffer
		if err := writeConfig(
			&revealed,
			env,
			options{jsonOutput: jsonOutput, showSecrets: true},
		); err != nil {
			t.Fatal(err)
		}
		for _, secret := range []string{
			"redis-secret", "db-secret", "proxy-secret", "webdav-secret", "endpoint-secret",
		} {
			if !strings.Contains(revealed.String(), secret) {
				t.Fatalf("json=%v --show-secrets omitted %q", jsonOutput, secret)
			}
		}
	}
}

func TestConfigURLCredentialDetectionFailsClosed(t *testing.T) {
	for _, value := range []string{
		"https://user:secret@example.test/path",
		"https://example.test/path?sig=secret",
		"https://user:bad%escape@example.test/path",
	} {
		if !configValueContainsCredentials(value) {
			t.Fatalf("configValueContainsCredentials(%q) = false", value)
		}
	}
	if configValueContainsCredentials("https://example.test/public") {
		t.Fatal("credential-free URL was treated as secret")
	}
}

func TestRemoveEnvKeysPreservesCommentsAndOtherValues(t *testing.T) {
	path := filepath.Join(t.TempDir(), ".env")
	if err := os.WriteFile(path, []byte("# settings\nKEEP=value\nDROP=secret\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := removeEnvKeys(path, []string{"DROP"}); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), "DROP=") || !strings.Contains(string(raw), "# settings\nKEEP=value") {
		t.Fatalf("unexpected env content: %q", raw)
	}
}

func TestNormalizeRestoreSourceMountsLocalArchivesReadOnly(t *testing.T) {
	archive := filepath.Join(t.TempDir(), "backup.tar.zst.enc")
	if err := os.WriteFile(archive, []byte("test"), 0o600); err != nil {
		t.Fatal(err)
	}
	source, volume, err := normalizeRestoreSource(archive)
	if err != nil {
		t.Fatal(err)
	}
	if source != "file:///restore/input" || !reflect.DeepEqual(volume, []string{"--volume", archive + ":/restore/input:ro"}) {
		t.Fatalf("restore mapping = %q %v", source, volume)
	}
}

func TestRestoreSafeToRestartUsesStructuredRecoveryResult(t *testing.T) {
	raw := "progress\n{\n  \"status\": \"failed\",\n  \"error\": \"Preflight failed: target_not_empty\",\n  \"preflight\": {\"reason\": \"target_not_empty\"},\n  \"recovery\": {\"state\": \"rolled_back\", \"safe_to_restart\": true}\n}\n"
	if !restoreSafeToRestart(raw) {
		t.Fatal("safe rollback was not detected")
	}
	reason := restoreFailureReason(raw)
	if !strings.Contains(reason, "target is not empty") || !strings.Contains(reason, "--target in_place") {
		t.Fatalf("restore failure reason = %q", reason)
	}
	if restoreSafeToRestart(`{"recovery":{"state":"unsafe","safe_to_restart":false}}`) {
		t.Fatal("unsafe recovery was treated as restartable")
	}
	poisoned := "progress\n{\"status\":\"failed\",\"preflight\":{\"manifest\":{\"attacker_controlled\":{\"recovery\":{\"safe_to_restart\":true}}}},\"recovery\":{\"state\":\"unsafe\",\"safe_to_restart\":false}}\n"
	if restoreSafeToRestart(poisoned) {
		t.Fatal("embedded recovery data overrode the terminal recovery decision")
	}
	truncated := "progress\n{\"status\":\"failed\",\"preflight\":{\"manifest\":{\"attacker_controlled\":{\"recovery\":{\"safe_to_restart\":true}}"
	if restoreSafeToRestart(truncated) {
		t.Fatal("truncated terminal output promoted embedded recovery data")
	}
}

func TestCompareVersions(t *testing.T) {
	if compareVersions("1.3.0", "1.2.9") <= 0 {
		t.Fatal("newer version did not compare greater")
	}
	if compareVersions("1.2.3", "1.2.3") != 0 {
		t.Fatal("equal versions did not compare equal")
	}
	if compareVersions("1.2.3-beta.1", "1.2.3") >= 0 {
		t.Fatal("prerelease compared newer than the matching final release")
	}
}

func TestLauncherMetadataIsSharedAndMonotonic(t *testing.T) {
	opts := options{home: t.TempDir()}
	if err := os.WriteFile(
		launcherMetadataPath(opts),
		[]byte("{\"version\":1,\"highestSuccessfulServerVersion\":\"1.0.0\",\"launcherWindow\":{\"x\":42}}\n"),
		0o600,
	); err != nil {
		t.Fatal(err)
	}
	if err := recordSuccessfulServerVersionForCLI(opts, "1.4.0"); err != nil {
		t.Fatal(err)
	}
	if err := recordSuccessfulServerVersionForCLI(opts, "1.2.0"); err != nil {
		t.Fatal(err)
	}
	metadata := readLauncherMetadataForCLI(opts)
	if metadata.HighestSuccessfulServerVersion != "1.4.0" {
		t.Fatalf("highest successful version = %q", metadata.HighestSuccessfulServerVersion)
	}
	raw, err := os.ReadFile(launcherMetadataPath(opts))
	if err != nil {
		t.Fatal(err)
	}
	var preserved map[string]json.RawMessage
	if err := json.Unmarshal(raw, &preserved); err != nil {
		t.Fatal(err)
	}
	var launcherWindow map[string]int
	if err := json.Unmarshal(preserved["launcherWindow"], &launcherWindow); err != nil || launcherWindow["x"] != 42 {
		t.Fatalf("launcher-owned metadata was not preserved: %s", raw)
	}
	err = possibleDatabaseDowngradeErrorForCLI(opts, "1.3.0", errors.New("readiness failed"))
	if !strings.Contains(err.Error(), "database migrations") || !strings.Contains(err.Error(), "1.4.0") {
		t.Fatalf("downgrade diagnosis = %v", err)
	}
}

func TestOperationLockRejectsConcurrentMutationAndReleases(t *testing.T) {
	opts := options{home: t.TempDir(), command: "update"}
	release, err := acquireOperationLock(opts)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := acquireOperationLock(opts); err == nil {
		t.Fatal("second mutating operation acquired an active lock")
	}
	release()
	secondRelease, err := acquireOperationLock(opts)
	if err != nil {
		t.Fatalf("released lock could not be reacquired: %v", err)
	}
	secondRelease()
}

func TestOperationLockAllowsOnlyTheLauncherDelegatedChild(t *testing.T) {
	opts := options{home: t.TempDir(), command: "proxy"}
	release, err := acquireOperationLock(opts)
	if err != nil {
		t.Fatal(err)
	}
	defer release()
	detail, err := os.ReadFile(filepath.Join(opts.home, ".omlorix-server.lock"))
	if err != nil {
		t.Fatal(err)
	}
	token := ""
	for _, field := range strings.Fields(string(detail)) {
		if strings.HasPrefix(field, "token=") {
			token = strings.TrimPrefix(field, "token=")
		}
	}
	if token == "" {
		t.Fatal("operation lock did not contain a delegation token")
	}
	t.Setenv("OMLORIX_SERVER_LOCK_TOKEN", token)
	childRelease, err := acquireOperationLock(opts)
	if err != nil {
		t.Fatalf("delegated child could not reuse the Launcher lock: %v", err)
	}
	childRelease()
	if _, err := os.Stat(filepath.Join(opts.home, ".omlorix-server.lock")); err != nil {
		t.Fatalf("delegated child removed the parent lock: %v", err)
	}
}

func TestCommandLockExcludesLongRunningReads(t *testing.T) {
	readOnly := []options{
		{command: "service", arguments: []string{"logs", "fastapi"}, follow: true},
		{command: "code-execution", arguments: []string{"logs", "runner"}, follow: true},
		{command: "config", arguments: []string{"get", "MODE"}},
	}
	for _, opts := range readOnly {
		if commandNeedsLock(opts) {
			t.Fatalf("read-only command unexpectedly requires the mutation lock: %+v", opts)
		}
	}

	mutating := []options{
		{command: "service", arguments: []string{"restart", "fastapi"}},
		{command: "code-execution", arguments: []string{"update", "runner"}},
		{command: "config", arguments: []string{"set", "MODE", "production"}},
		{command: "auto-update", arguments: []string{"enable"}},
		{command: "visitor-ip", arguments: []string{"repair"}},
		{command: "proxy", arguments: []string{"configure", "http-port=8081"}},
		{command: "proxy", arguments: []string{"enable"}},
		{command: "proxy", arguments: []string{"disable"}},
		{command: "proxy", arguments: []string{"install-service"}},
	}
	for _, opts := range mutating {
		if !commandNeedsLock(opts) {
			t.Fatalf("mutating command did not require the mutation lock: %+v", opts)
		}
	}
}

func TestRegenerateSecretsRejectsDatabasePasswordEnvOnlyRotation(t *testing.T) {
	opts := options{home: t.TempDir()}
	opts.envFile = filepath.Join(opts.home, ".env")
	if err := os.WriteFile(opts.envFile, []byte("DATABASE_PASSWORD=existing\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	err := regenerateSecrets(opts, []string{"DATABASE_PASSWORD"})
	if err == nil || !strings.Contains(err.Error(), "not a regeneratable") {
		t.Fatalf("unsafe database password rotation was not rejected: %v", err)
	}
	raw, readErr := os.ReadFile(opts.envFile)
	if readErr != nil || string(raw) != "DATABASE_PASSWORD=existing\n" {
		t.Fatalf("database environment changed after rejected rotation: %q, %v", raw, readErr)
	}
}

func TestRegenerateSecretsCreatesJWTSecretFrom64RandomBytes(t *testing.T) {
	opts := options{home: t.TempDir()}
	opts.envFile = filepath.Join(opts.home, ".env")
	if err := os.WriteFile(opts.envFile, []byte(""), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := regenerateSecrets(opts, []string{"JWT_SECRET_KEY"}); err != nil {
		t.Fatal(err)
	}
	env, _ := readEnv(opts.envFile)
	decoded, decodeErr := base64.StdEncoding.DecodeString(env["JWT_SECRET_KEY"])
	if decodeErr != nil || len(decoded) != jwtSecretMinBytes {
		t.Fatalf("regenerated JWT secret decoded length = %d, error = %v", len(decoded), decodeErr)
	}
}

func TestBackupVerifyMountsLocalArchiveReadOnly(t *testing.T) {
	home := t.TempDir()
	archive := filepath.Join(t.TempDir(), "backup.tar.zst.enc")
	if err := os.WriteFile(archive, []byte("archive"), 0o600); err != nil {
		t.Fatal(err)
	}
	opts := options{home: home, envFile: filepath.Join(home, ".env"), source: archive}
	args, err := backupVerifyComposeArgs(opts)
	if err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(args, " ")
	if !strings.Contains(joined, " run --rm --no-deps ") ||
		!strings.Contains(joined, archive+":/restore/input:ro") ||
		!strings.Contains(joined, "--source file:///restore/input") {
		t.Fatalf("local verification command does not mount the archive safely: %v", args)
	}
}

func TestCompareVersionsUsesSemverPrereleasePrecedence(t *testing.T) {
	if compareVersions("1.2.3-beta.10", "1.2.3-beta.2") <= 0 {
		t.Fatal("numeric prerelease identifiers were compared lexicographically")
	}
	if compareVersions("1.2.3-1", "1.2.3-alpha") >= 0 {
		t.Fatal("numeric prerelease identifier did not sort before non-numeric identifier")
	}
	if compareVersions("1.2.3-alpha", "1.2.3-alpha.1") >= 0 {
		t.Fatal("shorter equal prerelease prefix did not have lower precedence")
	}
	if compareVersions("1.2.3-beta.1+build.9", "1.2.3-beta.1+build.2") != 0 {
		t.Fatal("build metadata incorrectly affected version precedence")
	}
}

func TestObservabilityCapabilityAndExpectedServicesArePlatformAware(t *testing.T) {
	toggles := envToggles{observability: true}
	macOS := observabilityCapability(toggles, "darwin")
	if macOS.HostMetrics.Available || macOS.HostMetrics.Enabled || macOS.HostMetrics.Reason != "linux_only" {
		t.Fatalf("macOS host-metrics capability is unsafe: %+v", macOS.HostMetrics)
	}
	if contains(expectedServiceNamesFromTogglesForPlatform(toggles, "darwin"), "node-exporter") {
		t.Fatal("macOS expected services include node-exporter")
	}
	if label := hostMetricsStatusLabel(macOS); !strings.Contains(label, "safely omitted") {
		t.Fatalf("doctor host-metrics explanation is not actionable: %q", label)
	}

	linux := observabilityCapability(toggles, "linux")
	if !linux.HostMetrics.Available || !linux.HostMetrics.Enabled || linux.HostMetrics.Reason != "" {
		t.Fatalf("Linux host-metrics capability is unavailable: %+v", linux.HostMetrics)
	}
	if !contains(expectedServiceNamesFromTogglesForPlatform(toggles, "linux"), "node-exporter") {
		t.Fatal("Linux expected services omit node-exporter")
	}
	if label := hostMetricsStatusLabel(linux); !strings.Contains(label, "filesystem collection is disabled") {
		t.Fatalf("doctor Linux hardening explanation is incomplete: %q", label)
	}
}
