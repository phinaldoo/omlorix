package main

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"time"
)

// serviceStatus is the stable, credential-free shape emitted by status and
// services. Keep it deliberately smaller than Docker Compose's evolving JSON.
type serviceStatus struct {
	Name     string `json:"name"`
	State    string `json:"state"`
	Health   string `json:"health,omitempty"`
	Status   string `json:"status,omitempty"`
	Image    string `json:"image,omitempty"`
	Ports    string `json:"ports,omitempty"`
	Expected bool   `json:"expected"`
	Missing  bool   `json:"missing"`
}

type dockerStatus struct {
	Installed bool   `json:"installed"`
	Running   bool   `json:"running"`
	Compose   bool   `json:"compose"`
	Version   string `json:"version,omitempty"`
	Error     string `json:"error,omitempty"`
}

type endpointStatus struct {
	URL        string `json:"url"`
	ReadyURL   string `json:"ready_url"`
	Checked    bool   `json:"checked"`
	Reachable  bool   `json:"reachable"`
	HTTPStatus int    `json:"http_status,omitempty"`
	Error      string `json:"error,omitempty"`
}

type stackStatus struct {
	Running       int             `json:"running"`
	Total         int             `json:"total"`
	Healthy       bool            `json:"healthy"`
	Services      []serviceStatus `json:"services"`
	Error         string          `json:"error,omitempty"`
	Missing       int             `json:"missing"`
	NotRunning    int             `json:"not_running"`
	ExpectedKnown bool            `json:"expected_known"`
	HealthIssues  int             `json:"health_issues"`
}

type hostMetricsStatus struct {
	Available bool   `json:"available"`
	Enabled   bool   `json:"enabled"`
	Reason    string `json:"reason,omitempty"`
}

type observabilityStatus struct {
	Enabled     bool              `json:"enabled"`
	HostMetrics hostMetricsStatus `json:"host_metrics"`
}

type serverStatus struct {
	CLI           string              `json:"cli_version"`
	Home          string              `json:"home"`
	Environment   string              `json:"environment"`
	Initialized   bool                `json:"initialized"`
	Configuration string              `json:"configuration"`
	Docker        dockerStatus        `json:"docker"`
	Stack         stackStatus         `json:"stack"`
	Observability observabilityStatus `json:"observability"`
	Endpoint      endpointStatus      `json:"endpoint"`
	Settings      map[string]bool     `json:"settings"`
	MissingFiles  []string            `json:"missing_files,omitempty"`
	VisitorIP     visitorIPStatus     `json:"visitor_ip"`
	Proxy         proxyStatus         `json:"proxy"`
}

type cliError struct {
	code    string
	message string
	cause   error
}

func (err *cliError) Error() string {
	if err.cause != nil {
		return err.cause.Error()
	}
	return err.message
}

func (err *cliError) Unwrap() error {
	return err.cause
}

type cliErrorResponse struct {
	OK    bool            `json:"ok"`
	Error cliErrorDetails `json:"error"`
}

type cliErrorDetails struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

func newCLIError(code string, message string, cause error) error {
	return &cliError{code: code, message: message, cause: cause}
}

func invalidArgumentsError(cause error) error {
	return newCLIError("invalid_arguments", "The command arguments are invalid.", cause)
}

func structuredCLIError(err error) cliErrorDetails {
	var typed *cliError
	if errors.As(err, &typed) {
		return cliErrorDetails{Code: typed.code, Message: typed.message}
	}
	switch strings.TrimSpace(err.Error()) {
	case "Docker is not installed or is not available on PATH":
		return cliErrorDetails{Code: "docker_not_available", Message: "Docker is not available."}
	case "Docker is installed but the engine is not running":
		return cliErrorDetails{Code: "docker_not_running", Message: "The Docker engine is not running."}
	case "Docker Compose is not available":
		return cliErrorDetails{Code: "docker_compose_not_available", Message: "Docker Compose is not available."}
	default:
		return cliErrorDetails{Code: "command_failed", Message: "The command failed."}
	}
}

func writeCLIErrorJSON(writer io.Writer, err error) error {
	return writeJSON(writer, cliErrorResponse{OK: false, Error: structuredCLIError(err)})
}

func classifyBackendCommandError(stdout string, stderr string, cause error) error {
	output := strings.TrimSpace(stderr + "\n" + stdout)
	if match := serviceNotRunningPattern.FindStringSubmatch(output); len(match) == 2 {
		service := strings.ToLower(match[1])
		return newCLIError(
			"service_not_running",
			fmt.Sprintf("The %s service is not running.", service),
			cause,
		)
	}
	lower := strings.ToLower(output)
	switch {
	case strings.Contains(lower, "backup job not found"):
		return newCLIError("not_found", "The requested backup job was not found.", cause)
	case strings.Contains(lower, "backup job is not complete"):
		return newCLIError("backup_not_complete", "The requested backup job is not complete.", cause)
	case strings.Contains(lower, "catalog checksum"), strings.Contains(lower, "checksum and size"):
		return newCLIError("backup_integrity_failed", "The backup artifact failed its catalog integrity check.", cause)
	case strings.Contains(lower, "cannot connect to the docker daemon"),
		strings.Contains(lower, "is the docker daemon running"),
		strings.Contains(lower, "error during connect"):
		return newCLIError("docker_not_running", "The Docker engine is not running.", cause)
	case strings.Contains(lower, "permission denied") && strings.Contains(lower, "docker"):
		return newCLIError("docker_permission_denied", "Docker access was denied.", cause)
	case strings.Contains(lower, "authentication required"),
		strings.Contains(lower, "authentication failed"),
		strings.Contains(lower, "unauthorized"):
		return newCLIError("authentication_failed", "Backend authentication failed.", cause)
	case strings.Contains(lower, "connection refused"),
		strings.Contains(lower, "connection reset"),
		strings.Contains(lower, "network is unreachable"),
		strings.Contains(lower, "i/o timeout"):
		return newCLIError("transport_error", "The backend could not be reached.", cause)
	case strings.Contains(lower, "usage:") && strings.Contains(lower, "error:"):
		return newCLIError("invalid_arguments", "The backend rejected the command arguments.", cause)
	default:
		return newCLIError("backend_command_failed", "The backend command failed.", cause)
	}
}

type backendCommandCapture func(string, []string, string) (string, string, error)

func runBackendCommand(opts options, args []string) error {
	if !opts.jsonOutput {
		return runDocker(args, opts.home)
	}
	return runJSONBackendCommand(opts, args, os.Stdout, runCaptureStreams)
}

func runJSONBackendCommand(
	opts options,
	args []string,
	writer io.Writer,
	capture backendCommandCapture,
) error {
	stdout, stderr, err := capture(dockerExecutable(), args, opts.home)
	if err != nil {
		return classifyBackendCommandError(stdout, stderr, err)
	}
	decoder := json.NewDecoder(strings.NewReader(stdout))
	decoder.UseNumber()
	var payload any
	if err := decoder.Decode(&payload); err != nil {
		return newCLIError("invalid_backend_response", "Omlorix returned an invalid JSON response.", err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return newCLIError("invalid_backend_response", "Omlorix returned an invalid JSON response.", err)
	}
	return writeJSON(writer, payload)
}

func mergedEnvKeySet(sets ...map[string]bool) map[string]bool {
	merged := map[string]bool{}
	for _, set := range sets {
		for key := range set {
			merged[key] = true
		}
	}
	return merged
}

var (
	envKeyPattern            = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)
	serviceNamePattern       = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]*$`)
	serviceNotRunningPattern = regexp.MustCompile(`(?i)service\s+["']?([a-z0-9_.-]+)["']?\s+is not running`)
	secretKeyPattern         = regexp.MustCompile(`(?i)(SECRET|PASSWORD|PASSPHRASE|TOKEN|CREDENTIAL|CONNECTION_STRING|ENCRYPTION_KEY|PRIVATE_KEY|ACCESS_KEY|API_KEY|CLIENT_SECRET|AUTHORIZATION|_KEY$|_SALT$)`)
	// Connection URLs can carry a password even when their key does not contain
	// the word PASSWORD.  These well-known classes are always secret; arbitrary
	// URL keys are additionally inspected by configValueContainsCredentials.
	credentialURLKeyPattern = regexp.MustCompile(`(?i)((^|_)(DATABASE|REDIS|WEBDAV)(_[A-Z0-9]+)*_(URL|URI)$|^(HTTP|HTTPS|ALL)_PROXY$)`)
	credentialQueryPattern  = regexp.MustCompile(`(?i)^(sig|signature|token|access_token|api_key|key|password|passwd|credential)$`)

	// Retired values are launcher-hidden so neither direct configuration nor an
	// imported legacy file can restore them after startup migration removes them.
	retiredEnvKeys = map[string]bool{
		"OMLORIX_GITHUB_TOKEN": true,
	}

	// Keep this ownership boundary in exact parity with
	// electron/server-manager.js:LAUNCHER_HIDDEN_ENV_KEYS. Neither ordinary
	// configuration-import mode may replace installation identity, proxy
	// credentials, or trust settings. Complete recovery restores active
	// launcher-owned values, while retired values stay excluded from every mode.
	launcherHiddenEnvKeys = mergedEnvKeySet(retiredEnvKeys, map[string]bool{
		"OMLORIX_INSTALLATION_ID":                       true,
		"OMLORIX_ALLOW_PROJECT_ADOPTION":                true,
		"OMLORIX_UPDATE_CHANNEL":                        true,
		"OMLORIX_BACKEND_IMAGE_REPOSITORY":              true,
		"OMLORIX_FRONTEND_IMAGE_REPOSITORY":             true,
		"FILE_SCANNER_COMMAND":                         true,
		"DATABASE_MIGRATION_HOST_OVERRIDE":             true,
		"DATABASE_MIGRATION_PORT_OVERRIDE":             true,
		"FRONTEND_USE_HTTPS":                           true,
		"FRONTEND_SSL_CERT_HOST_PATH":                  true,
		"FRONTEND_SSL_CERT_PATH":                       true,
		"FRONTEND_SSL_KEY_PATH":                        true,
		"FRONTEND_SSL_CHAIN_PATH":                      true,
		"FRONTEND_TRUST_PROXY_HEADERS":                 true,
		"FRONTEND_TRUSTED_UPSTREAMS":                   true,
		"OMLORIX_LAUNCHER_PROXY_SECRET":                 true,
		"OMLORIX_LAUNCHER_PROXY_ENABLED":                true,
		"OMLORIX_LAUNCHER_PROXY_AUTOSTART":              true,
		"OMLORIX_LAUNCHER_PROXY_BIND":                   true,
		"OMLORIX_LAUNCHER_PROXY_PUBLIC_HOSTNAME":        true,
		"OMLORIX_LAUNCHER_PROXY_HTTP_PORT":              true,
		"OMLORIX_LAUNCHER_PROXY_HTTPS_ENABLED":          true,
		"OMLORIX_LAUNCHER_PROXY_HTTPS_PORT":             true,
		"OMLORIX_LAUNCHER_PROXY_REDIRECT_HTTP_TO_HTTPS": true,
		"OMLORIX_LAUNCHER_PROXY_TLS_CERT_PATH":          true,
		"OMLORIX_LAUNCHER_PROXY_TLS_KEY_PATH":           true,
		"OMLORIX_LAUNCHER_PROXY_TLS_CA_PATH":            true,
		"OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE":     true,
		"CADDY_HTTP_HOST_PORT":                         true,
		"CADDY_HTTPS_HOST_PORT":                        true,
	})
)

func commandVersion(opts options) error {
	if len(opts.arguments) != 0 {
		return errors.New("version does not accept positional arguments")
	}
	if opts.jsonOutput {
		return printJSON(map[string]string{"version": cliVersion})
	}
	fmt.Printf("Omlorix Server CLI %s\n", cliVersion)
	return nil
}

func commandNeedsLock(opts options) bool {
	// Long-running reads such as `logs --follow` must never monopolize the
	// mutation lock. Resolve compound commands by subcommand so status, list,
	// logs, and connection-output workflows can run alongside ordinary server
	// administration without weakening serialization for actual writes.
	switch opts.command {
	case "init", "start", "stop", "restart", "update", "restore":
		return true
	case "backup":
		action := firstArgument(opts.arguments)
		return action == "" || action == "create" || action == "download"
	case "service":
		return firstArgument(opts.arguments) != "logs"
	case "config":
		return map[string]bool{"set": true, "unset": true, "edit": true, "import": true, "replace": true}[firstArgument(opts.arguments)]
	case "update-channel":
		return len(opts.arguments) == 1
	case "secrets":
		return map[string]bool{
			"regenerate":     true,
			"import":         true,
			"export":         true,
			"save-now":       true,
			"disable-backup": true,
		}[firstArgument(opts.arguments)]
	case "code-execution":
		return map[string]bool{
			"create":  true,
			"edit":    true,
			"start":   true,
			"stop":    true,
			"restart": true,
			"update":  true,
			"delete":  true,
		}[firstArgument(opts.arguments)]
	case "auto-update":
		// `run` acquires the lock inside runScheduledUpdate so it can record a
		// clean skipped result when another operation owns the server. The daemon
		// delegates to that same path for each maintenance window.
		return map[string]bool{"enable": true, "disable": true}[firstArgument(opts.arguments)]
	case "visitor-ip":
		return firstArgument(opts.arguments) == "repair"
	case "proxy":
		return map[string]bool{
			"configure": true, "enable": true, "disable": true, "start": true, "stop": true, "restart": true, "install-service": true,
			"uninstall-service": true, "refresh-service": true,
		}[firstArgument(opts.arguments)]
	case "storage":
		return map[string]bool{"probe": true, "migrate": true, "migrate-local": true}[firstArgument(opts.arguments)]
	default:
		return false
	}
}

func firstArgument(arguments []string) string {
	if len(arguments) == 0 {
		return ""
	}
	return strings.ToLower(strings.TrimSpace(arguments[0]))
}

// acquireOperationLock prevents two CLI processes from mutating the same
// Compose project or .env file concurrently. A very old lock is treated as a
// crashed process artifact; recent locks fail closed and retain diagnostic text.
func acquireOperationLock(opts options) (func(), error) {
	if err := os.MkdirAll(opts.home, 0o755); err != nil {
		return nil, err
	}
	path := filepath.Join(opts.home, ".omlorix-server.lock")
	open := func() (*os.File, error) {
		return os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	}
	file, err := open()
	if err != nil && errors.Is(err, os.ErrExist) {
		// Launcher-owned mutations may delegate one step to the bundled CLI (for
		// example native proxy service control). A random capability in the lock
		// file permits only that explicitly spawned child to reuse the parent lock.
		inheritedToken := strings.TrimSpace(os.Getenv("OMLORIX_SERVER_LOCK_TOKEN"))
		if inheritedToken != "" {
			detail, _ := os.ReadFile(path)
			for _, field := range strings.Fields(string(detail)) {
				if strings.TrimPrefix(field, "token=") == inheritedToken && strings.HasPrefix(field, "token=") {
					return func() {}, nil
				}
			}
		}
		if info, statErr := os.Stat(path); statErr == nil && time.Since(info.ModTime()) > 6*time.Hour {
			if removeErr := os.Remove(path); removeErr == nil {
				file, err = open()
			}
		}
	}
	if err != nil {
		detail, _ := os.ReadFile(path)
		return nil, fmt.Errorf("another Omlorix CLI operation is active%s", formatLockDetail(string(detail)))
	}
	_, _ = fmt.Fprintf(
		file,
		"pid=%d command=%s started=%s token=%s\n",
		os.Getpid(), opts.command, time.Now().UTC().Format(time.RFC3339), randomHex(32),
	)
	_ = file.Close()
	return func() { _ = os.Remove(path) }, nil
}

func formatLockDetail(detail string) string {
	fields := strings.Fields(detail)
	visible := fields[:0]
	for _, field := range fields {
		if !strings.HasPrefix(field, "token=") {
			visible = append(visible, field)
		}
	}
	if len(visible) == 0 {
		return ""
	}
	return " (" + strings.Join(visible, " ") + ")"
}

func commandStatus(opts options) error {
	if len(opts.arguments) != 0 {
		return errors.New("status does not accept positional arguments")
	}
	status := collectServerStatus(opts)
	if opts.jsonOutput {
		return printJSON(status)
	}
	printServerStatus(status)
	return nil
}

func runDoctor(opts options) error {
	status := collectServerStatus(opts)
	if opts.jsonOutput {
		if err := printJSON(status); err != nil {
			return err
		}
	} else {
		fmt.Printf("Omlorix Server CLI %s diagnostics\n\n", cliVersion)
		printServerStatus(status)
	}

	failures := doctorFailures(status)
	if len(failures) > 0 {
		return fmt.Errorf("doctor found %d blocking issue(s): %s", len(failures), strings.Join(failures, "; "))
	}
	return nil
}

func doctorFailures(status serverStatus) []string {
	var failures []string
	if !status.Docker.Installed {
		failures = append(failures, "Docker is not installed or is not on PATH")
	} else if !status.Docker.Running {
		failures = append(failures, "the Docker engine is not running")
	}
	if status.Docker.Installed && !status.Docker.Compose {
		failures = append(failures, "Docker Compose is unavailable")
	}
	if len(status.MissingFiles) > 0 {
		failures = append(failures, "required Compose files are missing")
	}
	if status.Configuration != "valid" {
		failures = append(failures, "the environment configuration is invalid")
	}
	if status.Stack.Total > 0 && !status.Stack.Healthy {
		failures = append(failures, "the configured Omlorix stack is not healthy")
	}
	if status.Stack.Error != "" {
		failures = append(failures, status.Stack.Error)
	}
	if status.VisitorIP.Pending {
		failures = append(failures, "proxy visitor-IP forwarding is not end-to-end verified")
	}
	return failures
}

func collectServerStatus(opts options) serverStatus {
	_, envErr := os.Stat(opts.envFile)
	initialized := envErr == nil
	env, _ := readEnv(opts.envFile)
	toggles := readEnvToggles(opts)
	status := serverStatus{
		CLI:           cliVersion,
		Home:          opts.home,
		Environment:   opts.envFile,
		Initialized:   initialized,
		Configuration: "valid",
		Settings: map[string]bool{
			"redis_enabled":    toggles.redisEnabled,
			"bundled_database": toggles.useBundledDB,
			"bundled_redis":    toggles.useBundledRedis,
			"pgbouncer":        toggles.usePgbouncer,
			"bundled_storage":  toggles.useBundledStorage,
			"observability":    toggles.observability,
		},
		MissingFiles:  missingRequiredFiles(opts),
		Observability: observabilityCapability(toggles, runtime.GOOS),
	}
	if err := validateProfileEnv(opts); err != nil {
		status.Configuration = err.Error()
	}

	status.Docker = inspectDocker(opts)
	if status.Docker.Running && status.Docker.Compose && len(status.MissingFiles) == 0 {
		if err := validateComposeOwnership(opts); err != nil {
			status.Stack = stackStatus{Services: []serviceStatus{}, Error: err.Error()}
		} else {
			status.Stack = inspectComposeStack(opts)
		}
	} else {
		status.Stack = stackStatus{Services: []serviceStatus{}}
	}

	endpointURL := resolveURL(opts, env)
	readyURL := strings.TrimRight(endpointURL, "/") + "/ready"
	status.Endpoint = endpointStatus{URL: endpointURL, ReadyURL: readyURL}
	if status.Stack.Error != "" {
		status.Endpoint.Error = status.Stack.Error
	} else if shouldProbeServerEndpoint(initialized, status.MissingFiles, status.Stack) {
		status.Endpoint.Checked = true
		if code, err := probeURL(readyURL); err == nil {
			status.Endpoint.HTTPStatus = code
			status.Endpoint.Reachable = code >= 200 && code < 300
		} else {
			status.Endpoint.Error = friendlyNetworkError(err)
		}
	}
	status.Stack.Healthy = serverStackHealthy(status.Stack, status.Endpoint)
	status.Proxy = collectProxyStatus(opts)
	status.VisitorIP = collectVisitorIPStatus(opts)
	return status
}

// shouldProbeServerEndpoint prevents an uninitialized or stopped home from
// claiming whatever unrelated process happens to answer on its configured
// port. A running frontend row is tied to this home's validated Compose
// project identity and is therefore the minimum safe attribution signal.
func shouldProbeServerEndpoint(initialized bool, missingFiles []string, stack stackStatus) bool {
	if !initialized || len(missingFiles) > 0 || stack.Error != "" {
		return false
	}
	for _, service := range stack.Services {
		if service.Expected && service.Name == "frontend" && !service.Missing && strings.EqualFold(service.State, "running") {
			return true
		}
	}
	return false
}

// serverStackHealthy is the single readiness contract shared by status,
// lifecycle waits, doctor, and automatic-update safeguards. A responsive
// frontend alone is not enough: every configured long-running service must be
// present, running, and past any starting or unhealthy state.
func serverStackHealthy(stack stackStatus, endpoint endpointStatus) bool {
	return stack.Total > 0 &&
		stack.Missing == 0 &&
		stack.Running == stack.Total &&
		stack.HealthIssues == 0 &&
		endpoint.Reachable
}

// inspectServerReadiness collects only the Compose and HTTP state needed for a
// lifecycle wait. It deliberately avoids the heavier proxy diagnostics in
// collectServerStatus while applying the exact same health predicate.
func inspectServerReadiness(opts options) (stackStatus, endpointStatus) {
	env, _, settingsErr := readManagedEnvironment(opts)
	endpointURL := resolveURL(opts, env)
	readyURL := strings.TrimRight(endpointURL, "/") + "/ready"
	stack := inspectComposeStack(opts)
	endpoint := endpointStatus{URL: endpointURL, ReadyURL: readyURL}
	if settingsErr != nil {
		endpoint.Error = settingsErr.Error()
		return stack, endpoint
	}
	if stack.Error != "" {
		endpoint.Error = stack.Error
		return stack, endpoint
	}
	endpoint.Checked = true
	if code, err := probeURL(readyURL); err == nil {
		endpoint.HTTPStatus = code
		endpoint.Reachable = code >= 200 && code < 300
	} else {
		endpoint.Error = friendlyNetworkError(err)
	}
	return stack, endpoint
}

// serverReadinessError turns the latest incomplete snapshot into a concise,
// actionable timeout reason without leaking Docker command details.
func serverReadinessError(stack stackStatus, endpoint endpointStatus) error {
	if stack.Error != "" {
		return fmt.Errorf("could not inspect the configured stack: %s", stack.Error)
	}
	if stack.Total == 0 {
		return errors.New("no configured long-running services were reported")
	}
	if stack.Missing > 0 {
		return fmt.Errorf("%d of %d configured services are missing", stack.Missing, stack.Total)
	}
	if stack.Running != stack.Total {
		return fmt.Errorf("%d of %d configured services are running", stack.Running, stack.Total)
	}
	if stack.HealthIssues > 0 {
		return fmt.Errorf("%d configured service(s) are still starting or unhealthy", stack.HealthIssues)
	}
	if endpoint.Error != "" {
		return fmt.Errorf("%s is not ready: %s", endpoint.ReadyURL, endpoint.Error)
	}
	if endpoint.HTTPStatus != 0 {
		return fmt.Errorf("%s returned HTTP %d", endpoint.ReadyURL, endpoint.HTTPStatus)
	}
	return fmt.Errorf("%s is not ready", endpoint.ReadyURL)
}

// waitForServerHealthy waits for the same full-stack condition emitted as
// stack.healthy by status. The injectable variant keeps timing tests fast and
// deterministic without requiring Docker.
func waitForServerHealthy(opts options, timeout time.Duration) error {
	return waitForServerHealthyWithInspector(opts, timeout, readinessInterval, inspectServerReadiness)
}

func waitForServerHealthyWithInspector(
	opts options,
	timeout time.Duration,
	interval time.Duration,
	inspect func(options) (stackStatus, endpointStatus),
) error {
	deadline := time.Now().Add(timeout)
	var lastErr error
	for {
		stack, endpoint := inspect(opts)
		if serverStackHealthy(stack, endpoint) {
			return nil
		}
		lastErr = serverReadinessError(stack, endpoint)
		if !time.Now().Before(deadline) {
			return lastErr
		}
		remaining := time.Until(deadline)
		pause := interval
		if pause <= 0 || pause > remaining {
			pause = remaining
		}
		time.Sleep(pause)
	}
}

func inspectDocker(opts options) dockerStatus {
	executable := dockerExecutable()
	version, err := runCapture(executable, []string{"--version"}, opts.home)
	if err != nil {
		return dockerStatus{Error: friendlyCommandError(err)}
	}
	result := dockerStatus{Installed: true, Version: strings.TrimSpace(firstLine(version))}
	if output, err := runCapture(executable, []string{"info"}, opts.home); err != nil {
		result.Error = firstNonBlank(strings.TrimSpace(firstLine(output)), friendlyCommandError(err))
		return result
	}
	result.Running = true
	if output, err := runCapture(executable, []string{"compose", "version"}, opts.home); err != nil {
		result.Error = firstNonBlank(strings.TrimSpace(firstLine(output)), friendlyCommandError(err))
		return result
	}
	result.Compose = true
	return result
}

func inspectComposeStack(opts options) stackStatus {
	expectedRaw, expectedErr := runCapture(dockerExecutable(), composeArgs(opts, "config", "--services"), opts.home)
	raw, err := runCapture(dockerExecutable(), composeArgs(opts, "ps", "--all", "--format", "json"), opts.home)
	if err != nil {
		return stackStatus{Services: []serviceStatus{}, Error: strings.TrimSpace(firstNonBlank(raw, friendlyCommandError(err)))}
	}
	services, parseErr := parseComposeServices(raw)
	if parseErr != nil {
		return stackStatus{Services: []serviceStatus{}, Error: parseErr.Error()}
	}
	expected := parseExpectedServiceNames(expectedRaw)
	expectedKnown := expectedErr == nil
	if !expectedKnown {
		expected = expectedServiceNamesFromToggles(readEnvToggles(opts))
	}
	services = mergeExpectedServices(expected, services)
	return summarizeStackServices(services, expectedKnown)
}

// summarizeStackServices is shared by status and unattended-update health.
// Keeping the pure aggregation separate also makes the one-shot denominator
// contract independently testable from Docker command execution.
func summarizeStackServices(services []serviceStatus, expectedKnown bool) stackStatus {
	result := stackStatus{Services: services, ExpectedKnown: expectedKnown}
	for _, service := range services {
		// Successful one-shot initialization jobs and other diagnostic runtime
		// rows remain visible, but only configured long-running services belong in
		// the readiness denominator.
		if !service.Expected {
			continue
		}
		result.Total++
		if strings.EqualFold(service.State, "running") {
			result.Running++
			health := strings.ToLower(firstNonBlank(service.Health, service.Status))
			if strings.Contains(health, "unhealthy") || strings.Contains(health, "starting") {
				result.HealthIssues++
			}
		}
		if service.Missing {
			result.Missing++
		}
	}
	result.NotRunning = result.Total - result.Running
	return result
}

func parseExpectedServiceNames(raw string) []string {
	ignored := map[string]bool{"migrate": true, "minio_init": true, "metrics_token": true}
	seen := map[string]bool{}
	var names []string
	for _, name := range strings.Fields(raw) {
		if ignored[name] || seen[name] {
			continue
		}
		seen[name] = true
		names = append(names, name)
	}
	return names
}

func linuxHostMetricsSupported(goos string) bool {
	return goos == "linux"
}

func observabilityCapability(toggles envToggles, goos string) observabilityStatus {
	available := linuxHostMetricsSupported(goos)
	reason := ""
	if !available {
		reason = "linux_only"
	}
	return observabilityStatus{
		Enabled: toggles.observability,
		HostMetrics: hostMetricsStatus{
			Available: available,
			Enabled:   toggles.observability && available,
			Reason:    reason,
		},
	}
}

func hostMetricsStatusLabel(status observabilityStatus) string {
	if !status.Enabled {
		return ""
	}
	if status.HostMetrics.Enabled {
		return "enabled (Linux node-exporter; host filesystem collection is disabled)"
	}
	return "unavailable (node-exporter is Linux-only and is safely omitted on this platform)"
}

func expectedServiceNamesFromToggles(toggles envToggles) []string {
	return expectedServiceNamesFromTogglesForPlatform(toggles, runtime.GOOS)
}

var dedicatedWorkerServiceNames = []string{
	"operations_worker",
	"generation_worker",
	"research_worker",
	"file_processing_worker",
	"account_lifecycle_worker",
	"maintenance_worker",
	"rendering_worker",
	"media_worker",
	"connector_worker",
	"audit_event_worker",
	"realtime_gateway",
}

var restoreInfrastructureServiceNames = map[string]bool{
	"postgres":          true,
	"redis":             true,
	"pgbouncer":         true,
	"minio":             true,
	"otel-collector":    true,
	"jaeger":            true,
	"prometheus":        true,
	"alertmanager":      true,
	"postgres-exporter": true,
	"redis-exporter":    true,
	"node-exporter":     true,
	"grafana":           true,
}

var dockerContainerIDPattern = regexp.MustCompile(`^[a-fA-F0-9]{12,64}$`)

const composeOneOffLabel = "com.docker.compose.oneoff"

func offlineApplicationServiceNames() []string {
	names := []string{"frontend", "email_worker"}
	names = append(names, dedicatedWorkerServiceNames...)
	return append(names, "automation_scheduler", "automation_worker", "fastapi")
}

func offlineMigrationDrainCommand() []string {
	// `down --remove-orphans` is intentionally broader than the known service
	// list. It also terminates writers left behind by a renamed or removed
	// Compose service before the schema compatibility boundary moves.
	return []string{"down", "--remove-orphans"}
}

func offlineMigrationResetCommand() []string {
	return []string{"rm", "-sf", "migrate"}
}

func offlineMigrationRunCommand() []string {
	return []string{"up", "-d", "--force-recreate", "migrate"}
}

func restoreApplicationContainerIDs(raw string) ([]string, error) {
	normalized := strings.TrimSpace(raw)
	if normalized == "" {
		return []string{}, nil
	}
	var rows []map[string]any
	if err := json.Unmarshal([]byte(normalized), &rows); err != nil {
		for _, line := range strings.Split(normalized, "\n") {
			line = strings.TrimSpace(line)
			if line == "" {
				continue
			}
			var row map[string]any
			if err := json.Unmarshal([]byte(line), &row); err != nil {
				return nil, errors.New("Docker Compose returned invalid container inventory JSON")
			}
			rows = append(rows, row)
		}
	}

	seen := map[string]bool{}
	ids := make([]string, 0, len(rows))
	for _, row := range rows {
		rawState, stateOK := row["State"].(string)
		if !stateOK || strings.TrimSpace(rawState) == "" {
			return nil, errors.New("Docker Compose returned an invalid container state")
		}
		state := strings.ToLower(strings.TrimSpace(rawState))
		if state != "running" && state != "restarting" && state != "paused" {
			continue
		}
		rawService, serviceOK := row["Service"].(string)
		if !serviceOK {
			return nil, errors.New("Docker Compose returned an invalid active container service")
		}
		service := strings.ToLower(strings.TrimSpace(rawService))
		if restoreInfrastructureServiceNames[service] {
			oneOff, err := composeContainerIsOneOff(row)
			if err != nil {
				return nil, err
			}
			if !oneOff {
				continue
			}
		}
		id, idOK := row["ID"].(string)
		id = strings.TrimSpace(id)
		if !idOK || !dockerContainerIDPattern.MatchString(id) {
			return nil, errors.New("Docker Compose returned an invalid active container ID")
		}
		if !seen[id] {
			seen[id] = true
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	return ids, nil
}

func composeContainerIsOneOff(row map[string]any) (bool, error) {
	rawLabels, ok := row["Labels"]
	if !ok || rawLabels == nil {
		return false, errors.New("Docker Compose omitted the one-off label for an active infrastructure container")
	}

	var rawValue any
	switch labels := rawLabels.(type) {
	case map[string]any:
		rawValue, ok = labels[composeOneOffLabel]
	case string:
		matches := 0
		for _, label := range strings.Split(labels, ",") {
			parts := strings.SplitN(label, "=", 2)
			if len(parts) != 2 || strings.TrimSpace(parts[0]) != composeOneOffLabel {
				continue
			}
			matches++
			rawValue = strings.TrimSpace(parts[1])
		}
		ok = matches == 1
	default:
		return false, errors.New("Docker Compose returned invalid labels for an active infrastructure container")
	}
	if !ok {
		return false, errors.New("Docker Compose omitted or duplicated the one-off label for an active infrastructure container")
	}

	switch value := rawValue.(type) {
	case bool:
		return value, nil
	case string:
		switch strings.ToLower(strings.TrimSpace(value)) {
		case "true":
			return true, nil
		case "false":
			return false, nil
		}
	}
	return false, errors.New("Docker Compose returned an invalid one-off label for an active infrastructure container")
}

func stopRemainingRestoreApplicationContainers(opts options) error {
	raw, err := runCapture(
		dockerExecutable(),
		composeArgs(opts, "ps", "--all", "--orphans", "--format", "json"),
		opts.home,
	)
	if err != nil {
		return fmt.Errorf("could not inventory Compose containers before restore: %w", err)
	}
	containerIDs, err := restoreApplicationContainerIDs(raw)
	if err != nil {
		return err
	}
	if len(containerIDs) == 0 {
		return nil
	}
	return runDocker(append([]string{"stop", "--time", "60"}, containerIDs...), opts.home)
}

func expectedServiceNamesFromTogglesForPlatform(toggles envToggles, goos string) []string {
	var names []string
	if toggles.useBundledDB {
		names = append(names, "postgres")
	}
	if toggles.redisEnabled && toggles.useBundledRedis {
		names = append(names, "redis")
	}
	if toggles.usePgbouncer {
		names = append(names, "pgbouncer")
	}
	if toggles.useBundledStorage {
		names = append(names, "minio")
	}
	if toggles.redisEnabled {
		names = append(names, "automation_scheduler", "automation_worker")
	}
	names = append(names, "email_worker")
	names = append(names, dedicatedWorkerServiceNames...)
	names = append(names, "fastapi", "frontend")
	if toggles.observability {
		names = append(names, "otel-collector", "jaeger", "prometheus", "alertmanager", "postgres-exporter")
		if toggles.redisEnabled {
			names = append(names, "redis-exporter")
		}
		if linuxHostMetricsSupported(goos) {
			names = append(names, "node-exporter")
		}
		names = append(names, "grafana")
	}
	return names
}

func mergeExpectedServices(expected []string, runtime []serviceStatus) []serviceStatus {
	byName := map[string]serviceStatus{}
	for _, service := range runtime {
		byName[service.Name] = service
	}
	var merged []serviceStatus
	seen := map[string]bool{}
	for _, name := range expected {
		service, ok := byName[name]
		if !ok {
			service = serviceStatus{Name: name, State: "not_created", Missing: true}
		}
		service.Expected = true
		merged = append(merged, service)
		seen[name] = true
	}
	for _, service := range runtime {
		if !seen[service.Name] {
			merged = append(merged, service)
		}
	}
	sort.Slice(merged, func(i, j int) bool { return merged[i].Name < merged[j].Name })
	return merged
}

func parseComposeServices(raw string) ([]serviceStatus, error) {
	normalized := strings.TrimSpace(raw)
	if normalized == "" {
		return []serviceStatus{}, nil
	}
	var rows []map[string]any
	var arrayRows []map[string]any
	if err := json.Unmarshal([]byte(normalized), &arrayRows); err == nil {
		rows = arrayRows
	} else {
		for _, line := range strings.Split(normalized, "\n") {
			line = strings.TrimSpace(line)
			if line == "" {
				continue
			}
			var row map[string]any
			if err := json.Unmarshal([]byte(line), &row); err != nil {
				return nil, fmt.Errorf("Docker Compose returned invalid service status JSON")
			}
			rows = append(rows, row)
		}
	}
	services := make([]serviceStatus, 0, len(rows))
	for _, row := range rows {
		service := serviceStatus{
			Name:   jsonString(row, "Service", "Name", "Names"),
			State:  jsonString(row, "State"),
			Health: jsonString(row, "Health"),
			Status: jsonString(row, "Status"),
			Image:  jsonString(row, "Image"),
			Ports:  jsonString(row, "Publishers", "Ports"),
		}
		if service.Name == "" {
			continue
		}
		services = append(services, service)
	}
	sort.Slice(services, func(i, j int) bool { return services[i].Name < services[j].Name })
	return services, nil
}

func jsonString(row map[string]any, keys ...string) string {
	for _, key := range keys {
		value, ok := row[key]
		if !ok || value == nil {
			continue
		}
		switch typed := value.(type) {
		case string:
			return typed
		default:
			encoded, _ := json.Marshal(typed)
			return string(encoded)
		}
	}
	return ""
}

func printServerStatus(status serverStatus) {
	fmt.Printf("Omlorix Server\n")
	fmt.Printf("  Home          %s\n", status.Home)
	fmt.Printf("  Environment   %s\n", status.Environment)
	fmt.Printf("  Initialized   %t\n", status.Initialized)
	fmt.Printf("  Configuration %s\n", status.Configuration)
	fmt.Printf("  Docker        %s\n", statusLabel(status.Docker.Running, status.Docker.Error))
	fmt.Printf("  Compose       %s\n", statusLabel(status.Docker.Compose, status.Docker.Error))
	if status.Stack.Total == 0 {
		fmt.Printf("  Stack         stopped\n")
	} else {
		fmt.Printf("  Stack         %d/%d services running\n", status.Stack.Running, status.Stack.Total)
	}
	if !status.Endpoint.Checked {
		reason := "the owned frontend is not running"
		if !status.Initialized {
			reason = "server home is not initialized"
		}
		fmt.Printf("  Endpoint      not checked (%s)\n", reason)
	} else if status.Endpoint.Reachable {
		fmt.Printf("  Endpoint      ready (%s)\n", status.Endpoint.URL)
	} else {
		fmt.Printf("  Endpoint      unavailable (%s)\n", status.Endpoint.URL)
	}
	if status.Proxy.Enabled {
		fmt.Printf("  Proxy         %s\n", map[bool]string{true: "running", false: "stopped"}[status.Proxy.Running])
	}
	fmt.Printf("  Visitor IPs   %s\n", map[bool]string{true: "verified", false: "not verified"}[status.VisitorIP.Ready])
	if label := hostMetricsStatusLabel(status.Observability); label != "" {
		fmt.Printf("  Host metrics  %s\n", label)
	}
	if len(status.MissingFiles) > 0 {
		fmt.Printf("  Missing files %s\n", strings.Join(status.MissingFiles, ", "))
	}
	if len(status.Stack.Services) > 0 {
		fmt.Println("\nServices")
		printServiceTable(status.Stack.Services)
	}
}

func statusLabel(ok bool, detail string) string {
	if ok {
		return "ready"
	}
	if strings.TrimSpace(detail) != "" {
		return "unavailable (" + detail + ")"
	}
	return "unavailable"
}

func commandServices(opts options) error {
	if err := ensureServerHome(opts); err != nil {
		return err
	}
	if err := ensureDockerReady(opts); err != nil {
		return err
	}
	stack := inspectComposeStack(opts)
	if stack.Error != "" {
		return errors.New(stack.Error)
	}
	if opts.jsonOutput {
		return printJSON(stack.Services)
	}
	if len(stack.Services) == 0 {
		fmt.Println("No Omlorix services have been created yet.")
		return nil
	}
	printServiceTable(stack.Services)
	return nil
}

func printServiceTable(services []serviceStatus) {
	fmt.Printf("  %-28s %-12s %-12s %s\n", "SERVICE", "STATE", "HEALTH", "STATUS")
	for _, service := range services {
		fmt.Printf("  %-28s %-12s %-12s %s\n", truncate(service.Name, 28), truncate(firstNonBlank(service.State, "unknown"), 12), truncate(firstNonBlank(service.Health, "-"), 12), service.Status)
	}
}

func commandService(opts options) error {
	if len(opts.arguments) != 2 {
		return errors.New("usage: omlorix-server service <start|stop|restart|logs> <name>")
	}
	action := strings.ToLower(opts.arguments[0])
	name := opts.arguments[1]
	if err := ensureServerHome(opts); err != nil {
		return err
	}
	if err := validateProfileEnv(opts); err != nil {
		return err
	}
	if err := ensureDockerReady(opts); err != nil {
		return err
	}
	if err := validateServiceName(opts, name); err != nil {
		return err
	}
	switch action {
	case "start":
		if err := runDocker(composeArgs(opts, "up", "-d", "--no-deps", name), opts.home); err != nil {
			return err
		}
		return waitForServiceIfRequested(opts, name)
	case "stop":
		return runDocker(composeArgs(opts, "stop", name), opts.home)
	case "restart":
		if err := runDocker(composeArgs(opts, "restart", name), opts.home); err != nil {
			return err
		}
		return waitForServiceIfRequested(opts, name)
	case "logs":
		lines, err := normalizeLogLineCount(opts.lines)
		if err != nil {
			return err
		}
		args := []string{"logs", "--tail", fmt.Sprint(lines), "--no-color"}
		if opts.follow {
			args = append(args, "--follow")
		}
		args = append(args, name)
		return runDocker(composeArgs(opts, args...), opts.home)
	default:
		return fmt.Errorf("unknown service action %q", action)
	}
}

// waitForServiceIfRequested gives service lifecycle commands the same
// readiness contract as full-stack start while preserving --no-wait for
// orchestration scripts that deliberately poll themselves.
func waitForServiceIfRequested(opts options, name string) error {
	if opts.noWait {
		return nil
	}
	deadline := time.Now().Add(opts.timeout)
	for {
		stack := inspectComposeStack(opts)
		if stack.Error != "" {
			return errors.New(stack.Error)
		}
		for _, service := range stack.Services {
			if service.Name != name {
				continue
			}
			health := strings.ToLower(firstNonBlank(service.Health, service.Status))
			if strings.EqualFold(service.State, "running") &&
				!strings.Contains(health, "starting") && !strings.Contains(health, "unhealthy") {
				fmt.Printf("Service %s is ready.\n", name)
				return nil
			}
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("service %s did not become ready within %s", name, opts.timeout)
		}
		time.Sleep(readinessInterval)
	}
}

func validateServiceName(opts options, name string) error {
	if !serviceNamePattern.MatchString(name) {
		return fmt.Errorf("invalid service name %q", name)
	}
	raw, err := runCapture(dockerExecutable(), composeArgs(opts, "config", "--services"), opts.home)
	if err != nil {
		return fmt.Errorf("could not validate service name: %w", err)
	}
	for _, candidate := range parseExpectedServiceNames(raw) {
		if candidate == name {
			return nil
		}
	}
	return fmt.Errorf("unknown or disabled Omlorix service %q", name)
}

func commandConfig(opts options) error {
	if len(opts.arguments) == 0 {
		return errors.New("usage: omlorix-server config <list|get|set|unset|path|edit|export|import> [arguments]")
	}
	if err := ensureServerHome(opts); err != nil {
		return err
	}
	action := strings.ToLower(opts.arguments[0])
	arguments := opts.arguments[1:]
	isManagedProxySetting := func(key string) bool {
		return isManagedProxySettingsEnvKey(key)
	}
	switch action {
	case "list":
		if len(arguments) != 0 {
			return errors.New("config list does not accept positional arguments")
		}
		env, _ := readEnv(opts.envFile)
		return printConfig(env, opts)
	case "get":
		if len(arguments) != 1 {
			return errors.New("usage: omlorix-server config get KEY")
		}
		if isManagedProxySetting(arguments[0]) {
			return fmt.Errorf("%s is host proxy state; use proxy settings", arguments[0])
		}
		env, _ := readEnv(opts.envFile)
		value, ok := env[arguments[0]]
		if !ok {
			return fmt.Errorf("configuration key %s is not set", arguments[0])
		}
		if shouldRedactConfigValue(arguments[0], value) && !opts.showSecrets {
			value = redactedValue(value)
		}
		if opts.jsonOutput {
			return printJSON(map[string]string{arguments[0]: value})
		}
		fmt.Println(value)
		return nil
	case "set":
		if len(arguments) != 2 {
			return errors.New("usage: omlorix-server config set KEY VALUE")
		}
		if launcherHiddenEnvKeys[arguments[0]] {
			if retiredEnvKeys[arguments[0]] {
				return fmt.Errorf("%s is retired and cannot be configured", arguments[0])
			}
			if isManagedProxySetting(arguments[0]) {
				return fmt.Errorf("%s is host proxy state; use proxy configure", arguments[0])
			}
			if arguments[0] == "OMLORIX_UPDATE_CHANNEL" {
				return errors.New("OMLORIX_UPDATE_CHANNEL is management state; use update-channel")
			}
			if arguments[0] == "OMLORIX_BACKEND_IMAGE_REPOSITORY" || arguments[0] == "OMLORIX_FRONTEND_IMAGE_REPOSITORY" {
				return fmt.Errorf("%s is retired; release image repositories are fixed", arguments[0])
			}
			if arguments[0] == "FILE_SCANNER_COMMAND" {
				return errors.New("FILE_SCANNER_COMMAND is retired; external file scanning is no longer supported")
			}
			return fmt.Errorf("%s is protected management state, not editable container configuration", arguments[0])
		}
		if err := validateConfigValue(opts, arguments[0], arguments[1]); err != nil {
			return err
		}
		if err := writeEnv(opts.envFile, map[string]string{arguments[0]: arguments[1]}); err != nil {
			return err
		}
		fmt.Printf("Updated %s. Restart Omlorix for container settings to take effect.\n", arguments[0])
		return nil
	case "unset":
		if len(arguments) != 1 || !envKeyPattern.MatchString(arguments[0]) {
			return errors.New("usage: omlorix-server config unset KEY")
		}
		if map[string]bool{
			"JWT_SECRET_KEY": true, "ENCRYPTION_KEY": true,
			"PASSWORD_RESET_IDENTIFIER_HASH_SALT": true,
			"LOG_IP_HASH_SALT":                    true,
		}[arguments[0]] {
			return fmt.Errorf("%s is required and cannot be unset", arguments[0])
		}
		if retiredEnvKeys[arguments[0]] {
			return removeEnvKeys(opts.envFile, []string{arguments[0]})
		}
		if launcherHiddenEnvKeys[arguments[0]] {
			return fmt.Errorf("%s is protected management state and cannot be unset through config", arguments[0])
		}
		return removeEnvKeys(opts.envFile, []string{arguments[0]})
	case "path":
		if len(arguments) != 0 {
			return errors.New("config path does not accept positional arguments")
		}
		fmt.Println(opts.envFile)
		return nil
	case "edit":
		if len(arguments) != 0 {
			return errors.New("config edit does not accept positional arguments")
		}
		return editConfig(opts)
	case "export":
		if len(arguments) != 1 {
			return errors.New("usage: omlorix-server config export FILE")
		}
		return exportConfig(opts, arguments[0])
	case "import":
		if len(arguments) != 1 {
			return errors.New("usage: omlorix-server config import FILE")
		}
		return importConfig(opts, arguments[0], false)
	case "replace":
		if len(arguments) != 1 {
			return errors.New("usage: omlorix-server config replace FILE")
		}
		return importConfig(opts, arguments[0], true)
	default:
		return fmt.Errorf("unknown config action %q", action)
	}
}

func commandSecrets(opts options) error {
	if len(opts.arguments) == 0 {
		return errors.New("usage: omlorix-server secrets <regenerate|export|import|backup-status|save-now|disable-backup> [arguments]")
	}
	if err := ensureServerHome(opts); err != nil {
		return err
	}
	action := strings.ToLower(opts.arguments[0])
	arguments := opts.arguments[1:]
	switch action {
	case "export":
		if len(arguments) != 1 {
			return errors.New("usage: omlorix-server secrets export FILE")
		}
		return configureAutomaticEnvBackup(opts, arguments[0])
	case "import":
		if len(arguments) != 1 {
			return errors.New("usage: omlorix-server secrets import FILE")
		}
		return restoreCompleteEnv(opts, arguments[0])
	case "backup-status":
		if len(arguments) != 0 {
			return errors.New("secrets backup-status does not accept positional arguments")
		}
		return commandAutomaticEnvBackupStatus(opts)
	case "save-now":
		if len(arguments) != 0 {
			return errors.New("secrets save-now does not accept positional arguments")
		}
		config, err := readAutomaticEnvBackupConfig(opts.envFile)
		if err != nil {
			return err
		}
		if config.Target == "" {
			return errors.New("choose an automatic .env backup location with secrets export first")
		}
		if err := refreshAutomaticEnvBackup(opts.envFile); err != nil {
			return err
		}
		fmt.Printf("Automatic .env backup refreshed at %s\n", config.Target)
		return nil
	case "disable-backup":
		if len(arguments) != 0 {
			return errors.New("secrets disable-backup does not accept positional arguments")
		}
		// Persist an explicit empty record so neither surface falls back to a
		// legacy Launcher target on the next status refresh.
		if err := writeAutomaticEnvBackupConfig(opts.envFile, automaticEnvBackupConfig{}); err != nil {
			return err
		}
		fmt.Println("Automatic .env backup disabled. The existing recovery file was retained.")
		return nil
	case "regenerate":
		return regenerateSecrets(opts, arguments)
	default:
		return fmt.Errorf("unknown secrets action %q", action)
	}
}

func regenerateSecrets(opts options, requested []string) error {
	allowed := map[string]bool{
		"JWT_SECRET_KEY":                       true,
		"ENCRYPTION_KEY":                       true,
		"PASSWORD_RESET_IDENTIFIER_HASH_SALT":  true,
		"LOG_IP_HASH_SALT":                     true,
		"BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE": true,
		"REDIS_PASSWORD":                       true,
		"MINIO_ROOT_USER":                      true,
		"MINIO_ROOT_PASSWORD":                  true,
		"GRAFANA_ADMIN_USER":                   true,
		"GRAFANA_ADMIN_PASSWORD":               true,
	}
	if len(requested) == 0 {
		requested = []string{
			"JWT_SECRET_KEY",
			"PASSWORD_RESET_IDENTIFIER_HASH_SALT",
			"LOG_IP_HASH_SALT",
			"BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE",
			"REDIS_PASSWORD",
		}
	}
	updates := make(map[string]string, len(requested)+1)
	for _, key := range requested {
		key = strings.ToUpper(strings.TrimSpace(key))
		if !allowed[key] {
			return fmt.Errorf("%s is not a regeneratable Omlorix secret", key)
		}
		if key == "ENCRYPTION_KEY" && opts.confirm != "ROTATE-ENCRYPTION-KEY" {
			return errors.New("rotating ENCRYPTION_KEY requires --confirm ROTATE-ENCRYPTION-KEY")
		}
		switch key {
		case "JWT_SECRET_KEY":
			updates[key] = randomSecret(jwtSecretMinBytes)
		case "ENCRYPTION_KEY":
			updates[key] = randomFernetKey()
		case "REDIS_PASSWORD":
			updates[key] = randomURLSecret(36)
		case "PASSWORD_RESET_IDENTIFIER_HASH_SALT", "LOG_IP_HASH_SALT":
			updates[key] = randomHex(32)
		case "MINIO_ROOT_USER", "GRAFANA_ADMIN_USER":
			updates[key] = "omlorix-" + randomToken(12)
		default:
			updates[key] = randomSecret(36)
		}
	}
	if password, ok := updates["REDIS_PASSWORD"]; ok {
		toggles := readEnvToggles(opts)
		if toggles.redisEnabled && toggles.useBundledRedis {
			env, _ := readEnv(opts.envFile)
			updates["REDIS_URL"] = defaultLocalRedisURL(env, password)
		}
	}
	if err := writeEnv(opts.envFile, updates); err != nil {
		return err
	}
	keys := make([]string, 0, len(requested))
	for _, key := range requested {
		keys = append(keys, strings.ToUpper(key))
	}
	sort.Strings(keys)
	fmt.Printf("Regenerated %s. Restart Omlorix and update any external clients or recovery copies that depend on them.\n", strings.Join(keys, ", "))
	if _, rotated := updates["JWT_SECRET_KEY"]; rotated {
		fmt.Println("Restarting with the new JWT_SECRET_KEY signs out every user.")
	}
	if _, rotated := updates["ENCRYPTION_KEY"]; rotated {
		fmt.Println("Rotating ENCRYPTION_KEY invalidates stored encrypted data.")
	}
	return nil
}

func printConfig(env map[string]string, opts options) error {
	return writeConfig(os.Stdout, env, opts)
}

func writeConfig(writer io.Writer, env map[string]string, opts options) error {
	// Keep human and JSON output on one rendering path so a future secret class
	// cannot accidentally be protected in only one format.
	keys := make([]string, 0, len(env))
	for key := range env {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	output := make(map[string]string, len(keys))
	for _, key := range keys {
		value := env[key]
		if shouldRedactConfigValue(key, value) && !opts.showSecrets {
			value = redactedValue(value)
		}
		output[key] = value
	}
	if opts.jsonOutput {
		encoder := json.NewEncoder(writer)
		encoder.SetIndent("", "  ")
		return encoder.Encode(output)
	}
	for _, key := range keys {
		if _, err := fmt.Fprintf(writer, "%s=%s\n", key, quoteEnv(output[key])); err != nil {
			return err
		}
	}
	return nil
}

func isSecretKey(key string) bool {
	return secretKeyPattern.MatchString(key) || credentialURLKeyPattern.MatchString(key)
}

func shouldRedactConfigValue(key, value string) bool {
	return isSecretKey(key) || configValueContainsCredentials(value)
}

func configValueContainsCredentials(value string) bool {
	trimmed := strings.TrimSpace(value)
	if trimmed == "" {
		return false
	}

	parsed, err := url.Parse(trimmed)
	if err == nil && parsed.Scheme != "" {
		if parsed.User != nil {
			return true
		}
		for queryKey := range parsed.Query() {
			if credentialQueryPattern.MatchString(queryKey) {
				return true
			}
		}
	}

	// url.Parse rejects malformed percent escapes.  Default-redacted output
	// must fail closed rather than print a credential-bearing authority merely
	// because an imported URL is malformed.
	schemeSeparator := strings.Index(trimmed, "://")
	if schemeSeparator < 1 {
		return false
	}
	authority := trimmed[schemeSeparator+3:]
	if boundary := strings.IndexAny(authority, "/?#"); boundary >= 0 {
		authority = authority[:boundary]
	}
	return strings.Contains(authority, "@")
}

func redactedValue(value string) string {
	if strings.TrimSpace(value) == "" {
		return "(not set)"
	}
	return "******** (set)"
}

func validateEnvAssignment(key, value string) error {
	if !envKeyPattern.MatchString(key) {
		return fmt.Errorf("invalid environment key %q", key)
	}
	if strings.ContainsAny(value, "\x00\r\n") {
		return fmt.Errorf("%s cannot contain NUL or newline characters", key)
	}
	return nil
}

func removeEnvKeys(path string, keys []string) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	remove := make(map[string]bool, len(keys))
	for _, key := range keys {
		remove[key] = true
	}
	nextRaw := removeEnvKeysFromContent(string(raw), remove)
	if err := atomicWriteFile(path, []byte(nextRaw), 0o600); err != nil {
		return err
	}
	refreshAutomaticEnvBackupAfterWrite(path)
	return nil
}

// removeEnvKeysFromContent removes active dotenv assignments while preserving
// comments, blank lines, and unrelated formatting. Imports use the in-memory
// form so protected values are stripped before any temporary or live write.
func removeEnvKeysFromContent(raw string, remove map[string]bool) string {
	lines := make([]string, 0)
	for _, line := range strings.Split(strings.TrimRight(raw, "\n"), "\n") {
		trimmed := strings.TrimSpace(line)
		keyPart, _, ok := strings.Cut(trimmed, "=")
		key := strings.TrimSpace(strings.TrimPrefix(keyPart, "export "))
		if ok && remove[key] {
			continue
		}
		lines = append(lines, line)
	}
	return strings.Join(lines, "\n") + "\n"
}

func editConfig(opts options) error {
	path := opts.envFile
	original, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	editor := firstNonBlank(os.Getenv("VISUAL"), os.Getenv("EDITOR"))
	if editor == "" {
		if runtime.GOOS == "windows" {
			editor = "notepad"
		} else {
			editor = "vi"
		}
	}
	parts := strings.Fields(editor)
	if len(parts) == 0 {
		return errors.New("VISUAL or EDITOR is empty")
	}
	cmd := exec.Command(parts[0], append(parts[1:], path)...)
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return err
	}
	edited, err := os.ReadFile(path)
	if err == nil {
		edited = []byte(removeEnvKeysFromContent(string(edited), retiredEnvKeys))
		err = atomicWriteFile(path, edited, 0o600)
	}
	if err == nil {
		err = validateImportedEnvironment(opts, string(edited))
	}
	if err == nil {
		err = validateProfileEnv(opts)
	}
	if err != nil {
		if restoreErr := atomicWriteFile(path, original, 0o600); restoreErr != nil {
			return fmt.Errorf("edited environment is invalid (%v) and the original could not be restored: %w", err, restoreErr)
		}
		return fmt.Errorf("edited environment is invalid; the original was restored: %w", err)
	}
	refreshAutomaticEnvBackupAfterWrite(path)
	return nil
}

func exportConfig(opts options, target string) error {
	target, err := filepath.Abs(target)
	if err != nil {
		return err
	}
	raw, err := os.ReadFile(opts.envFile)
	if err != nil {
		return err
	}
	raw = []byte(removeEnvKeysFromContent(string(raw), retiredEnvKeys))
	if err := atomicWriteFile(target, raw, 0o600); err != nil {
		return err
	}
	fmt.Printf("Exported the complete environment, including secrets, to %s\n", target)
	return nil
}

func importConfig(opts options, source string, replace bool) error {
	info, err := os.Stat(source)
	if err != nil {
		return fmt.Errorf("could not read import file: %w", err)
	}
	if !info.Mode().IsRegular() || info.Size() > 1024*1024 {
		return errors.New("the import must be a regular .env file smaller than 1 MB")
	}
	raw, err := os.ReadFile(source)
	if err != nil {
		return err
	}
	if err := validateImportedEnvironment(opts, string(raw)); err != nil {
		return fmt.Errorf("imported environment is invalid: %w", err)
	}
	current, err := os.ReadFile(opts.envFile)
	if err != nil {
		return err
	}
	settings, err := readServerSettings(opts)
	if err != nil {
		return err
	}
	imported := parseEnvContent(string(raw))
	filteredImported := make(map[string]string, len(imported))
	for key, value := range imported {
		if !launcherHiddenEnvKeys[key] {
			filteredImported[key] = value
		}
	}

	nextRaw := raw
	if replace {
		// Replacement still preserves launcher-owned values from the live file.
		// Strip attacker-controlled copies from the source before restoring the
		// trusted values, matching the Launcher's replacement projection.
		protected := map[string]string{}
		currentValues := parseEnvContent(string(current))
		for key := range launcherHiddenEnvKeys {
			if retiredEnvKeys[key] {
				continue
			}
			if value, ok := currentValues[key]; ok {
				protected[key] = value
			}
		}
		nextRaw = []byte(updateEnvContent(
			removeEnvKeysFromContent(string(raw), launcherHiddenEnvKeys),
			protected,
		))
		nextRaw = []byte(updateEnvContent(
			string(nextRaw),
			launcherImportInvariantUpdates(applyServerSettingsToEnv(parseEnvContent(string(nextRaw)), settings)),
		))
	} else {
		// Configuration imports mirror Launcher semantics: supplied keys replace
		// their current values while omitted and commented settings survive.
		effective := parseEnvContent(string(current))
		for key, value := range filteredImported {
			effective[key] = value
		}
		for key, value := range topologyInvariantUpdates(effective) {
			filteredImported[key] = value
		}
		for key, value := range launcherImportInvariantUpdates(applyServerSettingsToEnv(effective, settings)) {
			filteredImported[key] = value
		}
		nextRaw = []byte(updateEnvContent(string(current), filteredImported))
	}
	nextRaw = []byte(removeEnvKeysFromContent(string(nextRaw), retiredEnvKeys))
	if err := validateIndependentSecuritySecrets(parseEnvContent(string(nextRaw))); err != nil {
		return fmt.Errorf("imported environment is invalid: %w", err)
	}
	temporary := opts.envFile + ".import"
	if err := atomicWriteFile(temporary, nextRaw, 0o600); err != nil {
		return err
	}
	defer os.Remove(temporary)
	preview := opts
	preview.envFile = temporary
	if err := validateProfileEnv(preview); err != nil {
		return fmt.Errorf("imported environment is invalid: %w", err)
	}
	// Configuration import is intentionally a direct file operation. Running
	// services keep their current process environment until the operator invokes
	// restart; importing never stops Docker or the independently managed proxy.
	if err := atomicWriteFile(opts.envFile, nextRaw, 0o600); err != nil {
		return err
	}
	refreshAutomaticEnvBackupAfterWrite(opts.envFile)
	fmt.Printf("Imported %s. Restart Omlorix to apply the new values to running services.\n", source)
	return nil
}

// restoreCompleteEnv replaces the live file with an exact trusted recovery
// snapshot. Unlike configuration import, every launcher-owned identity,
// credential, trust, TLS, and listener value is restored from the source.
// Runtime is intentionally untouched until the operator explicitly restarts.
func restoreCompleteEnv(opts options, source string) error {
	info, err := os.Stat(source)
	if err != nil {
		return fmt.Errorf("could not read recovery file: %w", err)
	}
	if !info.Mode().IsRegular() || info.Size() > 1024*1024 {
		return errors.New("the recovery file must be a regular .env file smaller than 1 MB")
	}
	sourcePath, err := filepath.Abs(source)
	if err != nil {
		return err
	}
	homePath, err := filepath.Abs(opts.home)
	if err != nil {
		return err
	}
	relative, err := filepath.Rel(homePath, sourcePath)
	if err != nil {
		return err
	}
	if relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return errors.New("the complete .env recovery file must be outside the server directory")
	}

	raw, err := os.ReadFile(sourcePath)
	if err != nil {
		return err
	}
	if err := validateImportedEnvironment(opts, string(raw)); err != nil {
		return fmt.Errorf("recovery environment is invalid: %w", err)
	}
	env := parseEnvContent(string(raw))
	for key, value := range env {
		if retiredEnvKeys[key] {
			continue
		}
		if err := validateConfigValue(opts, key, value); err != nil {
			return fmt.Errorf("recovery environment is invalid: %w", err)
		}
	}
	if strings.TrimSpace(env["JWT_SECRET_KEY"]) == "" || strings.TrimSpace(env["ENCRYPTION_KEY"]) == "" {
		return errors.New("the recovery file is not a complete Omlorix .env backup")
	}
	if len(strings.TrimSpace(env["PASSWORD_RESET_IDENTIFIER_HASH_SALT"])) < 16 {
		return errors.New("the recovery file is missing a valid password reset salt")
	}
	if len(strings.TrimSpace(env["LOG_IP_HASH_SALT"])) < 16 {
		return errors.New("the recovery file is missing a valid audit IP hash salt")
	}

	settings, err := readServerSettings(opts)
	if err != nil {
		return err
	}
	updateChannel, updateChannelPresent := env["OMLORIX_UPDATE_CHANNEL"]
	if updateChannelPresent {
		validatedChannel, validationErr := validateUpdateChannel(updateChannel)
		if validationErr != nil || validatedChannel == "" {
			return errors.New("the recovery file update channel must be stable or beta")
		}
		settings.UpdateChannel = validatedChannel
	}
	legacyProxyPresent := false
	for _, key := range managedProxySettingsEnvKeys {
		if _, present := env[key]; present {
			legacyProxyPresent = true
			break
		}
	}
	if legacyProxyPresent {
		settings.Proxy = proxySettingsFromEnv(env)
	}
	// Recovery-only management fields are committed to server-settings.json and
	// must never reach Compose through the live dotenv file.
	managementKeys := make(map[string]bool, len(managedProxySettingsEnvKeys)+len(retiredEnvKeys)+1)
	managementKeys["OMLORIX_UPDATE_CHANNEL"] = true
	for _, key := range managedProxySettingsEnvKeys {
		managementKeys[key] = true
	}
	for key := range retiredEnvKeys {
		managementKeys[key] = true
	}
	liveRaw := []byte(strings.TrimRight(removeEnvKeysFromContent(string(raw), managementKeys), "\r\n") + "\n")
	temporary := opts.envFile + ".recovery"
	if err := atomicWriteFile(temporary, liveRaw, 0o600); err != nil {
		return err
	}
	defer os.Remove(temporary)
	preview := opts
	preview.envFile = temporary
	if err := validateProfileEnv(preview); err != nil {
		return fmt.Errorf("recovery environment is invalid: %w", err)
	}
	if err := atomicWriteFile(opts.envFile, liveRaw, 0o600); err != nil {
		return err
	}
	if updateChannelPresent || legacyProxyPresent {
		if err := writeServerSettings(opts, settings); err != nil {
			return err
		}
	}
	fmt.Printf("Restored the complete environment from %s. Restart Omlorix manually to apply it.\n", sourcePath)
	return nil
}

// launcherImportInvariantUpdates mirrors the derived trust boundary applied by
// the Electron Launcher. Imported files cannot set these hidden values, but
// the management layer still recalculates them from the protected live proxy
// configuration before committing either import mode.
func launcherImportInvariantUpdates(env map[string]string) map[string]string {
	launcherProxyEnabled := envTruthy(env["OMLORIX_LAUNCHER_PROXY_ENABLED"], false)
	externalProxyEnabled := strings.TrimSpace(env["FRONTEND_TRUSTED_UPSTREAMS"]) != ""
	updates := map[string]string{
		"FRONTEND_TRUST_PROXY_HEADERS": fmt.Sprintf("%t", launcherProxyEnabled || externalProxyEnabled),
	}
	if launcherProxyEnabled {
		updates["FRONTEND_HTTP_HOST_BIND"] = "127.0.0.1"
	}
	return updates
}

func commandCheckUpdate(opts options) error {
	env, _ := readEnv(opts.envFile)
	settings, err := readServerSettings(opts)
	if err != nil {
		return err
	}
	channel, err := validateUpdateChannel(firstNonBlank(opts.channel, settings.UpdateChannel))
	if err != nil {
		return fmt.Errorf("invalid update channel: %w", err)
	}
	release, err := releaseInfoForChannel(channel)
	if err != nil {
		return fmt.Errorf("could not check the latest %s release: %w", channel, err)
	}
	latest := release.Version
	current := firstNonBlank(env["OMLORIX_VERSION"], "stable")
	available := current == "stable" || current == "beta" || compareVersions(latest, current) > 0
	compatible := managementCompatibilityError(release) == nil
	payload := map[string]any{
		"channel": channel, "current_version": current, "latest_version": latest, "update_available": available,
		"management_compatible": compatible, "minimum_cli_version": release.MinimumManagementVersion,
		"compatibility_reason": release.UpdateReason, "release_url": release.ReleaseURL,
	}
	if opts.jsonOutput {
		return printJSON(payload)
	}
	fmt.Printf("Channel: %s\nCurrent version: %s\nLatest version: %s\n", channel, current, latest)
	if available {
		if compatible {
			fmt.Println("An update is available. Run: omlorix-server update")
		} else {
			fmt.Printf("Update the Omlorix Server CLI to %s or newer before installing this server release.\n", release.MinimumManagementVersion)
		}
	} else {
		fmt.Println("Omlorix is up to date.")
	}
	return nil
}

// rollbackUpdate restores the selected image tag and channel only while the
// target migration has definitely not started. Once it may have started, the
// target release remains selected and every application container is drained:
// database migrations are forward-only and an older image must never be
// restarted against a schema it may no longer understand.
func rollbackUpdate(
	opts options,
	previous,
	previousChannel,
	attempted string,
	cause error,
	containersMayHaveChanged bool,
	migrationMayHaveStarted bool,
) error {
	fmt.Fprintf(os.Stderr, "Update to %s failed: %s\n", attempted, cause)
	if migrationMayHaveStarted {
		fmt.Fprintf(
			os.Stderr,
			"The target release %s remains selected because database migrations may have started. Leaving Omlorix offline.\n",
			attempted,
		)
		if err := runDocker(composeArgs(opts, offlineMigrationDrainCommand()...), opts.home); err != nil {
			return fmt.Errorf(
				"update to %s failed after database migrations may have started; the target release remains selected, but not every application container could be confirmed stopped: %v: %w",
				attempted,
				err,
				cause,
			)
		}
		return fmt.Errorf(
			"update to %s failed after database migrations may have started; the target release remains selected and Omlorix was left offline: %w",
			attempted,
			cause,
		)
	}

	fmt.Fprintf(os.Stderr, "Rolling image settings back to %s on the %s channel. Database migrations are not reverted.\n", previous, previousChannel)
	if err := writeEnv(opts.envFile, map[string]string{"OMLORIX_VERSION": previous}); err != nil {
		return fmt.Errorf("update failed and image settings could not be restored: %w", err)
	}
	if err := writeUpdateChannel(opts, previousChannel); err != nil {
		return fmt.Errorf("update failed and the previous update channel could not be restored: %w", err)
	}
	if containersMayHaveChanged {
		// A failed drain may have stopped only part of the project while leaving
		// an orphaned old writer alive. Never bring the rollback image up until a
		// second project-wide drain has completed successfully.
		if err := runDocker(composeArgs(opts, offlineMigrationDrainCommand()...), opts.home); err != nil {
			return fmt.Errorf("update failed; settings were restored but rollback was left offline because application writers could not be drained: %w", err)
		}
		if err := runDocker(composeArgs(opts, "pull"), opts.home); err != nil {
			return fmt.Errorf("update failed; settings were restored but previous images could not be pulled: %w", err)
		}
		if err := runDocker(composeArgs(opts, "up", "-d", "--force-recreate", "--remove-orphans"), opts.home); err != nil {
			return fmt.Errorf("update failed; settings were restored but previous containers could not be started: %w", err)
		}
	}
	return fmt.Errorf("update to %s failed; image settings were rolled back to %s: %w", attempted, previous, cause)
}

func commandBackupOptions(opts options) error {
	if err := ensureServerHome(opts); err != nil {
		return err
	}
	if err := validateProfileEnv(opts); err != nil {
		return err
	}
	if err := ensureDockerReady(opts); err != nil {
		return err
	}
	args := composeArgs(opts, "exec", "-T", "fastapi", "python", "-m", "app.backups.cli", "options")
	return runBackendCommand(opts, args)
}

type backupDownloadResult struct {
	OK    bool   `json:"ok"`
	JobID string `json:"job_id"`
	Path  string `json:"path"`
	Bytes int64  `json:"bytes"`
}

type backupArtifactStream func(args []string, cwd string, writer io.Writer) (string, error)

type boundedCaptureWriter struct {
	buffer    bytes.Buffer
	remaining int
}

// Write accepts the complete process stream while retaining only the bounded
// prefix needed for safe failure classification. Reporting the original byte
// count prevents a verbose child process from blocking on a full stderr pipe.
func (writer *boundedCaptureWriter) Write(value []byte) (int, error) {
	written := len(value)
	if writer.remaining <= 0 {
		return written, nil
	}
	retained := min(len(value), writer.remaining)
	_, _ = writer.buffer.Write(value[:retained])
	writer.remaining -= retained
	return written, nil
}

// streamBackupArtifactFromDocker reserves stdout for archive bytes. Stderr is
// bounded and used only to classify a safe operator-facing failure.
func streamBackupArtifactFromDocker(args []string, cwd string, writer io.Writer) (string, error) {
	cmd := exec.Command(dockerExecutable(), args...)
	cmd.Dir = existingDir(cwd)
	cmd.Stdout = writer
	stderr := boundedCaptureWriter{remaining: 64 * 1024}
	cmd.Stderr = &stderr
	err := cmd.Run()
	return stderr.buffer.String(), err
}

// downloadBackupArtifact streams into a private sibling temporary file and
// commits it with a hard link. link(2) is atomic and fails if the explicit
// destination appeared meanwhile, so interrupted downloads and races never
// expose a partial archive or overwrite an existing file.
func downloadBackupArtifact(
	opts options,
	jobID string,
	target string,
	stream backupArtifactStream,
) (backupDownloadResult, error) {
	result := backupDownloadResult{}
	if stream == nil {
		return result, newCLIError("backup_download_failed", "The backup archive could not be downloaded.", nil)
	}
	absoluteTarget, err := filepath.Abs(target)
	if err != nil {
		return result, newCLIError("destination_unavailable", "The backup destination path is invalid.", err)
	}
	parent := filepath.Dir(absoluteTarget)
	parentInfo, err := os.Stat(parent)
	if err != nil || !parentInfo.IsDir() {
		return result, newCLIError("destination_unavailable", "The backup destination directory is unavailable.", err)
	}
	if _, err := os.Lstat(absoluteTarget); err == nil {
		return result, newCLIError("destination_exists", "The backup destination already exists.", nil)
	} else if !errors.Is(err, os.ErrNotExist) {
		return result, newCLIError("destination_unavailable", "The backup destination could not be inspected.", err)
	}

	temporary, err := os.CreateTemp(parent, ".omlorix-backup-download-*")
	if err != nil {
		return result, newCLIError("destination_unavailable", "The backup destination could not be created.", err)
	}
	temporaryPath := temporary.Name()
	closed := false
	defer func() {
		if !closed {
			_ = temporary.Close()
		}
		_ = os.Remove(temporaryPath)
	}()
	if err := temporary.Chmod(0o600); err != nil {
		return result, newCLIError("destination_unavailable", "The backup destination permissions could not be secured.", err)
	}

	command := composeArgs(
		opts,
		"exec", "-T", "fastapi", "python", "-m", "app.backups.cli", "download", jobID,
	)
	stderr, streamErr := stream(command, opts.home, temporary)
	if streamErr != nil {
		classified := classifyBackendCommandError("", stderr, streamErr)
		details := structuredCLIError(classified)
		return result, newCLIError(details.Code, details.Message, nil)
	}
	if err := temporary.Sync(); err != nil {
		return result, newCLIError("destination_unavailable", "The downloaded backup could not be written safely.", err)
	}
	if err := temporary.Close(); err != nil {
		return result, newCLIError("destination_unavailable", "The downloaded backup could not be finalized.", err)
	}
	closed = true

	if err := os.Link(temporaryPath, absoluteTarget); err != nil {
		if errors.Is(err, os.ErrExist) {
			return result, newCLIError("destination_exists", "The backup destination already exists.", err)
		}
		return result, newCLIError("destination_unavailable", "The downloaded backup could not be committed atomically.", err)
	}
	info, err := os.Stat(absoluteTarget)
	if err != nil {
		_ = os.Remove(absoluteTarget)
		return result, newCLIError("destination_unavailable", "The downloaded backup could not be confirmed.", err)
	}
	return backupDownloadResult{
		OK:    true,
		JobID: jobID,
		Path:  absoluteTarget,
		Bytes: info.Size(),
	}, nil
}

func commandBackupVerify(opts options) error {
	if (strings.TrimSpace(opts.jobID) == "") == (strings.TrimSpace(opts.source) == "") {
		return invalidArgumentsError(errors.New("backup-verify requires exactly one of --job-id or --source"))
	}
	if err := ensureServerHome(opts); err != nil {
		return err
	}
	if err := ensureDockerReady(opts); err != nil {
		return err
	}
	args, err := backupVerifyComposeArgs(opts)
	if err != nil {
		return invalidArgumentsError(err)
	}
	return runBackendCommand(opts, args)
}

// backupVerifyComposeArgs mounts host archives into a one-shot backend
// container. A host path passed to `compose exec` is not visible inside the
// running FastAPI container, which made the documented local verification
// workflow fail unless the same path happened to be mounted already.
func backupVerifyComposeArgs(opts options) ([]string, error) {
	if strings.TrimSpace(opts.jobID) != "" {
		return composeArgs(
			opts,
			"exec", "-T", "fastapi", "python", "-m", "app.backups.cli", "verify",
			"--job-id", strings.TrimSpace(opts.jobID),
		), nil
	}

	verifySource, volumeArgs, err := normalizeRestoreSource(opts.source)
	if err != nil {
		return nil, err
	}
	command := []string{"run", "--rm", "--no-deps"}
	command = append(command, volumeArgs...)
	command = append(
		command,
		"fastapi", "python", "-m", "app.backups.cli", "verify",
		"--source", verifySource,
	)
	return composeArgs(opts, command...), nil
}

func commandOpen(opts options) error {
	if len(opts.arguments) != 0 {
		return errors.New("open does not accept positional arguments")
	}
	env, _ := readEnv(opts.envFile)
	url := resolveURL(opts, env)
	fmt.Printf("Opening %s\n", url)
	return openBrowser(url)
}

func runCoordinatedRestore(opts options) error {
	if (strings.TrimSpace(opts.source) == "") == (strings.TrimSpace(opts.jobID) == "") {
		return errors.New("restore requires exactly one of --source or --job-id")
	}
	if opts.target != "empty" && opts.target != "in_place" {
		return errors.New("--target must be empty or in_place")
	}
	if opts.target == "in_place" && opts.confirm != "RESTORE-IN-PLACE" {
		return errors.New("in-place restore requires --confirm RESTORE-IN-PLACE")
	}
	if err := ensureServerHome(opts); err != nil {
		return err
	}
	if err := validateProfileEnv(opts); err != nil {
		return err
	}
	if err := ensureDockerReady(opts); err != nil {
		return err
	}
	if err := ensureLauncherServicesNetwork(opts); err != nil {
		return err
	}

	restoreSource := ""
	volumeArgs := []string{}
	if strings.TrimSpace(opts.source) != "" {
		var err error
		restoreSource, volumeArgs, err = normalizeRestoreSource(opts.source)
		if err != nil {
			return err
		}
	}
	toggles := readEnvToggles(opts)
	applicationServices := offlineApplicationServiceNames()
	servicesToStart := []string{"frontend", "email_worker"}
	servicesToStart = append(servicesToStart, dedicatedWorkerServiceNames...)
	servicesToStart = append(servicesToStart, "fastapi")
	if toggles.redisEnabled {
		servicesToStart = []string{"frontend", "email_worker"}
		servicesToStart = append(servicesToStart, dedicatedWorkerServiceNames...)
		servicesToStart = append(servicesToStart, "automation_scheduler", "automation_worker", "fastapi")
	}
	fmt.Println("Stopping Omlorix application services for an offline restore ...")
	if err := runDocker(composeArgs(opts, append([]string{"stop"}, applicationServices...)...), opts.home); err != nil {
		// A failed stop can still leave a subset of services down. Recreate the
		// selected application set before returning so a pre-restore failure does
		// not turn into an avoidable outage.
		if restartErr := restartRestoreApplicationServices(opts, servicesToStart); restartErr != nil {
			return fmt.Errorf("could not stop application services safely: %w; recovery restart failed: %v", err, restartErr)
		}
		return fmt.Errorf("could not stop application services safely: %w", err)
	}
	if err := stopRemainingRestoreApplicationContainers(opts); err != nil {
		if restartErr := restartRestoreApplicationServices(opts, servicesToStart); restartErr != nil {
			return fmt.Errorf("could not fence all application containers safely: %w; recovery restart failed: %v", err, restartErr)
		}
		return fmt.Errorf("could not fence all application containers safely: %w", err)
	}

	restoreArgs := []string{"run", "--rm", "--no-deps", "--remove-orphans"}
	restoreArgs = append(restoreArgs, volumeArgs...)
	restoreArgs = append(restoreArgs, "fastapi", "python", "-m", "app.backups.cli", "restore")
	if strings.TrimSpace(opts.jobID) != "" {
		restoreArgs = append(restoreArgs, "--job-id", strings.TrimSpace(opts.jobID))
	} else {
		restoreArgs = append(restoreArgs, "--source", restoreSource)
	}
	restoreArgs = append(restoreArgs, "--target", opts.target, "--offline")
	if opts.confirm != "" {
		restoreArgs = append(restoreArgs, "--confirm", opts.confirm)
	}
	result := runCommandCaptured(dockerExecutable(), composeArgs(opts, restoreArgs...), opts.home, opts.verbose)
	if result.Err != nil {
		reason := restoreFailureReason(result.Stdout, result.Stderr)
		if restoreSafeToRestartOutputs(result.Stdout, result.Stderr) {
			fmt.Fprintln(os.Stderr, "Restore stopped safely; restarting Omlorix with the existing or recovered data.")
			if restartErr := restartRestoreApplicationServices(opts, servicesToStart); restartErr != nil {
				return fmt.Errorf("restore failed: %s; Omlorix recovery restart did not become healthy: %w", reason, restartErr)
			}
			return fmt.Errorf("restore failed: %s", reason)
		}
		return fmt.Errorf("restore failed: %s; safe recovery was not confirmed, so Omlorix was left stopped to protect server data", reason)
	}
	if !opts.verbose {
		fmt.Println("Backup data restored successfully; restarting Omlorix.")
	}

	fmt.Println("Starting Omlorix with the restored data ...")
	if err := restartRestoreApplicationServices(opts, servicesToStart); err != nil {
		return fmt.Errorf("server data was restored, but Omlorix failed to start: %w", err)
	}
	if !opts.noWait {
		env, _ := readEnv(opts.envFile)
		fmt.Printf("Omlorix was restored and is ready at %s\n", resolveURL(opts, env))
		if err := recordSuccessfulServerVersionForCLI(opts, env["OMLORIX_VERSION"]); err != nil {
			return err
		}
	}
	return nil
}

// restartRestoreApplicationServices is shared by successful restores and
// every confirmed-safe failure path. Unless the operator explicitly selected
// --no-wait, returning means the same full-stack health contract as start and
// restart has been satisfied.
func restartRestoreApplicationServices(opts options, services []string) error {
	args := append([]string{"up", "-d", "--no-deps", "--force-recreate", "--remove-orphans"}, services...)
	if err := runDocker(composeArgs(opts, args...), opts.home); err != nil {
		return err
	}
	if opts.noWait {
		return nil
	}
	return waitForServerHealthy(opts, opts.timeout)
}

func normalizeRestoreSource(source string) (string, []string, error) {
	trimmed := strings.TrimSpace(source)
	if windowsDrivePathPattern.MatchString(trimmed) {
		return normalizeLocalRestorePath(trimmed)
	}
	parsed, parseErr := url.Parse(trimmed)
	localPath := ""
	if parseErr == nil && parsed.Scheme == "file" {
		localPath, parseErr = url.PathUnescape(parsed.Path)
		if parseErr != nil {
			return "", nil, errors.New("restore file URI is invalid")
		}
		if runtime.GOOS == "windows" && len(localPath) >= 3 && localPath[0] == '/' && localPath[2] == ':' {
			localPath = localPath[1:]
		}
	} else if parseErr == nil && parsed.Scheme == "" {
		localPath = trimmed
	}
	if localPath == "" {
		return trimmed, nil, nil
	}
	return normalizeLocalRestorePath(localPath)
}

var windowsDrivePathPattern = regexp.MustCompile(`^[A-Za-z]:[\\/]`)

// normalizeLocalRestorePath validates and mounts a local backup archive.
func normalizeLocalRestorePath(localPath string) (string, []string, error) {
	abs, err := filepath.Abs(localPath)
	if err != nil {
		return "", nil, err
	}
	info, err := os.Stat(abs)
	if err != nil || !info.Mode().IsRegular() {
		return "", nil, errors.New("the restore source must be an existing, accessible backup file")
	}
	lower := strings.ToLower(abs)
	if !strings.HasSuffix(lower, ".tar.zst") && !strings.HasSuffix(lower, ".tar.zst.enc") {
		return "", nil, errors.New("the restore file must end in .tar.zst or .tar.zst.enc")
	}
	return "file:///restore/input", []string{"--volume", abs + ":/restore/input:ro"}, nil
}

type capturedResult struct {
	Stdout string
	Stderr string
	Err    error
}

func runCommandCaptured(name string, args []string, cwd string, verbose bool) capturedResult {
	cmd := exec.Command(name, args...)
	cmd.Dir = existingDir(cwd)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	if verbose {
		cmd.Stdout = io.MultiWriter(os.Stdout, &stdout)
		cmd.Stderr = io.MultiWriter(os.Stderr, &stderr)
	} else {
		cmd.Stdout = &stdout
		cmd.Stderr = &stderr
	}
	cmd.Stdin = os.Stdin
	err := cmd.Run()
	return capturedResult{Stdout: stdout.String(), Stderr: stderr.String(), Err: err}
}

func restoreSafeToRestart(raw string) bool {
	return restoreSafeToRestartOutputs(raw)
}

func restoreSafeToRestartOutputs(outputs ...string) bool {
	payload, ok := restoreCommandPayload(outputs...)
	if !ok {
		return false
	}
	recovery, ok := payload["recovery"].(map[string]any)
	if !ok {
		return false
	}
	safe, _ := recovery["safe_to_restart"].(bool)
	return safe
}

// restoreFailureReason translates the backend's structured, sanitized
// preflight result into an operator-facing explanation. It intentionally does
// not print nested details such as internal paths, checksums, or provider URIs.
func restoreFailureReason(outputs ...string) string {
	payload, ok := restoreCommandPayload(outputs...)
	if !ok {
		return "the backend restore command failed; check the restore logs for details"
	}
	preflight, _ := payload["preflight"].(map[string]any)
	reason, _ := preflight["reason"].(string)
	switch strings.TrimSpace(reason) {
	case "target_not_empty":
		return "the restore target is not empty; use --target in_place with --confirm RESTORE-IN-PLACE to replace existing data"
	case "missing_required_files":
		return "the backup archive is incomplete"
	case "checksum_mismatch":
		return "the backup archive failed checksum verification"
	case "encryption_key_mismatch":
		return "the backup archive cannot be decrypted with this server's encryption key"
	case "manifest_parse_failed":
		return "the backup manifest is invalid"
	case "payload_tar_parse_failed":
		return "a backup payload is invalid"
	case "archive_extracted_size_exceeded":
		return "the backup exceeds the configured restore size limit"
	case "insufficient_disk_space":
		return "there is not enough free disk space to restore this backup safely"
	case "source_access_failed":
		return "the backup source could not be accessed"
	}
	if safeError, _ := payload["error"].(string); strings.TrimSpace(safeError) != "" {
		return strings.TrimSpace(safeError)
	}
	if strings.TrimSpace(reason) != "" {
		return strings.ReplaceAll(strings.TrimSpace(reason), "_", " ")
	}
	return "the backend restore command failed; check the restore logs for details"
}

func restoreCommandPayload(outputs ...string) (map[string]any, bool) {
	for _, output := range outputs {
		var payload map[string]any
		if parseTrailingJSONObject(output, &payload) {
			return payload, true
		}
	}
	return nil, false
}

func parseTrailingJSONObject(raw string, target any) bool {
	for index := strings.Index(raw, "{"); index >= 0; {
		candidate := strings.TrimSpace(raw[index:])
		if json.Unmarshal([]byte(candidate), target) == nil {
			return true
		}
		next := strings.Index(raw[index+1:], "{")
		if next < 0 {
			break
		}
		index += next + 1
	}
	return false
}

func ensureDockerReady(opts options) error {
	status := inspectDocker(opts)
	if !status.Installed {
		return errors.New("Docker is not installed or is not available on PATH")
	}
	if !status.Running {
		return errors.New("Docker is installed but the engine is not running")
	}
	if !status.Compose {
		return errors.New("Docker Compose is not available")
	}
	return validateComposeOwnership(opts)
}

// validateComposeOwnership refuses to operate on a same-named project created
// by another server home. Compose already labels every container with its
// project; Omlorix's additional random installation label makes the ownership
// check independent of paths, working directories, and Docker platform quirks.
func validateComposeOwnership(opts options) error {
	env, _ := readEnv(opts.envFile)
	return validateComposeOwnershipWithEnv(opts, env)
}

// validateComposeOwnershipWithEnv supports strict post-adoption validation
// before the persisted one-time exception is removed.
func validateComposeOwnershipWithEnv(opts options, env map[string]string) error {
	project := strings.TrimSpace(env["COMPOSE_PROJECT_NAME"])
	identity := strings.TrimSpace(env["OMLORIX_INSTALLATION_ID"])
	if project == "" || identity == "" {
		return errors.New("server installation identity is missing; run omlorix-server init")
	}
	containers, err := runCapture(
		dockerExecutable(),
		[]string{"ps", "-a", "--filter", "label=com.docker.compose.project=" + project, "--format", "{{.ID}}"},
		opts.home,
	)
	if err != nil {
		return fmt.Errorf("could not verify Compose project ownership: %w", err)
	}
	for _, containerID := range strings.Fields(containers) {
		label, inspectErr := runCapture(
			dockerExecutable(),
			[]string{"inspect", "--format", `{{index .Config.Labels "com.omlorix.installation.id"}}`, containerID},
			opts.home,
		)
		if inspectErr != nil {
			return errors.New("could not verify an existing Compose container")
		}
		actualIdentity := strings.TrimSpace(label)
		legacyAdoption := envTruthy(env["OMLORIX_ALLOW_PROJECT_ADOPTION"], false) &&
			(actualIdentity == "" || actualIdentity == "unmanaged")
		if actualIdentity != identity && !legacyAdoption {
			return fmt.Errorf(
				"Compose project %q belongs to another or legacy installation; choose a unique project name or explicitly attach and migrate that installation",
				project,
			)
		}
	}
	return nil
}

// finalizeProjectAdoption closes the one-time legacy exception after Compose
// has recreated resources with this home's identity label.
func finalizeProjectAdoption(opts options) error {
	env, _ := readEnv(opts.envFile)
	if !envTruthy(env["OMLORIX_ALLOW_PROJECT_ADOPTION"], false) {
		return nil
	}
	// Verify the recreated containers strictly before removing the one-time
	// exception. If recreation did not apply labels, the operator can retry
	// without having to arm adoption a second time.
	env["OMLORIX_ALLOW_PROJECT_ADOPTION"] = "false"
	if err := validateComposeOwnershipWithEnv(opts, env); err != nil {
		return err
	}
	if err := writeEnv(opts.envFile, map[string]string{"OMLORIX_ALLOW_PROJECT_ADOPTION": "false"}); err != nil {
		return err
	}
	return nil
}

func ensureLauncherServicesNetwork(opts options) error {
	if _, err := os.Stat(filepath.Join(opts.home, "docker-compose.launcher-services.yml")); err != nil {
		return nil
	}
	if _, err := runCapture(dockerExecutable(), []string{"network", "inspect", "omlorix-launcher-services"}, opts.home); err == nil {
		return nil
	}
	if err := runDocker([]string{"network", "create", "--label", "com.omlorix.launcher.managed=true", "omlorix-launcher-services"}, opts.home); err != nil {
		if _, inspectErr := runCapture(dockerExecutable(), []string{"network", "inspect", "omlorix-launcher-services"}, opts.home); inspectErr != nil {
			return errors.New("could not create the private Omlorix helper services network")
		}
	}
	return nil
}

func dockerExecutable() string {
	if override := strings.TrimSpace(os.Getenv("DOCKER_BIN")); override != "" {
		return override
	}
	if path, err := exec.LookPath("docker"); err == nil {
		return path
	}
	candidates := []string{}
	if runtime.GOOS == "darwin" {
		candidates = append(candidates, "/Applications/Docker.app/Contents/Resources/bin/docker", "/usr/local/bin/docker", "/opt/homebrew/bin/docker")
	}
	if runtime.GOOS == "windows" {
		for _, root := range []string{os.Getenv("ProgramFiles"), os.Getenv("ProgramW6432"), os.Getenv("LOCALAPPDATA")} {
			if root != "" {
				candidates = append(candidates, filepath.Join(root, "Docker", "Docker", "resources", "bin", "docker.exe"))
			}
		}
	}
	for _, candidate := range candidates {
		if info, err := os.Stat(candidate); err == nil && !info.IsDir() {
			return candidate
		}
	}
	return "docker"
}

func printJSON(value any) error {
	return writeJSON(os.Stdout, value)
}

func writeJSON(writer io.Writer, value any) error {
	encoder := json.NewEncoder(writer)
	encoder.SetIndent("", "  ")
	return encoder.Encode(value)
}

func atomicWriteFile(path string, content []byte, mode os.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(filepath.Dir(path), ".omlorix-env-*")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	if err := temporary.Chmod(mode); err != nil {
		temporary.Close()
		return err
	}
	if _, err := temporary.Write(content); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Sync(); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporaryPath, path)
}

func randomHex(size int) string {
	data := make([]byte, size)
	if _, err := rand.Read(data); err != nil {
		panic(err)
	}
	return hex.EncodeToString(data)
}

func compareVersions(left, right string) int {
	leftParts := versionParts(left)
	rightParts := versionParts(right)
	for index := 0; index < 3; index++ {
		if leftParts[index] < rightParts[index] {
			return -1
		}
		if leftParts[index] > rightParts[index] {
			return 1
		}
	}
	leftPre := versionPrerelease(left)
	rightPre := versionPrerelease(right)
	if leftPre == "" && rightPre != "" {
		return 1
	}
	if leftPre != "" && rightPre == "" {
		return -1
	}
	return comparePrereleaseIdentifiers(leftPre, rightPre)
}

// comparePrereleaseIdentifiers implements SemVer 2.0 precedence for the
// portion after the hyphen. Numeric identifiers compare numerically, numeric
// identifiers sort before non-numeric identifiers, and a shorter equal prefix
// has lower precedence.
func comparePrereleaseIdentifiers(left, right string) int {
	leftIdentifiers := strings.Split(left, ".")
	rightIdentifiers := strings.Split(right, ".")
	limit := minInt(len(leftIdentifiers), len(rightIdentifiers))
	for index := 0; index < limit; index++ {
		comparison := comparePrereleaseIdentifier(leftIdentifiers[index], rightIdentifiers[index])
		if comparison != 0 {
			return comparison
		}
	}
	if len(leftIdentifiers) < len(rightIdentifiers) {
		return -1
	}
	if len(leftIdentifiers) > len(rightIdentifiers) {
		return 1
	}
	return 0
}

func comparePrereleaseIdentifier(left, right string) int {
	leftNumeric := isASCIIDigits(left)
	rightNumeric := isASCIIDigits(right)
	if leftNumeric && rightNumeric {
		leftNormalized := strings.TrimLeft(left, "0")
		rightNormalized := strings.TrimLeft(right, "0")
		if leftNormalized == "" {
			leftNormalized = "0"
		}
		if rightNormalized == "" {
			rightNormalized = "0"
		}
		if len(leftNormalized) < len(rightNormalized) {
			return -1
		}
		if len(leftNormalized) > len(rightNormalized) {
			return 1
		}
		return strings.Compare(leftNormalized, rightNormalized)
	}
	if leftNumeric {
		return -1
	}
	if rightNumeric {
		return 1
	}
	return strings.Compare(left, right)
}

func isASCIIDigits(value string) bool {
	if value == "" {
		return false
	}
	for _, character := range value {
		if character < '0' || character > '9' {
			return false
		}
	}
	return true
}

func minInt(left, right int) int {
	if left < right {
		return left
	}
	return right
}

func versionPrerelease(value string) string {
	value = strings.TrimPrefix(strings.TrimSpace(value), "v")
	value, _, _ = strings.Cut(value, "+")
	_, prerelease, found := strings.Cut(value, "-")
	if !found {
		return ""
	}
	return prerelease
}

func versionParts(value string) [3]int {
	var result [3]int
	value = strings.TrimPrefix(strings.TrimSpace(value), "v")
	core, _, _ := strings.Cut(value, "-")
	parts := strings.Split(core, ".")
	for index := 0; index < len(parts) && index < 3; index++ {
		fmt.Sscanf(parts[index], "%d", &result[index])
	}
	return result
}

func truncate(value string, length int) string {
	if len(value) <= length {
		return value
	}
	if length <= 1 {
		return value[:length]
	}
	return value[:length-1] + "…"
}

func friendlyCommandError(err error) string {
	if err == nil {
		return ""
	}
	var executableError *exec.Error
	if errors.As(err, &executableError) {
		return "command not found"
	}
	return err.Error()
}

func friendlyNetworkError(err error) string {
	if err == nil {
		return ""
	}
	return "not reachable"
}
