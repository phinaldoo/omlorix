package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// serverReleaseInfo is the CLI equivalent of Electron's normalized release
// metadata. Keeping the compatibility fields in this layer prevents a newer
// server release from being installed by management code that cannot safely
// operate it.
type serverReleaseInfo struct {
	Channel                  string
	Version                  string
	ReleaseURL               string
	ManifestURL              string
	MinimumManagementVersion string
	UpdateReason             string
}

type releaseManifest struct {
	MinimumLauncherVersion string `json:"minimumLauncherVersion"`
	LauncherUpdateReason   string `json:"launcherUpdateReason"`
	Notes                  string `json:"notes"`
}

type releaseFetchHTTPError struct {
	URL        string
	StatusCode int
}

func (failure *releaseFetchHTTPError) Error() string {
	return fmt.Sprintf("%s returned HTTP %d", failure.URL, failure.StatusCode)
}

func releaseInfoForChannel(channel string) (serverReleaseInfo, error) {
	return releaseInfoForChannelWithFetcher(channel, fetchCLIJSON)
}

func releaseInfoForChannelWithFetcher(
	channel string,
	fetcher func(string, any) error,
) (serverReleaseInfo, error) {
	channel = normalizeUpdateChannel(channel)
	// Channel feeds are a repository-owned trust boundary. Keep their URLs fixed
	// just like the Electron launcher does; the injected fetcher exists only to
	// make response handling deterministic in tests.
	feedURL := channelFeedURL(channel)
	var feed channelFeedResponse
	if err := fetcher(feedURL, &feed); err != nil {
		if channel == "beta" {
			return serverReleaseInfo{}, classifyMissingBetaFeed(
				fetcher,
				channelFeedURL("stable"),
				err,
			)
		}
		return serverReleaseInfo{}, err
	}
	version := strings.TrimPrefix(firstNonBlank(feed.Version, feed.Tag), "v")
	if version == "" {
		return serverReleaseInfo{}, errors.New("channel feed did not include a version")
	}
	info := serverReleaseInfo{
		Channel: channel, Version: version, ReleaseURL: feed.ReleaseURL,
		ManifestURL:              feed.ManifestURL,
		MinimumManagementVersion: feed.MinimumLauncherVersion,
		UpdateReason:             feed.LauncherUpdateReason,
	}
	if err := enrichReleaseCompatibilityWithFetcher(&info, fetcher); err != nil {
		return serverReleaseInfo{}, err
	}
	return info, nil
}

func releaseFetchStatus(err error) int {
	var fetchError *releaseFetchHTTPError
	if errors.As(err, &fetchError) {
		return fetchError.StatusCode
	}
	return 0
}

func classifyMissingBetaFeed(
	fetcher func(string, any) error,
	stableFeedURL string,
	betaError error,
) error {
	betaStatus := releaseFetchStatus(betaError)
	if betaStatus == http.StatusUnauthorized || betaStatus == http.StatusForbidden {
		return fmt.Errorf("beta release channel feed could not be accessed: %w", betaError)
	}
	if betaStatus != http.StatusNotFound {
		return betaError
	}

	var stableFeed channelFeedResponse
	stableError := fetcher(stableFeedURL, &stableFeed)
	if stableError == nil && firstNonBlank(stableFeed.Version, stableFeed.Tag) != "" {
		return fmt.Errorf(
			"beta channel feed is not published yet; the stable channel feed is available: %w",
			betaError,
		)
	}
	stableStatus := releaseFetchStatus(stableError)
	if stableStatus == http.StatusUnauthorized || stableStatus == http.StatusForbidden || stableStatus == http.StatusNotFound {
		return fmt.Errorf(
			"release channel feeds could not be accessed or are not published: beta: %v; stable: %v",
			betaError,
			stableError,
		)
	}
	return fmt.Errorf("beta channel feed lookup failed: %w", betaError)
}

// releaseInfoForVersion resolves compatibility metadata for the exact version
// requested by an explicit CLI pin. Reusing the moving channel response here
// would either apply the wrong manifest or silently skip the compatibility
// boundary when the requested version is not the channel's current release.
func releaseInfoForVersion(channel, version string) (serverReleaseInfo, error) {
	return releaseInfoForVersionWithFetcher(channel, version, fetchCLIJSON)
}

// releaseInfoForVersionWithFetcher isolates HTTP transport from the immutable
// release-resolution rules so compatibility behavior can be tested without a
// network listener.
func releaseInfoForVersionWithFetcher(
	channel,
	version string,
	fetcher func(string, any) error,
) (serverReleaseInfo, error) {
	channel = normalizeUpdateChannel(channel)
	version = strings.TrimPrefix(strings.TrimSpace(version), "v")
	if version == "" {
		return serverReleaseInfo{}, errors.New("explicit release version is required")
	}

	candidates := versionedReleaseAPIURLs(version)

	var release latestReleaseResponse
	var fetchErr error
	for _, candidate := range candidates {
		release = latestReleaseResponse{}
		fetchErr = fetcher(candidate, &release)
		if fetchErr == nil {
			break
		}
	}
	if fetchErr != nil {
		return serverReleaseInfo{}, fmt.Errorf("could not load release metadata for Omlorix %s: %w", version, fetchErr)
	}
	resolvedVersion := strings.TrimPrefix(strings.TrimSpace(release.TagName), "v")
	if resolvedVersion != version {
		return serverReleaseInfo{}, fmt.Errorf(
			"release metadata version mismatch: requested %s but received %s",
			version,
			firstNonBlank(resolvedVersion, "an empty tag"),
		)
	}

	info := serverReleaseInfo{Channel: channel, Version: version, ReleaseURL: release.HTMLURL}
	for _, asset := range release.Assets {
		if asset.Name == "omlorix-release-manifest.json" {
			info.ManifestURL = asset.BrowserDownloadURL
			break
		}
	}
	if err := enrichReleaseCompatibilityWithFetcher(&info, fetcher); err != nil {
		return serverReleaseInfo{}, err
	}
	return info, nil
}

// versionedReleaseAPIURLs returns the official GitHub endpoints for an exact
// Omlorix tag. Older releases did not consistently include the leading "v".
func versionedReleaseAPIURLs(version string) []string {
	escapedVersion := url.PathEscape(version)
	const baseURL = "https://api.github.com/repos/phinaldoo/omlorix/releases/tags/"
	return []string{baseURL + "v" + escapedVersion, baseURL + escapedVersion}
}

// enrichReleaseCompatibilityWithFetcher loads the optional manifest through
// the supplied transport while preserving the production 404 compatibility
// behavior for older releases that predate manifests.
func enrichReleaseCompatibilityWithFetcher(
	info *serverReleaseInfo,
	fetcher func(string, any) error,
) error {
	if info.ManifestURL == "" {
		return nil
	}
	var manifest releaseManifest
	if err := fetcher(info.ManifestURL, &manifest); err != nil {
		if strings.Contains(err.Error(), "returned HTTP 404") {
			return nil
		}
		return fmt.Errorf("could not load release compatibility manifest: %w", err)
	}
	info.MinimumManagementVersion = firstNonBlank(manifest.MinimumLauncherVersion, info.MinimumManagementVersion)
	info.UpdateReason = firstNonBlank(manifest.LauncherUpdateReason, manifest.Notes, info.UpdateReason)
	return nil
}

func fetchCLIJSON(url string, target any) error {
	client := http.Client{Timeout: 10 * time.Second}
	request, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	request.Header.Set("Accept", "application/json, application/vnd.github+json")
	request.Header.Set("User-Agent", "omlorix-server-cli/"+cliVersion)
	response, err := client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return &releaseFetchHTTPError{URL: url, StatusCode: response.StatusCode}
	}
	return json.NewDecoder(response.Body).Decode(target)
}

func managementCompatibilityError(info serverReleaseInfo) error {
	minimum := strings.TrimSpace(info.MinimumManagementVersion)
	if minimum == "" || minimum == "0.0.0" || compareVersions(cliVersion, minimum) >= 0 {
		return nil
	}
	detail := ""
	if strings.TrimSpace(info.UpdateReason) != "" {
		detail = " " + strings.TrimSpace(info.UpdateReason)
	}
	return fmt.Errorf("Omlorix %s requires Omlorix Server CLI %s or newer; current CLI is %s.%s", info.Version, minimum, cliVersion, detail)
}
