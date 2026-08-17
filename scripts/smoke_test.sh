#!/usr/bin/env bash
set -Eeuo pipefail

base_url="${MORNING_BASE_URL:-http://127.0.0.1:8000}"
token="${MORNING_API_TOKEN:-}"

curl --fail --silent --show-error "$base_url/health"
printf '\n'

if [[ -n "$token" ]]; then
  curl --fail --silent --show-error \
    -H "Authorization: Bearer $token" \
    "$base_url/v1/briefings/latest/text"
else
  curl --fail --silent --show-error "$base_url/v1/briefings/latest/text"
fi
printf '\n'
