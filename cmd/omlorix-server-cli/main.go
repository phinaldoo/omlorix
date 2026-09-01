package main

import (
	"crypto/rand"
	"crypto/sha256"
	"embed"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

//go:embed all:assets
var embeddedAssets embed.FS

//go:embed server-files.json
var serverFileManifestJSON []byte

const jwtSecretMinBytes = 64

type envToggles struct {
	useBundledDB      bool
	useBundledRedis   bool
	redisEnabled      bool
	usePgbouncer      bool
	useBundledStorage bool
	observability     bool
	devMode           bool
}

type options struct {
	command               string
	arguments             []string
	home                  string
	envFile               string
	sourceRoot            string
	version               string
	channel               string
	source                string
	target                string
	externalURL           string
	expectedIP            string
	confirm               string
	lines                 int
	timeout               time.Duration
	openBrowser           bool
	skipBackup            bool
	noEncrypted           bool
	noWait                bool
	follow                bool
	jsonOutput            bool
	showSecrets           bool
	destination           string
	destinationSet        bool
	output                string
	jobID                 string
	service               string
	since                 string
	name                  string
	port                  int
	memory                string
	maxConcurrent         int
	sessionTimeout        int
	networkAccess         bool
	allowPip              bool
	nameSet               bool
	versionSet            bool
	portSet               bool
	memorySet             bool
	maxConcurrentSet      bool
	sessionTimeoutSet     bool
	networkAccessSet      bool
	allowPipSet           bool
	schedule              string
	scheduleSet           bool
	weekdays              string
	weekdaysSet           bool
	timeOfDay             string
	allowUnhealthy        bool
	verbose               bool
	attachProject         string
	backupBeforeUpdateSet bool
	backupEncryptionSet   bool
	onlyWhenHealthySet    bool
	fromProvider          string
	toProvider            string
	storageScope          string
	userID                string
	onlyMigratedFrom      string
	createdAfter          string
	createdBefore         string
	batchSize             int
	maxFiles              int
	retries               int
	dryRun                bool
	deleteSource          bool
	force                 bool
}

type latestReleaseResponse struct {
	TagName string `json:"tag_name"`
	HTMLURL string `json:"html_url"`
	Assets  []struct {
		Name               string `json:"name"`
		BrowserDownloadURL string `json:"browser_download_url"`
	} `json:"assets"`
}

type channelFeedResponse struct {
	Version                string `json:"version"`
	Tag                    string `json:"tag"`
	ManifestURL            string `json:"manifestUrl"`
	ReleaseURL             string `json:"releaseUrl"`
	MinimumLauncherVersion string `json:"minimumLauncherVersion"`
	LauncherUpdateReason   string `json:"launcherUpdateReason"`
}

const (
	officialStableFeedURL = "https://raw.githubusercontent.com/phinaldoo/omlorix/release-feed/channels/stable.json"
	officialBetaFeedURL   = "https://raw.githubusercontent.com/phinaldoo/omlorix/release-feed/channels/beta.json"
	readinessTimeout      = 2 * time.Minute
	readinessInterval     = 5 * time.Second
)

var (
	logUnixTimestampPattern = regexp.MustCompile(`^[0-9]{1,19}(\.[0-9]{1,9})?$`)
	logCalendarDatePattern  = regexp.MustCompile(`^([0-9]{4})-([0-9]{2})-([0-9]{2})(.*)$`)
	logCalendarTimePattern  = regexp.MustCompile(`^T([0-9]{2})(:([0-9]{2})(:([0-9]{2})(\.[0-9]{1,9})?)?)?(Z|[+-][0-9]{2}:[0-9]{2})?$`)
	logTimezonePattern      = regexp.MustCompile(`^[+-]([0-9]{2}):([0-9]{2})$`)
)

// cliVersion is replaced with the validated launcher release version by the
// release workflow. The source fallback keeps local development builds useful.
var cliVersion = "0.9.41"

type serverFileManifest struct {
	Common  []string `json:"common"`
	CLIOnly []string `json:"cliOnly"`
}

var serverFiles = mustLoadServerFiles()

var grafanaProvisioningFiles = []string{
	"otel/grafana/provisioning/dashboards/dashboards.yml",
	"otel/grafana/provisioning/datasources/datasources.yml",
}

func mustLoadServerFiles() []string {
	var manifest serverFileManifest
	if err := json.Unmarshal(serverFileManifestJSON, &manifest); err != nil {
		panic(fmt.Sprintf("load packaged server-file contract: %v", err))
	}
	return append(append([]string{}, manifest.Common...), manifest.CLIOnly...)
}

func main() {
	opts, err := parseOptions(os.Args[1:])
	if err != nil {
		if !opts.jsonOutput {
			opts.jsonOutput = jsonOutputRequested(os.Args[1:])
		}
		fatalForOptions(opts, invalidArgumentsError(err))
	}
	if opts.command == "" || opts.command == "help" || opts.command == "--help" || opts.command == "-h" {
		printHelp()
		return
	}
	releaseLock := func() {}
	if commandNeedsLock(opts) {
		release, lockErr := acquireOperationLock(opts)
		if lockErr != nil {
			fatalForOptions(opts, lockErr)
		}
		releaseLock = release
	}

	switch opts.command {
	case "doctor":
		err = commandDoctor(opts)
	case "status":
		err = commandStatus(opts)
	case "init":
		err = commandInit(opts)
	case "start":
		err = commandStart(opts)
	case "stop":
		err = commandStop(opts)
	case "restart":
		err = commandRestart(opts)
	case "update":
		err = commandUpdate(opts)
	case "backup":
		err = commandBackup(opts)
	case "restore":
		err = commandRestore(opts)
	case "logs":
		err = commandLogs(opts)
	case "services":
		err = commandServices(opts)
	case "service":
		err = commandService(opts)
	case "config":
		err = commandConfig(opts)
	case "update-channel":
		err = commandUpdateChannel(opts)
	case "secrets":
		err = commandSecrets(opts)
	case "check-update":
		err = commandCheckUpdate(opts)
	case "backup-options":
		err = commandBackupOptions(opts)
	case "backup-verify":
		err = commandBackupVerify(opts)
	case "open":
		err = commandOpen(opts)
	case "version":
		err = commandVersion(opts)
	case "code-execution":
		err = commandCodeExecution(opts)
	case "auto-update":
		err = commandAutoUpdate(opts)
	case "visitor-ip":
		err = commandVisitorIP(opts)
	case "proxy":
		err = commandProxy(opts)
	case "storage":
		err = commandStorage(opts)
	default:
		err = fmt.Errorf("unknown command %q", opts.command)
	}
	releaseLock()
	if err != nil {
		fatalForOptions(opts, err)
	}
}

func jsonOutputRequested(args []string) bool {
	requested := false
	for _, arg := range args {
		key, value, hasValue := strings.Cut(arg, "=")
		if key != "--json" {
			continue
		}
		requested = !hasValue || strings.EqualFold(strings.TrimSpace(value), "true")
	}
	return requested
}

func readEnvToggles(opts options) envToggles {
	env, _ := readEnv(opts.envFile)
	defaultBundledDB := true
	defaultBundledRedis := true
	defaultPgbouncer := false
	defaultBundledStorage := false

	return envToggles{
		useBundledDB:      envTruthy(env["OMLORIX_USE_BUNDLED_DB"], defaultBundledDB),
		redisEnabled:      envTruthy(env["REDIS_ENABLED"], true),
		useBundledRedis:   envTruthy(env["REDIS_ENABLED"], true) && envTruthy(env["OMLORIX_USE_BUNDLED_REDIS"], defaultBundledRedis),
		usePgbouncer:      envTruthy(env["OMLORIX_USE_PGBOUNCER"], defaultPgbouncer),
		useBundledStorage: envTruthy(env["OMLORIX_USE_BUNDLED_STORAGE"], defaultBundledStorage),
		observability:     envTruthy(env["OTEL_ENABLED"], false),
		devMode:           strings.EqualFold(strings.TrimSpace(env["MODE"]), "dev"),
	}
}

func envTruthy(val string, def bool) bool {
	val = strings.TrimSpace(strings.ToLower(val))
	if val == "" {
		return def
	}
	return val == "1" || val == "true" || val == "yes" || val == "on" || val == "y"
}

func parseOptions(args []string) (options, error) {
	opts := options{
		home:         defaultServerHome(),
		target:       "empty",
		lines:        serverManagement.Logs.DefaultLines,
		timeout:      readinessTimeout,
		storageScope: "all",
		batchSize:    200,
		retries:      3,
	}
	if value := strings.TrimSpace(os.Getenv("OMLORIX_SERVER_HOME")); value != "" {
		opts.home = value
	}
	if value := strings.TrimSpace(os.Getenv("OMLORIX_DEPLOYMENT_ASSETS_SOURCE")); value != "" {
		opts.sourceRoot = value
	}
	if value := strings.TrimSpace(os.Getenv("OMLORIX_SERVER_CHANNEL")); value != "" {
		channel, err := validateUpdateChannel(value)
		if err != nil {
			return opts, fmt.Errorf("OMLORIX_SERVER_CHANNEL: %w", err)
		}
		opts.channel = channel
	}
	// Preserve --version as the explicit server-version selector for update and
	// Code Execution commands while still supporting the conventional top-level
	// version flags operators and package managers expect.
	if len(args) == 1 && (args[0] == "--version" || args[0] == "-v") {
		opts.command = "version"
		opts.home = filepath.Clean(opts.home)
		opts.envFile = filepath.Join(opts.home, ".env")
		return opts, nil
	}

	remaining := []string{}
	for index := 0; index < len(args); index++ {
		arg := args[index]
		if arg == "--" {
			remaining = append(remaining, args[index+1:]...)
			break
		}
		if !strings.HasPrefix(arg, "--") {
			remaining = append(remaining, arg)
			continue
		}
		key, value, hasInlineValue := strings.Cut(arg, "=")
		takeValue := func() (string, error) {
			if hasInlineValue {
				return value, nil
			}
			if index+1 >= len(args) {
				return "", fmt.Errorf("%s requires a value", key)
			}
			index++
			return args[index], nil
		}
		// Boolean safety flags support both the conventional bare spelling and
		// explicit values. Silently discarding an inline "=false" can invert an
		// operator's intent, which is especially dangerous for update safeguards.
		booleanValue := func(bareValue bool) (bool, error) {
			if !hasInlineValue {
				return bareValue, nil
			}
			switch strings.ToLower(strings.TrimSpace(value)) {
			case "true":
				return true, nil
			case "false":
				return false, nil
			default:
				return false, fmt.Errorf("%s accepts only true or false", key)
			}
		}

		switch key {
		case "--help":
			opts.command = "help"
			return opts, nil
		case "--home":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.home = value
		case "--env-file":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.envFile = value
		case "--source-root":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.sourceRoot = value
		case "--attach-project":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.attachProject = strings.TrimSpace(value)
		case "--version":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.version = value
			opts.versionSet = true
		case "--channel":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.channel, err = validateUpdateChannel(value)
			if err != nil {
				return opts, err
			}
		case "--source":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.source = value
		case "--target":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.target = value
		case "--external-url":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.externalURL = value
		case "--expected-ip":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.expectedIP = value
		case "--confirm":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.confirm = value
		case "--lines":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			lines, parseErr := strconv.Atoi(value)
			if parseErr != nil {
				return opts, fmt.Errorf(
					"--lines must be an integer from %d to %d",
					serverManagement.Logs.MinimumLines,
					serverManagement.Logs.MaximumLines,
				)
			}
			if err := validateLogLineCount(lines); err != nil {
				return opts, err
			}
			opts.lines = lines
		case "--timeout":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			duration, parseErr := time.ParseDuration(value)
			if parseErr != nil || duration <= 0 {
				return opts, fmt.Errorf("--timeout must be a positive duration such as 90s or 5m")
			}
			opts.timeout = duration
		case "--destination":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.destination = value
			opts.destinationSet = true
		case "--output":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.output = value
		case "--job-id":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.jobID = value
		case "--service":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.service = value
		case "--since":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.since, err = normalizeLogTimeBound(value)
			if err != nil {
				return opts, err
			}
		case "--name":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.name = value
			opts.nameSet = true
		case "--port":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			port, parseErr := strconv.Atoi(value)
			if parseErr != nil || port < 1 || port > 65535 {
				return opts, errors.New("--port must be between 1 and 65535")
			}
			opts.port = port
			opts.portSet = true
		case "--memory":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.memory = strings.ToLower(value)
			opts.memorySet = true
		case "--max-concurrent":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			count, parseErr := strconv.Atoi(value)
			if parseErr != nil || count < 1 || count > 100 {
				return opts, errors.New("--max-concurrent must be between 1 and 100")
			}
			opts.maxConcurrent = count
			opts.maxConcurrentSet = true
		case "--session-timeout":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			seconds, parseErr := strconv.Atoi(value)
			if parseErr != nil || seconds < 60 || seconds > 86400 {
				return opts, errors.New("--session-timeout must be between 60 and 86400 seconds")
			}
			opts.sessionTimeout = seconds
			opts.sessionTimeoutSet = true
		case "--open":
			opts.openBrowser = true
		case "--skip-backup":
			flagValue, err := booleanValue(true)
			if err != nil {
				return opts, err
			}
			opts.skipBackup = flagValue
			opts.backupBeforeUpdateSet = true
		case "--backup-before-update":
			flagValue, err := booleanValue(true)
			if err != nil {
				return opts, err
			}
			opts.skipBackup = !flagValue
			opts.backupBeforeUpdateSet = true
		case "--no-encrypted":
			flagValue, err := booleanValue(true)
			if err != nil {
				return opts, err
			}
			opts.noEncrypted = flagValue
			opts.backupEncryptionSet = true
		case "--no-wait":
			opts.noWait = true
		case "--follow":
			opts.follow = true
		case "--json":
			flagValue, err := booleanValue(true)
			if err != nil {
				return opts, err
			}
			opts.jsonOutput = flagValue
		case "--verbose":
			opts.verbose = true
		case "--show-secrets":
			opts.showSecrets = true
		case "--network-access":
			opts.networkAccess = true
			opts.networkAccessSet = true
		case "--no-network-access":
			opts.networkAccess = false
			opts.networkAccessSet = true
		case "--allow-pip":
			opts.allowPip = true
			opts.allowPipSet = true
		case "--no-allow-pip":
			opts.allowPip = false
			opts.allowPipSet = true
		case "--schedule":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.schedule = strings.ToLower(strings.TrimSpace(value))
			opts.scheduleSet = true
		case "--weekdays":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.weekdays = value
			opts.weekdaysSet = true
		case "--time":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.timeOfDay = value
		case "--allow-unhealthy":
			flagValue, err := booleanValue(true)
			if err != nil {
				return opts, err
			}
			opts.allowUnhealthy = flagValue
			opts.onlyWhenHealthySet = true
		case "--only-when-healthy":
			flagValue, err := booleanValue(true)
			if err != nil {
				return opts, err
			}
			opts.allowUnhealthy = !flagValue
			opts.onlyWhenHealthySet = true
		case "--from-provider":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.fromProvider = value
		case "--to-provider":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.toProvider = value
		case "--scope":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.storageScope = value
		case "--user-id":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.userID = value
		case "--only-migrated-from":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.onlyMigratedFrom = value
		case "--created-after":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.createdAfter = value
		case "--created-before":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.createdBefore = value
		case "--batch-size":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.batchSize, err = strconv.Atoi(value)
			if err != nil || opts.batchSize < 1 {
				return opts, errors.New("--batch-size must be at least 1")
			}
		case "--max-files":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.maxFiles, err = strconv.Atoi(value)
			if err != nil || opts.maxFiles < 0 {
				return opts, errors.New("--max-files must be 0 or greater")
			}
		case "--retries":
			value, err := takeValue()
			if err != nil {
				return opts, err
			}
			opts.retries, err = strconv.Atoi(value)
			if err != nil || opts.retries < 1 {
				return opts, errors.New("--retries must be at least 1")
			}
		case "--dry-run":
			opts.dryRun = true
		case "--delete-source":
			opts.deleteSource = true
		case "--force":
			opts.force = true
		default:
			return opts, fmt.Errorf("unknown flag %s", key)
		}
	}

	if len(remaining) > 0 {
		opts.command = remaining[0]
		opts.arguments = append([]string(nil), remaining[1:]...)
	}
	switch opts.command {
	case "backup", "service", "config", "secrets", "code-execution", "auto-update", "visitor-ip", "proxy", "storage", "update-channel":
		// These commands own their positional subcommands and arguments.
	default:
		if len(opts.arguments) > 0 {
			return opts, fmt.Errorf("unexpected positional argument %q for %s", opts.arguments[0], opts.command)
		}
	}
	opts.home = filepath.Clean(opts.home)
	if opts.envFile == "" {
		opts.envFile = filepath.Join(opts.home, ".env")
	}
	if opts.attachProject != "" && opts.command != "init" {
		return opts, errors.New("--attach-project can only be used with init")
	}
	if opts.channel != "" {
		opts.channel = normalizeUpdateChannel(opts.channel)
	}
	return opts, nil
}

func normalizeUpdateChannel(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "beta":
		return "beta"
	default:
		return "stable"
	}
}

// validateUpdateChannel rejects operator typos instead of silently moving an
// installation onto the stable channel. Empty values remain valid because the
// caller can still apply the configured/default channel afterwards.
func validateUpdateChannel(value string) (string, error) {
	normalized := strings.ToLower(strings.TrimSpace(value))
	if normalized == "" {
		return "", nil
	}
	if normalized != "stable" && normalized != "beta" {
		return "", fmt.Errorf("--channel must be stable or beta (got %q)", value)
	}
	return normalized, nil
}

// normalizeLogTimeBound mirrors the Launcher's bounded log contract. It accepts
// documented Go durations, Unix timestamps, and calendar timestamps for
// Docker's --since flag while rejecting malformed values before Docker starts.
func normalizeLogTimeBound(value string) (string, error) {
	normalized := strings.TrimSpace(value)
	if normalized == "" {
		return "", nil
	}
	_, durationErr := time.ParseDuration(normalized)
	valid := durationErr == nil
	valid = valid || validLogUnixTimestamp(normalized)
	valid = valid || validLogCalendarTimestamp(normalized)
	if utf8.RuneCountInString(normalized) > serverManagement.Logs.MaximumTimeBoundLength || !valid {
		return "", errors.New("--since must be a valid log time bound such as 5m or 2026-08-23T10:30:00Z")
	}
	return normalized, nil
}

func validLogUnixTimestamp(value string) bool {
	if !logUnixTimestampPattern.MatchString(value) {
		return false
	}
	seconds, _, _ := strings.Cut(value, ".")
	_, err := strconv.ParseInt(seconds, 10, 64)
	return err == nil
}

func validLogCalendarTimestamp(value string) bool {
	match := logCalendarDatePattern.FindStringSubmatch(value)
	if match == nil {
		return false
	}
	year, _ := strconv.Atoi(match[1])
	month, _ := strconv.Atoi(match[2])
	day, _ := strconv.Atoi(match[3])
	// JavaScript's Date.UTC treats years 0-99 as 1900-1999, so the Launcher
	// rejects them when it verifies the normalized calendar components.
	if year < 100 {
		return false
	}
	date := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
	if date.Year() != year || int(date.Month()) != month || date.Day() != day {
		return false
	}

	remainder := match[4]
	if remainder == "" {
		return true
	}
	if remainder[0] != 'T' {
		return validLogTimezone(remainder)
	}
	timeMatch := logCalendarTimePattern.FindStringSubmatch(remainder)
	if timeMatch == nil {
		return false
	}
	hour, _ := strconv.Atoi(timeMatch[1])
	minute, _ := strconv.Atoi(timeMatch[3])
	second, _ := strconv.Atoi(timeMatch[5])
	if hour > 23 || minute > 59 || second > 59 {
		return false
	}
	return validLogTimezone(timeMatch[7])
}

func validLogTimezone(value string) bool {
	if value == "" || value == "Z" {
		return true
	}
	match := logTimezonePattern.FindStringSubmatch(value)
	if match == nil {
		return false
	}
	hour, _ := strconv.Atoi(match[1])
	minute, _ := strconv.Atoi(match[2])
	return hour <= 23 && minute <= 59
}

func printHelp() {
	fmt.Printf(`Omlorix Server CLI

Usage:
  omlorix-server [flags] doctor
  omlorix-server [flags] status [--json]
  omlorix-server [flags] init
  omlorix-server [flags] start [--open] [--no-wait]
  omlorix-server [flags] stop
  omlorix-server [flags] restart
  omlorix-server [flags] update [--version <tag>] [--channel stable|beta] [--skip-backup] [--destination <id>] [--no-encrypted]
  omlorix-server [flags] check-update [--channel stable|beta] [--json]
  omlorix-server [flags] update-channel [stable|beta]
  omlorix-server [flags] services [--json]
  omlorix-server [flags] service <start|stop|restart|logs> <name> [--no-wait]
  omlorix-server [flags] config <list|get|set|unset|path|edit|export|import|replace> [arguments]
  omlorix-server [flags] secrets <regenerate|export|import|backup-status|save-now|disable-backup> [arguments]
  omlorix-server [flags] backup-options [--json]
  omlorix-server [flags] backup [--destination <id>] [--no-encrypted] [--json]
  omlorix-server [flags] backup list [--json]
  omlorix-server [flags] backup show <job-id> [--json]
  omlorix-server [flags] backup download <job-id> --output <path> [--json]
  omlorix-server [flags] backup-verify (--job-id <id> | --source <uri>) [--json]
  omlorix-server [flags] restore (--source <uri> | --job-id <id>) [--target empty|in_place] [--confirm RESTORE-IN-PLACE]
  omlorix-server [flags] logs [--lines %d] [--follow] [--service <name>] [--since <time>]
  omlorix-server [flags] open
  omlorix-server [flags] code-execution <list|versions|create|edit|check-update|start|stop|restart|update|logs|connection|delete> [id]
  omlorix-server [flags] auto-update <status|enable|disable|run|daemon>
  omlorix-server [flags] visitor-ip <status|detect|repair|verify>
  omlorix-server [flags] proxy <status|settings|configure|enable|disable|start|stop|restart|install-service|refresh-service|uninstall-service>
  omlorix-server [flags] storage probe [--json]
  omlorix-server [flags] storage migrate --from-provider <provider> [--to-provider <provider>] [migration flags] [--json]
  omlorix-server [flags] storage migrate-local [migration flags] [--json]
  omlorix-server version | --version | -v

Flags:
  --home <path>          Server data directory
  --channel <channel>   stable or beta update channel
  --env-file <path>     Environment file path
  --external-url <url>  Public URL for external-proxy visitor-IP diagnostics
  --expected-ip <ip>    Expected caller IP for external-proxy verification
  --source-root <path>  Directory containing bundled Compose files
  --attach-project <name>  Explicitly adopt a legacy Compose project during init
  --timeout <duration>  Readiness timeout (default 2m)
  --lines <count>       Log snapshot line count (%d-%d; default %d)
  --json                Machine-readable output where supported
  --verbose             Include diagnostic command output
  --show-secrets        Allow config output to contain secret values
  --output <path>       Explicit path for a downloaded backup archive

Code Execution create flags:
  --name <name>          Display name (required)
  --version <version>    Pinned semantic version (latest when omitted)
  --port <port>          Loopback gateway port (first free 8000-8999 when omitted)
  --memory <limit>       256m, 512m, 1g, 2g, 4g, or 8g
  --max-concurrent <n>   Maximum concurrent executions (1-100)
  --session-timeout <s> Session timeout in seconds (60-86400)
  --network-access       Allow sandbox network access
  --allow-pip            Allow pip installs inside sandboxes

Run "omlorix-server code-execution versions" to list concrete published versions.

Code Execution edit flags use the same settings and also support:
  --no-network-access    Disable sandbox network access
  --no-allow-pip         Disable pip installs inside sandboxes

Automatic update flags:
  --schedule <mode>      daily, weekends, or custom
  --weekdays <days>      Comma-separated weekdays 0-6 (Sunday is 0)
  --time <HH:MM>         Local maintenance-window time
  --skip-backup[=bool]   Do not create a backup before an automatic update
  --backup-before-update[=bool]
                         Require a backup before an automatic update
  --destination <id>     Backup destination for manual and automatic updates
  --no-encrypted[=bool]  Disable archive encryption for update backups
  --allow-unhealthy[=bool]
                         Allow automatic updates when Omlorix is not healthy
  --only-when-healthy[=bool]
                         Require Omlorix health before an automatic update

Storage migration flags:
  --from-provider <name> Source provider: local, s3, gcs, azure, or webdav
  --to-provider <name>   Destination provider (defaults to FILE_STORAGE_PROVIDER)
  --scope <scope>        all, files, deep-research, or presentations (default all)
  --dry-run              Report matching records without copying or updating them
  --delete-source        Delete source objects after verification and database commit
  --force                Overwrite conflicting destination objects
  --user-id <id>         Limit migration to one user
  --only-migrated-from <provider>  Limit to records with matching migration provenance
  --created-after <date> Include records created on/after YYYY-MM-DD
  --created-before <date> Include records created on/before YYYY-MM-DD
  --batch-size <n>       Database fetch batch size (default 200)
  --max-files <n>        Maximum owning records to process (0 means unlimited)
  --retries <n>          Copy and cleanup attempts per object (default 3)

Configuration is primarily done via toggles in the .env file:
  OMLORIX_USE_BUNDLED_DB=true/false
  REDIS_ENABLED=true/false
  OMLORIX_USE_BUNDLED_REDIS=true/false
  OMLORIX_USE_PGBOUNCER=true/false
  OMLORIX_USE_BUNDLED_STORAGE=true/false
`,
		serverManagement.Logs.DefaultLines,
		serverManagement.Logs.MinimumLines,
		serverManagement.Logs.MaximumLines,
		serverManagement.Logs.DefaultLines,
	)
}

func commandDoctor(opts options) error {
	return runDoctor(opts)
}

func commandInit(opts options) error {
	if err := ensureServerHome(opts); err != nil {
		return err
	}
	if opts.attachProject != "" {
		if !serviceNamePattern.MatchString(opts.attachProject) {
			return errors.New("--attach-project must be a valid Docker Compose project name")
		}
		if err := writeEnv(opts.envFile, map[string]string{
			"COMPOSE_PROJECT_NAME":          opts.attachProject,
			"OMLORIX_ALLOW_PROJECT_ADOPTION": "true",
		}); err != nil {
			return err
		}
		fmt.Printf("Explicitly attached this home to legacy Compose project %s. Ownership labels will be applied on the next start.\n", opts.attachProject)
	}
	env, _ := readEnv(opts.envFile)
	fmt.Printf("Initialized Omlorix server files in %s\n", opts.home)
	fmt.Printf("Open URL: %s\n", resolveURL(opts, env))
	return nil
}

func commandStart(opts options) error {
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
	envBeforeStart, _, err := readManagedEnvironment(opts)
	if err != nil {
		return err
	}
	launcherProxyEnabled := envTruthy(envBeforeStart["OMLORIX_LAUNCHER_PROXY_ENABLED"], false)
	externalProxyEnabled := strings.TrimSpace(envBeforeStart["FRONTEND_TRUSTED_UPSTREAMS"]) != ""
	if launcherProxyEnabled || externalProxyEnabled {
		if err := ensureManagedProxyFrontendIsolation(opts, envBeforeStart); err != nil {
			return err
		}
		if err := ensureLauncherProxySecret(opts); err != nil {
			return err
		}
		if launcherProxyEnabled && envTruthy(envBeforeStart["OMLORIX_LAUNCHER_PROXY_AUTOSTART"], true) {
			if err := startManagedProxy(opts); err != nil {
				return err
			}
		}
	}
	if err := runDocker(composeArgs(opts, "pull"), opts.home); err != nil {
		return err
	}
	fmt.Println("Taking the Omlorix stack offline for database migrations ...")
	if err := runDocker(
		composeArgs(opts, offlineMigrationDrainCommand()...),
		opts.home,
	); err != nil {
		return err
	}
	if err := runDocker(
		composeArgs(opts, offlineMigrationResetCommand()...),
		opts.home,
	); err != nil {
		return err
	}
	if err := runDocker(
		composeArgs(opts, offlineMigrationRunCommand()...),
		opts.home,
	); err != nil {
		printMigrationFailureLogs(opts, os.Stderr, runCapture)
		return err
	}
	if err := runDocker(composeArgs(opts, "up", "-d", "--remove-orphans"), opts.home); err != nil {
		printMigrationFailureLogs(opts, os.Stderr, runCapture)
		return err
	}
	if err := finalizeProjectAdoption(opts); err != nil {
		return err
	}
	env, _, err := readManagedEnvironment(opts)
	if err != nil {
		return err
	}
	url := resolveURL(opts, env)
	if !opts.noWait {
		readyURL := strings.TrimRight(url, "/") + "/ready"
		fmt.Printf("Waiting for the complete Omlorix stack at %s ...\n", readyURL)
		if err := waitForServerHealthy(opts, opts.timeout); err != nil {
			return possibleDatabaseDowngradeErrorForCLI(opts, env["OMLORIX_VERSION"], fmt.Errorf("Omlorix containers started but readiness failed: %w", err))
		}
		if err := recordSuccessfulServerVersionForCLI(opts, env["OMLORIX_VERSION"]); err != nil {
			return err
		}
		if envTruthy(env["OMLORIX_LAUNCHER_PROXY_ENABLED"], false) || strings.TrimSpace(env["FRONTEND_TRUSTED_UPSTREAMS"]) != "" {
			fmt.Println("Calibrating and verifying visitor IP forwarding ...")
			if err := repairVisitorIPMutation(opts); err != nil {
				return err
			}
		}
		fmt.Printf("Omlorix is ready at %s\n", url)
	} else {
		fmt.Printf("Omlorix is starting at %s\n", url)
	}
	if opts.openBrowser {
		return openBrowser(url)
	}
	return nil
}

func commandStop(opts options) error {
	if err := ensureServerHome(opts); err != nil {
		return err
	}
	if err := ensureDockerReady(opts); err != nil {
		return err
	}
	if err := runDocker(composeArgs(opts, "down", "--remove-orphans"), opts.home); err != nil {
		return err
	}
	// A full server shutdown owns the managed ingress lifecycle as well. Leaving
	// it behind only serves 502 responses and keeps a public listener open.
	if collectProxyStatus(opts).Running {
		if err := stopManagedProxy(opts); err != nil {
			return fmt.Errorf("Omlorix stopped, but the managed proxy could not be stopped: %w", err)
		}
	}
	return nil
}

func commandRestart(opts options) error {
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
	envBeforeRestart, _, err := readManagedEnvironment(opts)
	if err != nil {
		return err
	}
	launcherProxyEnabled := envTruthy(envBeforeRestart["OMLORIX_LAUNCHER_PROXY_ENABLED"], false)
	externalProxyEnabled := strings.TrimSpace(envBeforeRestart["FRONTEND_TRUSTED_UPSTREAMS"]) != ""
	proxyWasRunning := collectProxyStatus(opts).Running
	resumeManagedProxy := launcherProxyEnabled && (proxyWasRunning || envTruthy(envBeforeRestart["OMLORIX_LAUNCHER_PROXY_AUTOSTART"], true))
	if launcherProxyEnabled || externalProxyEnabled {
		if err := ensureManagedProxyFrontendIsolation(opts, envBeforeRestart); err != nil {
			return err
		}
		if err := ensureLauncherProxySecret(opts); err != nil {
			return err
		}
	}
	// `restart` is the operator's explicit apply step after either import mode.
	// Stop a live proxy even when the restored configuration disables it; when
	// it remains enabled, start it again only after the containers are replaced.
	if proxyWasRunning {
		if err := stopManagedProxy(opts); err != nil {
			return err
		}
	}
	fmt.Println("Taking the Omlorix stack offline for database migrations ...")
	if err := runDocker(composeArgs(opts, offlineMigrationDrainCommand()...), opts.home); err != nil {
		return err
	}
	if err := runDocker(composeArgs(opts, offlineMigrationResetCommand()...), opts.home); err != nil {
		return err
	}
	if err := runDocker(composeArgs(opts, offlineMigrationRunCommand()...), opts.home); err != nil {
		printMigrationFailureLogs(opts, os.Stderr, runCapture)
		return err
	}
	restartArgs := []string{"up", "-d", "--force-recreate", "--remove-orphans"}
	if err := runDocker(composeArgs(opts, restartArgs...), opts.home); err != nil {
		return err
	}
	if err := finalizeProjectAdoption(opts); err != nil {
		return err
	}
	if resumeManagedProxy {
		if err := startManagedProxy(opts); err != nil {
			return err
		}
	}
	if opts.noWait {
		return nil
	}
	env, _, err := readManagedEnvironment(opts)
	if err != nil {
		return err
	}
	if err := waitForServerHealthy(opts, opts.timeout); err != nil {
		return possibleDatabaseDowngradeErrorForCLI(opts, env["OMLORIX_VERSION"], fmt.Errorf("Omlorix containers restarted but readiness failed: %w", err))
	}
	if err := recordSuccessfulServerVersionForCLI(opts, env["OMLORIX_VERSION"]); err != nil {
		return err
	}
	if envTruthy(env["OMLORIX_LAUNCHER_PROXY_ENABLED"], false) || strings.TrimSpace(env["FRONTEND_TRUSTED_UPSTREAMS"]) != "" {
		if err := repairVisitorIPMutation(opts); err != nil {
			return err
		}
	}
	fmt.Printf("Omlorix is ready at %s\n", resolveURL(opts, env))
	return nil
}

func commandUpdate(opts options) error {
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
	env, _ := readEnv(opts.envFile)
	settings, err := readServerSettings(opts)
	if err != nil {
		return err
	}
	previous := strings.TrimPrefix(strings.TrimSpace(firstNonBlank(env["OMLORIX_VERSION"], "stable")), "v")
	previousChannel := settings.UpdateChannel
	channel := normalizeUpdateChannel(firstNonBlank(opts.channel, settings.UpdateChannel))
	nextVersion := strings.TrimPrefix(strings.TrimSpace(opts.version), "v")

	if nextVersion == "" {
		release, err := releaseInfoForChannel(channel)
		if err != nil {
			return fmt.Errorf("could not check latest %s release: %w", channel, err)
		}
		if err := managementCompatibilityError(release); err != nil {
			return err
		}
		nextVersion = release.Version
		fmt.Printf("Latest %s release: %s\n", channel, nextVersion)
	} else {
		// Explicit pins still need the same management compatibility gate. Resolve
		// immutable metadata for the requested tag when it differs from the moving
		// channel release so the wrong manifest can never authorize the update.
		release, err := releaseInfoForChannel(channel)
		if err != nil {
			return fmt.Errorf("could not check release compatibility: %w", err)
		}
		if release.Version != nextVersion {
			release, err = releaseInfoForVersion(channel, nextVersion)
			if err != nil {
				return err
			}
		}
		if err := managementCompatibilityError(release); err != nil {
			return err
		}
	}
	if !opts.skipBackup {
		fmt.Println("Creating a backup before updating. Use --skip-backup only when you have a separate backup.")
		if err := commandBackup(opts); err != nil {
			return fmt.Errorf("backup failed, update aborted: %w", err)
		}
	}
	if nextVersion != "" {
		if err := writeEnv(opts.envFile, map[string]string{"OMLORIX_VERSION": nextVersion}); err != nil {
			return err
		}
		if err := writeUpdateChannel(opts, channel); err != nil {
			_ = writeEnv(opts.envFile, map[string]string{"OMLORIX_VERSION": previous})
			return err
		}
	}

	if err := runDocker(composeArgs(opts, "pull"), opts.home); err != nil {
		return rollbackUpdate(opts, previous, previousChannel, nextVersion, err, false, false)
	}
	fmt.Println("Taking the Omlorix stack offline for database migrations ...")
	if err := runDocker(
		composeArgs(opts, offlineMigrationDrainCommand()...),
		opts.home,
	); err != nil {
		return rollbackUpdate(opts, previous, previousChannel, nextVersion, err, true, false)
	}
	if err := runDocker(
		composeArgs(opts, offlineMigrationResetCommand()...),
		opts.home,
	); err != nil {
		return rollbackUpdate(opts, previous, previousChannel, nextVersion, err, true, false)
	}
	if err := runDocker(composeArgs(opts, offlineMigrationRunCommand()...), opts.home); err != nil {
		return rollbackUpdate(opts, previous, previousChannel, nextVersion, err, true, true)
	}
	if err := runDocker(composeArgs(opts, "up", "-d", "--force-recreate", "--remove-orphans"), opts.home); err != nil {
		return rollbackUpdate(opts, previous, previousChannel, nextVersion, err, true, true)
	}
	if err := finalizeProjectAdoption(opts); err != nil {
		return rollbackUpdate(opts, previous, previousChannel, nextVersion, err, true, true)
	}
	env, _ = readEnv(opts.envFile)
	readyURL := strings.TrimRight(resolveURL(opts, env), "/") + "/ready"
	readyErr := waitForServerHealthy(opts, opts.timeout)
	if readyErr == nil {
		if err := recordSuccessfulServerVersionForCLI(opts, nextVersion); err != nil {
			return rollbackUpdate(
				opts,
				previous,
				previousChannel,
				nextVersion,
				fmt.Errorf("could not record the successful target release: %w", err),
				true,
				true,
			)
		}
		fmt.Printf("Omlorix is ready at %s\n", readyURL)
		return nil
	}

	return rollbackUpdate(
		opts,
		previous,
		previousChannel,
		nextVersion,
		fmt.Errorf("readiness failed at %s: %w", readyURL, readyErr),
		true,
		true,
	)
}

func commandBackup(opts options) error {
	if err := ensureServerHome(opts); err != nil {
		return err
	}
	if err := validateProfileEnv(opts); err != nil {
		return err
	}
	if err := ensureDockerReady(opts); err != nil {
		return err
	}
	action := "create"
	arguments := opts.arguments
	if len(arguments) > 0 {
		action = strings.ToLower(strings.TrimSpace(arguments[0]))
		arguments = arguments[1:]
	}
	if action == "list" {
		if len(arguments) != 0 {
			return invalidArgumentsError(errors.New("backup list does not accept positional arguments"))
		}
		return runBackendCommand(opts, composeArgs(opts, "exec", "-T", "fastapi", "python", "-m", "app.backups.cli", "list"))
	}
	if action == "show" {
		if len(arguments) != 1 || strings.TrimSpace(arguments[0]) == "" {
			return invalidArgumentsError(errors.New("usage: omlorix-server backup show <job-id>"))
		}
		return runBackendCommand(opts, composeArgs(opts, "exec", "-T", "fastapi", "python", "-m", "app.backups.cli", "show", strings.TrimSpace(arguments[0])))
	}
	if action == "download" {
		if len(arguments) != 1 || strings.TrimSpace(arguments[0]) == "" || strings.TrimSpace(opts.output) == "" {
			return invalidArgumentsError(errors.New("usage: omlorix-server backup download <job-id> --output <path>"))
		}
		result, err := downloadBackupArtifact(
			opts,
			strings.TrimSpace(arguments[0]),
			strings.TrimSpace(opts.output),
			streamBackupArtifactFromDocker,
		)
		if err != nil {
			return err
		}
		if opts.jsonOutput {
			return printJSON(result)
		}
		fmt.Printf("Downloaded backup %s to %s (%d bytes)\n", result.JobID, result.Path, result.Bytes)
		return nil
	}
	if action != "create" || len(arguments) != 0 {
		return invalidArgumentsError(fmt.Errorf("unknown backup action %q", action))
	}
	args := composeArgs(opts, "exec", "-T", "fastapi", "python", "-m", "app.backups.cli", "create", "--safe-output")
	if strings.TrimSpace(opts.destination) != "" {
		args = append(args, "--destination", strings.TrimSpace(opts.destination))
	}
	if opts.noEncrypted {
		args = append(args, "--no-encrypted")
	}
	return runBackendCommand(opts, args)
}

func commandRestore(opts options) error {
	return runCoordinatedRestore(opts)
}

func commandLogs(opts options) error {
	if err := ensureServerHome(opts); err != nil {
		return err
	}
	if err := validateProfileEnv(opts); err != nil {
		return err
	}
	if err := ensureDockerReady(opts); err != nil {
		return err
	}
	lines, err := normalizeLogLineCount(opts.lines)
	if err != nil {
		return err
	}
	args := []string{"logs", "--tail", strconv.Itoa(lines), "--no-color"}
	if opts.follow {
		args = append(args, "--follow")
	}
	if strings.TrimSpace(opts.since) != "" {
		args = append(args, "--since", strings.TrimSpace(opts.since))
	}
	if strings.TrimSpace(opts.service) != "" {
		if err := validateServiceName(opts, opts.service); err != nil {
			return err
		}
		args = append(args, opts.service)
	}
	return runDocker(composeArgs(opts, args...), opts.home)
}

func validateProfileEnv(opts options) error {
	if _, err := readServerSettings(opts); err != nil {
		return fmt.Errorf("invalid server settings in %s: %w", serverSettingsPath(opts), err)
	}
	env, _ := readEnv(opts.envFile)
	toggles := readEnvToggles(opts)
	if toggles.usePgbouncer {
		poolMode := strings.ToLower(strings.TrimSpace(env["PGBOUNCER_POOL_MODE"]))
		if poolMode == "" {
			poolMode = "transaction"
		}
		if poolMode != "transaction" && poolMode != "session" {
			return fmt.Errorf("PGBOUNCER_POOL_MODE must be transaction or session in %s", opts.envFile)
		}
	}

	missing := []string{}

	if !toggles.useBundledDB {
		if isBlank(env["DATABASE_URL"]) {
			missing = append(missing, "DATABASE_URL")
		}
	}
	if toggles.redisEnabled && !toggles.useBundledRedis {
		if isBlank(env["REDIS_URL"]) {
			missing = append(missing, "REDIS_URL")
		} else {
			redisURL := strings.TrimSpace(env["REDIS_URL"])
			if strings.Contains(redisURL, "localhost:") || strings.Contains(redisURL, "127.0.0.1:") || redisURL == "redis://redis:6379/0" {
				return fmt.Errorf("external Redis required but REDIS_URL points to localhost/bundled redis in %s", opts.envFile)
			}
		}
	}
	if !toggles.useBundledStorage && isBlank(env["FILE_STORAGE_PROVIDER"]) {
		missing = append(missing, "FILE_STORAGE_PROVIDER")
	}
	if isManagedCloudTopology(toggles) && strings.EqualFold(strings.TrimSpace(env["FILE_STORAGE_PROVIDER"]), "local") {
		return fmt.Errorf("managed cloud requires FILE_STORAGE_PROVIDER to be s3, gcs, azure, or webdav in %s", opts.envFile)
	}
	if len(missing) > 0 {
		return fmt.Errorf("external dependency toggles require %s in %s", strings.Join(missing, ", "), opts.envFile)
	}

	return validateSensitiveEnv(opts, env, toggles)
}

func ensureServerHome(opts options) error {
	if err := os.MkdirAll(opts.home, 0o755); err != nil {
		return err
	}
	sourceRoot := findSourceRoot(opts)
	for _, relative := range serverFiles {
		target := filepath.Join(opts.home, filepath.FromSlash(relative))
		source := ""
		if sourceRoot != "" {
			source = filepath.Join(sourceRoot, filepath.FromSlash(relative))
		}
		if source == "" {
			if embedErr := copyEmbeddedFile(relative, target); embedErr != nil {
				return fmt.Errorf("copy embedded server file failed: source=%q relative=%q target=%q: %w", source, relative, target, embedErr)
			}
			continue
		}
		if _, err := os.Stat(source); err != nil {
			if embedErr := copyEmbeddedFile(relative, target); embedErr != nil {
				return fmt.Errorf("copy embedded server file failed: source=%q relative=%q target=%q: %w", source, relative, target, embedErr)
			}
			continue
		}
		if err := copyFile(source, target); err != nil {
			return err
		}
	}
	freshEnvironment := false
	if _, err := os.Stat(opts.envFile); err != nil {
		freshEnvironment = true
		example := filepath.Join(opts.home, ".env.example")
		if _, exampleErr := os.Stat(example); exampleErr == nil {
			if err := copyFile(example, opts.envFile); err != nil {
				return err
			}
		} else if err := os.WriteFile(opts.envFile, []byte(""), 0o600); err != nil {
			return err
		}
	}
	if err := ensureGeneratedEnv(opts, freshEnvironment); err != nil {
		return err
	}
	return migrateLegacyServerSettings(opts)
}

func ensureGeneratedEnv(opts options, freshEnvironment ...bool) error {
	env, raw := readEnv(opts.envFile)
	updates := map[string]string{}
	fresh := len(freshEnvironment) > 0 && freshEnvironment[0]
	if isBlank(env["COMPOSE_PROJECT_NAME"]) || fresh {
		updates["COMPOSE_PROJECT_NAME"] = composeProjectNameForHome(opts.home)
	}
	if isBlank(env["OMLORIX_INSTALLATION_ID"]) || strings.EqualFold(strings.TrimSpace(env["OMLORIX_INSTALLATION_ID"]), "CHANGE_ME") {
		updates["OMLORIX_INSTALLATION_ID"] = randomHex(32)
	}
	if isBlank(env["FRONTEND_HTTP_HOST_BIND"]) {
		updates["FRONTEND_HTTP_HOST_BIND"] = "127.0.0.1"
	}

	if isBlank(env["OMLORIX_USE_BUNDLED_DB"]) && isBlank(updates["OMLORIX_USE_BUNDLED_DB"]) {
		updates["OMLORIX_USE_BUNDLED_DB"] = "true"
	}
	if isBlank(env["OMLORIX_USE_BUNDLED_REDIS"]) && isBlank(updates["OMLORIX_USE_BUNDLED_REDIS"]) {
		updates["OMLORIX_USE_BUNDLED_REDIS"] = "true"
	}
	if isBlank(env["REDIS_ENABLED"]) {
		updates["REDIS_ENABLED"] = "true"
	}
	if isBlank(env["OMLORIX_USE_PGBOUNCER"]) && isBlank(updates["OMLORIX_USE_PGBOUNCER"]) {
		updates["OMLORIX_USE_PGBOUNCER"] = "false"
	}
	if isBlank(env["OMLORIX_USE_BUNDLED_STORAGE"]) && isBlank(updates["OMLORIX_USE_BUNDLED_STORAGE"]) {
		updates["OMLORIX_USE_BUNDLED_STORAGE"] = "false"
	}

	if isBlank(env["JWT_SECRET_KEY"]) {
		updates["JWT_SECRET_KEY"] = randomSecret(jwtSecretMinBytes)
	}
	if isBlank(env["ENCRYPTION_KEY"]) {
		updates["ENCRYPTION_KEY"] = randomFernetKey()
	}
	if len(strings.TrimSpace(env["PASSWORD_RESET_IDENTIFIER_HASH_SALT"])) < 16 {
		updates["PASSWORD_RESET_IDENTIFIER_HASH_SALT"] = randomHex(32)
	}
	if len(strings.TrimSpace(env["LOG_IP_HASH_SALT"])) < 16 {
		updates["LOG_IP_HASH_SALT"] = randomHex(32)
	}
	if isBlank(env["BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE"]) {
		updates["BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE"] = randomSecret(36)
	}
	if isBlank(env["DATABASE_PASSWORD"]) || env["DATABASE_PASSWORD"] == "CHANGE_ME" {
		updates["DATABASE_PASSWORD"] = randomSecret(48)
	}
	if isBlank(env["REDIS_PASSWORD"]) || env["REDIS_PASSWORD"] == "CHANGE_ME" {
		updates["REDIS_PASSWORD"] = randomURLSecret(48)
	}
	redisPassword := firstNonBlank(updates["REDIS_PASSWORD"], env["REDIS_PASSWORD"])

	// Temporarily merge toggles for generation checks
	simulatedEnv := copyEnvMap(env)
	for k, v := range updates {
		simulatedEnv[k] = v
	}
	// Repair homes created by versions that started the PgBouncer profile
	// without atomically routing application traffic through it.
	for key, value := range topologyInvariantUpdates(simulatedEnv) {
		if simulatedEnv[key] != value {
			updates[key] = value
			simulatedEnv[key] = value
		}
	}
	simulatedToggles := envToggles{
		redisEnabled:      envTruthy(simulatedEnv["REDIS_ENABLED"], true),
		useBundledRedis:   envTruthy(simulatedEnv["REDIS_ENABLED"], true) && envTruthy(simulatedEnv["OMLORIX_USE_BUNDLED_REDIS"], true),
		useBundledStorage: envTruthy(simulatedEnv["OMLORIX_USE_BUNDLED_STORAGE"], false),
	}
	if simulatedToggles.redisEnabled && simulatedToggles.useBundledRedis && !isBlank(redisPassword) {
		expectedRedisURL := defaultLocalRedisURL(env, redisPassword)
		if strings.TrimSpace(env["REDIS_URL"]) != expectedRedisURL {
			updates["REDIS_URL"] = expectedRedisURL
		}
	}
	if shouldResetGrafanaAdminUser(env["GRAFANA_ADMIN_USER"]) {
		updates["GRAFANA_ADMIN_USER"] = defaultGrafanaAdminUser()
	}
	if isBlank(env["GRAFANA_ADMIN_PASSWORD"]) || env["GRAFANA_ADMIN_PASSWORD"] == "CHANGE_ME" {
		updates["GRAFANA_ADMIN_PASSWORD"] = randomSecret(48)
	}
	if isBlank(env["OMLORIX_VERSION"]) {
		updates["OMLORIX_VERSION"] = "stable"
	}
	if isBlank(env["FRONTEND_HTTP_HOST_PORT"]) || env["FRONTEND_HTTP_HOST_PORT"] == "80" {
		updates["FRONTEND_HTTP_HOST_PORT"] = "8080"
	}

	if simulatedToggles.useBundledStorage {
		if !strings.EqualFold(strings.TrimSpace(env["FILE_STORAGE_PROVIDER"]), "s3") {
			updates["FILE_STORAGE_PROVIDER"] = "s3"
		}
		if isBlank(env["MINIO_ROOT_USER"]) || env["MINIO_ROOT_USER"] == "CHANGE_ME" {
			updates["MINIO_ROOT_USER"] = "omlorix-" + randomToken(18)
		}
		if isBlank(env["MINIO_ROOT_PASSWORD"]) || env["MINIO_ROOT_PASSWORD"] == "CHANGE_ME" {
			updates["MINIO_ROOT_PASSWORD"] = randomSecret(48)
		}
	} else if isBlank(env["FILE_STORAGE_PROVIDER"]) {
		updates["FILE_STORAGE_PROVIDER"] = "local"
	}

	if len(updates) == 0 {
		return nil
	}

	// Create final content without optional empty values meant for deletion.
	// DATABASE_URL is different: an explicit empty value is the bundled-mode
	// guard that prevents a stale URL from outranking the derived split fields.
	finalUpdates := map[string]string{}
	for k, v := range updates {
		if v != "" || k == "DATABASE_URL" {
			finalUpdates[k] = v
		}
	}

	newContent := updateEnvContent(raw, finalUpdates)
	if err := validateIndependentSecuritySecrets(parseEnvContent(newContent)); err != nil {
		return err
	}

	if err := atomicWriteFile(opts.envFile, []byte(newContent), 0o600); err != nil {
		return err
	}
	refreshAutomaticEnvBackupAfterWrite(opts.envFile)
	return nil
}

// composeProjectNameForHome gives each independent server home a deterministic
// Compose namespace without disclosing the filesystem path in Docker metadata.
func composeProjectNameForHome(home string) string {
	absolute, err := filepath.Abs(home)
	if err != nil {
		absolute = filepath.Clean(home)
	}
	normalized := filepath.ToSlash(filepath.Clean(absolute))
	if runtime.GOOS == "windows" {
		normalized = strings.ToLower(normalized)
	}
	digest := sha256.Sum256([]byte(normalized))
	return fmt.Sprintf("omlorix-%x", digest[:6])
}

func copyEnvMap(m map[string]string) map[string]string {
	n := make(map[string]string)
	for k, v := range m {
		n[k] = v
	}
	return n
}

func validateSensitiveEnv(opts options, env map[string]string, toggles envToggles) error {
	if err := validateIndependentSecuritySecrets(env); err != nil {
		return err
	}
	if len(strings.TrimSpace(env["JWT_SECRET_KEY"])) < jwtSecretMinBytes {
		return fmt.Errorf("JWT_SECRET_KEY must be set and at least 64 bytes long in %s", opts.envFile)
	}
	if strings.EqualFold(strings.TrimSpace(env["MODE"]), "dev") {
		return nil
	}
	errors := []string{}
	if isBlank(env["ENCRYPTION_KEY"]) {
		errors = append(errors, "ENCRYPTION_KEY must be set")
	}
	if len(strings.TrimSpace(env["PASSWORD_RESET_IDENTIFIER_HASH_SALT"])) < 16 {
		errors = append(errors, "PASSWORD_RESET_IDENTIFIER_HASH_SALT must contain at least 16 characters")
	}
	if len(strings.TrimSpace(env["LOG_IP_HASH_SALT"])) < 16 {
		errors = append(errors, "LOG_IP_HASH_SALT must contain at least 16 characters")
	}

	if toggles.useBundledDB {
		if isBlank(env["DATABASE_PASSWORD"]) || env["DATABASE_PASSWORD"] == "CHANGE_ME" {
			errors = append(errors, "DATABASE_PASSWORD must be set to a non-placeholder value")
		}
	}
	if toggles.redisEnabled && toggles.useBundledRedis {
		if isBlank(env["REDIS_PASSWORD"]) || env["REDIS_PASSWORD"] == "CHANGE_ME" {
			errors = append(errors, "REDIS_PASSWORD must be set to a non-placeholder value")
		}
		if isBlank(env["REDIS_URL"]) || strings.Contains(env["REDIS_URL"], "CHANGE_ME") {
			errors = append(errors, "REDIS_URL must be set and must not contain CHANGE_ME")
		}
	} else if toggles.redisEnabled {
		redisURL := strings.TrimSpace(env["REDIS_URL"])
		if isBlank(redisURL) || strings.Contains(redisURL, "CHANGE_ME") {
			errors = append(errors, "REDIS_URL must be set and must not contain CHANGE_ME")
		}
	}

	if len(errors) > 0 {
		return fmt.Errorf("production env preflight failed for %s: %s", opts.envFile, strings.Join(errors, "; "))
	}
	return nil
}

func defaultLocalRedisURL(env map[string]string, password string) string {
	// Backend containers reach bundled Redis over the Compose service network.
	// Encode every byte except RFC 3986 unreserved characters; query escaping is
	// unsuitable because its `+` space convention does not apply to URI userinfo.
	return fmt.Sprintf("redis://:%s@redis:6379/0", percentEncodeURLComponent(password))
}

func percentEncodeURLComponent(value string) string {
	const hex = "0123456789ABCDEF"
	var encoded strings.Builder
	for _, currentByte := range []byte(value) {
		if (currentByte >= 'a' && currentByte <= 'z') ||
			(currentByte >= 'A' && currentByte <= 'Z') ||
			(currentByte >= '0' && currentByte <= '9') ||
			currentByte == '-' || currentByte == '.' || currentByte == '_' || currentByte == '~' {
			encoded.WriteByte(currentByte)
			continue
		}
		encoded.WriteByte('%')
		encoded.WriteByte(hex[currentByte>>4])
		encoded.WriteByte(hex[currentByte&0x0f])
	}
	return encoded.String()
}

func shouldResetGrafanaAdminUser(value string) bool {
	trimmed := strings.TrimSpace(value)
	return trimmed == "" || trimmed == "CHANGE_ME" || trimmed == "admin"
}

func defaultGrafanaAdminUser() string {
	return "omlorix-admin"
}

// firstNonBlank returns the first non-empty trimmed value from the provided candidates.
func firstNonBlank(values ...string) string {
	for _, value := range values {
		if trimmed := strings.TrimSpace(value); trimmed != "" {
			return trimmed
		}
	}
	return ""
}

func copyEmbeddedFile(relative string, target string) error {
	content, err := embeddedAssets.ReadFile(filepath.ToSlash(filepath.Join("assets", relative)))
	if err != nil {
		return err
	}
	mode := os.FileMode(0o644)
	if strings.HasSuffix(relative, ".sh") {
		mode = 0o755
	}
	return atomicWriteFile(target, content, mode)
}

func samePath(left string, right string) bool {
	leftAbsolute, leftErr := filepath.Abs(left)
	rightAbsolute, rightErr := filepath.Abs(right)
	if leftErr == nil && rightErr == nil && filepath.Clean(leftAbsolute) == filepath.Clean(rightAbsolute) {
		return true
	}
	leftInfo, leftStatErr := os.Stat(left)
	rightInfo, rightStatErr := os.Stat(right)
	return leftStatErr == nil && rightStatErr == nil && os.SameFile(leftInfo, rightInfo)
}

func findSourceRoot(opts options) string {
	candidates := []string{}
	if opts.sourceRoot != "" {
		candidates = append(candidates, opts.sourceRoot)
	}
	if exe, err := os.Executable(); err == nil {
		exeDir := filepath.Dir(exe)
		candidates = append(candidates, exeDir, filepath.Dir(exeDir))
	}
	if cwd, err := os.Getwd(); err == nil {
		candidates = append(candidates, cwd)
	}
	for _, candidate := range candidates {
		if candidate == "" || samePath(candidate, opts.home) {
			continue
		}
		if _, err := os.Stat(filepath.Join(candidate, ".env.example")); err == nil {
			return candidate
		}
	}
	if opts.sourceRoot != "" && !samePath(opts.sourceRoot, opts.home) {
		return opts.sourceRoot
	}
	// A bundled CLI is normally launched with the server home as cwd. Returning
	// that directory would make every deployment source equal its destination;
	// an empty result deliberately selects the embedded, versioned assets.
	return ""
}

func missingRequiredFiles(opts options) []string {
	missing := []string{}
	for _, file := range requiredServerFiles(opts) {
		if _, err := os.Stat(filepath.Join(opts.home, file)); err != nil {
			missing = append(missing, file)
		}
	}
	sort.Strings(missing)
	return missing
}

func requiredServerFiles(opts options) []string {
	files := composeFileList(opts)
	if readEnvToggles(opts).observability {
		files = append(files, grafanaProvisioningFiles...)
	}
	return files
}

func composeFileList(opts options) []string {
	return composeFileListForPlatform(opts, runtime.GOOS)
}

func composeFileListForPlatform(opts options, goos string) []string {
	toggles := readEnvToggles(opts)
	files := []string{}

	useManagedCloud := isManagedCloudTopology(toggles)
	if useManagedCloud {
		files = append(files, "docker-compose.managed-cloud.yml")
	} else {
		files = append(files, "docker-compose.server.yml")
	}

	files = append(files, "docker-compose.frontend-port.yml")

	if toggles.devMode && !useManagedCloud {
		files = append(files, "docker-compose.dev-ports.yml")
	}
	if toggles.observability {
		files = append(files, "docker-compose.observability.yml")
		if linuxHostMetricsSupported(goos) {
			files = append(files, "docker-compose.observability-linux.yml")
		}
	}
	if _, err := os.Stat(filepath.Join(opts.home, "docker-compose.launcher-services.yml")); err == nil {
		files = append(files, "docker-compose.launcher-services.yml")
	}

	return files
}

// isManagedCloudTopology identifies the all-external deployment shape shared
// by profile validation and Compose-file selection.
func isManagedCloudTopology(toggles envToggles) bool {
	return !toggles.useBundledDB &&
		(!toggles.redisEnabled || !toggles.useBundledRedis) &&
		!toggles.usePgbouncer &&
		!toggles.useBundledStorage
}

func buildComposeProfiles(toggles envToggles) []string {
	var profiles []string
	if toggles.useBundledDB {
		profiles = append(profiles, "bundled-db")
	}
	if toggles.redisEnabled {
		profiles = append(profiles, "redis-enabled")
	}
	if toggles.redisEnabled && toggles.useBundledRedis {
		profiles = append(profiles, "bundled-redis")
	}
	if toggles.usePgbouncer {
		profiles = append(profiles, "pgbouncer")
	}
	if toggles.useBundledStorage {
		profiles = append(profiles, "bundled-storage")
	}
	return profiles
}

func composeArgs(opts options, args ...string) []string {
	result := []string{"compose", "--env-file", opts.envFile}
	for _, file := range composeFileList(opts) {
		result = append(result, "-f", filepath.Join(opts.home, file))
	}

	for _, profile := range buildComposeProfiles(readEnvToggles(opts)) {
		result = append(result, "--profile", profile)
	}

	return append(result, args...)
}

func runDocker(args []string, cwd string) error {
	return runCommand(dockerExecutable(), args, cwd)
}

// printMigrationFailureLogs gives terminal starts the same actionable failure
// detail as the Launcher. Compose's `up` output reports only that the one-shot
// migration service exited; its real database or Alembic error remains in the
// stopped container log until it is queried separately.
func printMigrationFailureLogs(
	opts options,
	writer io.Writer,
	capture func(string, []string, string) (string, error),
) {
	if writer == nil || capture == nil {
		return
	}
	fmt.Fprintln(writer, "\nStart failed. Recent migration logs:")
	output, err := capture(
		dockerExecutable(),
		composeArgs(opts, "logs", "--tail", "120", "--no-color", "migrate"),
		opts.home,
	)
	if output != "" {
		fmt.Fprint(writer, output)
		if !strings.HasSuffix(output, "\n") {
			fmt.Fprintln(writer)
		}
	}
	if err != nil {
		fmt.Fprintf(writer, "Could not read migration logs: %v\n", err)
	}
}

func runCommand(name string, args []string, cwd string) error {
	cmd := exec.Command(name, args...)
	cmd.Dir = existingDir(cwd)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	return cmd.Run()
}

func runCapture(name string, args []string, cwd string) (string, error) {
	cmd := exec.Command(name, args...)
	cmd.Dir = existingDir(cwd)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return string(output), err
	}
	return string(output), nil
}

func runCaptureStreams(name string, args []string, cwd string) (string, string, error) {
	cmd := exec.Command(name, args...)
	cmd.Dir = existingDir(cwd)
	var stdout strings.Builder
	var stderr strings.Builder
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	cmd.Stdin = os.Stdin
	err := cmd.Run()
	return stdout.String(), stderr.String(), err
}

func readEnv(path string) (map[string]string, string) {
	rawBytes, err := os.ReadFile(path)
	if err != nil {
		return map[string]string{}, ""
	}
	raw := string(rawBytes)
	return parseEnvContent(raw), raw
}

// parseEnvContent applies the same conservative dotenv parsing to in-memory
// previews that readEnv applies to the live file.
func parseEnvContent(raw string) map[string]string {
	values := map[string]string{}
	for _, line := range strings.Split(raw, "\n") {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		key, value, ok := strings.Cut(trimmed, "=")
		if !ok {
			continue
		}
		values[strings.TrimSpace(strings.TrimPrefix(key, "export "))] = unquoteEnv(strings.TrimSpace(stripInlineComment(value)))
	}
	return values
}

// topologyInvariantUpdates returns derived settings that both management
// surfaces must commit in the same transaction as an operator's edit.
func topologyInvariantUpdates(env map[string]string) map[string]string {
	updates := map[string]string{}
	useBundledDB := envTruthy(env["OMLORIX_USE_BUNDLED_DB"], true)
	usePgbouncer := useBundledDB && envTruthy(env["OMLORIX_USE_PGBOUNCER"], false)
	if !useBundledDB {
		updates["OMLORIX_USE_PGBOUNCER"] = "false"
	} else {
		// DATABASE_URL takes precedence over the split connection fields in the
		// backend, so a stale external URL must not bypass bundled routing.
		updates["DATABASE_URL"] = ""
		if usePgbouncer {
			updates["DATABASE_HOST_OVERRIDE"] = "pgbouncer"
		} else {
			updates["DATABASE_HOST_OVERRIDE"] = "postgres"
		}
		updates["DATABASE_PORT_OVERRIDE"] = "5432"
		updates["DATABASE_MIGRATION_HOST_OVERRIDE"] = "postgres"
		updates["DATABASE_MIGRATION_PORT_OVERRIDE"] = "5432"
	}
	redisEnabled := envTruthy(env["REDIS_ENABLED"], true)
	useBundledRedis := redisEnabled && envTruthy(env["OMLORIX_USE_BUNDLED_REDIS"], true)
	if !redisEnabled {
		updates["OMLORIX_USE_BUNDLED_REDIS"] = "false"
	}
	if envTruthy(env["OMLORIX_USE_BUNDLED_STORAGE"], false) {
		updates["FILE_STORAGE_PROVIDER"] = "s3"
	}
	if useBundledRedis && strings.TrimSpace(env["REDIS_PASSWORD"]) != "" {
		updates["REDIS_URL"] = defaultLocalRedisURL(env, env["REDIS_PASSWORD"])
	}
	return updates
}

func updateEnvContent(raw string, updates map[string]string) string {
	seen := map[string]bool{}
	lines := []string{}
	if strings.TrimSpace(raw) != "" {
		lines = strings.Split(strings.TrimRight(raw, "\n"), "\n")
	}
	for index, line := range lines {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") || !strings.Contains(trimmed, "=") {
			continue
		}
		keyPart, _, _ := strings.Cut(trimmed, "=")
		key := strings.TrimSpace(strings.TrimPrefix(keyPart, "export "))
		if value, ok := updates[key]; ok {
			seen[key] = true
			prefix := line[:strings.Index(line, keyPart)]
			lines[index] = fmt.Sprintf("%s%s=%s", prefix, keyPart, quoteEnv(value))
		}
	}
	newKeys := make([]string, 0, len(updates))
	for key := range updates {
		if !seen[key] {
			newKeys = append(newKeys, key)
		}
	}
	sort.Strings(newKeys)
	for _, key := range newKeys {
		lines = append(lines, fmt.Sprintf("%s=%s", key, quoteEnv(updates[key])))
	}
	return strings.Join(lines, "\n") + "\n"
}

func existingDir(path string) string {
	if path != "" {
		if stat, err := os.Stat(path); err == nil && stat.IsDir() {
			return path
		}
	}
	return "."
}

func writeEnv(path string, updates map[string]string) error {
	env, raw := readEnv(path)
	for key, value := range updates {
		if isManagedProxySettingsEnvKey(key) {
			delete(updates, key)
			continue
		}
		env[key] = value
	}
	for key, value := range topologyInvariantUpdates(env) {
		updates[key] = value
	}
	if err := validateIndependentSecuritySecrets(env); err != nil {
		return err
	}
	if err := atomicWriteFile(path, []byte(updateEnvContent(raw, updates)), 0o600); err != nil {
		return err
	}
	refreshAutomaticEnvBackupAfterWrite(path)
	return nil
}

func stripInlineComment(value string) string {
	quote := rune(0)
	escaped := false
	previous := rune(0)
	for index, char := range value {
		if escaped {
			escaped = false
			previous = char
			continue
		}
		if char == '\\' && quote == '"' {
			escaped = true
			previous = char
			continue
		}
		if char == '"' || char == '\'' {
			if quote == 0 {
				quote = char
			} else if quote == char {
				quote = 0
			}
			previous = char
			continue
		}
		// Compose begins an unquoted inline comment only when whitespace
		// separates `#` from the value. Literal hashes remain credential bytes.
		if char == '#' && quote == 0 && previous != 0 && (previous == ' ' || previous == '\t') {
			return strings.TrimSpace(value[:index])
		}
		previous = char
	}
	return value
}

func unquoteEnv(value string) string {
	value = strings.TrimSpace(value)
	if len(value) >= 2 {
		first := value[0]
		last := value[len(value)-1]
		if first == '"' && last == '"' {
			if decoded, err := strconv.Unquote(value); err == nil {
				return decoded
			}
			return value[1 : len(value)-1]
		}
		if first == '\'' && last == '\'' {
			return value[1 : len(value)-1]
		}
	}
	return value
}

func quoteEnv(value string) string {
	if value == "" {
		return "\"\""
	}
	if strings.ContainsAny(value, " \t#\"'") {
		return strconv.Quote(value)
	}
	return value
}

func copyFile(source string, target string) error {
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return err
	}
	input, err := os.Open(source)
	if err != nil {
		return err
	}
	defer input.Close()
	output, err := os.CreateTemp(filepath.Dir(target), ".omlorix-asset-*")
	if err != nil {
		return err
	}
	temporaryPath := output.Name()
	defer os.Remove(temporaryPath)
	mode := os.FileMode(0o644)
	if strings.HasSuffix(source, ".sh") {
		mode = 0o755
	}
	if err := output.Chmod(mode); err != nil {
		output.Close()
		return err
	}
	if _, err = io.Copy(output, input); err != nil {
		output.Close()
		return err
	}
	if err := output.Sync(); err != nil {
		output.Close()
		return err
	}
	if err := output.Close(); err != nil {
		return err
	}
	return os.Rename(temporaryPath, target)
}

func defaultServerHome() string {
	switch runtime.GOOS {
	case "windows":
		if local := os.Getenv("LOCALAPPDATA"); local != "" {
			return filepath.Join(local, "Omlorix Server")
		}
	case "darwin":
		if home := userHome(); home != "" {
			return filepath.Join(home, "Library", "Application Support", "Omlorix Server")
		}
	default:
		if xdg := os.Getenv("XDG_DATA_HOME"); xdg != "" {
			return filepath.Join(xdg, "omlorix-server")
		}
		if home := userHome(); home != "" {
			return filepath.Join(home, ".local", "share", "omlorix-server")
		}
	}
	return "omlorix-server"
}

func userHome() string {
	home, _ := os.UserHomeDir()
	return home
}

func resolveURL(opts options, env map[string]string) string {
	// The standalone launcher always probes the local HTTP port it exposes.
	return "http://localhost:" + firstNonBlank(env["FRONTEND_HTTP_HOST_PORT"], "8080")
}

func probeURL(url string) (int, error) {
	client := http.Client{Timeout: 3500 * time.Millisecond}
	response, err := client.Get(url)
	if err != nil {
		return 0, err
	}
	defer response.Body.Close()
	return response.StatusCode, nil
}

func latestChannelVersion(channel string) (string, error) {
	return latestVersionFromChannelFeed(channelFeedURL(channel))
}

func channelFeedURL(channel string) string {
	channel = normalizeUpdateChannel(channel)
	return map[string]string{
		"stable": officialStableFeedURL,
		"beta":   officialBetaFeedURL,
	}[channel]
}

func latestVersionFromChannelFeed(feedURL string) (string, error) {
	client := http.Client{Timeout: 10 * time.Second}
	request, err := http.NewRequest(http.MethodGet, feedURL, nil)
	if err != nil {
		return "", err
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("User-Agent", "omlorix-server-launcher")
	response, err := client.Do(request)
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return "", fmt.Errorf("%s returned HTTP %d", feedURL, response.StatusCode)
	}
	var payload channelFeedResponse
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		return "", err
	}
	version := strings.TrimPrefix(firstNonBlank(payload.Version, payload.Tag), "v")
	if version == "" {
		return "", errors.New("channel feed did not include a version")
	}
	return version, nil
}

func openBrowser(url string) error {
	switch runtime.GOOS {
	case "darwin":
		return runCommand("open", []string{url}, ".")
	case "windows":
		return runCommand("rundll32", []string{"url.dll,FileProtocolHandler", url}, ".")
	default:
		return runCommand("xdg-open", []string{url}, ".")
	}
}

func randomSecret(bytes int) string {
	data := make([]byte, bytes)
	if _, err := rand.Read(data); err != nil {
		panic(err)
	}
	return base64.StdEncoding.EncodeToString(data)
}

func randomURLSecret(bytes int) string {
	data := make([]byte, bytes)
	if _, err := rand.Read(data); err != nil {
		panic(err)
	}
	return base64.RawURLEncoding.EncodeToString(data)
}

func randomFernetKey() string {
	data := make([]byte, 32)
	if _, err := rand.Read(data); err != nil {
		panic(err)
	}
	return strings.TrimRight(base64.URLEncoding.EncodeToString(data), "\n")
}

func randomToken(bytes int) string {
	data := make([]byte, bytes)
	if _, err := rand.Read(data); err != nil {
		panic(err)
	}
	return strings.TrimRight(base64.RawURLEncoding.EncodeToString(data), "=")
}

func firstLine(value string) string {
	line, _, _ := strings.Cut(value, "\n")
	return line
}

func isBlank(value string) bool {
	return strings.TrimSpace(value) == "" || strings.TrimSpace(value) == "\"\""
}

func fatal(err error) {
	fmt.Fprintf(os.Stderr, "Error: %s\n", err)
	os.Exit(1)
}

func fatalForOptions(opts options, err error) {
	if !opts.jsonOutput {
		fatal(err)
	}
	_ = writeCLIErrorJSON(os.Stdout, err)
	os.Exit(1)
}
