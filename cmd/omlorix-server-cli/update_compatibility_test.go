package main

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func TestReleaseFetcherUsesAnonymousPublicGitHubHeaders(t *testing.T) {
	previousTransport := http.DefaultTransport
	t.Cleanup(func() { http.DefaultTransport = previousTransport })
	http.DefaultTransport = roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if authorization := request.Header.Get("Authorization"); authorization != "" {
			t.Fatalf("anonymous release request sent Authorization = %q", authorization)
		}
		if accept := request.Header.Get("Accept"); accept != "application/json, application/vnd.github+json" {
			t.Fatalf("Accept = %q", accept)
		}
		if userAgent := request.Header.Get("User-Agent"); userAgent != "omlorix-server-cli/"+cliVersion {
			t.Fatalf("User-Agent = %q", userAgent)
		}
		return &http.Response{
			StatusCode: http.StatusOK,
			Header:     make(http.Header),
			Body:       io.NopCloser(strings.NewReader(`{"version":"1.2.3"}`)),
			Request:    request,
		}, nil
	})

	payload := map[string]any{}
	if err := fetchCLIJSON(
		"https://api.github.com/repos/phinaldoo/omlorix/releases/latest",
		&payload,
	); err != nil {
		t.Fatal(err)
	}
	if payload["version"] != "1.2.3" {
		t.Fatalf("payload = %#v", payload)
	}
}

func TestManagementCompatibilityRejectsOlderCLI(t *testing.T) {
	original := cliVersion
	cliVersion = "1.2.0"
	t.Cleanup(func() { cliVersion = original })
	err := managementCompatibilityError(serverReleaseInfo{Version: "2.0.0", MinimumManagementVersion: "1.3.0"})
	if err == nil {
		t.Fatal("older CLI was allowed to install an incompatible server release")
	}
	if err := managementCompatibilityError(serverReleaseInfo{Version: "2.0.0", MinimumManagementVersion: "1.2.0"}); err != nil {
		t.Fatalf("compatible CLI was rejected: %v", err)
	}
}

func TestBetaFeed404IsDistinguishedFromFeedAccessFailure(t *testing.T) {
	missingBeta := func(rawURL string, target any) error {
		if strings.HasSuffix(rawURL, "/beta.json") {
			return &releaseFetchHTTPError{URL: rawURL, StatusCode: http.StatusNotFound}
		}
		return json.Unmarshal([]byte(`{"channel":"stable","version":"1.1.6"}`), target)
	}
	_, err := releaseInfoForChannelWithFetcher(
		"beta",
		missingBeta,
	)
	if err == nil || !strings.Contains(err.Error(), "beta channel feed is not published yet") {
		t.Fatalf("missing beta error = %v", err)
	}

	inaccessible := func(rawURL string, target any) error {
		return &releaseFetchHTTPError{URL: rawURL, StatusCode: http.StatusNotFound}
	}
	_, err = releaseInfoForChannelWithFetcher(
		"beta",
		inaccessible,
	)
	if err == nil || !strings.Contains(err.Error(), "could not be accessed or are not published") {
		t.Fatalf("feed access error = %v", err)
	}
}

func TestExplicitVersionLoadsItsOwnCompatibilityManifest(t *testing.T) {
	fetcher := func(rawURL string, target any) error {
		var payload string
		switch rawURL {
		case "https://api.github.com/repos/phinaldoo/omlorix/releases/tags/v1.2.3":
			payload = `{"tag_name":"v1.2.3","html_url":"https://example.test/release","assets":[{"name":"omlorix-release-manifest.json","browser_download_url":"https://example.test/manifest"}]}`
		case "https://example.test/manifest":
			payload = `{"minimumLauncherVersion":"9.0.0","launcherUpdateReason":"Upgrade management first."}`
		default:
			return errors.New("unexpected test URL: " + rawURL)
		}
		return json.Unmarshal([]byte(payload), target)
	}
	info, err := releaseInfoForVersionWithFetcher(
		"stable",
		"1.2.3",
		fetcher,
	)
	if err != nil {
		t.Fatal(err)
	}
	if info.Version != "1.2.3" || info.MinimumManagementVersion != "9.0.0" {
		t.Fatalf("version-specific compatibility metadata = %+v", info)
	}
	if err := managementCompatibilityError(info); err == nil {
		t.Fatal("explicit version bypassed its minimum CLI version")
	}
}
