#!/usr/bin/env bash

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <artifact-directory> <sha256sum|shasum>" >&2
  exit 2
fi

artifact_dir="$1"
verifier="$2"

if [ ! -d "$artifact_dir" ]; then
  echo "Artifact directory does not exist: $artifact_dir" >&2
  exit 1
fi

case "$verifier" in
  sha256sum)
    verify_command=(sha256sum --check)
    ;;
  shasum)
    verify_command=(shasum -a 256 -c)
    ;;
  *)
    echo "Unsupported SHA-256 verifier: $verifier" >&2
    exit 2
    ;;
esac

if ! command -v "${verify_command[0]}" >/dev/null 2>&1; then
  echo "Required SHA-256 verifier is unavailable: ${verify_command[0]}" >&2
  exit 1
fi

checksums=()
while IFS= read -r -d '' checksum; do
  checksums+=("$checksum")
done < <(find "$artifact_dir" -maxdepth 1 -type f -name '*.sha256' -print0)

if [ "${#checksums[@]}" -eq 0 ]; then
  echo "No checksum files were found in $artifact_dir." >&2
  exit 1
fi

for checksum in "${checksums[@]}"; do
  checksum_name="$(basename "$checksum")"
  expected_name="${checksum_name%.sha256}"
  line_count="$(awk 'END { print NR }' "$checksum")"
  if [ "$line_count" -ne 1 ]; then
    echo "Checksum must contain exactly one entry: $checksum_name" >&2
    exit 1
  fi

  entry="$(sed -n '1p' "$checksum")"
  recorded_hash="${entry:0:64}"
  separator="${entry:64:2}"
  recorded_name="${entry:66}"

  if ! [[ "$recorded_hash" =~ ^[0-9a-f]{64}$ ]]; then
    echo "Checksum has an invalid SHA-256 digest: $checksum_name" >&2
    exit 1
  fi
  if [ "$separator" != "  " ] && [ "$separator" != " *" ]; then
    echo "Checksum has an invalid digest/name separator: $checksum_name" >&2
    exit 1
  fi
  if [ "$recorded_name" != "$expected_name" ]; then
    echo "Checksum entry must name its release asset basename: $checksum_name references $recorded_name" >&2
    exit 1
  fi
  case "$recorded_name" in
    */*|*\\*)
      echo "Checksum entry must not contain a path: $checksum_name references $recorded_name" >&2
      exit 1
      ;;
  esac
  if [ ! -f "$artifact_dir/$recorded_name" ]; then
    echo "Checksum references a missing release asset: $recorded_name" >&2
    exit 1
  fi

  (
    cd "$artifact_dir"
    "${verify_command[@]}" "$checksum_name"
  )
done

for artifact in "$artifact_dir"/*; do
  [ -f "$artifact" ] || continue
  artifact_name="$(basename "$artifact")"
  case "$artifact_name" in
    *.dmg|*.zip|*.exe|*.AppImage|*.deb|*.tar.gz|omlorix-server-cli-*|chatui-server-cli-*)
      case "$artifact_name" in
        *.sha256|*.blockmap)
          continue
          ;;
      esac
      if [ ! -f "$artifact.sha256" ]; then
        echo "Release asset is missing its checksum: $artifact_name" >&2
        exit 1
      fi
      ;;
  esac
done
