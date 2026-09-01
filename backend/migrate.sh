#!/bin/sh
set -e

echo "Running database migrations..."

python -m app.migrations.cli run

echo "Migrations complete."
