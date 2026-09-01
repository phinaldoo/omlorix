package main

import (
	"encoding/base64"
	"errors"
	"fmt"
	"net/url"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

var envIntegerSuffix = regexp.MustCompile(`(_SECONDS|_RPM|_SIZE|_AGE|_CONN|_TIMEOUT)$`)
var envPortSuffix = regexp.MustCompile(`(_PORT|_HOST_PORT|PORT)$`)
var envURLSuffix = regexp.MustCompile(`(_URL|_ENDPOINT_URL|ROOT_URL)$`)

var envEnumOptions = map[string]map[string]bool{
	"DB_MIGRATIONS_MODE":    {"off": true, "auto": true, "required": true},
	"FILE_STORAGE_PROVIDER": {"local": true, "s3": true, "gcs": true, "azure": true, "webdav": true},
	"MODE":                  {"production": true, "dev": true},
	"PGBOUNCER_POOL_MODE":   {"session": true, "transaction": true},
	"OTEL_TRACES_SAMPLER":   {"always_on": true, "always_off": true, "traceidratio": true, "parentbased_traceidratio": true},
}

// validateConfigValue mirrors the Launcher's environment editor validation so
// terminal configuration cannot persist a value the GUI would reject.
func validateConfigValue(opts options, key, value string) error {
	if err := validateEnvAssignment(key, value); err != nil {
		return err
	}
	defaults, _ := readEnv(filepath.Join(opts.home, ".env.example"))
	defaultValue := defaults[key]
	normalized := strings.ToLower(strings.TrimSpace(value))
	if key == "JWT_SECRET_KEY" && len(strings.TrimSpace(value)) < jwtSecretMinBytes {
		return errors.New("JWT_SECRET_KEY must contain at least 64 bytes")
	}
	if key == "PASSWORD_RESET_IDENTIFIER_HASH_SALT" && len(strings.TrimSpace(value)) < 16 {
		return errors.New("PASSWORD_RESET_IDENTIFIER_HASH_SALT must contain at least 16 characters")
	}
	if key == "LOG_IP_HASH_SALT" && len(strings.TrimSpace(value)) < 16 {
		return errors.New("LOG_IP_HASH_SALT must contain at least 16 characters")
	}

	if enumValues, ok := envEnumOptions[key]; ok {
		if normalized != "" && !enumValues[normalized] {
			return fmt.Errorf("%s must be one of the supported values", key)
		}
		return nil
	}
	if normalized != "" && (normalized == "true" || normalized == "false" || strings.EqualFold(defaultValue, "true") || strings.EqualFold(defaultValue, "false")) {
		if normalized != "true" && normalized != "false" {
			return fmt.Errorf("%s must be true or false", key)
		}
	}
	if value != "" && envPortSuffix.MatchString(key) {
		port, err := strconv.Atoi(value)
		if err != nil || port < 1 || port > 65535 {
			return fmt.Errorf("%s must be a port from 1 to 65535", key)
		}
	}
	if value != "" && envIntegerSuffix.MatchString(key) {
		if _, err := strconv.Atoi(value); err != nil {
			return fmt.Errorf("%s must be a whole number", key)
		}
	}
	if value != "" && envURLSuffix.MatchString(key) {
		parsed, err := url.Parse(value)
		if err != nil || parsed.Scheme == "" {
			return fmt.Errorf("%s must be a valid URL", key)
		}
		allowed := map[string]bool{"http": true, "https": true}
		if key == "DATABASE_URL" || key == "AUDIT_DATABASE_URL" {
			allowed = map[string]bool{"postgres": true, "postgresql": true, "postgresql+psycopg": true, "postgresql+psycopg2": true, "sqlite": true}
		} else if key == "REDIS_URL" {
			allowed = map[string]bool{"redis": true, "rediss": true}
		}
		if !allowed[strings.ToLower(parsed.Scheme)] {
			return fmt.Errorf("%s uses an unsupported URL scheme", key)
		}
	}
	if key == "ENCRYPTION_KEY" && value != "" && !validFernetKey(value) {
		return errors.New("ENCRYPTION_KEY must be a URL-safe base64 Fernet key containing 32 bytes")
	}
	return nil
}

// validateIndependentSecuritySecrets enforces relationships that cannot be
// checked while validating one environment assignment in isolation. Compare
// the normalized values used by the backend so quoting or surrounding
// whitespace cannot disguise reuse of JWT signing material as an audit salt.
func validateIndependentSecuritySecrets(env map[string]string) error {
	jwtSecret := strings.TrimSpace(env["JWT_SECRET_KEY"])
	logIPHashSalt := strings.TrimSpace(env["LOG_IP_HASH_SALT"])
	if jwtSecret != "" && logIPHashSalt != "" && jwtSecret == logIPHashSalt {
		return errors.New("LOG_IP_HASH_SALT must differ from JWT_SECRET_KEY")
	}
	return nil
}

func validFernetKey(value string) bool {
	decoded, err := base64.URLEncoding.DecodeString(strings.TrimSpace(value))
	return err == nil && len(decoded) == 32
}

func validateImportedEnvironment(opts options, raw string) error {
	seen := map[string]bool{}
	values := map[string]string{}
	for lineNumber, line := range strings.Split(raw, "\n") {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		keyPart, valuePart, ok := strings.Cut(trimmed, "=")
		key := strings.TrimSpace(strings.TrimPrefix(keyPart, "export "))
		if !ok || !envKeyPattern.MatchString(key) {
			return fmt.Errorf("import line %d is not a valid environment assignment", lineNumber+1)
		}
		if seen[key] {
			return fmt.Errorf("import contains duplicate key %s", key)
		}
		seen[key] = true
		value := unquoteEnv(strings.TrimSpace(stripInlineComment(valuePart)))
		// Launcher-owned values are parsed for file integrity but ignored by both
		// import modes. Validate only values that can reach the future config so a
		// malformed protected value cannot create CLI/Launcher semantic drift.
		if !launcherHiddenEnvKeys[key] {
			if err := validateConfigValue(opts, key, value); err != nil {
				return err
			}
		}
		values[key] = value
	}
	if len(values) == 0 {
		return errors.New("the import does not contain any environment variables")
	}
	return validateIndependentSecuritySecrets(values)
}
