#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "usage: $0 <repo-slug> <repo-path> [image-tag]" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
slug="$1"
repo_path="$(realpath "$2")"
image_tag="${3:-agent-team/${slug}-sandbox:latest}"
base_image="${BASE_IMAGE:-agent-team/agent-team-sandbox:latest}"
deps="${repo_path}/node_modules"

if [[ ! "$slug" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$ ]]; then
  echo "invalid repository slug: $slug" >&2
  exit 2
fi
if [[ ! -d "$deps" ]]; then
  echo "node_modules not found: $deps" >&2
  exit 1
fi

docker build \
  --build-arg "BASE_IMAGE=${base_image}" \
  --build-arg "REPO_SLUG=${slug}" \
  --tag "$image_tag" \
  --file "${ROOT_DIR}/infra/runtime/project-deps.Dockerfile" \
  "$deps"

echo "Built isolated project runtime: $image_tag"
