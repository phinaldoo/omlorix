#!/usr/bin/env bash

set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "Usage: $0 <owner/repository> <release-tag> <download-directory> [asset-name ...]" >&2
  exit 2
fi

repository="$1"
release_tag="$2"
download_dir="$3"
shift 3
requested_assets=("$@")

if [ -z "$repository" ] || [ -z "$release_tag" ] || [ -z "$download_dir" ]; then
  echo "Repository, release tag, and download directory must not be empty." >&2
  exit 2
fi

for requested_asset in "${requested_assets[@]}"; do
  case "$requested_asset" in
    ''|.|..|*/*|*\\*)
      echo "Invalid requested draft release asset name: $requested_asset" >&2
      exit 2
      ;;
  esac
done

mkdir -p "$download_dir"
asset_metadata="$(mktemp)"
trap 'rm -f "$asset_metadata"' EXIT

# `gh release download <tag>` resolves only published releases. Resolve the
# authenticated draft first, then use its stable REST API asset URLs.
release_metadata="$(gh release view "$release_tag" \
  --repo "$repository" \
  --json apiUrl,isDraft --jq '[.isDraft, .apiUrl] | @tsv')"
IFS=$'\t' read -r is_draft release_api_url <<< "$release_metadata"
if [ "$is_draft" != "true" ] || [ -z "$release_api_url" ]; then
  echo "Release $release_tag is not an accessible draft release." >&2
  exit 1
fi

# Paginate so a large release cannot be partially verified and then published.
gh api --paginate "$release_api_url/assets?per_page=100" \
  --jq '.[] | [.name, .url] | @tsv' > "$asset_metadata"

asset_requested() {
  if [ "${#requested_assets[@]}" -eq 0 ]; then
    return 0
  fi
  for requested_asset in "${requested_assets[@]}"; do
    if [ "$1" = "$requested_asset" ]; then
      return 0
    fi
  done
  return 1
}

asset_count=0
while IFS=$'\t' read -r asset_name asset_api_url; do
  if [ -z "$asset_name" ] || [ -z "$asset_api_url" ]; then
    echo "GitHub returned incomplete draft release asset metadata." >&2
    exit 1
  fi
  if ! asset_requested "$asset_name"; then
    continue
  fi
  case "$asset_name" in
    .|..|*/*|*\\*)
      echo "Refusing unsafe draft release asset name: $asset_name" >&2
      exit 1
      ;;
  esac
  if [ -e "$download_dir/$asset_name" ]; then
    echo "Duplicate draft release asset name: $asset_name" >&2
    exit 1
  fi
  gh api \
    --header "Accept: application/octet-stream" \
    "$asset_api_url" > "$download_dir/$asset_name"
  asset_count=$((asset_count + 1))
done < "$asset_metadata"

if [ "$asset_count" -eq 0 ]; then
  echo "Draft release $release_tag has no matching assets to verify." >&2
  exit 1
fi

for requested_asset in "${requested_assets[@]}"; do
  if [ ! -f "$download_dir/$requested_asset" ]; then
    echo "Draft release $release_tag is missing required asset: $requested_asset" >&2
    exit 1
  fi
done
