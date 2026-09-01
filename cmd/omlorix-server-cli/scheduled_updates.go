package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

type scheduledUpdateSettings struct {
	Enabled                 bool   `json:"enabled"`
	Channel                 string `json:"channel"`
	Schedule                string `json:"schedule"`
	Weekdays                []int  `json:"weekdays"`
	Time                    string `json:"time"`
	BackupBeforeUpdate      bool   `json:"backupBeforeUpdate"`
	BackupDestinationID     string `json:"backupDestinationId"`
	BackupEncryptionEnabled bool   `json:"backupEncryptionEnabled"`
	OnlyWhenHealthy         bool   `json:"onlyWhenHealthy"`
}

type scheduledUpdateStatus struct {
	State          string `json:"state"`
	NextRunAt      string `json:"nextRunAt"`
	LastAttemptAt  string `json:"lastAttemptAt"`
	LastSuccessAt  string `json:"lastSuccessAt"`
	LastFailureAt  string `json:"lastFailureAt"`
	LastCheckedAt  string `json:"lastCheckedAt"`
	LastMessage    string `json:"lastMessage"`
	CurrentVersion string `json:"currentVersion"`
	LatestVersion  string `json:"latestVersion"`
	LastWindowKey  string `json:"lastAttemptWindowKey"`
}

type scheduledUpdateStore struct {
	Settings scheduledUpdateSettings `json:"settings"`
	Status   scheduledUpdateStatus   `json:"status"`
}

func defaultScheduledUpdateStore() scheduledUpdateStore {
	return scheduledUpdateStore{Settings: scheduledUpdateSettings{
		Channel: "stable", Schedule: "daily", Weekdays: []int{0, 1, 2, 3, 4, 5, 6},
		Time: "03:00", BackupBeforeUpdate: true, BackupEncryptionEnabled: true, OnlyWhenHealthy: true,
	}, Status: scheduledUpdateStatus{State: "idle", LastMessage: "Automatic updates are disabled."}}
}

func scheduledUpdatePath(opts options) string {
	return filepath.Join(opts.home, "scheduled-updates.json")
}

func readScheduledUpdates(opts options) (scheduledUpdateStore, error) {
	store := defaultScheduledUpdateStore()
	raw, err := os.ReadFile(scheduledUpdatePath(opts))
	if errors.Is(err, os.ErrNotExist) {
		return store, nil
	}
	if err != nil {
		return store, err
	}
	if err := json.Unmarshal(raw, &store); err != nil {
		return store, errors.New("scheduled update settings are invalid")
	}
	store.Settings.Channel = normalizeUpdateChannel(store.Settings.Channel)
	if store.Settings.Time == "" {
		store.Settings.Time = "03:00"
	}
	return store, nil
}

func writeScheduledUpdates(opts options, store scheduledUpdateStore) error {
	store.Status.NextRunAt = ""
	if next := nextScheduledUpdate(store.Settings, time.Now()); next != nil {
		store.Status.NextRunAt = next.Format(time.RFC3339)
	}
	raw, err := json.MarshalIndent(store, "", "  ")
	if err != nil {
		return err
	}
	return atomicWriteFile(scheduledUpdatePath(opts), append(raw, '\n'), 0o600)
}

func commandAutoUpdate(opts options) error {
	if len(opts.arguments) != 1 {
		return errors.New("usage: omlorix-server auto-update <status|enable|disable|run|daemon>")
	}
	action := strings.ToLower(opts.arguments[0])
	store, err := readScheduledUpdates(opts)
	if err != nil {
		return err
	}
	switch action {
	case "status":
		if opts.jsonOutput {
			return printJSON(store)
		}
		fmt.Printf(
			"Automatic updates: %s\nSchedule: %s at %s\nPre-update backup: %s; destination: %s; archive encryption: %s\nNext run: %s\nLast result: %s\n",
			boolChoice(store.Settings.Enabled, "enabled", "disabled"),
			store.Settings.Schedule,
			store.Settings.Time,
			boolChoice(store.Settings.BackupBeforeUpdate, "enabled", "disabled"),
			firstNonBlank(store.Settings.BackupDestinationID, "local storage (server disk)"),
			boolChoice(store.Settings.BackupEncryptionEnabled, "enabled", "disabled"),
			firstNonBlank(store.Status.NextRunAt, "not scheduled"),
			store.Status.LastMessage,
		)
		return nil
	case "enable":
		settings, err := scheduledSettingsFromOptions(opts, store.Settings)
		if err != nil {
			return err
		}
		settings.Enabled = true
		store.Settings = settings
		store.Status.State = "scheduled"
		store.Status.LastMessage = "Automatic updates are scheduled."
		if err := writeScheduledUpdates(opts, store); err != nil {
			return err
		}
		fmt.Printf("Automatic updates enabled: %s at %s. Keep `omlorix-server auto-update daemon` running with your service manager.\n", settings.Schedule, settings.Time)
		return nil
	case "disable":
		store.Settings.Enabled = false
		store.Status.State = "idle"
		store.Status.LastMessage = "Automatic updates are disabled."
		if err := writeScheduledUpdates(opts, store); err != nil {
			return err
		}
		fmt.Println("Automatic updates disabled.")
		return nil
	case "run":
		return runScheduledUpdate(opts, &store, true)
	case "daemon":
		return runScheduledUpdateDaemon(opts)
	default:
		return fmt.Errorf("unknown auto-update action %q", action)
	}
}

func scheduledSettingsFromOptions(opts options, current scheduledUpdateSettings) (scheduledUpdateSettings, error) {
	previousSchedule := current.Schedule
	if opts.scheduleSet {
		current.Schedule = opts.schedule
	}
	if !map[string]bool{"daily": true, "weekends": true, "custom": true}[current.Schedule] {
		return current, errors.New("--schedule must be daily, weekends, or custom")
	}
	if opts.timeOfDay != "" {
		current.Time = opts.timeOfDay
	}
	if _, err := time.Parse("15:04", current.Time); err != nil {
		return current, errors.New("--time must use HH:MM")
	}
	if opts.channel != "" {
		current.Channel = normalizeUpdateChannel(opts.channel)
	}
	if opts.backupBeforeUpdateSet {
		current.BackupBeforeUpdate = !opts.skipBackup
	}
	if opts.destinationSet {
		current.BackupDestinationID = strings.TrimSpace(opts.destination)
		if len(current.BackupDestinationID) > 255 {
			return current, errors.New("--destination must be 255 characters or fewer")
		}
	}
	if opts.backupEncryptionSet {
		current.BackupEncryptionEnabled = !opts.noEncrypted
	}
	if opts.onlyWhenHealthySet {
		current.OnlyWhenHealthy = !opts.allowUnhealthy
	}
	if opts.weekdaysSet && current.Schedule != "custom" {
		return current, errors.New("--weekdays can only be used with --schedule custom")
	}
	// A transition to custom must state its intended days in the same command.
	// Reusing the previous daily/weekend expansion makes an apparently custom
	// schedule silently run on days the operator never selected.
	if opts.scheduleSet && current.Schedule == "custom" && previousSchedule != "custom" && !opts.weekdaysSet {
		return current, errors.New("switching to --schedule custom requires --weekdays in the same command")
	}
	if current.Schedule == "daily" {
		current.Weekdays = []int{0, 1, 2, 3, 4, 5, 6}
	}
	if current.Schedule == "weekends" {
		current.Weekdays = []int{0, 6}
	}
	if opts.weekdaysSet {
		if strings.TrimSpace(opts.weekdays) == "" {
			return current, errors.New("custom schedules require --weekdays")
		}
		seen := map[int]bool{}
		current.Weekdays = nil
		for _, raw := range strings.Split(opts.weekdays, ",") {
			day, err := strconv.Atoi(strings.TrimSpace(raw))
			if err != nil || day < 0 || day > 6 {
				return current, errors.New("--weekdays must contain comma-separated values from 0 to 6")
			}
			if !seen[day] {
				current.Weekdays = append(current.Weekdays, day)
				seen[day] = true
			}
		}
		sort.Ints(current.Weekdays)
	}
	if current.Schedule == "custom" && len(current.Weekdays) == 0 {
		return current, errors.New("custom schedules require --weekdays")
	}
	return current, nil
}

func nextScheduledUpdate(settings scheduledUpdateSettings, after time.Time) *time.Time {
	if !settings.Enabled {
		return nil
	}
	clock, err := time.Parse("15:04", settings.Time)
	if err != nil {
		return nil
	}
	days := map[int]bool{}
	for _, day := range settings.Weekdays {
		days[day] = true
	}
	for offset := 0; offset < 14; offset++ {
		candidate := time.Date(after.Year(), after.Month(), after.Day()+offset, clock.Hour(), clock.Minute(), 0, 0, after.Location())
		if days[int(candidate.Weekday())] && candidate.After(after) {
			return &candidate
		}
	}
	return nil
}

func runScheduledUpdate(opts options, store *scheduledUpdateStore, manual bool) error {
	if !manual && !store.Settings.Enabled {
		return nil
	}
	releaseLock, err := acquireOperationLock(options{home: opts.home, command: "auto-update"})
	if err != nil {
		store.Status.State = "skipped"
		store.Status.LastMessage = err.Error()
		_ = writeScheduledUpdates(opts, *store)
		if manual {
			return err
		}
		return nil
	}
	defer releaseLock()
	now := time.Now()
	store.Status.State = "running"
	store.Status.LastAttemptAt = now.Format(time.RFC3339)
	store.Status.LastCheckedAt = now.Format(time.RFC3339)
	if store.Settings.OnlyWhenHealthy {
		status := collectServerStatus(opts)
		expectedServicesReady := status.Stack.Healthy
		if status.Stack.ExpectedKnown {
			expectedServicesReady = status.Stack.Total > 0 &&
				status.Stack.Running == status.Stack.Total &&
				status.Stack.Missing == 0
		}
		if !status.Docker.Installed || !status.Docker.Running || !status.Docker.Compose ||
			!status.Stack.Healthy || !expectedServicesReady {
			store.Status.State = "skipped"
			store.Status.LastMessage = "Skipped because Omlorix or Docker is not healthy."
			return finishScheduledUpdateSkip(opts, store, manual)
		}
	}
	env, _ := readEnv(opts.envFile)
	release, err := releaseInfoForChannel(store.Settings.Channel)
	if err == nil {
		err = managementCompatibilityError(release)
	}
	if err != nil {
		store.Status.State = "error"
		store.Status.LastFailureAt = now.Format(time.RFC3339)
		store.Status.LastMessage = err.Error()
		_ = writeScheduledUpdates(opts, *store)
		return err
	}
	current := firstNonBlank(env["OMLORIX_VERSION"], store.Settings.Channel)
	store.Status.CurrentVersion, store.Status.LatestVersion = current, release.Version
	if current != "stable" && current != "beta" && compareVersions(release.Version, current) <= 0 {
		store.Status.State = "skipped"
		store.Status.LastMessage = "No Omlorix update is available."
		return finishScheduledUpdateSkip(opts, store, manual)
	}
	updateOpts := scheduledUpdateOptions(opts, store.Settings)
	if err := commandUpdate(updateOpts); err != nil {
		store.Status.State = "error"
		store.Status.LastFailureAt = time.Now().Format(time.RFC3339)
		store.Status.LastMessage = err.Error()
		_ = writeScheduledUpdates(opts, *store)
		return err
	}
	store.Status.State = "success"
	store.Status.LastSuccessAt = time.Now().Format(time.RFC3339)
	store.Status.LastMessage = fmt.Sprintf("Updated Omlorix to %s.", release.Version)
	return writeScheduledUpdates(opts, *store)
}

// scheduledUpdateOptions carries the reviewed policy from the shared store to
// the ordinary update/backup path without applying a second set of defaults.
func scheduledUpdateOptions(opts options, settings scheduledUpdateSettings) options {
	opts.command = "update"
	opts.channel = settings.Channel
	opts.skipBackup = !settings.BackupBeforeUpdate
	opts.destination = settings.BackupDestinationID
	opts.noEncrypted = !settings.BackupEncryptionEnabled
	return opts
}

// finishScheduledUpdateSkip makes a manual run self-explanatory while retaining
// the same persisted status consumed by the daemon and Launcher.
func finishScheduledUpdateSkip(opts options, store *scheduledUpdateStore, manual bool) error {
	if err := writeScheduledUpdates(opts, *store); err != nil {
		return err
	}
	if manual {
		if opts.jsonOutput {
			return printJSON(store)
		}
		fmt.Println(store.Status.LastMessage)
	}
	return nil
}

func runScheduledUpdateDaemon(opts options) error {
	fmt.Println("Omlorix automatic update daemon is running. Stop it with Ctrl+C or your service manager.")
	for {
		store, err := readScheduledUpdates(opts)
		if err != nil {
			return err
		}
		now := time.Now()
		if window, due := scheduledUpdateWindowDue(store.Settings, store.Status, now); due {
			store.Status.LastWindowKey = window
			_ = runScheduledUpdate(opts, &store, false)
		}
		time.Sleep(30 * time.Second)
	}
}

// scheduledUpdateWindowDue tolerates normal daemon startup and polling jitter
// without replaying an already attempted maintenance window.
func scheduledUpdateWindowDue(settings scheduledUpdateSettings, status scheduledUpdateStatus, now time.Time) (string, bool) {
	if !settings.Enabled {
		return "", false
	}
	clock, err := time.Parse("15:04", settings.Time)
	if err != nil {
		return "", false
	}
	allowedDay := false
	for _, weekday := range settings.Weekdays {
		if weekday == int(now.Weekday()) {
			allowedDay = true
			break
		}
	}
	if !allowedDay {
		return "", false
	}
	candidate := time.Date(now.Year(), now.Month(), now.Day(), clock.Hour(), clock.Minute(), 0, 0, now.Location())
	window := candidate.Format("2006-01-02T15:04")
	due := !now.Before(candidate) && now.Before(candidate.Add(2*time.Minute)) && status.LastWindowKey != window
	return window, due
}
