#!/usr/bin/env bash
# =============================================================================
# scripts/build-runtime-images.sh
#
# Build (and optionally push) agent_team isolated-runtime sandbox images.
# Meant to run on the operator's Docker host / build server — NOT during the
# app's normal lifecycle.
#
# Usage:
#   ./scripts/build-runtime-images.sh                 # build all *.Dockerfile
#   ./scripts/build-runtime-images.sh full            # build a single template
#   PUSH=1 ./scripts/build-runtime-images.sh full     # also `docker push`
#
#   # Push under your registry/account:
#   REGISTRY=<dockerhub_user> PUSH=1 ./scripts/build-runtime-images.sh full
#
#   # Pin CLI versions baked into the `full` image:
#   CLAUDE_CODE_VERSION=2.1.146 CODEX_VERSION=0.9.0 \
#     ./scripts/build-runtime-images.sh full
#
# Env vars honoured:
#   REGISTRY             registry prefix (default: agent-team)
#   IMAGE_VERSION        tag suffix    (default: v1)  → e.g. `:v1`
#   PUSH                 set to 1 to `docker push` after build
#   BUILD_PLATFORM       target arch   (default: linux/amd64). On Apple Silicon
#                        you MUST cross-compile to amd64 — running ARM binaries
#                        under QEMU on an amd64 OpenSandbox host makes execd
#                        hang → "Sandbox health check timed out". Set to
#                        `linux/arm64` only when the server is arm64; `none`
#                        disables --platform (use local host arch).
#   CLAUDE_CODE_VERSION  forwarded as --build-arg to full.Dockerfile
#   CODEX_VERSION        forwarded as --build-arg to full.Dockerfile
#   BASE_IMAGE           override the OpenSandbox base image
# =============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"                 # plugin root
IMG_DIR="${ROOT_DIR}/infra/runtime/images"
VERSION="${IMAGE_VERSION:-v1}"
REGISTRY="${REGISTRY:-agent-team}"
BUILD_PLATFORM="${BUILD_PLATFORM:-linux/amd64}"

# Collect target list from argv or auto-discover all *.Dockerfile.
targets=()
if [[ $# -gt 0 ]]; then
  for name in "$@"; do
    if [[ -f "${IMG_DIR}/${name}.Dockerfile" ]]; then
      targets+=("${name}")
    else
      echo "✗ Template '${name}.Dockerfile' not found in ${IMG_DIR}" >&2
      exit 1
    fi
  done
else
  shopt -s nullglob
  for f in "${IMG_DIR}"/*.Dockerfile; do
    targets+=("$(basename "${f}" .Dockerfile)")
  done
  shopt -u nullglob
fi

if [[ ${#targets[@]} -eq 0 ]]; then
  echo "✗ No .Dockerfile templates found in ${IMG_DIR}" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "✗ Docker daemon not reachable" >&2
  exit 1
fi

for name in "${targets[@]}"; do
  tag_versioned="${REGISTRY}/runtime-${name}:${VERSION}"
  tag_latest="${REGISTRY}/runtime-${name}:latest"

  # Per-template build-args + build context. Adding new templates? Append arms.
  build_args=()
  if [[ -n "${BASE_IMAGE:-}" ]]; then
    build_args+=(--build-arg "BASE_IMAGE=${BASE_IMAGE}")
  fi
  # Context = plugin root so the Dockerfile can COPY the runtime subtree (ACP
  # stack + sidecar server) — no `src/` needed.
  context="${ROOT_DIR}"
  case "${name}" in
    full)
      if [[ -n "${CLAUDE_CODE_VERSION:-}" ]]; then
        build_args+=(--build-arg "CLAUDE_CODE_VERSION=${CLAUDE_CODE_VERSION}")
      fi
      if [[ -n "${CODEX_VERSION:-}" ]]; then
        build_args+=(--build-arg "CODEX_VERSION=${CODEX_VERSION}")
      fi
      ;;
  esac

  platform_args=()
  if [[ "${BUILD_PLATFORM}" != "none" ]]; then
    platform_args+=(--platform "${BUILD_PLATFORM}")
  fi

  echo ""
  echo "→ Building ${tag_versioned} (platform=${BUILD_PLATFORM}, context=${context})"
  # ${arr[@]+"${arr[@]}"} keeps `set -u` happy when the array is empty.
  docker build \
      ${platform_args[@]+"${platform_args[@]}"} \
      ${build_args[@]+"${build_args[@]}"} \
      -t "${tag_versioned}" \
      -t "${tag_latest}" \
      -f "${IMG_DIR}/${name}.Dockerfile" \
      "${context}"
  if [[ "${PUSH:-0}" == "1" ]]; then
    echo "→ Pushing ${tag_versioned} & ${tag_latest}"
    docker push "${tag_versioned}"
    docker push "${tag_latest}"
  fi
done

echo ""
echo "✓ Built ${#targets[@]} runtime image(s): ${targets[*]}"
echo "  Point the runtime profile at it, e.g.:"
echo "    AGENT_TEAM_RUNTIME_PROVIDER=opensandbox"
echo "    AGENT_TEAM_RUNTIME_IMAGE=${REGISTRY}/runtime-${targets[0]}:${VERSION}"
