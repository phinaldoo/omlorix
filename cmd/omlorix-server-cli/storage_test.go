package main

import (
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func storageTestOptions(t *testing.T) options {
	t.Helper()
	home := t.TempDir()
	return options{
		command:      "storage",
		home:         home,
		envFile:      filepath.Join(home, ".env"),
		storageScope: "all",
		batchSize:    200,
		retries:      3,
	}
}

func TestParseStorageMigrationOptions(t *testing.T) {
	opts, err := parseOptions([]string{
		"storage", "migrate",
		"--from-provider", "local",
		"--to-provider", "webdav",
		"--scope", "presentations",
		"--dry-run",
		"--delete-source",
		"--force",
		"--user-id", "user-1",
		"--only-migrated-from", "s3",
		"--created-after", "2026-01-01",
		"--created-before", "2026-07-31",
		"--batch-size", "50",
		"--max-files", "12",
		"--retries", "5",
	})
	if err != nil {
		t.Fatal(err)
	}
	if opts.command != "storage" || !reflect.DeepEqual(opts.arguments, []string{"migrate"}) {
		t.Fatalf("unexpected command: %+v", opts)
	}
	if opts.fromProvider != "local" || opts.toProvider != "webdav" || opts.storageScope != "presentations" {
		t.Fatalf("provider or scope flags were not parsed: %+v", opts)
	}
	if !opts.dryRun || !opts.deleteSource || !opts.force || opts.batchSize != 50 || opts.maxFiles != 12 || opts.retries != 5 {
		t.Fatalf("migration safety flags were not parsed: %+v", opts)
	}
}

func TestStorageBackendArgsExposeFullMigrationContract(t *testing.T) {
	opts := storageTestOptions(t)
	opts.arguments = []string{"migrate"}
	opts.fromProvider = "s3-compatible"
	opts.toProvider = "webdav"
	opts.storageScope = "deep-research"
	opts.userID = "user-1"
	opts.onlyMigratedFrom = "local"
	opts.createdAfter = "2026-01-01"
	opts.createdBefore = "2026-02-01"
	opts.batchSize = 25
	opts.maxFiles = 100
	opts.retries = 4
	opts.dryRun = true
	opts.deleteSource = true
	opts.force = true

	args, err := storageBackendArgs(opts)
	if err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(args, " ")
	for _, expected := range []string{
		"exec -T fastapi python -m app.files.cli migrate-files",
		"--from-provider s3 --to-provider webdav",
		"--scope deep-research",
		"--user-id user-1",
		"--only-migrated-from local",
		"--created-after 2026-01-01 --created-before 2026-02-01",
		"--batch-size 25 --max-files 100 --retries 4",
		"--dry-run --delete-source --force",
	} {
		if !strings.Contains(joined, expected) {
			t.Fatalf("storage command missing %q: %s", expected, joined)
		}
	}
}

func TestStorageProbeAndLocalCompatibilityCommands(t *testing.T) {
	probe := storageTestOptions(t)
	probe.arguments = []string{"probe"}
	probeArgs, err := storageBackendArgs(probe)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(strings.Join(probeArgs, " "), "python -m app.files.cli storage-probe") {
		t.Fatalf("unexpected probe command: %v", probeArgs)
	}
	if !commandNeedsLock(probe) {
		t.Fatal("storage probe must share the storage-operation lock")
	}

	local := storageTestOptions(t)
	local.arguments = []string{"migrate-local"}
	local.dryRun = true
	localArgs, err := storageBackendArgs(local)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(strings.Join(localArgs, " "), "app.files.cli migrate-local-files") {
		t.Fatalf("unexpected local migration command: %v", localArgs)
	}
	if !commandNeedsLock(local) {
		t.Fatal("storage migration must acquire the mutation lock")
	}
}

func TestStorageMigrationRejectsUnsafeOrInvalidSelections(t *testing.T) {
	tests := []struct {
		name string
		edit func(*options)
		want string
	}{
		{name: "missing source", edit: func(opts *options) {}, want: "requires --from-provider"},
		{name: "same provider", edit: func(opts *options) { opts.fromProvider = "gcs"; opts.toProvider = "gcs" }, want: "must be different"},
		{name: "invalid scope", edit: func(opts *options) { opts.fromProvider = "local"; opts.storageScope = "unknown" }, want: "--scope"},
		{name: "reversed dates", edit: func(opts *options) {
			opts.fromProvider = "local"
			opts.createdAfter = "2026-02-01"
			opts.createdBefore = "2026-01-01"
		}, want: "must not be later"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			opts := storageTestOptions(t)
			opts.arguments = []string{"migrate"}
			tc.edit(&opts)
			_, err := storageBackendArgs(opts)
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("expected %q error, got %v", tc.want, err)
			}
		})
	}
}
