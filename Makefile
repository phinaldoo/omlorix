SHELL := /bin/bash

# Read the last active assignment using the dotenv forms supported by
# compose.sh. Every topology decision below comes from `.env`, so caller or
# command-line variables cannot select different files than the containers use.
define read_env_value
$(strip $(shell awk -v target='$(1)' '$$0 ~ ("^[[:space:]]*(export[[:space:]]+)?" target "[[:space:]]*=") { value=$$0; sub("^[[:space:]]*(export[[:space:]]+)?" target "[[:space:]]*=[[:space:]]*", "", value); sub(/[[:space:]]+\#.*$$/, "", value); gsub(/^[[:space:]"'\'']+|[[:space:]"'\'']+$$/, "", value); result=tolower(value) } END { print result }' .env 2>/dev/null))
endef

override CONFIG_MODE := $(call read_env_value,MODE)
override CONFIG_BUNDLED_DB := $(or $(call read_env_value,OMLORIX_USE_BUNDLED_DB),true)
override CONFIG_BUNDLED_REDIS := $(or $(call read_env_value,OMLORIX_USE_BUNDLED_REDIS),true)
override CONFIG_REDIS_ENABLED := $(or $(call read_env_value,REDIS_ENABLED),true)
override CONFIG_PGBOUNCER := $(or $(call read_env_value,OMLORIX_USE_PGBOUNCER),false)
override CONFIG_BUNDLED_STORAGE := $(or $(call read_env_value,OMLORIX_USE_BUNDLED_STORAGE),false)
override CONFIG_OTEL_ENABLED := $(or $(call read_env_value,OTEL_ENABLED),false)

TRUE_VALUES := true 1 yes on
override CONFIG_BUNDLED_DB_ACTIVE := $(if $(filter $(TRUE_VALUES),$(CONFIG_BUNDLED_DB)),true,false)
override CONFIG_BUNDLED_REDIS_ACTIVE := $(if $(filter $(TRUE_VALUES),$(CONFIG_BUNDLED_REDIS)),true,false)
override CONFIG_REDIS_ACTIVE := $(if $(filter $(TRUE_VALUES),$(CONFIG_REDIS_ENABLED)),true,false)
override CONFIG_PGBOUNCER_ACTIVE := $(if $(filter $(TRUE_VALUES),$(CONFIG_PGBOUNCER)),true,false)
override CONFIG_BUNDLED_STORAGE_ACTIVE := $(if $(filter $(TRUE_VALUES),$(CONFIG_BUNDLED_STORAGE)),true,false)
override CONFIG_OTEL_ACTIVE := $(if $(filter $(TRUE_VALUES),$(CONFIG_OTEL_ENABLED)),true,false)
override HOST_OS := $(shell uname -s 2>/dev/null)

ifeq ($(CONFIG_OTEL_ACTIVE),true)
COMPOSE_OBSERVABILITY := -f docker-compose.observability.yml
ifeq ($(HOST_OS),Linux)
COMPOSE_OBSERVABILITY += -f docker-compose.observability-linux.yml
endif
else
COMPOSE_OBSERVABILITY :=
endif

# Base Compose command
COMPOSE_BASE := ./script/compose.sh

# Source checkout stack: production topology with locally built application images.
COMPOSE_DEV_PORTS := -f docker-compose.dev-ports.yml
COMPOSE_SOURCE_FILES := -f docker-compose.server.yml -f docker-compose.source-build.yml -f docker-compose.frontend-port.yml $(COMPOSE_OBSERVABILITY)

# Managed cloud stack (external DB/Redis)
COMPOSE_MANAGED_CLOUD_FILES := -f docker-compose.managed-cloud.yml $(COMPOSE_OBSERVABILITY)

BUILD ?= true
ifneq ($(filter true 1 yes on,$(BUILD)),)
COMPOSE_BUILD_FLAG := --build
else
COMPOSE_BUILD_FLAG :=
endif
DEFAULT_GOAL := help
FILES_MIGRATE_ARGS ?=
BACKUP_CREATE_ARGS ?=
BACKUP_VERIFY_ARGS ?=
BACKUP_TARGET ?= empty

# Select one Compose topology for the entire invocation. Keeping this decision
# at parse time prevents `up`, `down`, logs, migrations, and backup operations
# from accidentally targeting different stacks.
CURRENT_COMPOSE_FILES := $(COMPOSE_SOURCE_FILES)

# Match the Launcher and standalone CLI: use managed cloud only when every
# stateful dependency is external (or Redis is disabled) and no bundled proxy
# or storage service requires the server topology.
CONFIG_MANAGED_CLOUD := false
ifeq ($(CONFIG_BUNDLED_DB_ACTIVE),false)
ifeq ($(CONFIG_REDIS_ACTIVE),false)
CONFIG_MANAGED_CLOUD := true
else ifeq ($(CONFIG_BUNDLED_REDIS_ACTIVE),false)
CONFIG_MANAGED_CLOUD := true
endif
endif
ifneq ($(filter true,$(CONFIG_PGBOUNCER_ACTIVE) $(CONFIG_BUNDLED_STORAGE_ACTIVE)),)
CONFIG_MANAGED_CLOUD := false
endif

ifeq ($(CONFIG_MANAGED_CLOUD),true)
CURRENT_COMPOSE_FILES := $(COMPOSE_MANAGED_CLOUD_FILES)
endif

# Development host-port overrides are valid for local stacks, but managed cloud
# intentionally has no bundled Postgres or Redis services to expose.
ifneq ($(filter dev,$(CONFIG_MODE)),)
ifeq ($(CONFIG_MANAGED_CLOUD),true)
else
CURRENT_COMPOSE_FILES := $(CURRENT_COMPOSE_FILES) $(COMPOSE_DEV_PORTS)
endif
endif

CURRENT_COMPOSE := $(COMPOSE_BASE) $(CURRENT_COMPOSE_FILES)

.PHONY: help setup up down restart logs ps update migrate source-probe files-migrate files-migrate-local backup-create backup-verify backup-restore

help:
	@echo "Available targets:"
	@echo "  setup         Run setup.sh to prepare config files"
	@echo "  up            Start the app stack (use env vars for configuration)"
	@echo "  down          Stop containers but keep data volumes"
	@echo "  restart       Restart all services"
	@echo "  logs          Follow logs for all services"
	@echo "  ps            Show container status"
	@echo "  migrate       Take the stack offline, then run one-shot DB migrations (main + audit)"
	@echo "  source-probe   Run storage provider connectivity probe"
	@echo "  files-migrate Migrate files and generated artifacts between storage providers"
	@echo "  files-migrate-local Migrate local files/artifacts to the configured provider"
	@echo "  backup-create  Create a full instance backup"
	@echo "  backup-verify Verify a backup"
	@echo "  backup-restore Verify and restore a full instance backup"
	@echo "  update         Pull latest changes from git"
	@echo ""
	@echo "Stack configuration (edit .env):"
	@echo "  MODE=dev|production              Select the application mode"
	@echo "  OMLORIX_USE_PGBOUNCER=true        Route database traffic through PgBouncer"
	@echo "  OMLORIX_USE_BUNDLED_STORAGE=true  Use the bundled MinIO storage service"
	@echo "  REDIS_ENABLED=false              Keep automation workers disabled"
	@echo "  OTEL_ENABLED=true                Include the observability stack"
	@echo "  Fully external DB/Redis/storage  Selects managed cloud automatically"
	@echo ""
	@echo "Make invocation option:"
	@echo "  BUILD=false  Skip image rebuilds"
	@echo ""
	@echo "Restore examples:"
	@echo "  make backup-restore BACKUP_SOURCE=<container-visible-uri>"
	@echo "  make backup-restore BACKUP_JOB_ID=<id>"
	@echo "  make backup-restore BACKUP_SOURCE=<uri> BACKUP_TARGET=in_place BACKUP_CONFIRM=RESTORE-IN-PLACE"

setup:
ifeq ($(filter setup,$(MAKECMDGOALS)),setup)
	@./setup.sh
else
	@./setup.sh > /dev/null
endif

up: setup
	@# compose.sh derives every service profile and host override from .env.
	@# Remove every current and orphaned database writer before a potentially new
	@# migration image runs. Data volumes remain intact.
	@$(CURRENT_COMPOSE) down --remove-orphans
	@$(CURRENT_COMPOSE) rm -sf migrate
	@$(CURRENT_COMPOSE) up -d $(COMPOSE_BUILD_FLAG) --force-recreate migrate
	@$(CURRENT_COMPOSE) up -d $(COMPOSE_BUILD_FLAG) || { \
		status=$$?; \
		echo ""; \
		echo "up failed. Showing recent migrate logs:"; \
		$(CURRENT_COMPOSE) logs --no-color --tail=120 migrate || true; \
		echo ""; \
		echo "Tip: run 'make ps' and '$(CURRENT_COMPOSE) logs -f migrate' for more details."; \
		exit $$status; \
	}

down:
	$(CURRENT_COMPOSE) down --remove-orphans

restart: down up

logs:
	$(CURRENT_COMPOSE) logs -f

ps:
	$(CURRENT_COMPOSE) ps

update:
	git pull --rebase --autostash

migrate:
	@# The database gates intentionally reject pre-fence application writers.
	@# Leave application services stopped after this operator-only migration;
	@# `make up` performs the same drain and then starts the complete stack.
	$(CURRENT_COMPOSE) down --remove-orphans
	$(CURRENT_COMPOSE) run --rm $(COMPOSE_BUILD_FLAG) migrate

source-probe:
	$(CURRENT_COMPOSE) run --rm fastapi python -m app.files.cli storage-probe

files-migrate:
	$(CURRENT_COMPOSE) run --rm fastapi python -m app.files.cli migrate-files $(FILES_MIGRATE_ARGS)

files-migrate-local:
	$(CURRENT_COMPOSE) run --rm fastapi python -m app.files.cli migrate-local-files $(FILES_MIGRATE_ARGS)

backup-create:
	$(CURRENT_COMPOSE) run --rm fastapi python -m app.backups.cli create $(if $(BACKUP_DESTINATION),--destination "$(BACKUP_DESTINATION)",) $(BACKUP_CREATE_ARGS)

backup-verify:
	@if [ -z "$(BACKUP_JOB_ID)" ] && [ -z "$(BACKUP_SOURCE)" ]; then \
		echo "Usage: make backup-verify BACKUP_JOB_ID=<id> | BACKUP_SOURCE=<uri>"; \
		exit 1; \
	fi
	$(CURRENT_COMPOSE) run --rm fastapi python -m app.backups.cli verify $(if $(BACKUP_JOB_ID),--job-id "$(BACKUP_JOB_ID)",) $(if $(BACKUP_SOURCE),--source "$(BACKUP_SOURCE)",) $(BACKUP_VERIFY_ARGS)

backup-restore:
	@./script/coordinated-backup-restore.sh $(CURRENT_COMPOSE_FILES) -- \
		$(if $(BACKUP_SOURCE),--source "$(BACKUP_SOURCE)",) \
		$(if $(BACKUP_JOB_ID),--job-id "$(BACKUP_JOB_ID)",) \
		--target "$(BACKUP_TARGET)" \
		$(if $(BACKUP_CONFIRM),--confirm "$(BACKUP_CONFIRM)",)
