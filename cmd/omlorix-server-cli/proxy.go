package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/youmark/pkcs8"
)

const (
	defaultProxyHTTPPort   = "8081"
	defaultProxyHTTPSPort  = "8443"
	proxyReadHeaderTimeout = 10 * time.Second
	proxyIdleTimeout       = 2 * time.Minute
	proxyControlTimeout    = 750 * time.Millisecond
	visitorStateVersion    = 1
	visitorVerificationTTL = 60 * time.Second
)

// managedProxyConfig is shared by every CLI proxy lifecycle command. Its keys
// intentionally match Electron's normalizeProxyConfig contract.
type managedProxyConfig struct {
	Enabled             bool
	Autostart           bool
	BindHost            string
	HTTPPort            string
	HTTPSEnabled        bool
	HTTPSPort           string
	RedirectHTTPToHTTPS bool
	TLSCertPath         string
	TLSKeyPath          string
	TLSCAPath           string
	TLSKeyPassphrase    string
	PublicHostname      string
	LauncherSecret      string
	Target              *url.URL
}

type proxyStatus struct {
	Enabled               bool   `json:"enabled"`
	Running               bool   `json:"running"`
	ConfigurationCurrent  bool   `json:"configuration_current"`
	ServiceInstalled      bool   `json:"service_installed"`
	ServiceUpdateRequired bool   `json:"service_update_required"`
	HTTPURL               string `json:"http_url,omitempty"`
	HTTPSURL              string `json:"https_url,omitempty"`
	PID                   int    `json:"pid,omitempty"`
}

// proxyRuntimeState is the ownership and readiness receipt for one live proxy.
// A PID alone is not an identity because operating systems reuse process IDs.
// The random token binds lifecycle commands to the loopback control listener
// owned by this exact process, and the configuration fingerprint lets callers
// restart a live proxy when security-sensitive settings change.
type proxyRuntimeState struct {
	PID                      int    `json:"pid"`
	ControlURL               string `json:"control_url"`
	ControlToken             string `json:"control_token"`
	ConfigurationFingerprint string `json:"configuration_fingerprint"`
}

type proxyControlServer struct {
	listener net.Listener
	server   *http.Server
	stop     chan struct{}
	stopOnce sync.Once
}

type visitorIPDetection struct {
	FrontendIP        string `json:"frontend_ip,omitempty"`
	FrontendContainer string `json:"frontend_container_id,omitempty"`
	BackendContainer  string `json:"backend_container_id,omitempty"`
	GatewayIP         string `json:"gateway_ip,omitempty"`
	Network           string `json:"network,omitempty"`
	Loopback          bool   `json:"frontend_bound_to_loopback"`
}

type visitorIPVerification struct {
	Verified            bool   `json:"verified"`
	VerifiedAt          string `json:"verified_at,omitempty"`
	VerificationPath    string `json:"verification_path,omitempty"`
	TopologyFingerprint string `json:"topology_fingerprint,omitempty"`
	ClientIP            string `json:"client_ip,omitempty"`
	Scheme              string `json:"scheme,omitempty"`
	Host                string `json:"host,omitempty"`
	ErrorCode           string `json:"error_code,omitempty"`
}

type visitorIPStatus struct {
	Ready        bool                  `json:"ready"`
	Configured   bool                  `json:"configured"`
	Pending      bool                  `json:"pending_calibration"`
	ProxyRunning bool                  `json:"proxy_running"`
	Detection    visitorIPDetection    `json:"detection"`
	Verification visitorIPVerification `json:"verification"`
}

type proxyVerificationResponse struct {
	ClientIP           string `json:"client_ip"`
	Scheme             string `json:"scheme"`
	Host               string `json:"host"`
	Nonce              string `json:"nonce"`
	TrustChainAccepted bool   `json:"trust_chain_accepted"`
}

func proxyPIDPath(opts options) string     { return filepath.Join(opts.home, ".omlorix-proxy.pid") }
func proxyLogPath(opts options) string     { return filepath.Join(opts.home, "omlorix-proxy.log") }
func visitorStatePath(opts options) string { return filepath.Join(opts.home, ".visitor-ip-state.json") }

func randomHexSecret(bytes int) (string, error) {
	value := make([]byte, bytes)
	if _, err := rand.Read(value); err != nil {
		return "", err
	}
	return hex.EncodeToString(value), nil
}

func normalizeManagedProxyConfig(env map[string]string) (managedProxyConfig, error) {
	bindHost := firstNonBlank(env["OMLORIX_LAUNCHER_PROXY_BIND"], "0.0.0.0")
	frontendPort := firstNonBlank(env["FRONTEND_HTTP_HOST_PORT"], "8080")
	trustedPublicHost := firstNonBlank(strings.Split(env["TRUSTED_HOSTS"], ",")...)
	publicHostname := firstNonBlank(env["OMLORIX_LAUNCHER_PROXY_PUBLIC_HOSTNAME"], trustedPublicHost, publicHostForProxyBind(bindHost))
	target, err := url.Parse("http://127.0.0.1:" + frontendPort)
	if err != nil {
		return managedProxyConfig{}, errors.New("the Docker frontend target is invalid")
	}
	config := managedProxyConfig{
		Enabled:             envTruthy(env["OMLORIX_LAUNCHER_PROXY_ENABLED"], false),
		Autostart:           envTruthy(env["OMLORIX_LAUNCHER_PROXY_AUTOSTART"], true),
		BindHost:            bindHost,
		HTTPPort:            firstNonBlank(env["OMLORIX_LAUNCHER_PROXY_HTTP_PORT"], defaultProxyHTTPPort),
		HTTPSEnabled:        envTruthy(env["OMLORIX_LAUNCHER_PROXY_HTTPS_ENABLED"], false),
		HTTPSPort:           firstNonBlank(env["OMLORIX_LAUNCHER_PROXY_HTTPS_PORT"], defaultProxyHTTPSPort),
		RedirectHTTPToHTTPS: envTruthy(env["OMLORIX_LAUNCHER_PROXY_REDIRECT_HTTP_TO_HTTPS"], false),
		TLSCertPath:         strings.TrimSpace(env["OMLORIX_LAUNCHER_PROXY_TLS_CERT_PATH"]),
		TLSKeyPath:          strings.TrimSpace(env["OMLORIX_LAUNCHER_PROXY_TLS_KEY_PATH"]),
		TLSCAPath:           strings.TrimSpace(env["OMLORIX_LAUNCHER_PROXY_TLS_CA_PATH"]),
		TLSKeyPassphrase:    env["OMLORIX_LAUNCHER_PROXY_TLS_KEY_PASSPHRASE"],
		PublicHostname:      publicHostname,
		LauncherSecret:      strings.TrimSpace(env["OMLORIX_LAUNCHER_PROXY_SECRET"]),
		Target:              target,
	}
	if net.ParseIP(config.BindHost) == nil && config.BindHost != "localhost" {
		return managedProxyConfig{}, errors.New("proxy bind address must be an IP address or localhost")
	}
	if !validPublicHostname(config.PublicHostname) {
		return managedProxyConfig{}, errors.New("proxy public hostname is invalid")
	}
	for label, value := range map[string]string{"HTTP": config.HTTPPort, "HTTPS": config.HTTPSPort} {
		port, parseErr := strconv.Atoi(value)
		if parseErr != nil || port < 1 || port > 65535 {
			return managedProxyConfig{}, fmt.Errorf("proxy %s port must be between 1 and 65535", label)
		}
	}
	bindCouldReachTarget := map[string]bool{
		"0.0.0.0":   true,
		"::":        true,
		"127.0.0.1": true,
		"localhost": true,
		"::1":       true,
	}[config.BindHost]
	if bindCouldReachTarget && config.HTTPPort == frontendPort {
		return managedProxyConfig{}, errors.New("HTTP proxy port must be different from the Omlorix Docker port")
	}
	if config.HTTPSEnabled && config.HTTPPort == config.HTTPSPort {
		return managedProxyConfig{}, errors.New("HTTPS proxy port must be different from the HTTP proxy port")
	}
	if config.HTTPSEnabled && bindCouldReachTarget && config.HTTPSPort == frontendPort {
		return managedProxyConfig{}, errors.New("HTTPS proxy port must be different from the Omlorix Docker port")
	}
	if config.Enabled {
		decoded, decodeErr := hex.DecodeString(config.LauncherSecret)
		if decodeErr != nil || len(decoded) != 32 {
			return managedProxyConfig{}, errors.New("managed proxy authentication credential is missing or invalid")
		}
	}
	if config.HTTPSEnabled && (config.TLSCertPath == "" || config.TLSKeyPath == "") {
		return managedProxyConfig{}, errors.New("HTTPS requires a TLS certificate and private key")
	}
	return config, nil
}

// proxyConfigFingerprint covers every value that changes the live listener,
// forwarding boundary, or TLS identity. Only the digest is persisted, so the
// launcher secret and private-key passphrase never enter status output.
func proxyConfigFingerprint(config managedProxyConfig) string {
	serialized, _ := json.Marshal(struct {
		Enabled             bool
		BindHost            string
		HTTPPort            string
		HTTPSEnabled        bool
		HTTPSPort           string
		RedirectHTTPToHTTPS bool
		TLSCertPath         string
		TLSKeyPath          string
		TLSCAPath           string
		TLSKeyPassphrase    string
		PublicHostname      string
		LauncherSecret      string
		Target              string
	}{
		Enabled:             config.Enabled,
		BindHost:            config.BindHost,
		HTTPPort:            config.HTTPPort,
		HTTPSEnabled:        config.HTTPSEnabled,
		HTTPSPort:           config.HTTPSPort,
		RedirectHTTPToHTTPS: config.RedirectHTTPToHTTPS,
		TLSCertPath:         config.TLSCertPath,
		TLSKeyPath:          config.TLSKeyPath,
		TLSCAPath:           config.TLSCAPath,
		TLSKeyPassphrase:    config.TLSKeyPassphrase,
		PublicHostname:      config.PublicHostname,
		LauncherSecret:      config.LauncherSecret,
		Target:              config.Target.String(),
	})
	digest := sha256.Sum256(serialized)
	return hex.EncodeToString(digest[:])
}

func validPublicHostname(value string) bool {
	host := strings.TrimSpace(value)
	if host == "" || len(host) > 253 || strings.ContainsAny(host, " /\\\t\r\n") {
		return false
	}
	if strings.HasPrefix(host, "[") && strings.HasSuffix(host, "]") {
		return net.ParseIP(strings.TrimSuffix(strings.TrimPrefix(host, "["), "]")) != nil
	}
	if net.ParseIP(host) != nil {
		return true
	}
	for _, label := range strings.Split(host, ".") {
		if label == "" || len(label) > 63 || strings.HasPrefix(label, "-") || strings.HasSuffix(label, "-") {
			return false
		}
		for _, character := range label {
			if (character >= 'a' && character <= 'z') || (character >= 'A' && character <= 'Z') ||
				(character >= '0' && character <= '9') || character == '-' {
				continue
			}
			return false
		}
	}
	return true
}

func publicHostForProxyBind(bind string) string {
	switch bind {
	case "", "0.0.0.0", "::":
		return "localhost"
	default:
		return strings.Trim(bind, "[]")
	}
}

func proxyPublicURL(config managedProxyConfig, httpsEnabled bool) string {
	protocol, port := "http", config.HTTPPort
	if httpsEnabled {
		protocol, port = "https", config.HTTPSPort
	}
	host := config.PublicHostname
	if strings.Contains(host, ":") && !strings.HasPrefix(host, "[") {
		host = "[" + host + "]"
	}
	if (protocol == "http" && port == "80") || (protocol == "https" && port == "443") {
		return protocol + "://" + host
	}
	return protocol + "://" + host + ":" + port
}

func proxySettingsOutput(settings serverSettings) map[string]any {
	proxy := settings.Proxy
	return map[string]any{
		"enabled": proxy.Enabled, "autostart": proxy.Autostart,
		"bind": proxy.BindHost, "public_hostname": proxy.PublicHostname,
		"http_port": proxy.HTTPPort, "https_enabled": proxy.HTTPSEnabled,
		"https_port": proxy.HTTPSPort, "redirect_http_to_https": proxy.RedirectHTTPToHTTPS,
		"tls_cert_path": proxy.TLSCertPath, "tls_key_path": proxy.TLSKeyPath,
		"tls_ca_path": proxy.TLSCAPath, "tls_key_passphrase_set": proxy.TLSKeyPassphrase != "",
	}
}

func parseProxySettingBool(name, value string) (bool, error) {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "true":
		return true, nil
	case "false":
		return false, nil
	default:
		return false, fmt.Errorf("proxy setting %s must be true or false", name)
	}
}

// configureProxySettings gives standalone CLI installations an intentional
// management interface now that host listener state no longer belongs in .env.
func configureProxySettings(opts options, arguments []string) error {
	settings, err := readServerSettings(opts)
	if err != nil {
		return err
	}
	for _, argument := range arguments {
		name, value, found := strings.Cut(argument, "=")
		if !found || strings.TrimSpace(name) == "" {
			return errors.New("proxy settings use name=value arguments")
		}
		name = strings.ToLower(strings.TrimSpace(name))
		switch name {
		case "enabled":
			// Enabling and disabling are lifecycle transitions, not ordinary
			// listener settings. Keeping them behind the dedicated commands makes
			// it impossible to leave a disabled listener running or to enable one
			// without applying the container trust and isolation boundary.
			return errors.New("use `omlorix-server proxy enable` or `omlorix-server proxy disable` to change enabled state")
		case "autostart", "https-enabled", "redirect-http-to-https":
			parsed, parseErr := parseProxySettingBool(name, value)
			if parseErr != nil {
				return parseErr
			}
			switch name {
			case "autostart":
				settings.Proxy.Autostart = parsed
			case "https-enabled":
				settings.Proxy.HTTPSEnabled = parsed
			case "redirect-http-to-https":
				settings.Proxy.RedirectHTTPToHTTPS = parsed
			}
		case "bind":
			settings.Proxy.BindHost = value
		case "public-hostname":
			settings.Proxy.PublicHostname = value
		case "http-port":
			settings.Proxy.HTTPPort = value
		case "https-port":
			settings.Proxy.HTTPSPort = value
		case "tls-cert-path":
			settings.Proxy.TLSCertPath = value
		case "tls-key-path":
			settings.Proxy.TLSKeyPath = value
		case "tls-ca-path":
			settings.Proxy.TLSCAPath = value
		case "tls-key-passphrase":
			settings.Proxy.TLSKeyPassphrase = value
		case "tls-key-passphrase-file":
			info, statErr := os.Stat(value)
			if statErr != nil || !info.Mode().IsRegular() || info.Size() > 64*1024 {
				return errors.New("TLS key passphrase file must be a regular file smaller than 64 KiB")
			}
			contents, readErr := os.ReadFile(value)
			if readErr != nil {
				return errors.New("could not read TLS key passphrase file")
			}
			settings.Proxy.TLSKeyPassphrase = strings.TrimSuffix(string(contents), "\n")
			settings.Proxy.TLSKeyPassphrase = strings.TrimSuffix(settings.Proxy.TLSKeyPassphrase, "\r")
		default:
			return fmt.Errorf("unknown proxy setting %q", name)
		}
	}
	if settings.Proxy.Enabled {
		if err := ensureLauncherProxySecret(opts); err != nil {
			return err
		}
	}
	env, _ := readEnv(opts.envFile)
	applyServerSettingsToEnv(env, settings)
	if _, err := normalizeManagedProxyConfig(env); err != nil {
		return err
	}
	if settings.Proxy.Enabled && strings.TrimSpace(env["FRONTEND_HTTP_HOST_BIND"]) != "127.0.0.1" {
		if err := writeEnv(opts.envFile, map[string]string{"FRONTEND_HTTP_HOST_BIND": "127.0.0.1"}); err != nil {
			return errors.New("could not isolate the Docker frontend behind the managed proxy")
		}
	}
	return writeServerSettings(opts, settings)
}

func commandProxy(opts options) error {
	if len(opts.arguments) < 1 {
		return errors.New("usage: omlorix-server proxy <status|settings|configure|enable|disable|start|stop|restart|install-service|refresh-service|uninstall-service>")
	}
	action := strings.ToLower(opts.arguments[0])
	arguments := opts.arguments[1:]
	if action != "configure" && len(arguments) != 0 {
		return fmt.Errorf("proxy %s does not accept additional arguments", action)
	}
	if action != "run" {
		if err := ensureServerHome(opts); err != nil {
			return err
		}
	} else if err := migrateLegacyServerSettings(opts); err != nil {
		// Native service entry points intentionally skip the asset installer, but
		// they must still understand a home created by the preceding env-backed
		// proxy version before deciding whether the listener is enabled.
		return err
	}
	switch action {
	case "settings":
		settings, err := readServerSettings(opts)
		if err != nil {
			return err
		}
		if opts.jsonOutput {
			return printJSON(proxySettingsOutput(settings))
		}
		values := proxySettingsOutput(settings)
		for _, key := range []string{
			"enabled", "autostart", "bind", "public_hostname", "http_port",
			"https_enabled", "https_port", "redirect_http_to_https",
			"tls_cert_path", "tls_key_path", "tls_ca_path", "tls_key_passphrase_set",
		} {
			fmt.Printf("%s=%v\n", key, values[key])
		}
		return nil
	case "configure":
		if len(arguments) == 0 {
			return errors.New("usage: omlorix-server proxy configure name=value [name=value ...]")
		}
		if err := configureProxySettings(opts, arguments); err != nil {
			return err
		}
		if opts.jsonOutput {
			settings, _ := readServerSettings(opts)
			return printJSON(proxySettingsOutput(settings))
		}
		fmt.Println("Proxy settings saved. Restart the proxy to apply listener changes.")
		return nil
	case "status":
		status := collectProxyStatus(opts)
		if opts.jsonOutput {
			return printJSON(status)
		}
		fmt.Printf("Proxy %s\n", map[bool]string{true: "running", false: "stopped"}[status.Running])
		fmt.Printf("  Enabled           %t\n", status.Enabled)
		fmt.Printf("  Service installed %t\n", status.ServiceInstalled)
		if status.HTTPURL != "" {
			fmt.Printf("  HTTP              %s\n", status.HTTPURL)
		}
		if status.HTTPSURL != "" {
			fmt.Printf("  HTTPS             %s\n", status.HTTPSURL)
		}
		return nil
	case "enable":
		if err := enableManagedProxy(opts); err != nil {
			return err
		}
		status := collectProxyStatus(opts)
		if opts.jsonOutput {
			return printJSON(status)
		}
		fmt.Println("Managed proxy enabled and started.")
		printProxyExposureWarning(opts)
		return nil
	case "disable":
		if err := disableManagedProxy(opts); err != nil {
			return err
		}
		if opts.jsonOutput {
			return printJSON(collectProxyStatus(opts))
		}
		fmt.Println("Managed proxy disabled. The Docker frontend remains bound to loopback until you explicitly change it.")
		return nil
	case "start":
		if err := startManagedProxy(opts); err != nil {
			return err
		}
		if opts.jsonOutput {
			return printJSON(collectProxyStatus(opts))
		}
		return nil
	case "stop":
		if err := stopManagedProxy(opts); err != nil {
			return err
		}
		if opts.jsonOutput {
			return printJSON(collectProxyStatus(opts))
		}
		return nil
	case "restart":
		if err := restartManagedProxy(opts); err != nil {
			return err
		}
		if opts.jsonOutput {
			return printJSON(collectProxyStatus(opts))
		}
		return nil
	case "run":
		return runManagedProxy(opts)
	case "install-service":
		if err := installProxyService(opts); err != nil {
			return err
		}
		if opts.jsonOutput {
			return printJSON(collectProxyStatus(opts))
		}
		return nil
	case "refresh-service":
		if err := refreshProxyService(opts); err != nil {
			return err
		}
		if opts.jsonOutput {
			return printJSON(collectProxyStatus(opts))
		}
		return nil
	case "uninstall-service":
		if err := uninstallProxyService(opts); err != nil {
			return err
		}
		if opts.jsonOutput {
			return printJSON(collectProxyStatus(opts))
		}
		return nil
	default:
		return fmt.Errorf("unknown proxy action %q", action)
	}
}

// validateManagedProxyEnableConfig checks the configuration that would become
// active before the enabled bit is persisted. HTTPS validation loads the real
// certificate, CA chain, and private key so an unusable TLS setup cannot be
// saved as an apparently enabled proxy.
func validateManagedProxyEnableConfig(env map[string]string) (managedProxyConfig, error) {
	prospective := make(map[string]string, len(env)+1)
	for key, value := range env {
		prospective[key] = value
	}
	prospective["OMLORIX_LAUNCHER_PROXY_ENABLED"] = "true"
	// A new installation does not have its random proxy credential yet. Use a
	// valid in-memory placeholder for structural validation; enableManagedProxy
	// generates and persists the real credential only after bind/TLS checks pass.
	decodedSecret, decodeErr := hex.DecodeString(strings.TrimSpace(prospective["OMLORIX_LAUNCHER_PROXY_SECRET"]))
	if decodeErr != nil || len(decodedSecret) != 32 {
		prospective["OMLORIX_LAUNCHER_PROXY_SECRET"] = strings.Repeat("0", 64)
	}
	config, err := normalizeManagedProxyConfig(prospective)
	if err != nil {
		return managedProxyConfig{}, err
	}
	if config.HTTPSEnabled {
		if _, err := loadProxyTLSCertificate(config); err != nil {
			return managedProxyConfig{}, err
		}
	}
	return config, nil
}

// writeManagedProxyEnabled persists host-listener state in the shared
// management settings file. These values used to live in .env, but that file
// is now reserved for settings consumed by Compose and the application.
func writeManagedProxyEnabled(opts options, enabled bool) error {
	settings, err := readServerSettings(opts)
	if err != nil {
		return err
	}
	settings.Proxy.Enabled = enabled
	return writeServerSettings(opts, settings)
}

// enableManagedProxy exposes the same safe workflow as the Launcher's enable
// toggle: validate first, persist the setting, isolate the Docker frontend, and
// start the authoritative host listener. A startup failure leaves the frontend
// fail-closed on loopback rather than reopening a proxy bypass.
func enableManagedProxy(opts options) error {
	env, _, err := readManagedEnvironment(opts)
	if err != nil {
		return err
	}
	if _, err := validateManagedProxyEnableConfig(env); err != nil {
		return fmt.Errorf("managed proxy settings are invalid: %w", err)
	}
	if err := ensureLauncherProxySecret(opts); err != nil {
		return err
	}
	if err := writeEnv(opts.envFile, map[string]string{
		"FRONTEND_HTTP_HOST_BIND":      "127.0.0.1",
		"FRONTEND_TRUST_PROXY_HEADERS": "true",
	}); err != nil {
		return fmt.Errorf("could not enable the managed proxy: %w", err)
	}
	if err := writeManagedProxyEnabled(opts, true); err != nil {
		return fmt.Errorf("could not save the enabled managed proxy setting: %w", err)
	}
	if err := startManagedProxy(opts); err != nil {
		return fmt.Errorf("managed proxy was enabled but could not start; the Docker frontend remains isolated on loopback: %w", err)
	}
	// Match the Launcher's enable toggle for an already-running stack: apply and
	// verify the authenticated forwarding boundary immediately. A stopped stack
	// is calibrated later by the ordinary start workflow.
	stack := inspectComposeStack(opts)
	applicationRunning := map[string]bool{}
	for _, service := range stack.Services {
		if strings.EqualFold(service.State, "running") {
			applicationRunning[service.Name] = true
		}
	}
	if stack.Error == "" && applicationRunning["frontend"] && applicationRunning["fastapi"] {
		if err := repairVisitorIPMutation(opts); err != nil {
			return fmt.Errorf("managed proxy is enabled, but visitor-IP forwarding could not be verified: %w", err)
		}
	}
	return nil
}

// disableManagedProxy closes the public listener before persisting the
// disabled setting. It intentionally keeps the Docker frontend on loopback so
// disabling ingress cannot accidentally expose an unauthenticated bypass.
func disableManagedProxy(opts options) error {
	if err := stopManagedProxy(opts); err != nil {
		return fmt.Errorf("could not stop the managed proxy, so it was not disabled: %w", err)
	}
	if err := writeManagedProxyEnabled(opts, false); err != nil {
		return fmt.Errorf("managed proxy stopped but its disabled setting could not be saved: %w", err)
	}
	return nil
}

// printProxyExposureWarning makes the security consequence of a public HTTP
// listener explicit at the moment it is enabled.
func printProxyExposureWarning(opts options) {
	env, _, settingsErr := readManagedEnvironment(opts)
	if settingsErr != nil {
		return
	}
	config, err := normalizeManagedProxyConfig(env)
	if err != nil {
		return
	}
	bindIP := net.ParseIP(config.BindHost)
	publicBind := config.BindHost != "localhost" && (bindIP == nil || !bindIP.IsLoopback())
	if publicBind && !config.HTTPSEnabled {
		fmt.Fprintln(os.Stderr, "Warning: the managed proxy is publicly bound over HTTP. Use HTTPS or a trusted TLS-terminating upstream before sending credentials over an untrusted network.")
	}
}

func collectProxyStatus(opts options) proxyStatus {
	env, _, settingsErr := readManagedEnvironment(opts)
	config, configErr := normalizeManagedProxyConfig(env)
	if settingsErr != nil {
		configErr = settingsErr
	}
	runtimeState := readProxyRuntimeState(opts)
	running := proxyRuntimeHealthy(runtimeState)
	serviceInstalled := proxyServiceInstalled(opts)
	status := proxyStatus{
		Enabled:               config.Enabled,
		Running:               running,
		ServiceInstalled:      serviceInstalled,
		ServiceUpdateRequired: serviceInstalled && !proxyServiceExecutableCurrent(opts),
		PID:                   runtimeState.PID,
	}
	if configErr == nil {
		status.ConfigurationCurrent = running && subtle.ConstantTimeCompare(
			[]byte(runtimeState.ConfigurationFingerprint),
			[]byte(proxyConfigFingerprint(config)),
		) == 1
		status.HTTPURL = proxyPublicURL(config, false)
		if config.HTTPSEnabled {
			status.HTTPSURL = proxyPublicURL(config, true)
		}
	} else {
		status.Enabled = envTruthy(env["OMLORIX_LAUNCHER_PROXY_ENABLED"], false)
	}
	return status
}

func readProxyRuntimeState(opts options) proxyRuntimeState {
	raw, err := os.ReadFile(proxyPIDPath(opts))
	if err != nil {
		return proxyRuntimeState{}
	}
	state := proxyRuntimeState{}
	if json.Unmarshal(raw, &state) != nil || !validProxyRuntimeState(state) {
		return proxyRuntimeState{}
	}
	return state
}

func validProxyRuntimeState(state proxyRuntimeState) bool {
	if state.PID < 1 || len(state.ControlToken) != 64 || len(state.ConfigurationFingerprint) != 64 {
		return false
	}
	if _, err := hex.DecodeString(state.ControlToken); err != nil {
		return false
	}
	if _, err := hex.DecodeString(state.ConfigurationFingerprint); err != nil {
		return false
	}
	controlURL, err := url.Parse(state.ControlURL)
	if err != nil || controlURL.Scheme != "http" || controlURL.User != nil || controlURL.Path != "" || controlURL.RawQuery != "" {
		return false
	}
	host, _, err := net.SplitHostPort(controlURL.Host)
	return err == nil && net.ParseIP(host) != nil && net.ParseIP(host).IsLoopback()
}

func proxyRuntimeRequest(state proxyRuntimeState, method string, requestPath string) bool {
	if !validProxyRuntimeState(state) {
		return false
	}
	request, err := http.NewRequest(method, strings.TrimRight(state.ControlURL, "/")+requestPath, nil)
	if err != nil {
		return false
	}
	request.Header.Set("Authorization", "Bearer "+state.ControlToken)
	client := &http.Client{
		Timeout: proxyControlTimeout,
		Transport: &http.Transport{
			Proxy: nil,
		},
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	response, err := client.Do(request)
	if err != nil {
		return false
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 1024))
	return response.StatusCode == http.StatusNoContent
}

func proxyRuntimeHealthy(state proxyRuntimeState) bool {
	return proxyRuntimeRequest(state, http.MethodGet, "/ready")
}

// ensureLauncherProxySecret creates the same fixed-format credential used by
// Electron. The value is never printed and is covered by normal .env backups.
func ensureLauncherProxySecret(opts options) error {
	env, _ := readEnv(opts.envFile)
	decoded, err := hex.DecodeString(strings.TrimSpace(env["OMLORIX_LAUNCHER_PROXY_SECRET"]))
	if err == nil && len(decoded) == 32 {
		return nil
	}
	secret, err := randomHexSecret(32)
	if err != nil {
		return err
	}
	return writeEnv(opts.envFile, map[string]string{"OMLORIX_LAUNCHER_PROXY_SECRET": secret})
}

// ensureManagedProxyFrontendIsolation closes the direct Docker ingress before
// either the proxy or Compose starts. This mutation is a security prerequisite,
// not readiness work, so asynchronous --no-wait workflows must apply it too.
func ensureManagedProxyFrontendIsolation(opts options, env map[string]string) error {
	if !envTruthy(env["OMLORIX_LAUNCHER_PROXY_ENABLED"], false) {
		return nil
	}
	if strings.TrimSpace(env["FRONTEND_HTTP_HOST_BIND"]) == "127.0.0.1" {
		return nil
	}
	if err := writeEnv(opts.envFile, map[string]string{"FRONTEND_HTTP_HOST_BIND": "127.0.0.1"}); err != nil {
		return errors.New("could not isolate the Docker frontend behind the managed proxy")
	}
	env["FRONTEND_HTTP_HOST_BIND"] = "127.0.0.1"
	return nil
}

func startManagedProxy(opts options) error {
	if err := ensureLauncherProxySecret(opts); err != nil {
		return err
	}
	env, _, err := readManagedEnvironment(opts)
	if err != nil {
		return err
	}
	previousBind := strings.TrimSpace(env["FRONTEND_HTTP_HOST_BIND"])
	if err := ensureManagedProxyFrontendIsolation(opts, env); err != nil {
		return err
	}
	// If the frontend container is already running, changing .env alone does
	// not close its public Docker port. Recreate it before opening the managed
	// listener so there is never a proxy-bypass window.
	isolatedBind := strings.TrimSpace(env["FRONTEND_HTTP_HOST_BIND"]) == "127.0.0.1"
	publicBinding := isolatedBind && runningFrontendHasPublicBinding(opts)
	if isolatedBind && (previousBind != "127.0.0.1" || publicBinding) {
		container, _ := runCapture(dockerExecutable(), composeArgs(opts, "ps", "-q", "frontend"), opts.home)
		if strings.TrimSpace(container) != "" {
			if err := runDocker(composeArgs(opts, "up", "-d", "--no-deps", "--force-recreate", "frontend"), opts.home); err != nil {
				return errors.New("could not close the direct frontend listener before starting the managed proxy")
			}
		}
	}
	env, _, err = readManagedEnvironment(opts)
	if err != nil {
		return err
	}
	config, err := normalizeManagedProxyConfig(env)
	if err != nil {
		return err
	}
	if !config.Enabled {
		return errors.New("enable the managed proxy before starting it")
	}
	status := collectProxyStatus(opts)
	if status.ServiceInstalled && status.ServiceUpdateRequired {
		if err := refreshProxyService(opts); err != nil {
			return fmt.Errorf("could not refresh the installed proxy service: %w", err)
		}
		status = collectProxyStatus(opts)
	}
	if status.Running {
		if status.ConfigurationCurrent {
			return nil
		}
		return restartManagedProxy(opts)
	}
	if status.ServiceInstalled {
		if err := controlProxyService(opts, true); err != nil {
			return err
		}
		return waitForProxyProcess(opts, true)
	}
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	logFile, err := os.OpenFile(proxyLogPath(opts), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer logFile.Close()
	command := exec.Command(executable, "--home", opts.home, "--env-file", opts.envFile, "proxy", "run")
	command.Stdout = logFile
	command.Stderr = logFile
	command.Stdin = nil
	detachProxyProcess(command)
	if err := command.Start(); err != nil {
		return err
	}
	return waitForProxyProcess(opts, true)
}

// runningFrontendHasPublicBinding checks the live container rather than
// trusting .env alone; changing a bind value does not alter an existing Docker
// port publication until the container is recreated.
func runningFrontendHasPublicBinding(opts options) bool {
	container, err := runCapture(dockerExecutable(), composeArgs(opts, "ps", "-q", "frontend"), opts.home)
	containerID := strings.TrimSpace(firstLine(container))
	if err != nil || containerID == "" {
		return false
	}
	raw, err := runCapture(
		dockerExecutable(),
		[]string{"inspect", "--format", `{{json .HostConfig.PortBindings}}`, containerID},
		opts.home,
	)
	if err != nil {
		// Fail safe: force recreation when the live publication cannot be proven
		// loopback-only.
		return true
	}
	var bindings map[string][]struct {
		HostIP string `json:"HostIp"`
	}
	if json.Unmarshal([]byte(strings.TrimSpace(raw)), &bindings) != nil {
		return true
	}
	for _, published := range bindings {
		for _, binding := range published {
			host := strings.TrimSpace(binding.HostIP)
			ip := net.ParseIP(host)
			if ip == nil || !ip.IsLoopback() {
				return true
			}
		}
	}
	return false
}

// restartManagedProxy always replaces the live process. Server-level restart
// uses this path so narrowed binds, renewed certificates, and changed secrets
// take effect instead of being hidden by a merely-live PID.
func restartManagedProxy(opts options) error {
	if collectProxyStatus(opts).Running {
		if err := stopManagedProxy(opts); err != nil {
			return err
		}
	}
	return startManagedProxy(opts)
}

func stopManagedProxy(opts options) error {
	if proxyServiceInstalled(opts) {
		if err := controlProxyService(opts, false); err != nil {
			return err
		}
		return waitForProxyProcess(opts, false)
	}
	runtimeState := readProxyRuntimeState(opts)
	if !proxyRuntimeHealthy(runtimeState) {
		_ = os.Remove(proxyPIDPath(opts))
		return nil
	}
	if !proxyRuntimeRequest(runtimeState, http.MethodPost, "/stop") {
		return errors.New("proxy process rejected the authenticated stop request")
	}
	return waitForProxyProcess(opts, false)
}

func waitForProxyProcess(opts options, running bool) error {
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); {
		if collectProxyStatus(opts).Running == running {
			if !running {
				_ = os.Remove(proxyPIDPath(opts))
			}
			if !opts.jsonOutput {
				fmt.Printf("Omlorix proxy %s.\n", map[bool]string{true: "started", false: "stopped"}[running])
			}
			return nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	return fmt.Errorf("proxy process did not become %s", map[bool]string{true: "ready", false: "stopped"}[running])
}

// controlProxyService keeps proxy start/stop semantics identical whether the
// service was installed from Electron or directly through the CLI.
func controlProxyService(opts options, start bool) error {
	action := map[bool]string{true: "start", false: "stop"}[start]
	var output string
	var err error
	switch runtime.GOOS {
	case "darwin":
		domain := fmt.Sprintf("gui/%s", currentUserID())
		if start {
			output, err = runCapture("launchctl", []string{"bootstrap", domain, proxyServiceDefinitionPath(opts)}, opts.home)
		} else {
			output, err = runCapture("launchctl", []string{"bootout", domain, proxyServiceDefinitionPath(opts)}, opts.home)
		}
	case "windows":
		output, err = runCapture("sc.exe", []string{action, "OmlorixServerProxy"}, opts.home)
	default:
		output, err = runCapture("systemctl", []string{"--user", action, "omlorix-server-proxy.service"}, opts.home)
	}
	if err != nil {
		// Native service managers use non-zero exit codes when a stop request
		// finds an installed service that is already stopped or unloaded. The
		// requested postcondition is already true, so disabling remains
		// idempotent while unrelated manager errors still fail closed.
		if !start && serviceManagerStopAlreadySatisfied(output) {
			return nil
		}
		return fmt.Errorf("could not %s the proxy service: %s", action, strings.TrimSpace(output))
	}
	return nil
}

func canonicalRemoteAddress(remoteAddr string) string {
	host, _, err := net.SplitHostPort(remoteAddr)
	if err != nil {
		host = remoteAddr
	}
	host = strings.TrimSpace(strings.TrimSuffix(strings.Split(host, "%")[0], "]"))
	host = strings.TrimPrefix(host, "[")
	ip := net.ParseIP(host)
	if ip == nil {
		return ""
	}
	return ip.String()
}

func newManagedReverseProxy(config managedProxyConfig) *httputil.ReverseProxy {
	reverseProxy := httputil.NewSingleHostReverseProxy(config.Target)
	// Rewrite runs after ReverseProxy removes client-supplied forwarding
	// headers. Unlike Director, it is also the final header-writing stage, so Go
	// cannot append RemoteAddr and turn the canonical value into an address list.
	reverseProxy.Director = nil
	reverseProxy.Rewrite = func(proxyRequest *httputil.ProxyRequest) {
		proxyRequest.SetURL(config.Target)
		// SetURL intentionally rewrites Host to the loopback target. Restore the
		// inbound value so nginx and FastAPI can validate the public Host exactly
		// as they do for requests handled by Electron's managed proxy.
		proxyRequest.Out.Host = proxyRequest.In.Host
		incoming := proxyRequest.In
		outgoing := proxyRequest.Out
		clientIP := canonicalRemoteAddress(incoming.RemoteAddr)
		verificationNonce := ""
		parsedClientIP := net.ParseIP(clientIP)
		if parsedClientIP != nil && parsedClientIP.IsLoopback() && incoming.URL.Path == "/api/v1/proxy-verification" {
			candidate := incoming.URL.Query().Get("nonce")
			if validVerificationNonce(candidate) {
				verificationNonce = candidate
			}
		}
		outgoing.Header.Del("Forwarded")
		outgoing.Header.Del("X-Omlorix-Proxy-Verification")
		outgoing.Header.Del("X-Omlorix-Proxy-Verification-Nonce")
		outgoing.Header.Set("X-Forwarded-For", clientIP)
		outgoing.Header.Set("X-Real-IP", clientIP)
		outgoing.Header.Set("X-Forwarded-Proto", map[bool]string{true: "https", false: "http"}[incoming.TLS != nil])
		outgoing.Header.Set("X-Forwarded-Host", config.PublicHostname)
		outgoing.Header.Set("X-Omlorix-Launcher-Secret", config.LauncherSecret)
		outgoing.Header.Set("X-Omlorix-Verification-Nonce", verificationNonce)
	}
	reverseProxy.ErrorHandler = func(response http.ResponseWriter, _ *http.Request, _ error) {
		response.Header().Set("Content-Type", "application/json")
		response.Header().Set("Cache-Control", "no-store")
		response.WriteHeader(http.StatusBadGateway)
		_, _ = io.WriteString(response, `{"detail":"Omlorix proxy upstream is unavailable"}`)
	}
	return reverseProxy
}

func newProxyHTTPServer(address string, handler http.Handler) *http.Server {
	return &http.Server{
		Addr:              address,
		Handler:           handler,
		ReadHeaderTimeout: proxyReadHeaderTimeout,
		IdleTimeout:       proxyIdleTimeout,
		MaxHeaderBytes:    1 << 20,
	}
}

func bindProxyListeners(servers []*http.Server) ([]net.Listener, error) {
	listeners := make([]net.Listener, 0, len(servers))
	for _, server := range servers {
		listener, err := net.Listen("tcp", server.Addr)
		if err != nil {
			for _, opened := range listeners {
				_ = opened.Close()
			}
			return nil, fmt.Errorf("could not bind managed proxy listener %s: %w", server.Addr, err)
		}
		listeners = append(listeners, listener)
	}
	return listeners, nil
}

func newProxyControlServer() (*proxyControlServer, string, error) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, "", fmt.Errorf("could not bind the proxy control listener: %w", err)
	}
	token, err := randomHexSecret(32)
	if err != nil {
		_ = listener.Close()
		return nil, "", err
	}
	control := &proxyControlServer{
		listener: listener,
		stop:     make(chan struct{}),
	}
	authorized := func(request *http.Request) bool {
		provided := strings.TrimPrefix(request.Header.Get("Authorization"), "Bearer ")
		return len(provided) == len(token) && subtle.ConstantTimeCompare([]byte(provided), []byte(token)) == 1
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/ready", func(response http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodGet || !authorized(request) {
			http.Error(response, "not found", http.StatusNotFound)
			return
		}
		response.WriteHeader(http.StatusNoContent)
	})
	mux.HandleFunc("/stop", func(response http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost || !authorized(request) {
			http.Error(response, "not found", http.StatusNotFound)
			return
		}
		if !proxyControlStopAllowed() {
			http.Error(response, "service manager controls this proxy", http.StatusForbidden)
			return
		}
		response.WriteHeader(http.StatusNoContent)
		control.stopOnce.Do(func() { close(control.stop) })
	})
	control.server = &http.Server{
		Handler:           mux,
		ReadHeaderTimeout: 2 * time.Second,
		IdleTimeout:       5 * time.Second,
		MaxHeaderBytes:    8 * 1024,
	}
	return control, token, nil
}

func runManagedProxy(opts options) error {
	env, _, err := readManagedEnvironment(opts)
	if err != nil {
		return err
	}
	config, err := normalizeManagedProxyConfig(env)
	if err != nil {
		return err
	}
	if !config.Enabled {
		// An installed boot service may observe that the operator disabled the
		// proxy while it was stopped. Exit successfully so service managers with
		// restart-on-failure do not spin on an intentionally disabled listener.
		return nil
	}
	reverseProxy := newManagedReverseProxy(config)
	httpHandler := http.Handler(reverseProxy)
	if config.RedirectHTTPToHTTPS && config.HTTPSEnabled {
		httpHandler = http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
			location := strings.TrimRight(proxyPublicURL(config, true), "/") + request.URL.RequestURI()
			response.Header().Set("Location", location)
			response.Header().Set("Cache-Control", "no-store")
			response.WriteHeader(http.StatusPermanentRedirect)
		})
	}
	servers := []*http.Server{newProxyHTTPServer(net.JoinHostPort(config.BindHost, config.HTTPPort), httpHandler)}
	if config.HTTPSEnabled {
		certificate, certificateErr := loadProxyTLSCertificate(config)
		if certificateErr != nil {
			return certificateErr
		}
		httpsServer := newProxyHTTPServer(net.JoinHostPort(config.BindHost, config.HTTPSPort), reverseProxy)
		httpsServer.TLSConfig = &tls.Config{
			MinVersion:   tls.VersionTLS12,
			Certificates: []tls.Certificate{certificate},
		}
		servers = append(servers, httpsServer)
	}
	listeners, err := bindProxyListeners(servers)
	if err != nil {
		return err
	}
	control, controlToken, err := newProxyControlServer()
	if err != nil {
		for _, listener := range listeners {
			_ = listener.Close()
		}
		return err
	}
	runtimeState := proxyRuntimeState{
		PID:                      os.Getpid(),
		ControlURL:               "http://" + control.listener.Addr().String(),
		ControlToken:             controlToken,
		ConfigurationFingerprint: proxyConfigFingerprint(config),
	}
	runtimeJSON, _ := json.Marshal(runtimeState)
	if err := atomicWriteFile(proxyPIDPath(opts), append(runtimeJSON, '\n'), 0o600); err != nil {
		_ = control.listener.Close()
		for _, listener := range listeners {
			_ = listener.Close()
		}
		return err
	}
	defer os.Remove(proxyPIDPath(opts))
	go func() { _ = control.server.Serve(control.listener) }()
	if err := superviseProxyServers(servers, listeners, config, control.stop); err != nil {
		_ = control.server.Shutdown(context.Background())
		return err
	}
	_ = control.server.Shutdown(context.Background())
	return shutdownProxyServers(servers)
}

// loadProxyTLSCertificate gives the background service the same encrypted-key
// workflow as Electron for traditional encrypted PEM private keys. Modern
// unencrypted PKCS#8, RSA, and EC keys continue through tls.X509KeyPair.
func loadProxyTLSCertificate(config managedProxyConfig) (tls.Certificate, error) {
	certificatePEM, err := os.ReadFile(config.TLSCertPath)
	if err != nil {
		return tls.Certificate{}, errors.New("could not read the proxy TLS certificate")
	}
	keyPEM, err := os.ReadFile(config.TLSKeyPath)
	if err != nil {
		return tls.Certificate{}, errors.New("could not read the proxy TLS private key")
	}
	if config.TLSCAPath != "" {
		caPEM, readErr := os.ReadFile(config.TLSCAPath)
		if readErr != nil {
			return tls.Certificate{}, errors.New("could not read the proxy TLS CA chain")
		}
		if !containsOnlyCertificates(caPEM) {
			return tls.Certificate{}, errors.New("the proxy TLS CA chain is not valid certificate PEM")
		}
		certificatePEM = append(bytes.TrimSpace(certificatePEM), '\n')
		certificatePEM = append(certificatePEM, bytes.TrimSpace(caPEM)...)
		certificatePEM = append(certificatePEM, '\n')
	}
	if config.TLSKeyPassphrase != "" {
		block, remaining := pem.Decode(keyPEM)
		if block == nil {
			return tls.Certificate{}, errors.New("the proxy TLS private key is not valid PEM")
		}
		if block.Type == "ENCRYPTED PRIVATE KEY" {
			privateKey, parseErr := pkcs8.ParsePKCS8PrivateKey(block.Bytes, []byte(config.TLSKeyPassphrase))
			if parseErr != nil {
				return tls.Certificate{}, errors.New("the proxy TLS private-key passphrase is incorrect")
			}
			decrypted, marshalErr := x509.MarshalPKCS8PrivateKey(privateKey)
			if marshalErr != nil {
				return tls.Certificate{}, errors.New("the proxy TLS private key could not be decoded")
			}
			keyPEM = append(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: decrypted}), remaining...)
		} else if x509.IsEncryptedPEMBlock(block) { //nolint:staticcheck // Required for legacy encrypted PEM parity.
			decrypted, decryptErr := x509.DecryptPEMBlock(block, []byte(config.TLSKeyPassphrase)) //nolint:staticcheck
			if decryptErr != nil {
				return tls.Certificate{}, errors.New("the proxy TLS private-key passphrase is incorrect")
			}
			keyPEM = append(pem.EncodeToMemory(&pem.Block{Type: block.Type, Bytes: decrypted}), remaining...)
		}
	}
	certificate, err := tls.X509KeyPair(certificatePEM, keyPEM)
	if err != nil {
		return tls.Certificate{}, errors.New("the proxy TLS certificate and private key could not be loaded")
	}
	return certificate, nil
}

func containsOnlyCertificates(value []byte) bool {
	remaining := value
	count := 0
	for len(bytes.TrimSpace(remaining)) > 0 {
		block, rest := pem.Decode(remaining)
		if block == nil || block.Type != "CERTIFICATE" {
			return false
		}
		if _, err := x509.ParseCertificate(block.Bytes); err != nil {
			return false
		}
		count++
		remaining = rest
	}
	return count > 0
}

func startProxyServers(servers []*http.Server, listeners []net.Listener, config managedProxyConfig) <-chan error {
	errorChannel := make(chan error, len(servers))
	go func() { errorChannel <- servers[0].Serve(listeners[0]) }()
	if config.HTTPSEnabled {
		go func() { errorChannel <- servers[1].ServeTLS(listeners[1], "", "") }()
	}
	return errorChannel
}

func shutdownProxyServers(servers []*http.Server) error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	var shutdownError error
	for _, server := range servers {
		if err := server.Shutdown(ctx); err != nil && shutdownError == nil {
			shutdownError = err
		}
	}
	return shutdownError
}

func validVerificationNonce(value string) bool {
	if len(value) < 16 || len(value) > 128 {
		return false
	}
	for _, character := range value {
		if (character >= 'a' && character <= 'z') ||
			(character >= 'A' && character <= 'Z') ||
			(character >= '0' && character <= '9') || character == '_' || character == '-' {
			continue
		}
		return false
	}
	return true
}

func commandVisitorIP(opts options) error {
	if len(opts.arguments) != 1 {
		return errors.New("usage: omlorix-server visitor-ip <status|detect|repair|verify>")
	}
	if err := ensureServerHome(opts); err != nil {
		return err
	}
	switch action := strings.ToLower(opts.arguments[0]); action {
	case "status":
		status := collectVisitorIPStatus(opts)
		if opts.jsonOutput {
			return printJSON(status)
		}
		fmt.Printf("Visitor IPs %s\n", map[bool]string{true: "ready", false: "not ready"}[status.Ready])
		fmt.Printf("  Proxy running %t\n", status.ProxyRunning)
		fmt.Printf("  Frontend IP  %s\n", firstNonBlank(status.Detection.FrontendIP, "unavailable"))
		fmt.Printf("  Verified at  %s\n", firstNonBlank(status.Verification.VerifiedAt, "never"))
		return nil
	case "detect":
		detection, err := detectVisitorIPTopology(opts)
		if err != nil {
			return err
		}
		if opts.jsonOutput {
			return printJSON(detection)
		}
		fmt.Printf("Frontend %s on %s (gateway %s)\n", detection.FrontendIP, detection.Network, detection.GatewayIP)
		return nil
	case "repair":
		return repairVisitorIP(opts)
	case "verify":
		verification, err := verifyVisitorIP(opts)
		if err != nil {
			return err
		}
		if opts.jsonOutput {
			return printJSON(verification)
		}
		fmt.Printf(
			"Visitor IP path verified: %s via %s (%s)\n",
			verification.ClientIP,
			verification.VerificationPath,
			verification.Scheme,
		)
		return nil
	default:
		return fmt.Errorf("unknown visitor-ip action %q", action)
	}
}

func detectVisitorIPTopology(opts options) (visitorIPDetection, error) {
	env, _ := readEnv(opts.envFile)
	networkName := firstNonBlank(env["COMPOSE_PROJECT_NAME"], "omlorix") + "_omlorix-network"
	containerRaw, err := runCapture(dockerExecutable(), composeArgs(opts, "ps", "-q", "frontend"), opts.home)
	containerID := strings.TrimSpace(firstLine(containerRaw))
	if err != nil || containerID == "" {
		return visitorIPDetection{}, errors.New("frontend container is unavailable; start Omlorix before detection")
	}
	backendRaw, backendErr := runCapture(dockerExecutable(), composeArgs(opts, "ps", "-q", "fastapi"), opts.home)
	backendContainerID := strings.TrimSpace(firstLine(backendRaw))
	if backendErr != nil || backendContainerID == "" {
		return visitorIPDetection{}, errors.New("backend container is unavailable; start Omlorix before detection")
	}
	inspectRaw, err := runCapture(dockerExecutable(), []string{"inspect", "--format", "{{json .NetworkSettings.Networks}}", containerID}, opts.home)
	if err != nil {
		return visitorIPDetection{}, errors.New("could not inspect the frontend network")
	}
	var networks map[string]struct {
		IPAddress string `json:"IPAddress"`
		Gateway   string `json:"Gateway"`
	}
	if json.Unmarshal([]byte(strings.TrimSpace(inspectRaw)), &networks) != nil {
		return visitorIPDetection{}, errors.New("Docker returned invalid frontend network data")
	}
	endpoint, ok := networks[networkName]
	if !ok || net.ParseIP(endpoint.IPAddress) == nil || net.ParseIP(endpoint.Gateway) == nil {
		return visitorIPDetection{}, errors.New("the named Omlorix Docker network has no valid frontend address and gateway")
	}
	return visitorIPDetection{
		FrontendIP:        endpoint.IPAddress,
		FrontendContainer: containerID,
		BackendContainer:  backendContainerID,
		GatewayIP:         endpoint.Gateway,
		Network:           networkName,
		Loopback:          strings.EqualFold(env["FRONTEND_HTTP_HOST_BIND"], "127.0.0.1"),
	}, nil
}

func visitorTopologyFingerprint(opts options, env map[string]string, detection visitorIPDetection) string {
	material := strings.Join([]string{
		detection.FrontendIP, detection.FrontendContainer, detection.BackendContainer,
		detection.GatewayIP, detection.Network,
		env["FRONTEND_HTTP_HOST_BIND"], env["FRONTEND_HTTP_HOST_PORT"],
		env["TRUSTED_PROXIES"], env["UVICORN_FORWARDED_ALLOW_IPS"],
		env["FRONTEND_TRUSTED_UPSTREAMS"],
		env["OMLORIX_LAUNCHER_PROXY_ENABLED"], env["OMLORIX_LAUNCHER_PROXY_BIND"],
		env["OMLORIX_LAUNCHER_PROXY_HTTP_PORT"], env["OMLORIX_LAUNCHER_PROXY_HTTPS_PORT"],
		env["OMLORIX_LAUNCHER_PROXY_HTTPS_ENABLED"],
		env["OMLORIX_LAUNCHER_PROXY_REDIRECT_HTTP_TO_HTTPS"],
		env["OMLORIX_LAUNCHER_PROXY_PUBLIC_HOSTNAME"],
		env["OMLORIX_LAUNCHER_PROXY_TLS_CERT_PATH"], env["OMLORIX_LAUNCHER_PROXY_TLS_KEY_PATH"],
		env["OMLORIX_LAUNCHER_PROXY_TLS_CA_PATH"],
		env["OMLORIX_LAUNCHER_PROXY_SECRET"],
		strconv.Itoa(collectProxyStatus(opts).PID),
	}, "\x00")
	digest := sha256.Sum256([]byte(material))
	return hex.EncodeToString(digest[:])
}

func readVisitorVerification(opts options) visitorIPVerification {
	raw, err := os.ReadFile(visitorStatePath(opts))
	if err != nil {
		return visitorIPVerification{}
	}
	var state struct {
		Version      int                   `json:"version"`
		Verification visitorIPVerification `json:"verification"`
	}
	if json.Unmarshal(raw, &state) != nil || state.Version != visitorStateVersion {
		return visitorIPVerification{}
	}
	return state.Verification
}

func writeVisitorVerification(opts options, verification visitorIPVerification) error {
	payload := struct {
		Version      int                   `json:"version"`
		Verification visitorIPVerification `json:"verification"`
	}{Version: visitorStateVersion, Verification: verification}
	raw, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		return err
	}
	return atomicWriteFile(visitorStatePath(opts), append(raw, '\n'), 0o600)
}

func collectVisitorIPStatus(opts options) visitorIPStatus {
	env, _, _ := readManagedEnvironment(opts)
	detection, _ := detectVisitorIPTopology(opts)
	verification := readVisitorVerification(opts)
	fingerprint := visitorTopologyFingerprint(opts, env, detection)
	verifiedAt, _ := time.Parse(time.RFC3339, verification.VerifiedAt)
	recent := !verifiedAt.IsZero() && time.Since(verifiedAt) >= 0 && time.Since(verifiedAt) < visitorVerificationTTL
	current := verification.Verified && recent && verification.TopologyFingerprint == fingerprint
	configured := envTruthy(env["TRUST_PROXY_HEADERS"], false) && strings.TrimSpace(env["TRUSTED_PROXIES"]) != ""
	proxy := collectProxyStatus(opts)
	managedEnabled := envTruthy(env["OMLORIX_LAUNCHER_PROXY_ENABLED"], false)
	externalConfigured := strings.TrimSpace(env["FRONTEND_TRUSTED_UPSTREAMS"]) != ""
	return visitorIPStatus{
		Ready:        current && configured && ((managedEnabled && proxy.Running) || (!managedEnabled && externalConfigured)),
		Configured:   configured,
		Pending:      (managedEnabled || externalConfigured) && !current,
		ProxyRunning: proxy.Running,
		Detection:    detection,
		Verification: verification,
	}
}

func verifyVisitorIP(opts options) (visitorIPVerification, error) {
	env, _, settingsErr := readManagedEnvironment(opts)
	if settingsErr != nil {
		return visitorIPVerification{}, settingsErr
	}
	explicitExternal := explicitExternalVisitorIPVerification(opts)
	if explicitExternal {
		// Validate explicit operator intent before Docker inspection. Invalid or
		// incomplete public-path flags must never fall through to a healthy
		// managed-proxy check and report a false success.
		if _, _, err := parseExternalVisitorIPVerificationOptions(opts); err != nil {
			return visitorIPVerification{}, err
		}
	}
	config := managedProxyConfig{}
	if !explicitExternal {
		var err error
		config, err = normalizeManagedProxyConfig(env)
		if err != nil {
			return visitorIPVerification{}, err
		}
	}
	detection, err := detectVisitorIPTopology(opts)
	if err != nil {
		return visitorIPVerification{}, err
	}
	return verifyVisitorIPWithDetection(opts, env, config, detection)
}

func explicitExternalVisitorIPVerification(opts options) bool {
	return strings.TrimSpace(opts.externalURL) != "" || strings.TrimSpace(opts.expectedIP) != ""
}

func verifyVisitorIPWithDetection(
	opts options,
	env map[string]string,
	config managedProxyConfig,
	detection visitorIPDetection,
) (visitorIPVerification, error) {
	if explicitExternalVisitorIPVerification(opts) || !config.Enabled {
		return verifyExternalProxy(opts, env, detection)
	}
	return verifyManagedProxy(opts, env, config, detection)
}

func verifyManagedProxy(
	opts options,
	env map[string]string,
	config managedProxyConfig,
	detection visitorIPDetection,
) (visitorIPVerification, error) {
	nonce, err := randomHexSecret(24)
	if err != nil {
		return visitorIPVerification{}, err
	}
	protocol, port := "http", config.HTTPPort
	transport := &http.Transport{}
	if config.HTTPSEnabled {
		protocol, port = "https", config.HTTPSPort
		// This request stays on loopback. Real visitors still receive the
		// configured certificate; local verification also supports private CAs.
		transport.TLSClientConfig = &tls.Config{MinVersion: tls.VersionTLS12, InsecureSkipVerify: true} //nolint:gosec
	}
	localHost := "127.0.0.1"
	if config.BindHost == "::" {
		localHost = "[::1]"
	}
	requestURL := fmt.Sprintf("%s://%s:%s/api/v1/proxy-verification?nonce=%s", protocol, localHost, port, nonce)
	request, _ := http.NewRequest(http.MethodGet, requestURL, nil)
	request.Host = config.PublicHostname
	client := &http.Client{Timeout: 5 * time.Second, Transport: transport}
	response, err := client.Do(request)
	if err != nil {
		return visitorIPVerification{}, errors.New("could not reach the proxy verification path")
	}
	defer response.Body.Close()
	var payload proxyVerificationResponse
	if response.StatusCode != http.StatusOK || json.NewDecoder(io.LimitReader(response.Body, 64*1024)).Decode(&payload) != nil {
		return visitorIPVerification{}, errors.New("the proxy verification endpoint returned an invalid response")
	}
	clientIP := net.ParseIP(payload.ClientIP)
	verified := payload.Nonce == nonce && payload.TrustChainAccepted &&
		clientIP != nil && clientIP.IsLoopback() && payload.Scheme == protocol &&
		strings.EqualFold(strings.TrimSpace(payload.Host), config.PublicHostname)
	verification := visitorIPVerification{
		Verified:            verified,
		VerificationPath:    "managed_proxy",
		TopologyFingerprint: visitorTopologyFingerprint(opts, env, detection),
		ClientIP:            payload.ClientIP,
		Scheme:              payload.Scheme,
		Host:                payload.Host,
	}
	if verified {
		verification.VerifiedAt = time.Now().UTC().Format(time.RFC3339)
	} else {
		verification.ErrorCode = "end_to_end_failed"
	}
	_ = writeVisitorVerification(opts, verification)
	if !verified {
		return verification, errors.New("end-to-end visitor IP and scheme verification failed")
	}
	return verification, nil
}

func parseExternalVisitorIPVerificationOptions(opts options) (*url.URL, net.IP, error) {
	publicURL, err := url.Parse(strings.TrimSpace(opts.externalURL))
	if err != nil || publicURL.Host == "" || (publicURL.Scheme != "http" && publicURL.Scheme != "https") {
		return nil, nil, errors.New("external proxy verification requires --external-url with an HTTP or HTTPS URL")
	}
	expectedIP := net.ParseIP(strings.TrimSpace(opts.expectedIP))
	if expectedIP == nil {
		return nil, nil, errors.New("external proxy verification requires --expected-ip with the caller's public IP")
	}
	return publicURL, expectedIP, nil
}

func verifyExternalProxy(opts options, env map[string]string, detection visitorIPDetection) (visitorIPVerification, error) {
	publicURL, expectedIP, err := parseExternalVisitorIPVerificationOptions(opts)
	if err != nil {
		return visitorIPVerification{}, err
	}
	endpoint := *publicURL
	endpoint.Path = strings.TrimRight(endpoint.Path, "/") + "/api/v1/client-ip"
	endpoint.RawQuery = ""
	request, _ := http.NewRequest(http.MethodGet, endpoint.String(), nil)
	client := &http.Client{Timeout: 8 * time.Second}
	response, requestErr := client.Do(request)
	if requestErr != nil {
		return visitorIPVerification{}, errors.New("could not reach the external proxy diagnostic path")
	}
	defer response.Body.Close()
	var payload struct {
		IP     string `json:"ip"`
		Scheme string `json:"scheme"`
		Host   string `json:"host"`
	}
	if response.StatusCode != http.StatusOK || json.NewDecoder(io.LimitReader(response.Body, 64*1024)).Decode(&payload) != nil {
		return visitorIPVerification{}, errors.New("the external proxy diagnostic endpoint returned an invalid response")
	}
	observedIP := net.ParseIP(payload.IP)
	verified := observedIP != nil && observedIP.Equal(expectedIP) && payload.Scheme == publicURL.Scheme
	verification := visitorIPVerification{
		Verified:            verified,
		VerificationPath:    "external_proxy",
		TopologyFingerprint: visitorTopologyFingerprint(opts, env, detection),
		ClientIP:            payload.IP,
		Scheme:              payload.Scheme,
		Host:                payload.Host,
	}
	if verified {
		verification.VerifiedAt = time.Now().UTC().Format(time.RFC3339)
	} else {
		verification.ErrorCode = "external_end_to_end_failed"
	}
	_ = writeVisitorVerification(opts, verification)
	if !verified {
		return verification, errors.New("external proxy visitor IP or scheme did not match the expected value")
	}
	return verification, nil
}

// repairVisitorIP is the presentation boundary for the explicit visitor-IP
// command. Callers that compose repair into a larger command must use the
// mutation function below so a --json invocation emits exactly one document.
func repairVisitorIP(opts options) error {
	if err := repairVisitorIPMutation(opts); err != nil {
		return err
	}
	if opts.jsonOutput {
		return printJSON(collectVisitorIPStatus(opts))
	}
	env, _, settingsErr := readManagedEnvironment(opts)
	if settingsErr != nil {
		return settingsErr
	}
	launcherEnabled := envTruthy(env["OMLORIX_LAUNCHER_PROXY_ENABLED"], false)
	if launcherEnabled || strings.TrimSpace(opts.externalURL) != "" {
		fmt.Println("Visitor IP forwarding was repaired and verified.")
	} else {
		fmt.Println("Visitor IP trust was repaired. Verify the external path with --external-url and --expected-ip.")
	}
	return nil
}

// repairVisitorIPMutation applies and verifies visitor-IP trust without
// writing command output. Keeping presentation out of this function lets
// proxy enable, start, and restart safely compose it into their own result.
func repairVisitorIPMutation(opts options) error {
	// Detection is deliberately the first mutation prerequisite. A stopped or
	// ambiguous Docker topology must not leave behind a partially updated .env.
	detection, err := detectVisitorIPTopology(opts)
	if err != nil {
		return err
	}
	env, originalRaw, settingsErr := readManagedEnvironment(opts)
	if settingsErr != nil {
		return settingsErr
	}
	launcherEnabled := envTruthy(env["OMLORIX_LAUNCHER_PROXY_ENABLED"], false)
	externalProxyEnabled := strings.TrimSpace(env["FRONTEND_TRUSTED_UPSTREAMS"]) != ""
	proxyWasRunning := collectProxyStatus(opts).Running
	if launcherEnabled || externalProxyEnabled {
		if err := ensureLauncherProxySecret(opts); err != nil {
			return err
		}
	}
	env, _, settingsErr = readManagedEnvironment(opts)
	if settingsErr != nil {
		return settingsErr
	}
	secret := strings.TrimSpace(env["OMLORIX_LAUNCHER_PROXY_SECRET"])
	if launcherEnabled || externalProxyEnabled {
		decoded, decodeErr := hex.DecodeString(secret)
		if decodeErr != nil || len(decoded) != 32 {
			_ = atomicWriteFile(opts.envFile, []byte(originalRaw), 0o600)
			return errors.New("ingress authentication credential is unavailable")
		}
	}
	backupPath := opts.envFile + ".visitor-ip-backup"
	if err := atomicWriteFile(backupPath, []byte(originalRaw), 0o600); err != nil {
		_ = atomicWriteFile(opts.envFile, []byte(originalRaw), 0o600)
		return err
	}
	rollback := func() {
		_ = atomicWriteFile(opts.envFile, []byte(originalRaw), 0o600)
		_, _ = runCapture(dockerExecutable(), composeArgs(opts, "up", "-d", "--force-recreate", "fastapi", "frontend"), opts.home)
		if launcherEnabled && !proxyWasRunning {
			_ = stopManagedProxy(opts)
		}
	}
	if launcherEnabled && !proxyWasRunning {
		if err := startManagedProxy(opts); err != nil {
			_ = stopManagedProxy(opts)
			_ = atomicWriteFile(opts.envFile, []byte(originalRaw), 0o600)
			_ = os.Remove(backupPath)
			return errors.New("managed proxy startup failed; the previous environment was restored")
		}
	}
	for attempt := 0; attempt < 2; attempt++ {
		proxyCIDR := detection.FrontendIP + "/32"
		if net.ParseIP(detection.FrontendIP).To4() == nil {
			proxyCIDR = detection.FrontendIP + "/128"
		}
		updates := map[string]string{
			"TRUST_PROXY_HEADERS":          "true",
			"TRUSTED_PROXIES":              proxyCIDR,
			"RATE_LIMIT_TRUSTED_PROXIES":   proxyCIDR,
			"AUTH_TRUSTED_PROXIES":         proxyCIDR,
			"UVICORN_FORWARDED_ALLOW_IPS":  detection.FrontendIP,
			"FRONTEND_TRUST_PROXY_HEADERS": strconv.FormatBool(launcherEnabled || externalProxyEnabled),
		}
		if launcherEnabled {
			updates["FRONTEND_HTTP_HOST_BIND"] = "127.0.0.1"
		}
		if err := writeEnv(opts.envFile, updates); err != nil {
			rollback()
			return errors.New("visitor IP settings could not be written; the previous environment was restored")
		}
		if err := runVisitorIPDocker(opts, composeArgs(opts, "up", "-d", "--force-recreate", "fastapi", "frontend")); err != nil {
			rollback()
			return errors.New("visitor IP repair failed; the previous environment was restored")
		}
		if err := waitForServerHealthy(opts, opts.timeout); err != nil {
			rollback()
			return errors.New("visitor IP repair did not become ready; the previous environment was restored")
		}
		finalDetection, detectErr := detectVisitorIPTopology(opts)
		if detectErr != nil {
			rollback()
			return errors.New("the final Docker topology could not be detected; the previous environment was restored")
		}
		if finalDetection.FrontendIP == detection.FrontendIP {
			break
		}
		if attempt == 1 {
			rollback()
			return errors.New("Docker topology did not stabilize; the previous environment was restored")
		}
		detection = finalDetection
	}
	if launcherEnabled {
		if _, err := verifyVisitorIP(opts); err != nil {
			rollback()
			return errors.New("visitor IP verification failed; the previous environment was restored")
		}
	} else if strings.TrimSpace(env["FRONTEND_TRUSTED_UPSTREAMS"]) != "" && strings.TrimSpace(opts.externalURL) != "" {
		if _, err := verifyVisitorIP(opts); err != nil {
			rollback()
			return errors.New("external proxy verification failed; the previous environment was restored")
		}
	}
	_ = os.Remove(backupPath)
	return nil
}

// runVisitorIPDocker keeps JSON workflows machine-readable while preserving
// Docker's ordinary progress output for interactive repairs.
func runVisitorIPDocker(opts options, args []string) error {
	if !opts.jsonOutput {
		return runDocker(args, opts.home)
	}
	_, err := runCapture(dockerExecutable(), args, opts.home)
	return err
}

func proxyServiceInstalled(opts options) bool {
	return nativeProxyServiceInstalled(opts)
}

func proxyServiceDefinitionPath(opts options) string {
	switch runtime.GOOS {
	case "darwin":
		return filepath.Join(userHome(), "Library", "LaunchAgents", "com.omlorix.server-proxy.plist")
	case "windows":
		return filepath.Join(opts.home, ".omlorix-proxy-service-installed")
	default:
		return filepath.Join(userHome(), ".config", "systemd", "user", "omlorix-server-proxy.service")
	}
}

// installStableProxyExecutable copies the currently selected CLI out of app
// bundles, mounted images, AppImage filesystems, and portable extraction
// directories. Native service definitions must reference this stable path so
// moving or updating the desktop app cannot invalidate a boot service.
func installStableProxyExecutable(opts options) (string, error) {
	source, err := os.Executable()
	if err != nil {
		return "", err
	}
	destination, err := stableProxyServiceExecutablePath(opts)
	if err != nil {
		return "", err
	}
	sourcePath, err := filepath.Abs(source)
	if err != nil {
		return "", err
	}
	sourcePath, err = filepath.EvalSymlinks(sourcePath)
	if err != nil {
		return "", fmt.Errorf("could not resolve the proxy service executable: %w", err)
	}
	destinationPath, err := filepath.Abs(destination)
	if err != nil {
		return "", err
	}
	if filepath.Clean(sourcePath) == filepath.Clean(destinationPath) {
		return destinationPath, nil
	}
	sourceInfo, err := os.Lstat(sourcePath)
	if err != nil {
		return "", fmt.Errorf("could not inspect the proxy service executable: %w", err)
	}
	if !sourceInfo.Mode().IsRegular() {
		return "", errors.New("proxy service executable must be a regular file")
	}
	if err := prepareStableProxyServiceDirectory(opts, filepath.Dir(destinationPath)); err != nil {
		return "", err
	}
	sourceFile, err := os.Open(sourcePath)
	if err != nil {
		return "", fmt.Errorf("could not open the proxy service executable: %w", err)
	}
	defer sourceFile.Close()
	temporary, err := os.CreateTemp(filepath.Dir(destinationPath), ".omlorix-server-*")
	if err != nil {
		return "", fmt.Errorf("could not create the protected proxy executable: %w", err)
	}
	temporaryPath := temporary.Name()
	cleanup := func() {
		_ = temporary.Close()
		_ = os.Remove(temporaryPath)
	}
	if err := temporary.Chmod(0o700); err != nil {
		cleanup()
		return "", err
	}
	if _, err := io.Copy(temporary, sourceFile); err != nil {
		cleanup()
		return "", fmt.Errorf("could not copy the proxy service executable: %w", err)
	}
	if err := temporary.Sync(); err != nil {
		cleanup()
		return "", err
	}
	if err := temporary.Close(); err != nil {
		_ = os.Remove(temporaryPath)
		return "", err
	}
	if err := finalizeStableProxyServiceExecutable(opts, temporaryPath); err != nil {
		_ = os.Remove(temporaryPath)
		return "", err
	}
	if err := replaceStableProxyServiceExecutable(temporaryPath, destinationPath); err != nil {
		_ = os.Remove(temporaryPath)
		return "", fmt.Errorf("could not install the protected proxy executable: %w", err)
	}
	return destinationPath, nil
}

func proxyServiceExecutableCurrent(opts options) bool {
	current, err := os.Executable()
	if err != nil {
		return false
	}
	stable, err := stableProxyServiceExecutablePath(opts)
	if err != nil {
		return false
	}
	currentDigest, err := fileSHA256(current)
	if err != nil {
		return false
	}
	stableDigest, err := fileSHA256(stable)
	return err == nil && subtle.ConstantTimeCompare(currentDigest, stableDigest) == 1
}

func fileSHA256(path string) ([]byte, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	digest := sha256.New()
	if _, err := io.Copy(digest, file); err != nil {
		return nil, err
	}
	return digest.Sum(nil), nil
}

func removeStableProxyServiceExecutable(opts options) error {
	path, err := stableProxyServiceExecutablePath(opts)
	if err != nil {
		return err
	}
	if err := removeStableProxyServiceFile(path); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	// Remove only the now-empty product-owned leaf directory. Parent platform
	// data directories are never recursively deleted.
	_ = os.Remove(filepath.Dir(path))
	return nil
}

func installProxyService(opts options) (returnErr error) {
	if err := ensureServerHome(opts); err != nil {
		return err
	}
	if proxyServiceInstalled(opts) {
		return errors.New("proxy background service is already installed; use refresh-service to update it")
	}
	// Check platform privileges and the protected target before interrupting a
	// working detached proxy. This is especially important on Windows, where a
	// standard token cannot write Program Files or create a service.
	if err := preflightProxyServiceInstall(opts); err != nil {
		return err
	}
	detachedWasRunning := collectProxyStatus(opts).Running && !proxyServiceInstalled(opts)
	committed := false
	defer func() {
		if !committed && detachedWasRunning {
			if restartErr := startManagedProxy(opts); restartErr != nil {
				returnErr = fmt.Errorf("%w; the previous detached proxy also could not be restarted: %v", returnErr, restartErr)
			}
		}
	}()
	// Migrate an existing detached CLI proxy into the native service without
	// leaving two processes competing for the same listeners.
	if collectProxyStatus(opts).Running && !proxyServiceInstalled(opts) {
		if err := stopManagedProxy(opts); err != nil {
			return err
		}
	}
	if err := ensureLauncherProxySecret(opts); err != nil {
		return err
	}
	env, _, settingsErr := readManagedEnvironment(opts)
	if settingsErr != nil {
		return settingsErr
	}
	config, configErr := normalizeManagedProxyConfig(env)
	if configErr != nil {
		return configErr
	}
	if !config.Enabled {
		return errors.New("enable the managed proxy before installing its background service")
	}
	executable, err := installStableProxyExecutable(opts)
	if err != nil {
		return err
	}
	definition := proxyServiceDefinitionPath(opts)
	defer func() {
		if !committed {
			rollbackProxyServiceInstall(opts, definition)
		}
	}()
	if err := os.MkdirAll(filepath.Dir(definition), 0o755); err != nil {
		return err
	}
	switch runtime.GOOS {
	case "darwin":
		content := fmt.Sprintf(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>Label</key><string>com.omlorix.server-proxy</string><key>ProgramArguments</key><array><string>%s</string><string>--home</string><string>%s</string><string>--env-file</string><string>%s</string><string>proxy</string><string>run</string></array><key>RunAtLoad</key><true/><key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict></dict></plist>
`, xmlEscape(executable), xmlEscape(opts.home), xmlEscape(opts.envFile))
		if err := atomicWriteFile(definition, []byte(content), 0o600); err != nil {
			return err
		}
		if output, commandErr := runCapture("launchctl", []string{"bootstrap", fmt.Sprintf("gui/%s", currentUserID()), definition}, opts.home); commandErr != nil {
			return fmt.Errorf("could not load launchd proxy service: %s", strings.TrimSpace(output))
		}
	case "windows":
		serviceCommand := fmt.Sprintf("\"%s\" --home \"%s\" --env-file \"%s\" proxy run", executable, opts.home, opts.envFile)
		// LocalSystem is explicit because the service must read the selected
		// operator profile and bind the configured public listeners. The binary
		// itself lives under an administrator-only Program Files ACL.
		if output, commandErr := runCapture("sc.exe", []string{"create", "OmlorixServerProxy", "binPath=", serviceCommand, "start=", "auto", "obj=", "LocalSystem"}, opts.home); commandErr != nil {
			return fmt.Errorf("could not install Windows Service: %s", strings.TrimSpace(output))
		}
		if output, commandErr := runCapture("sc.exe", []string{"start", "OmlorixServerProxy"}, opts.home); commandErr != nil {
			return fmt.Errorf("could not start Windows proxy service: %s", strings.TrimSpace(output))
		}
	default:
		content := fmt.Sprintf("[Unit]\nDescription=Omlorix authenticated reverse proxy\nAfter=network-online.target\n\n[Service]\nExecStart=%s --home %s --env-file %s proxy run\nRestart=on-failure\n\n[Install]\nWantedBy=default.target\n", systemdEscape(executable), systemdEscape(opts.home), systemdEscape(opts.envFile))
		if err := atomicWriteFile(definition, []byte(content), 0o600); err != nil {
			return err
		}
		if output, commandErr := runCapture("systemctl", []string{"--user", "daemon-reload"}, opts.home); commandErr != nil {
			return fmt.Errorf("could not reload systemd: %s", strings.TrimSpace(output))
		}
		if output, commandErr := runCapture("systemctl", []string{"--user", "enable", "--now", "omlorix-server-proxy.service"}, opts.home); commandErr != nil {
			return fmt.Errorf("could not enable proxy service: %s", strings.TrimSpace(output))
		}
	}
	if err := waitForProxyProcess(opts, true); err != nil {
		return err
	}
	committed = true
	if !opts.jsonOutput {
		fmt.Println("Omlorix proxy service installed and enabled.")
	}
	return nil
}

// rollbackProxyServiceInstall removes every artifact created before an install
// failure. The rollback is deliberately best-effort because the original
// failure remains the actionable error, but it covers both service-manager
// state and the stable executable so a failed operation cannot leave hidden
// automatic privileged persistence.
func rollbackProxyServiceInstall(opts options, definition string) {
	switch runtime.GOOS {
	case "darwin":
		_, _ = runCapture("launchctl", []string{"bootout", fmt.Sprintf("gui/%s", currentUserID()), definition}, opts.home)
	case "windows":
		_, _ = runCapture("sc.exe", []string{"stop", "OmlorixServerProxy"}, opts.home)
		_, _ = runCapture("sc.exe", []string{"delete", "OmlorixServerProxy"}, opts.home)
	default:
		_, _ = runCapture("systemctl", []string{"--user", "disable", "--now", "omlorix-server-proxy.service"}, opts.home)
		_, _ = runCapture("systemctl", []string{"--user", "daemon-reload"}, opts.home)
	}
	_ = os.Remove(definition)
	_ = removeStableProxyServiceExecutable(opts)
}

// refreshProxyService updates the protected executable without changing the
// native service definition. A running service is stopped first so Windows can
// replace the image atomically, then restored to its previous running state.
func refreshProxyService(opts options) error {
	if !proxyServiceInstalled(opts) {
		return errors.New("proxy background service is not installed")
	}
	wasRunning := collectProxyStatus(opts).Running
	if wasRunning {
		if err := controlProxyService(opts, false); err != nil {
			return err
		}
		if err := waitForProxyProcess(opts, false); err != nil {
			return err
		}
	}
	if _, err := installStableProxyExecutable(opts); err != nil {
		if wasRunning {
			_ = controlProxyService(opts, true)
		}
		return err
	}
	if wasRunning {
		if err := controlProxyService(opts, true); err != nil {
			return err
		}
		if err := waitForProxyProcess(opts, true); err != nil {
			return err
		}
	}
	if !opts.jsonOutput {
		fmt.Println("Omlorix proxy service executable refreshed.")
	}
	return nil
}

func uninstallProxyService(opts options) error {
	definition := proxyServiceDefinitionPath(opts)
	switch runtime.GOOS {
	case "darwin":
		if output, err := runCapture("launchctl", []string{"bootout", fmt.Sprintf("gui/%s", currentUserID()), definition}, opts.home); err != nil && !serviceManagerAlreadyAbsent(output) {
			return fmt.Errorf("could not unload the launchd proxy service: %s", strings.TrimSpace(output))
		}
	case "windows":
		if collectProxyStatus(opts).Running {
			if err := controlProxyService(opts, false); err != nil {
				return err
			}
			if err := waitForProxyProcess(opts, false); err != nil {
				return err
			}
		}
		if output, err := runCapture("sc.exe", []string{"delete", "OmlorixServerProxy"}, opts.home); err != nil && !serviceManagerAlreadyAbsent(output) {
			return fmt.Errorf("could not delete the Windows proxy service: %s", strings.TrimSpace(output))
		}
	default:
		if output, err := runCapture("systemctl", []string{"--user", "disable", "--now", "omlorix-server-proxy.service"}, opts.home); err != nil && !serviceManagerAlreadyAbsent(output) {
			return fmt.Errorf("could not disable the systemd proxy service: %s", strings.TrimSpace(output))
		}
	}
	if err := os.Remove(definition); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if runtime.GOOS != "windows" && runtime.GOOS != "darwin" {
		if output, err := runCapture("systemctl", []string{"--user", "daemon-reload"}, opts.home); err != nil {
			return fmt.Errorf("could not reload systemd after removing the proxy service: %s", strings.TrimSpace(output))
		}
	}
	if err := removeStableProxyServiceExecutable(opts); err != nil {
		return fmt.Errorf("could not remove the stable proxy service executable: %w", err)
	}
	if !opts.jsonOutput {
		fmt.Println("Omlorix proxy service removed.")
	}
	return nil
}

func serviceManagerAlreadyAbsent(output string) bool {
	value := strings.ToLower(output)
	return strings.Contains(value, "does not exist") ||
		strings.Contains(value, "not found") ||
		strings.Contains(value, "not loaded") ||
		strings.Contains(value, "1060")
}

// serviceManagerStopAlreadySatisfied recognizes only platform responses that
// unambiguously mean no service process remains to stop. It intentionally does
// not swallow access-denied, malformed-definition, or general command errors.
func serviceManagerStopAlreadySatisfied(output string) bool {
	value := strings.ToLower(output)
	return serviceManagerAlreadyAbsent(output) ||
		strings.Contains(value, "no such process") ||
		strings.Contains(value, "could not find specified service") ||
		strings.Contains(value, "has not been started") ||
		strings.Contains(value, "not running") ||
		strings.Contains(value, "1062")
}

func xmlEscape(value string) string {
	replacer := strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", "\"", "&quot;", "'", "&apos;")
	return replacer.Replace(value)
}

func systemdEscape(value string) string {
	// systemd expands percent specifiers even inside quoted ExecStart tokens.
	// Doubling them preserves an ordinary filesystem path verbatim.
	return strconv.Quote(strings.ReplaceAll(value, "%", "%%"))
}
