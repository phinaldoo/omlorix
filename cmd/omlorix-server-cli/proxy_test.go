package main

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"math/big"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/youmark/pkcs8"
)

func TestManagedProxyConfigUsesValidatedTrustedPublicHost(t *testing.T) {
	config, err := normalizeManagedProxyConfig(map[string]string{
		"TRUSTED_HOSTS":                       "chat.example.test,localhost",
		"OMLORIX_LAUNCHER_PROXY_HTTPS_ENABLED": "true",
		"OMLORIX_LAUNCHER_PROXY_TLS_CERT_PATH": "/tmp/cert.pem",
		"OMLORIX_LAUNCHER_PROXY_TLS_KEY_PATH":  "/tmp/key.pem",
	})
	if err != nil {
		t.Fatal(err)
	}
	if config.PublicHostname != "chat.example.test" {
		t.Fatalf("public hostname = %q", config.PublicHostname)
	}
	if got := proxyPublicURL(config, true); got != "https://chat.example.test:8443" {
		t.Fatalf("public URL = %q", got)
	}
}

func TestManagedProxyConfigRejectsRedirectHostInjection(t *testing.T) {
	for _, hostname := range []string{"example.test/evil", "example.test:443", "https://example.test"} {
		_, err := normalizeManagedProxyConfig(map[string]string{
			"OMLORIX_LAUNCHER_PROXY_PUBLIC_HOSTNAME": hostname,
		})
		if err == nil {
			t.Fatalf("unsafe public hostname %q was accepted", hostname)
		}
	}
}

func TestManagedProxyConfigRejectsListenerPortCollisions(t *testing.T) {
	tests := []map[string]string{
		{
			"FRONTEND_HTTP_HOST_PORT":         "8080",
			"OMLORIX_LAUNCHER_PROXY_HTTP_PORT": "8080",
		},
		{
			"OMLORIX_LAUNCHER_PROXY_HTTPS_ENABLED": "true",
			"OMLORIX_LAUNCHER_PROXY_HTTP_PORT":     "8443",
			"OMLORIX_LAUNCHER_PROXY_HTTPS_PORT":    "8443",
			"OMLORIX_LAUNCHER_PROXY_TLS_CERT_PATH": "/tmp/cert.pem",
			"OMLORIX_LAUNCHER_PROXY_TLS_KEY_PATH":  "/tmp/key.pem",
		},
		{
			"FRONTEND_HTTP_HOST_PORT":             "8443",
			"OMLORIX_LAUNCHER_PROXY_HTTPS_ENABLED": "true",
			"OMLORIX_LAUNCHER_PROXY_HTTPS_PORT":    "8443",
			"OMLORIX_LAUNCHER_PROXY_TLS_CERT_PATH": "/tmp/cert.pem",
			"OMLORIX_LAUNCHER_PROXY_TLS_KEY_PATH":  "/tmp/key.pem",
		},
	}
	for index, env := range tests {
		if _, err := normalizeManagedProxyConfig(env); err == nil {
			t.Fatalf("collision case %d was accepted", index)
		}
	}
}

func TestProxyEnableValidationChecksTLSBeforePersistingEnabledState(t *testing.T) {
	env := map[string]string{
		"OMLORIX_LAUNCHER_PROXY_ENABLED":       "false",
		"OMLORIX_LAUNCHER_PROXY_SECRET":        strings.Repeat("a", 64),
		"OMLORIX_LAUNCHER_PROXY_HTTPS_ENABLED": "true",
		"OMLORIX_LAUNCHER_PROXY_TLS_CERT_PATH": filepath.Join(t.TempDir(), "missing-cert.pem"),
		"OMLORIX_LAUNCHER_PROXY_TLS_KEY_PATH":  filepath.Join(t.TempDir(), "missing-key.pem"),
	}
	if _, err := validateManagedProxyEnableConfig(env); err == nil || !strings.Contains(err.Error(), "could not read") {
		t.Fatalf("invalid TLS files were accepted: %v", err)
	}
}

func TestProxyEnableValidationDoesNotRequirePersistingSecretFirst(t *testing.T) {
	config, err := validateManagedProxyEnableConfig(map[string]string{
		"OMLORIX_LAUNCHER_PROXY_ENABLED": "false",
	})
	if err != nil {
		t.Fatalf("new-install proxy settings were rejected before secret generation: %v", err)
	}
	if !config.Enabled {
		t.Fatal("prospective proxy configuration was not validated as enabled")
	}
}

func TestManagedReverseProxyEmitsOneCanonicalForwardedAddress(t *testing.T) {
	observed := make(chan http.Header, 1)
	upstream := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		observed <- request.Header.Clone()
		response.WriteHeader(http.StatusNoContent)
	}))
	defer upstream.Close()
	target, err := url.Parse(upstream.URL)
	if err != nil {
		t.Fatal(err)
	}
	proxy := httptest.NewServer(newManagedReverseProxy(managedProxyConfig{
		Target:         target,
		PublicHostname: "chat.example.test",
		LauncherSecret: strings.Repeat("a", 64),
	}))
	defer proxy.Close()
	request, err := http.NewRequest(http.MethodGet, proxy.URL+"/ready", nil)
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("X-Forwarded-For", "198.51.100.10, 203.0.113.5")
	request.Header.Set("Forwarded", "for=198.51.100.10")
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	_ = response.Body.Close()
	headers := <-observed
	if got := headers.Get("X-Forwarded-For"); got != "127.0.0.1" {
		t.Fatalf("X-Forwarded-For = %q, want one canonical address", got)
	}
	if got := headers.Get("Forwarded"); got != "" {
		t.Fatalf("Forwarded = %q, want removed", got)
	}
}

func TestManagedReverseProxyPreservesInboundHost(t *testing.T) {
	// The managed proxy routes to a loopback frontend, but nginx and FastAPI
	// still need the original Host to enforce the operator's public-host
	// allowlist. This also keeps the CLI aligned with Electron's changeOrigin:
	// false proxy behavior.
	observed := make(chan string, 1)
	upstream := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		observed <- request.Host
		response.WriteHeader(http.StatusNoContent)
	}))
	defer upstream.Close()
	target, err := url.Parse(upstream.URL)
	if err != nil {
		t.Fatal(err)
	}
	proxy := httptest.NewServer(newManagedReverseProxy(managedProxyConfig{
		Target:         target,
		PublicHostname: "chat.example.test",
		LauncherSecret: strings.Repeat("a", 64),
	}))
	defer proxy.Close()

	for _, inboundHost := range []string{
		"chat.example.test",
		"chat.example.test:8443",
		"untrusted.example.test",
	} {
		t.Run(inboundHost, func(t *testing.T) {
			request, requestErr := http.NewRequest(http.MethodGet, proxy.URL+"/ready", nil)
			if requestErr != nil {
				t.Fatal(requestErr)
			}
			request.Host = inboundHost
			response, requestErr := http.DefaultClient.Do(request)
			if requestErr != nil {
				t.Fatal(requestErr)
			}
			_ = response.Body.Close()

			if got := <-observed; got != inboundHost {
				t.Fatalf("upstream Host = %q, want inbound Host %q", got, inboundHost)
			}
		})
	}
}

func TestProxyHTTPServerUsesBoundedHeaderAndIdleTimeouts(t *testing.T) {
	server := newProxyHTTPServer("127.0.0.1:0", http.NotFoundHandler())
	if server.ReadHeaderTimeout != proxyReadHeaderTimeout {
		t.Fatalf("ReadHeaderTimeout = %s", server.ReadHeaderTimeout)
	}
	if server.IdleTimeout != proxyIdleTimeout {
		t.Fatalf("IdleTimeout = %s", server.IdleTimeout)
	}
}

func TestVerificationNonceRequiresStableSafeToken(t *testing.T) {
	if !validVerificationNonce(strings.Repeat("a", 16)) {
		t.Fatal("valid nonce was rejected")
	}
	for _, nonce := range []string{"short", strings.Repeat("a", 129), "unsafe/value-here"} {
		if validVerificationNonce(nonce) {
			t.Fatalf("unsafe nonce %q was accepted", nonce)
		}
	}
}

func TestExplicitExternalVisitorIPFlagsOverrideEnabledManagedProxy(t *testing.T) {
	requestCount := 0
	external := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		requestCount++
		if request.URL.Path != "/api/v1/client-ip" {
			t.Fatalf("external verification path = %q", request.URL.Path)
		}
		response.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(response).Encode(map[string]string{
			"ip":     "203.0.113.10",
			"scheme": "http",
			"host":   request.Host,
		})
	}))
	defer external.Close()

	home := t.TempDir()
	opts := options{
		home:        home,
		envFile:     filepath.Join(home, ".env"),
		externalURL: external.URL,
		expectedIP:  "203.0.113.99",
	}
	verification, err := verifyVisitorIPWithDetection(
		opts,
		map[string]string{"OMLORIX_LAUNCHER_PROXY_ENABLED": "true"},
		managedProxyConfig{Enabled: true},
		visitorIPDetection{FrontendIP: "172.30.0.2"},
	)
	if err == nil || !strings.Contains(err.Error(), "did not match") {
		t.Fatalf("wrong explicit expected IP error = %v", err)
	}
	if requestCount != 1 {
		t.Fatalf("external request count = %d, want 1", requestCount)
	}
	if verification.Verified || verification.VerificationPath != "external_proxy" {
		t.Fatalf("verification = %+v", verification)
	}
	encoded, marshalErr := json.Marshal(verification)
	if marshalErr != nil {
		t.Fatal(marshalErr)
	}
	if !strings.Contains(string(encoded), `"verification_path":"external_proxy"`) {
		t.Fatalf("JSON did not identify the verified path: %s", encoded)
	}
}

func TestExplicitExternalVisitorIPFlagsRemainStrictInManagedMode(t *testing.T) {
	config := managedProxyConfig{Enabled: true}
	detection := visitorIPDetection{FrontendIP: "172.30.0.2"}
	tests := []struct {
		name string
		opts options
		want string
	}{
		{
			name: "invalid scheme",
			opts: options{externalURL: "ftp://example.test", expectedIP: "203.0.113.10"},
			want: "HTTP or HTTPS URL",
		},
		{
			name: "missing expected IP",
			opts: options{externalURL: "https://example.test"},
			want: "requires --expected-ip",
		},
		{
			name: "missing external URL",
			opts: options{expectedIP: "203.0.113.10"},
			want: "requires --external-url",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := verifyVisitorIPWithDetection(test.opts, nil, config, detection)
			if err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("error = %v, want %q", err, test.want)
			}
		})
	}
}

func TestProxyPublicURLDropsStandardHTTPSPort(t *testing.T) {
	config := managedProxyConfig{
		PublicHostname: "chat.example.test",
		HTTPSPort:      "443",
		Target:         &url.URL{Scheme: "http", Host: "127.0.0.1:8080"},
	}
	if got := proxyPublicURL(config, true); got != "https://chat.example.test" {
		t.Fatalf("standard HTTPS URL = %q", got)
	}
}

func TestVisitorTopologyFingerprintTracksBackendAndProxySettings(t *testing.T) {
	home := t.TempDir()
	opts := options{home: home, envFile: filepath.Join(home, ".env")}
	env := map[string]string{
		"OMLORIX_LAUNCHER_PROXY_PUBLIC_HOSTNAME": "chat.example.test",
	}
	if err := os.WriteFile(opts.envFile, []byte("OMLORIX_LAUNCHER_PROXY_PUBLIC_HOSTNAME=chat.example.test\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	detection := visitorIPDetection{
		FrontendIP:        "172.31.250.10",
		FrontendContainer: "frontend-one",
		BackendContainer:  "backend-one",
		GatewayIP:         "172.31.250.1",
		Network:           "omlorix_omlorix-network",
	}
	baseline := visitorTopologyFingerprint(opts, env, detection)
	detection.BackendContainer = "backend-two"
	if changedBackend := visitorTopologyFingerprint(opts, env, detection); changedBackend == baseline {
		t.Fatal("backend recreation did not invalidate the visitor-IP fingerprint")
	}
	detection.BackendContainer = "backend-one"
	env["OMLORIX_LAUNCHER_PROXY_PUBLIC_HOSTNAME"] = "new.example.test"
	if changedProxy := visitorTopologyFingerprint(opts, env, detection); changedProxy == baseline {
		t.Fatal("proxy setting change did not invalidate the visitor-IP fingerprint")
	}
}

func TestLoadProxyTLSCertificateSupportsEncryptedPKCS8(t *testing.T) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{
		SerialNumber: big.NewInt(1),
		Subject:      pkix.Name{CommonName: "chat.example.test"},
		NotBefore:    time.Now().Add(-time.Minute),
		NotAfter:     time.Now().Add(time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment,
	}
	certificateDER, err := x509.CreateCertificate(rand.Reader, template, template, &privateKey.PublicKey, privateKey)
	if err != nil {
		t.Fatal(err)
	}
	passphrase := "correct horse battery staple"
	keyDER, err := pkcs8.MarshalPrivateKey(privateKey, []byte(passphrase), nil)
	if err != nil {
		t.Fatal(err)
	}
	directory := t.TempDir()
	certificatePath := filepath.Join(directory, "certificate.pem")
	caPath := filepath.Join(directory, "ca.pem")
	keyPath := filepath.Join(directory, "private-key.pem")
	certificatePEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certificateDER})
	if err := os.WriteFile(certificatePath, certificatePEM, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(caPath, certificatePEM, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(keyPath, pem.EncodeToMemory(&pem.Block{Type: "ENCRYPTED PRIVATE KEY", Bytes: keyDER}), 0o600); err != nil {
		t.Fatal(err)
	}

	certificate, err := loadProxyTLSCertificate(managedProxyConfig{
		TLSCertPath:      certificatePath,
		TLSKeyPath:       keyPath,
		TLSCAPath:        caPath,
		TLSKeyPassphrase: passphrase,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(certificate.Certificate) != 2 {
		t.Fatalf("certificate chain length = %d", len(certificate.Certificate))
	}
}

func TestProxyControlServerAuthenticatesReadinessAndStop(t *testing.T) {
	control, token, err := newProxyControlServer()
	if err != nil {
		t.Fatal(err)
	}
	go func() { _ = control.server.Serve(control.listener) }()
	defer control.server.Close()
	state := proxyRuntimeState{
		PID:                      os.Getpid(),
		ControlURL:               "http://" + control.listener.Addr().String(),
		ControlToken:             token,
		ConfigurationFingerprint: strings.Repeat("a", 64),
	}
	if !proxyRuntimeHealthy(state) {
		t.Fatal("authenticated readiness handshake failed")
	}
	invalid := state
	invalid.ControlToken = strings.Repeat("b", 64)
	if proxyRuntimeHealthy(invalid) {
		t.Fatal("invalid control token was accepted")
	}
	if !proxyRuntimeRequest(state, http.MethodPost, "/stop") {
		t.Fatal("authenticated stop request failed")
	}
	select {
	case <-control.stop:
	case <-time.After(time.Second):
		t.Fatal("control stop signal was not delivered")
	}
}

func TestManagedProxyIsolationRunsBeforeNoWaitStartup(t *testing.T) {
	home := t.TempDir()
	opts := options{home: home, envFile: filepath.Join(home, ".env")}
	if err := os.WriteFile(opts.envFile, []byte("OMLORIX_LAUNCHER_PROXY_ENABLED=true\nFRONTEND_HTTP_HOST_BIND=0.0.0.0\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	env, _ := readEnv(opts.envFile)
	if err := ensureManagedProxyFrontendIsolation(opts, env); err != nil {
		t.Fatal(err)
	}
	updated, _ := readEnv(opts.envFile)
	if got := updated["FRONTEND_HTTP_HOST_BIND"]; got != "127.0.0.1" {
		t.Fatalf("FRONTEND_HTTP_HOST_BIND = %q", got)
	}
}

func TestDisabledManagedProxyDoesNotChangeFrontendBind(t *testing.T) {
	home := t.TempDir()
	opts := options{home: home, envFile: filepath.Join(home, ".env")}
	if err := os.WriteFile(opts.envFile, []byte("OMLORIX_LAUNCHER_PROXY_ENABLED=false\nFRONTEND_HTTP_HOST_BIND=0.0.0.0\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	env, _ := readEnv(opts.envFile)
	previousBind := env["FRONTEND_HTTP_HOST_BIND"]
	if err := ensureManagedProxyFrontendIsolation(opts, env); err != nil {
		t.Fatal(err)
	}
	if previousBind != "0.0.0.0" || env["FRONTEND_HTTP_HOST_BIND"] != "0.0.0.0" {
		t.Fatal("disabled proxy unexpectedly changed the frontend bind")
	}
}

func TestManagedProxyEnabledStateUsesSharedServerSettings(t *testing.T) {
	home := t.TempDir()
	opts := options{home: home, envFile: filepath.Join(home, ".env")}
	if err := os.WriteFile(opts.envFile, []byte("FRONTEND_HTTP_HOST_BIND=127.0.0.1\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := writeManagedProxyEnabled(opts, true); err != nil {
		t.Fatal(err)
	}
	env, raw, err := readManagedEnvironment(opts)
	if err != nil {
		t.Fatal(err)
	}
	if !envTruthy(env["OMLORIX_LAUNCHER_PROXY_ENABLED"], false) {
		t.Fatal("enabled proxy state was not read from server-settings.json")
	}
	if strings.Contains(raw, "OMLORIX_LAUNCHER_PROXY_ENABLED") {
		t.Fatal("host-only proxy state leaked back into .env")
	}

	if err := writeManagedProxyEnabled(opts, false); err != nil {
		t.Fatal(err)
	}
	env, _, err = readManagedEnvironment(opts)
	if err != nil {
		t.Fatal(err)
	}
	if envTruthy(env["OMLORIX_LAUNCHER_PROXY_ENABLED"], true) {
		t.Fatal("disabled proxy state was not persisted")
	}
}

func TestProxyServiceStopTreatsAlreadyStoppedResponsesAsSuccess(t *testing.T) {
	alreadyStopped := []string{
		"Boot-out failed: 3: No such process",
		"Could not find specified service",
		"FAILED 1062: The service has not been started.",
		"Unit omlorix-server-proxy.service not loaded.",
	}
	for _, output := range alreadyStopped {
		if !serviceManagerStopAlreadySatisfied(output) {
			t.Fatalf("already-stopped service response was not recognized: %q", output)
		}
	}
	for _, output := range []string{
		"Access is denied.",
		"Input/output error",
		"The service did not respond to the control request.",
	} {
		if serviceManagerStopAlreadySatisfied(output) {
			t.Fatalf("real service-manager failure was ignored: %q", output)
		}
	}
}
