#!/usr/bin/env bash
# Start the opencode headless server in the background (idempotent).
# Invoked via devcontainer.json `postStartCommand`, so it runs on every
# container start — not just on creation. Safe to re-run: exits early if
# a server is already listening on the configured port.
#
# Port and bind address are fixed to match `forwardPorts` in
# devcontainer.json (opencode's default port). Fixed, not env-configurable,
# so the server can never drift out of sync with the forwarded port.
# Auth is via OPENCODE_SERVER_PASSWORD / OPENCODE_SERVER_USERNAME,
# forwarded from the host through `remoteEnv` when set.
set -euo pipefail

PORT="4096"
HOST="0.0.0.0"
LOG_DIR="${HOME}/.local/share/opencode"
LOG_FILE="${LOG_DIR}/serve.log"

mkdir -p "$LOG_DIR"

if ! command -v opencode >/dev/null 2>&1; then
  echo "[opencode-server] opencode not found on PATH, skipping" >&2
  exit 0
fi

# Already serving? Prefer an HTTP probe, fall back to a TCP connect and
# then to process matching (curl/pgrep may be missing in minimal images).
if command -v curl >/dev/null 2>&1; then
  if curl -sf --max-time 2 "http://127.0.0.1:${PORT}/doc" >/dev/null 2>&1; then
    echo "[opencode-server] already running on port ${PORT}"
    exit 0
  fi
fi
if (echo >/dev/tcp/127.0.0.1/"${PORT}") >/dev/null 2>&1; then
  echo "[opencode-server] something is already listening on port ${PORT}, skipping start"
  exit 0
fi
if command -v pgrep >/dev/null 2>&1; then
  # Bracket trick avoids matching this script's own command line.
  if pgrep -f "[o]pencode serve" >/dev/null 2>&1; then
    echo "[opencode-server] opencode serve process already running"
    exit 0
  fi
fi

echo "[opencode-server] starting 'opencode serve' on ${HOST}:${PORT} (log: ${LOG_FILE})"
# nohup + & so the server outlives postStartCommand; disown avoids hangs
# on shells with job control enabled.
# shellcheck disable=SC2086
nohup opencode serve --hostname "${HOST}" --port "${PORT}" >>"${LOG_FILE}" 2>&1 &
disown || true

echo "[opencode-server] started (pid $!). OpenAPI spec: http://localhost:${PORT}/doc"
