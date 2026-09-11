#!/usr/bin/env bash
# Start the opencode headless server in the background (idempotent).
# Invoked via devcontainer.json `postStartCommand`, so it runs on every
# container start — not just on creation. Safe to re-run: exits early if
# a server is already listening on the configured port.
#
# Port and bind address are fixed to match `forwardPorts`/`appPort` in
# devcontainer.json (opencode's default port). Fixed, not env-configurable,
# so the server can never drift out of sync with the forwarded port.
# Auth is via OPENCODE_SERVER_PASSWORD / OPENCODE_SERVER_USERNAME,
# forwarded from the host through `remoteEnv` when set.
#
# Robustness notes (learned the hard way):
# - Lifecycle commands may run as root with a minimal PATH that does not
#   include /home/vscode/.local/bin, so the opencode binary is resolved
#   via absolute fallback paths, never $PATH alone.
# - When running as root we re-exec as the remote user (default: vscode)
#   so HOME, config, and data dirs stay consistent.
# - The server is detached with setsid into a new session so it survives
#   process-group cleanup after the lifecycle command finishes.
# - Every invocation appends a timestamped marker to the log, so it is
#   always possible to tell whether postStart actually ran.
set -euo pipefail

PORT="4096"
HOST="0.0.0.0"
SERVER_USER="${OPENCODE_SERVER_USER:-vscode}"

# Lifecycle commands may execute as root (minimal PATH, HOME=/root).
# Re-exec as the remote user so HOME, config, and data dirs stay consistent.
if [ "$(id -u)" -eq 0 ] && [ "${OPENCODE_SERVER_REEXEC:-1}" = "1" ]; then
  if id "${SERVER_USER}" >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
    echo "[opencode-server] running as root, re-execing as ${SERVER_USER}" >&2
    exec sudo -u "${SERVER_USER}" env OPENCODE_SERVER_REEXEC=0 bash "$0" "$@"
  fi
  echo "[opencode-server] WARNING: running as root (no sudo/${SERVER_USER}), continuing" >&2
fi

LOG_DIR="${HOME}/.local/share/opencode"
LOG_FILE="${LOG_DIR}/serve.log"

mkdir -p "$LOG_DIR"

# Marker proving postStart ran (user, HOME, timestamp) — check this first
# when debugging a missing server.
echo "[opencode-server] postStart ran at $(date -u +%FT%TZ) as $(whoami) (HOME=${HOME})" >>"${LOG_FILE}"

# Resolve the binary without depending on $PATH: lifecycle commands running
# as root have a minimal PATH without /home/vscode/.local/bin.
OPENCODE_BIN="$(command -v opencode 2>/dev/null || true)"
if [ -z "${OPENCODE_BIN}" ]; then
  for candidate in \
    "${HOME}/.local/bin/opencode" \
    "/home/${SERVER_USER}/.local/bin/opencode" \
    "${HOME}/.opencode/bin/opencode" \
    "/home/${SERVER_USER}/.opencode/bin/opencode"; do
    if [ -x "${candidate}" ]; then
      OPENCODE_BIN="${candidate}"
      break
    fi
  done
fi

if [ -z "${OPENCODE_BIN}" ]; then
  echo "[opencode-server] ERROR: opencode binary not found (PATH=${PATH}, HOME=${HOME})" | tee -a "${LOG_FILE}" >&2
  exit 1
fi

# Already serving? Prefer an HTTP probe, fall back to a TCP connect and
# then to process matching (curl/pgrep may be missing in minimal images).
if command -v curl >/dev/null 2>&1; then
  if curl -sf --max-time 2 "http://127.0.0.1:${PORT}/doc" >/dev/null 2>&1; then
    echo "[opencode-server] already running on port ${PORT}" | tee -a "${LOG_FILE}"
    exit 0
  fi
fi
if (echo >/dev/tcp/127.0.0.1/"${PORT}") >/dev/null 2>&1; then
  echo "[opencode-server] something is already listening on port ${PORT}, skipping start" | tee -a "${LOG_FILE}"
  exit 0
fi
if command -v pgrep >/dev/null 2>&1; then
  # Bracket trick avoids matching this script's own command line.
  if pgrep -f "[o]pencode serve" >/dev/null 2>&1; then
    echo "[opencode-server] opencode serve process already running" | tee -a "${LOG_FILE}"
    exit 0
  fi
fi

echo "[opencode-server] starting 'opencode serve' on ${HOST}:${PORT} (log: ${LOG_FILE}, bin: ${OPENCODE_BIN})" | tee -a "${LOG_FILE}"
# setsid detaches into a new session so the server survives process-group
# cleanup when the lifecycle command finishes; nohup guards against SIGHUP;
# stdin is closed so the server never holds the caller's stdin open.
if command -v setsid >/dev/null 2>&1; then
  setsid nohup "${OPENCODE_BIN}" serve --hostname "${HOST}" --port "${PORT}" >>"${LOG_FILE}" 2>&1 < /dev/null &
else
  # shellcheck disable=SC2086
  nohup "${OPENCODE_BIN}" serve --hostname "${HOST}" --port "${PORT}" >>"${LOG_FILE}" 2>&1 < /dev/null &
fi

echo "[opencode-server] started (pid $!). OpenAPI spec: http://localhost:${PORT}/doc" | tee -a "${LOG_FILE}"
