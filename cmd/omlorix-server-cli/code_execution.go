package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	codeExecutionRegistryVersion   = 1
	codeExecutionDefaultVersion    = "0.9.0"
	codeExecutionNetwork           = "omlorix-launcher-services"
	codeExecutionLatestURL         = "https://api.github.com/repos/phinaldoo/omlorix-code-execution/releases/latest"
	codeExecutionReleasesURL       = "https://api.github.com/repos/phinaldoo/omlorix-code-execution/releases?per_page=50"
	codeExecutionHealthPath        = "/health"
	codeExecutionHealthDetailsPath = "/health/details"
)

var codeExecutionIDPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,62}$`)

// These immutable management files are refreshed before every start/restart so
// existing instances receive launcher security and operational fixes without
// changing their registry, API key, sandbox environment, or data volume.
var codeExecutionInstanceBundleFiles = []string{
	"docker-compose.yml",
	"haproxy.cfg",
	"TECNATIVA_DOCKER_SOCKET_PROXY_LICENSE.txt",
}

type codeExecutionInstance struct {
	ID             string `json:"id"`
	Name           string `json:"name"`
	Version        string `json:"version"`
	Port           int    `json:"port"`
	Memory         string `json:"memory"`
	MaxConcurrent  int    `json:"maxConcurrent"`
	SessionTimeout int    `json:"sessionTimeout"`
	NetworkAccess  bool   `json:"networkAccess"`
	AllowPip       bool   `json:"allowPip"`
	ImageSource    string `json:"imageSource"`
	CreatedAt      string `json:"createdAt"`
	UpdatedAt      string `json:"updatedAt"`
}

type codeExecutionRegistry struct {
	Version   int                     `json:"version"`
	Instances []codeExecutionInstance `json:"instances"`
}

// codeExecutionRelease contains only the GitHub fields required to offer safe
// stable release choices. Prereleases remain available through --version.
type codeExecutionRelease struct {
	TagName    string `json:"tag_name"`
	Draft      bool   `json:"draft"`
	Prerelease bool   `json:"prerelease"`
}

type codeExecutionView struct {
	codeExecutionInstance
	Running               bool            `json:"running"`
	Healthy               bool            `json:"healthy"`
	HealthStatus          string          `json:"healthStatus"`
	Services              []serviceStatus `json:"services"`
	ActiveExecutions      int             `json:"activeExecutions"`
	ActiveRenders         int             `json:"activeRenders"`
	SandboxImageAvailable bool            `json:"sandboxImageAvailable"`
	Runtime               string          `json:"runtime"`
	ComposeError          string          `json:"composeError,omitempty"`
	ConnectionURL         string          `json:"connectionUrl"`
	LocalURL              string          `json:"localUrl"`
	Home                  string          `json:"home"`
}

func commandCodeExecution(opts options) error {
	if len(opts.arguments) == 0 {
		return errors.New("usage: omlorix-server code-execution <list|versions|create|edit|check-update|start|stop|restart|update|logs|connection|delete> [id]")
	}
	if err := ensureServerHome(opts); err != nil {
		return err
	}
	action := strings.ToLower(opts.arguments[0])
	arguments := opts.arguments[1:]
	if action == "list" {
		if len(arguments) != 0 {
			return errors.New("code-execution list does not accept an instance ID")
		}
		return listCodeExecution(opts)
	}
	if action == "versions" {
		if len(arguments) != 0 {
			return errors.New("code-execution versions does not accept an instance ID")
		}
		return listCodeExecutionVersions(opts)
	}
	if action == "create" {
		if len(arguments) != 0 {
			return errors.New("code-execution create does not accept an instance ID")
		}
		return createCodeExecution(opts)
	}
	if len(arguments) != 1 || !codeExecutionIDPattern.MatchString(arguments[0]) {
		return fmt.Errorf("code-execution %s requires one valid instance ID", action)
	}
	id := arguments[0]
	switch action {
	case "edit":
		return editCodeExecution(opts, id)
	case "check-update":
		return checkCodeExecutionUpdate(opts, id)
	case "start":
		return startCodeExecution(opts, id)
	case "stop":
		return stopCodeExecution(opts, id)
	case "restart":
		return restartCodeExecution(opts, id)
	case "update":
		return updateCodeExecution(opts, id)
	case "logs":
		return logsCodeExecution(opts, id)
	case "connection":
		return connectionCodeExecution(opts, id)
	case "delete":
		return deleteCodeExecution(opts, id)
	default:
		return fmt.Errorf("unknown code-execution action %q", action)
	}
}

func codeExecutionHome(opts options) string {
	return filepath.Join(opts.home, "code-execution")
}

func codeExecutionInstancesHome(opts options) string {
	return filepath.Join(codeExecutionHome(opts), "instances")
}

func codeExecutionRegistryPath(opts options) string {
	return filepath.Join(codeExecutionHome(opts), "instances.json")
}

func ensureCodeExecutionHome(opts options) error {
	if err := os.MkdirAll(codeExecutionInstancesHome(opts), 0o700); err != nil {
		return err
	}
	path := codeExecutionRegistryPath(opts)
	if _, err := os.Stat(path); errors.Is(err, os.ErrNotExist) {
		return writeCodeExecutionRegistry(opts, codeExecutionRegistry{Version: codeExecutionRegistryVersion, Instances: []codeExecutionInstance{}})
	}
	return nil
}

func readCodeExecutionRegistry(opts options) (codeExecutionRegistry, error) {
	if err := ensureCodeExecutionHome(opts); err != nil {
		return codeExecutionRegistry{}, err
	}
	raw, err := os.ReadFile(codeExecutionRegistryPath(opts))
	if err != nil {
		return codeExecutionRegistry{}, err
	}
	var registry codeExecutionRegistry
	if err := json.Unmarshal(raw, &registry); err != nil {
		return codeExecutionRegistry{}, errors.New("the Code Execution instance registry is invalid")
	}
	if registry.Instances == nil {
		registry.Instances = []codeExecutionInstance{}
	}
	return registry, nil
}

func writeCodeExecutionRegistry(opts options, registry codeExecutionRegistry) error {
	registry.Version = codeExecutionRegistryVersion
	raw, err := json.MarshalIndent(registry, "", "  ")
	if err != nil {
		return err
	}
	return atomicWriteFile(codeExecutionRegistryPath(opts), append(raw, '\n'), 0o600)
}

func registeredCodeExecution(opts options, id string) (codeExecutionRegistry, codeExecutionInstance, error) {
	registry, err := readCodeExecutionRegistry(opts)
	if err != nil {
		return registry, codeExecutionInstance{}, err
	}
	for _, instance := range registry.Instances {
		if instance.ID == id {
			return registry, instance, nil
		}
	}
	return registry, codeExecutionInstance{}, fmt.Errorf("Code Execution instance %q was not found", id)
}

func codeExecutionInstanceHome(opts options, id string) (string, error) {
	if !codeExecutionIDPattern.MatchString(id) {
		return "", errors.New("invalid Code Execution instance ID")
	}
	base := filepath.Clean(codeExecutionInstancesHome(opts))
	home := filepath.Join(base, id)
	if relative, err := filepath.Rel(base, home); err != nil || relative == "." || strings.HasPrefix(relative, "..") {
		return "", errors.New("invalid Code Execution instance path")
	}
	return home, nil
}

func codeExecutionComposeArgs(opts options, id string, args ...string) ([]string, error) {
	home, err := codeExecutionInstanceHome(opts, id)
	if err != nil {
		return nil, err
	}
	result := []string{"compose", "--env-file", filepath.Join(home, ".env"), "-f", filepath.Join(home, "docker-compose.yml")}
	return append(result, args...), nil
}

func listCodeExecution(opts options) error {
	registry, err := readCodeExecutionRegistry(opts)
	if err != nil {
		return err
	}
	views := make([]codeExecutionView, 0, len(registry.Instances))
	for _, instance := range registry.Instances {
		views = append(views, inspectCodeExecution(opts, instance))
	}
	if opts.jsonOutput {
		return printJSON(map[string]any{"shared_network": codeExecutionNetwork, "instances": views})
	}
	if len(views) == 0 {
		fmt.Println("No Code Execution services have been created.")
		return nil
	}
	fmt.Printf("%-28s %-24s %-10s %-9s %s\n", "ID", "NAME", "VERSION", "STATUS", "CONNECTION")
	for _, view := range views {
		status := "stopped"
		if view.Running {
			status = "starting"
		}
		if view.Healthy {
			status = "healthy"
		}
		fmt.Printf("%-28s %-24s %-10s %-9s %s\n", truncate(view.ID, 28), truncate(view.Name, 24), view.Version, status, view.ConnectionURL)
	}
	return nil
}

func inspectCodeExecution(opts options, instance codeExecutionInstance) codeExecutionView {
	home, _ := codeExecutionInstanceHome(opts, instance.ID)
	view := codeExecutionView{
		codeExecutionInstance: instance,
		ConnectionURL:         fmt.Sprintf("http://codeexec-%s:8000", instance.ID),
		LocalURL:              fmt.Sprintf("http://127.0.0.1:%d", instance.Port),
		Home:                  home,
	}
	args, err := codeExecutionComposeArgs(opts, instance.ID, "ps", "--all", "--format", "json")
	if err != nil {
		return view
	}
	raw, err := runCapture(dockerExecutable(), args, home)
	if err != nil {
		view.ComposeError = "Docker Compose status is unavailable."
		view.HealthStatus = "stopped"
		return view
	}
	services, _ := parseComposeServices(raw)
	view.Services = services
	for _, service := range services {
		if service.Name == "gateway" && strings.EqualFold(service.State, "running") {
			view.Running = true
			break
		}
	}
	if view.Running {
		env, _ := readEnv(filepath.Join(home, ".env"))
		health := requestCodeExecutionHealth(instance.Port, codeExecutionAPIKey(env), codeExecutionHealthDetailsPath)
		view.Healthy = health.OK
		view.HealthStatus = firstNonBlank(jsonMapString(health.Data, "status"), "starting")
		metrics, _ := health.Data["metrics"].(map[string]any)
		view.ActiveExecutions = jsonMapInt(metrics, "active_executions")
		view.ActiveRenders = jsonMapInt(metrics, "active_renders")
		view.SandboxImageAvailable = jsonMapBool(health.Data, "sandbox_image_available")
		view.Runtime = jsonMapString(health.Data, "sandbox_runtime")
	} else {
		view.HealthStatus = "stopped"
	}
	return view
}

func createCodeExecution(opts options) error {
	name := strings.TrimSpace(opts.name)
	if name == "" {
		return errors.New("code-execution create requires --name")
	}
	if len(name) > 80 {
		return errors.New("Code Execution names may contain at most 80 characters")
	}
	memory := firstNonBlank(opts.memory, "512m")
	if !map[string]bool{"256m": true, "512m": true, "1g": true, "2g": true, "4g": true, "8g": true}[memory] {
		return errors.New("--memory must be one of 256m, 512m, 1g, 2g, 4g, or 8g")
	}
	version := strings.TrimPrefix(strings.TrimSpace(opts.version), "v")
	if version == "" {
		latest, err := latestCodeExecutionVersion()
		if err != nil {
			return err
		}
		version = latest
	}
	if !semanticVersionPattern.MatchString(version) {
		return errors.New("Code Execution version must be a semantic version such as 0.9.0")
	}
	registry, err := readCodeExecutionRegistry(opts)
	if err != nil {
		return err
	}
	port := opts.port
	if port == 0 {
		port, err = nextCodeExecutionPort(registry)
		if err != nil {
			return err
		}
	} else if codeExecutionPortUsed(registry, port) || !portAvailable(port) {
		return fmt.Errorf("port %d is already in use", port)
	}
	now := time.Now().UTC().Format(time.RFC3339)
	id := fmt.Sprintf("%s-%s", slugify(name), randomHex(4))
	if len(id) > 63 {
		id = id[:63]
	}
	for _, existing := range registry.Instances {
		if existing.ID == id {
			return fmt.Errorf("Code Execution instance %q already exists", id)
		}
	}
	maxConcurrent := firstPositive(opts.maxConcurrent, 10)
	sessionTimeout := firstPositive(opts.sessionTimeout, 1200)
	if err := validateCodeExecutionLimits(maxConcurrent, sessionTimeout); err != nil {
		return err
	}
	instance := codeExecutionInstance{
		ID: id, Name: name, Version: version, Port: port, Memory: memory,
		MaxConcurrent: maxConcurrent, SessionTimeout: sessionTimeout,
		NetworkAccess: opts.networkAccess, AllowPip: opts.allowPip, ImageSource: "release", CreatedAt: now, UpdatedAt: now,
	}
	home, _ := codeExecutionInstanceHome(opts, id)
	if err := os.MkdirAll(home, 0o700); err != nil {
		return err
	}
	committed := false
	defer func() {
		if !committed {
			_ = os.RemoveAll(home)
		}
	}()
	if err := syncCodeExecutionInstanceBundle(opts, home); err != nil {
		return err
	}
	if err := atomicWriteFile(filepath.Join(home, ".env_sandbox"), []byte{}, 0o600); err != nil {
		return err
	}
	secret := randomHex(32)
	if err := atomicWriteFile(filepath.Join(home, ".env"), []byte(serializeCodeExecutionEnv(instance, secret)), 0o600); err != nil {
		return err
	}
	registry.Instances = append(registry.Instances, instance)
	if err := writeCodeExecutionRegistry(opts, registry); err != nil {
		return err
	}
	committed = true
	fmt.Printf("Created Code Execution service %s at %s\n", id, home)
	fmt.Printf("Start it with: omlorix-server --home %q code-execution start %s\n", opts.home, id)
	return nil
}

// syncCodeExecutionInstanceBundle installs the shared immutable runtime files
// while preserving all per-instance state in .env, .env_sandbox, and volumes.
func syncCodeExecutionInstanceBundle(opts options, instanceHome string) error {
	bundleRoot := filepath.Join(opts.home, "electron", "code-execution")
	for _, name := range codeExecutionInstanceBundleFiles {
		if err := copyFile(filepath.Join(bundleRoot, name), filepath.Join(instanceHome, name)); err != nil {
			return fmt.Errorf("could not install Code Execution runtime file %s: %w", name, err)
		}
	}
	return nil
}

// editCodeExecution provides the terminal equivalent of the Launcher's edit
// dialog. Only explicitly supplied flags are changed, and a running instance is
// recreated after its registry and environment have been committed.
func editCodeExecution(opts options, id string) error {
	registry, instance, err := registeredCodeExecution(opts, id)
	if err != nil {
		return err
	}
	wasRunning := inspectCodeExecution(opts, instance).Running
	if opts.nameSet {
		instance.Name = strings.TrimSpace(opts.name)
		if instance.Name == "" || len(instance.Name) > 80 {
			return errors.New("Code Execution names must contain 1-80 characters")
		}
	}
	if opts.versionSet {
		version := strings.TrimPrefix(strings.TrimSpace(opts.version), "v")
		if !semanticVersionPattern.MatchString(version) {
			return errors.New("Code Execution version must be a semantic version such as 0.9.0")
		}
		instance.Version = version
	}
	if opts.portSet && opts.port != instance.Port {
		if codeExecutionPortUsedExcept(registry, opts.port, id) || !portAvailable(opts.port) {
			return fmt.Errorf("port %d is already in use", opts.port)
		}
		instance.Port = opts.port
	}
	if opts.memorySet {
		if !map[string]bool{"256m": true, "512m": true, "1g": true, "2g": true, "4g": true, "8g": true}[opts.memory] {
			return errors.New("--memory must be one of 256m, 512m, 1g, 2g, 4g, or 8g")
		}
		instance.Memory = opts.memory
	}
	if opts.maxConcurrentSet {
		if err := validateCodeExecutionLimits(opts.maxConcurrent, instance.SessionTimeout); err != nil {
			return err
		}
		instance.MaxConcurrent = opts.maxConcurrent
	}
	if opts.sessionTimeoutSet {
		if err := validateCodeExecutionLimits(instance.MaxConcurrent, opts.sessionTimeout); err != nil {
			return err
		}
		instance.SessionTimeout = opts.sessionTimeout
	}
	if opts.networkAccessSet {
		instance.NetworkAccess = opts.networkAccess
	}
	if opts.allowPipSet {
		instance.AllowPip = opts.allowPip
	}
	instance.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
	home, _ := codeExecutionInstanceHome(opts, id)
	envPath := filepath.Join(home, ".env")
	originalEnv, err := os.ReadFile(envPath)
	if err != nil {
		return err
	}
	env, _ := readEnv(envPath)
	secret := codeExecutionAPIKey(env)
	if secret == "" {
		return errors.New("Code Execution API key is missing")
	}
	if err := atomicWriteFile(envPath, []byte(serializeCodeExecutionEnv(instance, secret)), 0o600); err != nil {
		return err
	}
	for index := range registry.Instances {
		if registry.Instances[index].ID == id {
			registry.Instances[index] = instance
		}
	}
	if err := writeCodeExecutionRegistry(opts, registry); err != nil {
		return restoreCodeExecutionEnv(envPath, originalEnv, err)
	}
	fmt.Printf("Updated Code Execution service %s.\n", id)
	if wasRunning {
		return startCodeExecution(opts, id)
	}
	return nil
}

func checkCodeExecutionUpdate(opts options, id string) error {
	_, instance, err := registeredCodeExecution(opts, id)
	if err != nil {
		return err
	}
	latest, err := latestCodeExecutionVersion()
	if err != nil {
		return err
	}
	payload := map[string]any{"id": id, "current_version": instance.Version, "latest_version": latest, "update_available": instance.Version != latest}
	if opts.jsonOutput {
		return printJSON(payload)
	}
	fmt.Printf("Current version: %s\nLatest version: %s\n", instance.Version, latest)
	if instance.Version == latest {
		fmt.Println("This Code Execution service is up to date.")
	} else {
		fmt.Printf("An update is available. Run: omlorix-server code-execution update %s\n", id)
	}
	return nil
}

func startCodeExecution(opts options, id string) error {
	_, instance, err := registeredCodeExecution(opts, id)
	if err != nil {
		return err
	}
	if err := ensureDockerReady(opts); err != nil {
		return err
	}
	if err := ensureLauncherServicesNetwork(opts); err != nil {
		return err
	}
	home, _ := codeExecutionInstanceHome(opts, id)
	if err := syncCodeExecutionInstanceBundle(opts, home); err != nil {
		return err
	}
	env, _ := readEnv(filepath.Join(home, ".env"))
	for _, image := range []string{env["CODE_EXECUTION_GATEWAY_IMAGE"], env["SANDBOX_IMAGE"]} {
		if strings.TrimSpace(image) == "" {
			return errors.New("Code Execution image settings are incomplete")
		}
		fmt.Printf("Pulling %s\n", image)
		if err := runDocker([]string{"pull", image}, home); err != nil {
			return err
		}
	}
	args, _ := codeExecutionComposeArgs(opts, id, "up", "-d", "--force-recreate", "--remove-orphans")
	if err := runDocker(args, home); err != nil {
		return err
	}
	if err := attachOmlorixBackendToHelperNetwork(opts); err != nil {
		fmt.Fprintf(os.Stderr, "Warning: could not attach the Omlorix backend to the helper services network: %v\n", err)
	}
	return waitForCodeExecutionHealthy(opts, instance, env)
}

func waitForCodeExecutionHealthy(opts options, instance codeExecutionInstance, env map[string]string) error {
	deadline := time.Now().Add(opts.timeout)
	for time.Now().Before(deadline) {
		if probeCodeExecution(instance.Port, codeExecutionAPIKey(env), codeExecutionHealthPath) {
			fmt.Printf("Code Execution %s is healthy at http://127.0.0.1:%d\n", instance.ID, instance.Port)
			return nil
		}
		time.Sleep(time.Second)
	}
	return errors.New("Code Execution started but did not become healthy before the timeout")
}

func stopCodeExecution(opts options, id string) error {
	_, _, err := registeredCodeExecution(opts, id)
	if err != nil {
		return err
	}
	home, _ := codeExecutionInstanceHome(opts, id)
	args, _ := codeExecutionComposeArgs(opts, id, "down", "--remove-orphans")
	return runDocker(args, home)
}

func restartCodeExecution(opts options, id string) error {
	_, instance, err := registeredCodeExecution(opts, id)
	if err != nil {
		return err
	}
	if err := ensureDockerReady(opts); err != nil {
		return err
	}
	if err := ensureLauncherServicesNetwork(opts); err != nil {
		return err
	}
	home, _ := codeExecutionInstanceHome(opts, id)
	if err := syncCodeExecutionInstanceBundle(opts, home); err != nil {
		return err
	}
	env, _ := readEnv(filepath.Join(home, ".env"))
	args, _ := codeExecutionComposeArgs(opts, id, "up", "-d", "--force-recreate", "--remove-orphans")
	if err := runDocker(args, home); err != nil {
		return err
	}
	if err := attachOmlorixBackendToHelperNetwork(opts); err != nil {
		fmt.Fprintf(os.Stderr, "Warning: could not attach the Omlorix backend to the helper services network: %v\n", err)
	}
	return waitForCodeExecutionHealthy(opts, instance, env)
}

func updateCodeExecution(opts options, id string) error {
	return updateCodeExecutionWith(
		opts,
		id,
		latestCodeExecutionVersion,
		startCodeExecution,
	)
}

// updateCodeExecutionWith keeps the update transaction testable while the
// production entry point supplies the real release lookup and lifecycle
// operation. A failed image pull, recreation, or health check restores both
// configuration stores and attempts to bring the previous version back.
func updateCodeExecutionWith(
	opts options,
	id string,
	latestVersion func() (string, error),
	start func(options, string) error,
) error {
	registry, instance, err := registeredCodeExecution(opts, id)
	if err != nil {
		return err
	}
	previousInstance := instance
	latest, err := latestVersion()
	if err != nil {
		return err
	}
	if instance.Version == latest {
		fmt.Printf("Code Execution %s is already at %s.\n", id, latest)
		return nil
	}
	instance.Version = latest
	instance.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
	home, _ := codeExecutionInstanceHome(opts, id)
	envPath := filepath.Join(home, ".env")
	originalEnv, err := os.ReadFile(envPath)
	if err != nil {
		return err
	}
	env, _ := readEnv(envPath)
	secret := codeExecutionAPIKey(env)
	if secret == "" {
		return errors.New("Code Execution API key is missing")
	}
	if err := atomicWriteFile(envPath, []byte(serializeCodeExecutionEnv(instance, secret)), 0o600); err != nil {
		return err
	}
	for index := range registry.Instances {
		if registry.Instances[index].ID == id {
			registry.Instances[index] = instance
		}
	}
	if err := writeCodeExecutionRegistry(opts, registry); err != nil {
		return restoreCodeExecutionEnv(envPath, originalEnv, err)
	}
	updateErr := start(opts, id)
	if updateErr == nil {
		return nil
	}

	if restoreErr := restoreCodeExecutionUpdateState(
		opts,
		id,
		registry,
		previousInstance,
		envPath,
		originalEnv,
	); restoreErr != nil {
		return errors.Join(
			fmt.Errorf("Code Execution update to %s failed: %w", latest, updateErr),
			fmt.Errorf("could not restore the previous Code Execution configuration: %w", restoreErr),
		)
	}
	if restartErr := start(opts, id); restartErr != nil {
		return errors.Join(
			fmt.Errorf("Code Execution update to %s failed: %w", latest, updateErr),
			fmt.Errorf("configuration was rolled back to %s but the service could not be restarted: %w", previousInstance.Version, restartErr),
		)
	}
	return fmt.Errorf(
		"Code Execution update to %s failed and the service was restored to %s: %w",
		latest,
		previousInstance.Version,
		updateErr,
	)
}

// restoreCodeExecutionUpdateState puts the environment and registry back on
// the same previous version before the rollback restart is attempted.
func restoreCodeExecutionUpdateState(
	opts options,
	id string,
	registry codeExecutionRegistry,
	previous codeExecutionInstance,
	envPath string,
	originalEnv []byte,
) error {
	if err := atomicWriteFile(envPath, originalEnv, 0o600); err != nil {
		return err
	}
	for index := range registry.Instances {
		if registry.Instances[index].ID == id {
			registry.Instances[index] = previous
		}
	}
	return writeCodeExecutionRegistry(opts, registry)
}

func logsCodeExecution(opts options, id string) error {
	_, _, err := registeredCodeExecution(opts, id)
	if err != nil {
		return err
	}
	home, _ := codeExecutionInstanceHome(opts, id)
	tail := "all"
	if opts.lines > 0 {
		tail = strconv.Itoa(opts.lines)
	}
	args := []string{"logs", "--tail", tail, "--no-color"}
	if opts.follow {
		args = append(args, "--follow")
	}
	compose, _ := codeExecutionComposeArgs(opts, id, args...)
	return runDocker(compose, home)
}

func connectionCodeExecution(opts options, id string) error {
	_, instance, err := registeredCodeExecution(opts, id)
	if err != nil {
		return err
	}
	// The exported hostname is reachable only across the private helper
	// network. Best-effort repair must not hide the locally persisted
	// credentials when Docker is stopped or the backend is unavailable.
	if err := ensureDockerReady(opts); err != nil {
		fmt.Fprintf(os.Stderr, "Warning: could not verify Docker readiness: %v\n", err)
	}
	if err := ensureLauncherServicesNetwork(opts); err != nil {
		fmt.Fprintf(os.Stderr, "Warning: could not ensure the helper services network: %v\n", err)
	}
	if err := attachOmlorixBackendToHelperNetwork(opts); err != nil {
		fmt.Fprintf(os.Stderr, "Warning: could not attach the Omlorix backend to the helper services network: %v\n", err)
	}
	home, _ := codeExecutionInstanceHome(opts, id)
	env, _ := readEnv(filepath.Join(home, ".env"))
	apiKey := codeExecutionAPIKey(env)
	if apiKey == "" {
		return errors.New("Code Execution API key is missing")
	}
	payload := map[string]any{
		"name": instance.Name, "base_url": fmt.Sprintf("http://codeexec-%s:8000", id), "api_key": apiKey,
		"enabled_for_code_execution": true, "enabled_for_latex_pdf": true, "enabled_for_slide_renderer": true, "weight": 1,
	}
	if opts.jsonOutput {
		return printJSON(payload)
	}
	fmt.Printf("name=%s\nbase_url=%s\napi_key=%s\nenabled_for_code_execution=true\nenabled_for_latex_pdf=true\nenabled_for_slide_renderer=true\nweight=1\n", instance.Name, payload["base_url"], apiKey)
	return nil
}

func deleteCodeExecution(opts options, id string) error {
	registry, _, err := registeredCodeExecution(opts, id)
	if err != nil {
		return err
	}
	if opts.confirm != id {
		return fmt.Errorf("deleting containers, sessions, settings, and the Redis volume requires --confirm %s", id)
	}
	home, _ := codeExecutionInstanceHome(opts, id)
	args, _ := codeExecutionComposeArgs(opts, id, "down", "--remove-orphans", "--volumes")
	if err := runDocker(args, home); err != nil {
		return err
	}
	registry.Instances = filterCodeExecutionInstances(registry.Instances, id)
	if err := writeCodeExecutionRegistry(opts, registry); err != nil {
		return err
	}
	if err := os.RemoveAll(home); err != nil {
		return err
	}
	fmt.Printf("Deleted Code Execution service %s, including its containers, settings, sessions, and Redis volume. This cannot be recovered by the CLI.\n", id)
	return nil
}

func serializeCodeExecutionEnv(instance codeExecutionInstance, secret string) string {
	// Keep CLI-created instances on the same explicit runtime contract as the
	// Electron Launcher. The gateway supplies these defaults too, but persisting
	// them makes upgrades deterministic and keeps generated instance files useful
	// for diagnostics. Legacy switches below preserve intentionally pinned older
	// Code Execution releases.
	values := map[string]string{
		"COMPOSE_PROJECT_NAME":                     "omlorix-codeexec-" + instance.ID,
		"CODE_EXECUTION_INSTANCE_ID":               instance.ID,
		"CODE_EXECUTION_NETWORK_ALIAS":             "codeexec-" + instance.ID,
		"CODE_EXECUTION_VERSION":                   instance.Version,
		"CODE_EXECUTION_GATEWAY_IMAGE":             "ghcr.io/phinaldoo/omlorix-code-execution-gateway:" + instance.Version,
		"SANDBOX_IMAGE":                            "ghcr.io/phinaldoo/omlorix-code-execution-sandbox:" + instance.Version,
		"GATEWAY_HOST_BIND":                        "127.0.0.1",
		"GATEWAY_PORT":                             strconv.Itoa(instance.Port),
		"REDIS_URL":                                "redis://redis:6379/0",
		"REDIS_SOCKET_CONNECT_TIMEOUT":             "5",
		"REDIS_SOCKET_TIMEOUT":                     "5",
		"REDIS_HEALTH_CHECK_INTERVAL":              "30",
		"GATEWAY_DOCKER_HOST":                      "tcp://docker-proxy:2375",
		"DOCKER_CLIENT_TIMEOUT":                    "30",
		"APP_ENV":                                  "production",
		"ALLOW_RESTRICTED_LOCAL_DOCKER_PROXY":      "true",
		"REQUIRE_AUTH":                             "true",
		"METRICS_AUTH_REQUIRED":                    "true",
		"API_KEYS":                                 "launcher:" + secret,
		"ENABLE_DOCS":                              "false",
		"ENABLE_CORS":                              "false",
		"ALLOWED_HOSTS":                            "127.0.0.1,localhost,gateway,codeexec-" + instance.ID,
		"RENDER_ALLOWED_HOSTS":                     "127.0.0.1,localhost,gateway",
		"REQUIRE_SHARED_STATE":                     "true",
		"SANDBOX_NETWORK_MODE":                     boolChoice(instance.NetworkAccess, "bridge", "none"),
		"ALLOW_PIP_INSTALLS":                       strconv.FormatBool(instance.AllowPip),
		"ALLOW_SANDBOX_ENV_INJECTION":              "false",
		"SANDBOX_ENV_TARGET_PATH":                  "/home/sandbox/.env",
		"USE_DOCKER_DEFAULT_SECCOMP":               "true",
		"REQUIRE_STRONG_SANDBOX_ISOLATION":         "false",
		"STRONG_SANDBOX_RUNTIMES":                  "runsc,kata,kata-runtime,io.containerd.runsc.v1,io.containerd.kata.v2",
		"SANDBOX_RUNTIME":                          "",
		"SECCOMP_PROFILE_DAEMON_PATH":              "",
		"MAX_REQUEST_BODY_SIZE":                    "33554432",
		"MAX_INPUT_FILES":                          "10",
		"MAX_INPUT_FILE_SIZE":                      "5242880",
		"MAX_INPUT_TOTAL_SIZE":                     "20971520",
		"MAX_FILE_NAME_LENGTH":                     "128",
		"MAX_CONCURRENT_EXECUTIONS":                strconv.Itoa(instance.MaxConcurrent),
		"MAX_ACTIVE_SESSIONS":                      strconv.Itoa(maxInt(instance.MaxConcurrent*10, 20)),
		"MAX_CONTAINERS_PER_PRINCIPAL":             "3",
		"CONTAINER_CREATE_GUARD_TIMEOUT":           "30",
		"DEFAULT_TIMEOUT":                          "30",
		"FILE_PROVISION_TIMEOUT":                   "30",
		"SESSION_TIMEOUT_SECONDS":                  strconv.Itoa(instance.SessionTimeout),
		"MAX_SESSION_LIFETIME_SECONDS":             strconv.Itoa(maxInt(instance.SessionTimeout, 3600)),
		"MAX_EXECUTIONS_PER_SESSION":               "100",
		"RATE_LIMIT_REQUESTS_PER_WINDOW":           "30",
		"RATE_LIMIT_WINDOW_SECONDS":                "60",
		"CONTAINER_RATE_LIMIT_REQUESTS_PER_WINDOW": "10",
		"CONTAINER_RATE_LIMIT_WINDOW_SECONDS":      "60",
		"SANDBOX_USER":                             "sandbox",
		"SANDBOX_MEM_LIMIT":                        instance.Memory,
		"SANDBOX_CPU_PERIOD":                       "100000",
		"SANDBOX_CPU_QUOTA":                        "100000",
		"SANDBOX_PIDS_LIMIT":                       "256",
		"SANDBOX_TMP_ROOT_SIZE":                    "512m",
		"SANDBOX_SHM_SIZE":                         "128m",
		"SANDBOX_HOME_TMPFS_SIZE":                  "256m",
		"SANDBOX_READ_ONLY_ROOTFS":                 "true",
		"MAX_PIP_PACKAGES":                         "5",
		"MAX_PIP_PACKAGE_NAME_LENGTH":              "64",
		"MAX_CONCURRENT_RENDERS":                   "2",
		"RENDER_RATE_LIMIT_REQUESTS_PER_WINDOW":    "10",
		"RENDER_RATE_LIMIT_WINDOW_SECONDS":         "60",
		"RENDER_MAX_REQUEST_BODY_BYTES":            "180000000",
		"RENDER_MAX_HTML_CHARS":                    "2000000",
		"RENDER_MAX_INPUT_FILES":                   "32",
		"RENDER_MAX_SLIDES":                        "200",
		"RENDER_MAX_ASSET_BYTES":                   "25000000",
		"RENDER_MAX_TOTAL_ASSET_BYTES":             "120000000",
		"RENDER_MAX_OUTPUT_BYTES":                  "220000000",
		"RENDER_SANDBOX_MEM_LIMIT":                 "2g",
		"RENDER_SANDBOX_CPU_PERIOD":                "100000",
		"RENDER_SANDBOX_CPU_QUOTA":                 "200000",
		"RENDER_SANDBOX_PIDS_LIMIT":                "512",
		"RENDER_SANDBOX_TMP_ROOT_SIZE":             "1g",
		"RENDER_SANDBOX_SHM_SIZE":                  "512m",
		"RENDER_SANDBOX_HOME_TMPFS_SIZE":           "256m",
	}
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	var lines []string
	for _, key := range keys {
		lines = append(lines, key+"="+quoteEnv(values[key]))
	}
	return strings.Join(lines, "\n") + "\n"
}

func latestCodeExecutionVersion() (string, error) {
	client := http.Client{Timeout: 10 * time.Second}
	request, _ := http.NewRequest(http.MethodGet, codeExecutionLatestURL, nil)
	request.Header.Set("Accept", "application/vnd.github+json")
	request.Header.Set("User-Agent", "omlorix-server-cli")
	response, err := client.Do(request)
	if err != nil {
		return "", fmt.Errorf("could not check the latest Code Execution release: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return "", fmt.Errorf("Code Execution release check returned HTTP %d", response.StatusCode)
	}
	var payload latestReleaseResponse
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		return "", err
	}
	version := strings.TrimPrefix(strings.TrimSpace(payload.TagName), "v")
	if !semanticVersionPattern.MatchString(version) {
		return "", errors.New("Code Execution release response did not contain a valid version")
	}
	return version, nil
}

// parseCodeExecutionVersions mirrors the launcher's safe picker policy: only
// unique, published stable semantic versions are shown by default.
func parseCodeExecutionVersions(releases []codeExecutionRelease) []string {
	seen := map[string]bool{}
	versions := []string{}
	for _, release := range releases {
		if release.Draft || release.Prerelease {
			continue
		}
		version := strings.TrimPrefix(strings.TrimSpace(release.TagName), "v")
		if !semanticVersionPattern.MatchString(version) || seen[version] {
			continue
		}
		seen[version] = true
		versions = append(versions, version)
	}
	return versions
}

// availableCodeExecutionVersions provides the terminal equivalent of the
// launcher's concrete release picker.
func availableCodeExecutionVersions() ([]string, error) {
	client := http.Client{Timeout: 10 * time.Second}
	request, _ := http.NewRequest(http.MethodGet, codeExecutionReleasesURL, nil)
	request.Header.Set("Accept", "application/vnd.github+json")
	request.Header.Set("User-Agent", "omlorix-server-cli")
	response, err := client.Do(request)
	if err != nil {
		return nil, fmt.Errorf("could not load Code Execution releases: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("Code Execution releases returned HTTP %d", response.StatusCode)
	}
	var payload []codeExecutionRelease
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		return nil, err
	}
	versions := parseCodeExecutionVersions(payload)
	if len(versions) == 0 {
		return nil, errors.New("Code Execution releases did not contain a stable semantic version")
	}
	return versions, nil
}

func listCodeExecutionVersions(opts options) error {
	versions, err := availableCodeExecutionVersions()
	if err != nil {
		return err
	}
	latest, err := latestCodeExecutionVersion()
	if err != nil {
		return err
	}
	ordered := []string{latest}
	for _, version := range versions {
		if version != latest {
			ordered = append(ordered, version)
		}
	}
	versions = ordered
	if opts.jsonOutput {
		return printJSON(map[string]any{"latest_version": latest, "versions": versions})
	}
	fmt.Printf("Latest version: %s\n", latest)
	for _, version := range versions {
		fmt.Println(version)
	}
	return nil
}

var semanticVersionPattern = regexp.MustCompile(`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?$`)

func nextCodeExecutionPort(registry codeExecutionRegistry) (int, error) {
	for port := 8000; port <= 8999; port++ {
		if !codeExecutionPortUsed(registry, port) && portAvailable(port) {
			return port, nil
		}
	}
	return 0, errors.New("no available Code Execution port was found between 8000 and 8999")
}

func codeExecutionPortUsed(registry codeExecutionRegistry, port int) bool {
	for _, instance := range registry.Instances {
		if instance.Port == port {
			return true
		}
	}
	return false
}

func codeExecutionPortUsedExcept(registry codeExecutionRegistry, port int, excludedID string) bool {
	for _, instance := range registry.Instances {
		if instance.ID != excludedID && instance.Port == port {
			return true
		}
	}
	return false
}

func portAvailable(port int) bool {
	listener, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", port))
	if err != nil {
		return false
	}
	_ = listener.Close()
	return true
}

func probeCodeExecution(port int, apiKey, path string) bool {
	return requestCodeExecutionHealth(port, apiKey, path).OK
}

type codeExecutionHealthResponse struct {
	OK   bool
	Data map[string]any
}

// requestCodeExecutionHealth mirrors the Launcher's authenticated loopback
// health request and caps response size before decoding diagnostic metadata.
func requestCodeExecutionHealth(port int, apiKey, requestPath string) codeExecutionHealthResponse {
	request, _ := http.NewRequest(http.MethodGet, fmt.Sprintf("http://127.0.0.1:%d%s", port, requestPath), nil)
	request.Header.Set("Authorization", "Bearer "+apiKey)
	client := http.Client{Timeout: 2500 * time.Millisecond}
	response, err := client.Do(request)
	if err != nil {
		return codeExecutionHealthResponse{}
	}
	defer response.Body.Close()
	data := decodeCodeExecutionHealth(response.Body)
	return codeExecutionHealthResponse{
		OK:   response.StatusCode >= 200 && response.StatusCode < 300,
		Data: data,
	}
}

func decodeCodeExecutionHealth(reader io.Reader) map[string]any {
	data := map[string]any{}
	_ = json.NewDecoder(io.LimitReader(reader, 1024*1024)).Decode(&data)
	return data
}

func jsonMapString(values map[string]any, key string) string {
	value, _ := values[key].(string)
	return value
}

func jsonMapInt(values map[string]any, key string) int {
	value, _ := values[key].(float64)
	return int(value)
}

func jsonMapBool(values map[string]any, key string) bool {
	value, _ := values[key].(bool)
	return value
}

func codeExecutionAPIKey(env map[string]string) string {
	_, secret, ok := strings.Cut(env["API_KEYS"], ":")
	if !ok {
		return ""
	}
	return secret
}

func attachOmlorixBackendToHelperNetwork(opts options) error {
	raw, err := runCapture(dockerExecutable(), composeArgs(opts, "ps", "-q", "fastapi"), opts.home)
	containerID := ""
	if err == nil && strings.TrimSpace(raw) != "" {
		containerID = strings.Fields(raw)[0]
	}
	if containerID == "" {
		containerID, err = backendContainerForPublishedFrontend(opts)
		if err != nil {
			return err
		}
	}
	if containerID == "" {
		return nil
	}
	if output, err := runCapture(dockerExecutable(), []string{"network", "connect", codeExecutionNetwork, containerID}, opts.home); err != nil {
		detail := strings.TrimSpace(output)
		if detail == "" {
			detail = err.Error()
		}
		if !strings.Contains(strings.ToLower(detail), "already exists") {
			return fmt.Errorf("Docker network connect failed: %s", detail)
		}
	}
	return nil
}

// backendContainerForPublishedFrontend finds the FastAPI service belonging to
// the single Compose frontend that publishes Omlorix's configured HTTP port.
// Ambiguous matches fail closed instead of attaching an unrelated stack.
func backendContainerForPublishedFrontend(opts options) (string, error) {
	env, _ := readEnv(opts.envFile)
	port, err := strconv.Atoi(firstNonBlank(env["FRONTEND_HTTP_HOST_PORT"], "8080"))
	if err != nil || port < 1 || port > 65535 {
		return "", nil
	}

	frontendRaw, err := runCapture(dockerExecutable(), []string{
		"ps",
		"--filter", "label=com.docker.compose.service=frontend",
		"--format", "{{.ID}}",
	}, opts.home)
	if err != nil {
		return "", nil
	}
	matchingFrontendIDs := []string{}
	for _, frontendID := range strings.Fields(frontendRaw) {
		portsRaw, inspectErr := runCapture(dockerExecutable(), []string{
			"inspect", "--format", "{{json .NetworkSettings.Ports}}", frontendID,
		}, opts.home)
		if inspectErr != nil {
			continue
		}
		var publishedPorts map[string][]map[string]string
		if err := json.Unmarshal([]byte(strings.TrimSpace(portsRaw)), &publishedPorts); err != nil {
			continue
		}
		matched := false
		for _, bindings := range publishedPorts {
			for _, binding := range bindings {
				if binding["HostPort"] == strconv.Itoa(port) {
					matched = true
					break
				}
			}
			if matched {
				break
			}
		}
		if matched {
			matchingFrontendIDs = append(matchingFrontendIDs, frontendID)
		}
	}
	if len(matchingFrontendIDs) != 1 {
		return "", nil
	}

	projectRaw, err := runCapture(dockerExecutable(), []string{
		"inspect", "--format", `{{ index .Config.Labels "com.docker.compose.project" }}`, matchingFrontendIDs[0],
	}, opts.home)
	if err != nil || strings.TrimSpace(projectRaw) == "" {
		return "", nil
	}
	projectName := strings.TrimSpace(projectRaw)
	backendRaw, err := runCapture(dockerExecutable(), []string{
		"ps",
		"--filter", "label=com.docker.compose.project=" + projectName,
		"--filter", "label=com.docker.compose.service=fastapi",
		"--format", "{{.ID}}",
	}, opts.home)
	if err != nil {
		return "", nil
	}
	backendIDs := strings.Fields(backendRaw)
	if len(backendIDs) != 1 {
		return "", nil
	}
	return backendIDs[0], nil
}

// validateCodeExecutionLimits keeps CLI persistence within the Launcher's
// accepted concurrency and session lifetime bounds.
func validateCodeExecutionLimits(maxConcurrent, sessionTimeout int) error {
	if maxConcurrent < 1 || maxConcurrent > 100 {
		return errors.New("--max-concurrent must be between 1 and 100")
	}
	if sessionTimeout < 60 || sessionTimeout > 86400 {
		return errors.New("--session-timeout must be between 60 and 86400 seconds")
	}
	return nil
}

// restoreCodeExecutionEnv rolls back the first store if the registry commit
// fails, preserving the instance's two-file transaction boundary.
func restoreCodeExecutionEnv(path string, original []byte, cause error) error {
	if err := atomicWriteFile(path, original, 0o600); err != nil {
		return errors.Join(cause, fmt.Errorf("could not restore the original Code Execution environment: %w", err))
	}
	return cause
}

func slugify(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	value = regexp.MustCompile(`[^a-z0-9]+`).ReplaceAllString(value, "-")
	value = strings.Trim(value, "-")
	if value == "" {
		value = "execution"
	}
	if len(value) > 36 {
		value = strings.Trim(value[:36], "-")
	}
	return value
}

func filterCodeExecutionInstances(values []codeExecutionInstance, id string) []codeExecutionInstance {
	result := make([]codeExecutionInstance, 0, len(values))
	for _, value := range values {
		if value.ID != id {
			result = append(result, value)
		}
	}
	return result
}

func firstPositive(value, fallback int) int {
	if value > 0 {
		return value
	}
	return fallback
}

func maxInt(left, right int) int {
	if left > right {
		return left
	}
	return right
}

func boolChoice(value bool, whenTrue, whenFalse string) string {
	if value {
		return whenTrue
	}
	return whenFalse
}
