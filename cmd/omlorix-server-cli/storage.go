package main

import (
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"
)

var storageProviders = map[string]bool{
	"local":  true,
	"s3":     true,
	"gcs":    true,
	"azure":  true,
	"webdav": true,
}

var storageScopes = map[string]bool{
	"all":           true,
	"files":         true,
	"deep-research": true,
	"presentations": true,
}

func normalizeStorageProvider(value string) (string, error) {
	normalized := strings.ToLower(strings.TrimSpace(value))
	if normalized == "s3-compatible" {
		normalized = "s3"
	}
	if !storageProviders[normalized] {
		return "", fmt.Errorf("storage provider must be local, s3, gcs, azure, or webdav (got %q)", value)
	}
	return normalized, nil
}

func validateStorageDate(flag string, value string) (string, error) {
	normalized := strings.TrimSpace(value)
	if normalized == "" {
		return "", nil
	}
	if _, err := time.Parse("2006-01-02", normalized); err != nil {
		return "", fmt.Errorf("%s must use YYYY-MM-DD", flag)
	}
	return normalized, nil
}

func validateStorageFilter(flag string, value string) (string, error) {
	normalized := strings.TrimSpace(value)
	if len(normalized) > 255 {
		return "", fmt.Errorf("%s must be 255 characters or fewer", flag)
	}
	if strings.ContainsAny(normalized, "\r\n\x00") {
		return "", fmt.Errorf("%s contains unsupported control characters", flag)
	}
	return normalized, nil
}

func storageBackendArgs(opts options) ([]string, error) {
	if len(opts.arguments) == 0 {
		return nil, errors.New("usage: omlorix-server storage <probe|migrate|migrate-local>")
	}
	action := strings.ToLower(strings.TrimSpace(opts.arguments[0]))
	if len(opts.arguments) != 1 {
		return nil, fmt.Errorf("storage %s does not accept positional arguments", action)
	}

	base := composeArgs(opts, "exec", "-T", "fastapi", "python", "-m", "app.files.cli")
	if action == "probe" {
		return append(base, "storage-probe"), nil
	}
	if action != "migrate" && action != "migrate-local" {
		return nil, fmt.Errorf("unknown storage action %q", action)
	}

	backendAction := "migrate-files"
	if action == "migrate-local" {
		backendAction = "migrate-local-files"
		if strings.TrimSpace(opts.fromProvider) != "" || strings.TrimSpace(opts.toProvider) != "" {
			return nil, errors.New("storage migrate-local uses local as the source and FILE_STORAGE_PROVIDER as the destination; use storage migrate for explicit providers")
		}
	} else {
		if strings.TrimSpace(opts.fromProvider) == "" {
			return nil, errors.New("storage migrate requires --from-provider")
		}
		source, err := normalizeStorageProvider(opts.fromProvider)
		if err != nil {
			return nil, fmt.Errorf("--from-provider: %w", err)
		}
		opts.fromProvider = source
		if strings.TrimSpace(opts.toProvider) != "" {
			destination, err := normalizeStorageProvider(opts.toProvider)
			if err != nil {
				return nil, fmt.Errorf("--to-provider: %w", err)
			}
			if source == destination {
				return nil, errors.New("source and destination storage providers must be different")
			}
			opts.toProvider = destination
		}
	}

	scope := strings.ToLower(strings.TrimSpace(opts.storageScope))
	if !storageScopes[scope] {
		return nil, errors.New("--scope must be all, files, deep-research, or presentations")
	}
	createdAfter, err := validateStorageDate("--created-after", opts.createdAfter)
	if err != nil {
		return nil, err
	}
	createdBefore, err := validateStorageDate("--created-before", opts.createdBefore)
	if err != nil {
		return nil, err
	}
	if createdAfter != "" && createdBefore != "" && createdAfter > createdBefore {
		return nil, errors.New("--created-after must not be later than --created-before")
	}
	userID, err := validateStorageFilter("--user-id", opts.userID)
	if err != nil {
		return nil, err
	}
	onlyMigratedFrom := ""
	if strings.TrimSpace(opts.onlyMigratedFrom) != "" {
		onlyMigratedFrom, err = normalizeStorageProvider(opts.onlyMigratedFrom)
		if err != nil {
			return nil, fmt.Errorf("--only-migrated-from: %w", err)
		}
	}

	args := append(base, backendAction)
	if action == "migrate" {
		args = append(args, "--from-provider", opts.fromProvider)
		if opts.toProvider != "" {
			args = append(args, "--to-provider", opts.toProvider)
		}
	}
	args = append(args,
		"--scope", scope,
		"--batch-size", strconv.Itoa(opts.batchSize),
		"--max-files", strconv.Itoa(opts.maxFiles),
		"--retries", strconv.Itoa(opts.retries),
	)
	for _, optional := range []struct{ flag, value string }{
		{"--user-id", userID},
		{"--only-migrated-from", onlyMigratedFrom},
		{"--created-after", createdAfter},
		{"--created-before", createdBefore},
	} {
		if optional.value != "" {
			args = append(args, optional.flag, optional.value)
		}
	}
	if opts.dryRun {
		args = append(args, "--dry-run")
	}
	if opts.deleteSource {
		args = append(args, "--delete-source")
	}
	if opts.force {
		args = append(args, "--force")
	}
	return args, nil
}

func commandStorage(opts options) error {
	args, err := storageBackendArgs(opts)
	if err != nil {
		return invalidArgumentsError(err)
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
	return runBackendCommand(opts, args)
}
