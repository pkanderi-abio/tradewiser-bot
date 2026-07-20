#!/bin/bash
# scripts/pi-deploy.sh
#
# Pull-based CI-gated auto-deploy for the Raspberry Pi.
#
# Runs on a systemd timer (tradewiser-deploy.timer, every ~2 min).
# Idempotent: no-op if the local checkout already matches origin/main.
# Refuses to deploy a commit whose CI hasn't gone green.
# Auto-rollback if the /health/ check fails after restart.
#
# Env knobs (override in /etc/tradewiser/deploy.env if you want):
#   APP_DIR              = /opt/tradewiser
#   SERVICE              = tradewiser
#   BRANCH               = main
#   REPO                 = pkanderi-abio/tradewiser-bot
#   HEALTH_URL           = http://localhost:8000/health/
#   HEALTH_TIMEOUT_SEC   = 30
#   REQUIRE_GREEN_CI     = 1   (set 0 to bypass the CI check — not recommended)
#
# All output goes to stdout/stderr, which systemd captures to journalctl.
# Grep with:   journalctl -u tradewiser-deploy -f

set -euo pipefail

# Load optional overrides
if [[ -f /etc/tradewiser/deploy.env ]]; then
    # shellcheck disable=SC1091
    source /etc/tradewiser/deploy.env
fi

APP_DIR="${APP_DIR:-/opt/tradewiser}"
SERVICE="${SERVICE:-tradewiser}"
BRANCH="${BRANCH:-main}"
REPO="${REPO:-pkanderi-abio/tradewiser-bot}"
HEALTH_URL="${HEALTH_URL:-http://localhost:8000/health/}"
HEALTH_TIMEOUT_SEC="${HEALTH_TIMEOUT_SEC:-30}"
REQUIRE_GREEN_CI="${REQUIRE_GREEN_CI:-1}"

log()  { echo "[deploy $(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }
fail() { log "FAIL: $*"; exit 1; }

cd "$APP_DIR" || fail "APP_DIR not found: $APP_DIR"

# 1. Sanity — must be a git checkout on the expected branch.
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "$APP_DIR is not a git repo"

# 2. Fetch remote state.
git fetch --quiet origin "$BRANCH" || fail "git fetch failed"
local_sha=$(git rev-parse HEAD)
remote_sha=$(git rev-parse "origin/$BRANCH")

if [[ "$local_sha" == "$remote_sha" ]]; then
    # No news is good news — keep the log quiet.
    exit 0
fi

log "new commit ${remote_sha:0:8} (was ${local_sha:0:8})"

# 3. Refuse to deploy if CI hasn't gone green.
#    /commits/{sha}/check-runs is unauthenticated for public repos (60 req/hr
#    limit; we poll every 2 min = 30 req/hr, safely under). Statuses:
#      success  → all check-runs concluded successfully
#      pending  → at least one still running or not yet reported
#      failure  → at least one non-success conclusion (failure/cancelled/timed_out)
if [[ "$REQUIRE_GREEN_CI" == "1" ]]; then
    status_json=$(curl -sf --max-time 15 \
        "https://api.github.com/repos/${REPO}/commits/${remote_sha}/check-runs" \
        || echo '{"check_runs":[]}')
    status=$(python3 - <<PY
import json, sys
d = json.loads('''${status_json}''')
runs = d.get("check_runs", [])
if not runs:
    print("pending")
    sys.exit(0)
conclusions = [r.get("conclusion") for r in runs]
if None in conclusions:
    print("pending")
elif all(c == "success" for c in conclusions):
    print("success")
else:
    print("failure")
PY
)
    case "$status" in
        success) log "CI green — proceeding" ;;
        pending) log "CI still pending for ${remote_sha:0:8} — skip this cycle"; exit 0 ;;
        failure) log "CI failed for ${remote_sha:0:8} — refusing to deploy"; exit 0 ;;
        *)       log "unknown CI status '${status}' — refusing to deploy"; exit 0 ;;
    esac
fi

# 4. Detect requirement changes so pip install only runs when needed.
pip_install_needed=false
if ! git diff --quiet "$local_sha" "$remote_sha" -- requirements.txt; then
    pip_install_needed=true
    log "requirements.txt changed — will pip install after checkout"
fi

# 5. Deploy atomically: reset first, then adjust deps, then restart.
#    NOTE: .env lives in /etc/tradewiser/.env (loaded via systemd EnvironmentFile)
#    so `git reset --hard` in APP_DIR is safe — nothing in-tree is user-modified.
git reset --hard "$remote_sha"
log "checked out ${remote_sha:0:8}"

if $pip_install_needed; then
    if ! "${APP_DIR}/venv/bin/pip" install --quiet -r requirements.txt; then
        log "pip install FAILED — rolling back"
        git reset --hard "$local_sha"
        "${APP_DIR}/venv/bin/pip" install --quiet -r requirements.txt || true
        fail "rolled back to ${local_sha:0:8} (pip install failed)"
    fi
fi

# 6. Restart. Requires passwordless sudo for `systemctl restart $SERVICE`
#    (pi-cd-install.sh installs a sudoers rule for the tradewiser user).
if ! sudo -n systemctl restart "$SERVICE"; then
    log "systemctl restart FAILED — rolling back checkout"
    git reset --hard "$local_sha"
    if $pip_install_needed; then
        "${APP_DIR}/venv/bin/pip" install --quiet -r requirements.txt || true
    fi
    sudo -n systemctl restart "$SERVICE" || log "restart still failing — service may be down"
    fail "rolled back to ${local_sha:0:8} (systemctl restart failed)"
fi
log "restarted ${SERVICE}"

# 7. Health check with retry until timeout. Uvicorn takes a few seconds to
#    bind after restart, so a single curl right after systemctl always races.
start_ts=$(date +%s)
ok=false
while (( $(date +%s) - start_ts < HEALTH_TIMEOUT_SEC )); do
    if curl -sf --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
        ok=true
        break
    fi
    sleep 2
done

if $ok; then
    log "DEPLOYED ${remote_sha:0:8} (healthy after $(( $(date +%s) - start_ts ))s)"
    exit 0
fi

# 8. Rollback path — health check never came back green.
log "health check FAILED after ${HEALTH_TIMEOUT_SEC}s — rolling back to ${local_sha:0:8}"
git reset --hard "$local_sha"
if $pip_install_needed; then
    "${APP_DIR}/venv/bin/pip" install --quiet -r requirements.txt || true
fi
sudo -n systemctl restart "$SERVICE" || log "restart after rollback also failed"

# Post-rollback health check so we know trading resumed on the old code.
sleep 5
if curl -sf --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
    log "ROLLED BACK to ${local_sha:0:8} (healthy after rollback)"
else
    log "ROLLED BACK to ${local_sha:0:8} but service is NOT responding — manual intervention needed"
fi
exit 1
