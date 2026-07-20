#!/bin/bash
#
# deploy-ubuntu.sh
# One-shot-ish deployment helper for TradeWiser on Ubuntu.
# Run as root or with sudo.
#
# Usage:
#   sudo ./scripts/deploy-ubuntu.sh
#   sudo ./scripts/deploy-ubuntu.sh --update
#

set -euo pipefail

APP_DIR="/opt/tradewiser"
ETC_DIR="/etc/tradewiser"
USER="tradewiser"
SERVICE_NAME="tradewiser"
REPO_URL="${REPO_URL:-}"   # set if cloning from git

log() { echo -e "\033[1;32m[INFO]\033[0m $*"; }
warn() { echo -e "\033[1;33m[WARN]\033[0m $*"; }
err() { echo -e "\033[1;31m[ERROR]\033[0m $*"; exit 1; }

require_root() {
    if [[ $EUID -ne 0 ]]; then
        err "Please run as root or with sudo"
    fi
}

install_deps() {
    log "Installing system dependencies..."
    apt-get update -y
    apt-get install -y python3 python3-venv python3-pip git curl nginx
}

create_user() {
    if ! id "$USER" &>/dev/null; then
        log "Creating system user $USER..."
        useradd --system --create-home --shell /usr/sbin/nologin "$USER"
    fi
    mkdir -p "$APP_DIR" "$ETC_DIR"
    chown -R "$USER:$USER" "$APP_DIR"
    chmod 750 "$APP_DIR"
    chmod 700 "$ETC_DIR"
}

clone_or_update_code() {
    if [[ -n "$REPO_URL" ]]; then
        if [[ ! -d "$APP_DIR/.git" ]]; then
            log "Cloning repository..."
            git clone "$REPO_URL" "$APP_DIR"
        else
            log "Updating repository..."
            pushd "$APP_DIR" >/dev/null
            sudo -u "$USER" git pull --ff-only
            popd >/dev/null
        fi
    else
        warn "REPO_URL not set. Assuming code is already present in $APP_DIR"
        if [[ ! -d "$APP_DIR/app" ]]; then
            err "No app/ directory found. Set REPO_URL or copy code first."
        fi
    fi
    chown -R "$USER:$USER" "$APP_DIR"
}

setup_venv() {
    log "Setting up Python virtual environment..."
    pushd "$APP_DIR" >/dev/null
    if [[ ! -d venv ]]; then
        sudo -u "$USER" python3 -m venv venv
    fi
    sudo -u "$USER" ./venv/bin/pip install --upgrade pip
    sudo -u "$USER" ./venv/bin/pip install -r requirements.txt
    popd >/dev/null
}

setup_env() {
    if [[ ! -f "$ETC_DIR/.env" ]]; then
        if [[ -f "$APP_DIR/sample.env" ]]; then
            log "Copying sample.env to $ETC_DIR/.env"
            cp "$APP_DIR/sample.env" "$ETC_DIR/.env"
        else
            warn "No sample.env found. Creating empty $ETC_DIR/.env"
            touch "$ETC_DIR/.env"
        fi
        chown "$USER:$USER" "$ETC_DIR/.env"
        chmod 600 "$ETC_DIR/.env"
        warn "==> Edit $ETC_DIR/.env with your real credentials before starting the service!"
    else
        log "$ETC_DIR/.env already exists"
    fi
}

install_systemd() {
    log "Installing systemd service..."
    cp "$APP_DIR/systemd/tradewiser.service" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
}

install_nginx() {
    log "Installing nginx config (optional)..."
    if [[ -f "$APP_DIR/nginx/tradewiser.conf" ]]; then
        cp "$APP_DIR/nginx/tradewiser.conf" /etc/nginx/sites-available/tradewiser
        ln -sf /etc/nginx/sites-available/tradewiser /etc/nginx/sites-enabled/tradewiser
        rm -f /etc/nginx/sites-enabled/default || true
        nginx -t && systemctl reload nginx || warn "Nginx reload failed - check config"
        log "Nginx config installed. Edit /etc/nginx/sites-available/tradewiser as needed."
    else
        warn "nginx/tradewiser.conf not found in repo"
    fi
}

start_service() {
    log "Starting $SERVICE_NAME service..."
    systemctl start "$SERVICE_NAME" || true
    systemctl status "$SERVICE_NAME" --no-pager || true
}

show_next_steps() {
    cat <<EOF

✅ Deployment steps completed.

Next steps:
  1. Edit your secrets:
     sudo nano $ETC_DIR/.env

  2. Start/restart the service:
     sudo systemctl restart $SERVICE_NAME
     sudo journalctl -u $SERVICE_NAME -f

  3. Test:
     curl http://localhost:8000/health/
     curl -H "X-API-Key: YOUR_BOT_API_KEY" http://localhost:8000/trades/strategy/status

  4. (Optional) Configure nginx + HTTPS:
     - Edit /etc/nginx/sites-available/tradewiser
     - sudo nginx -t && sudo systemctl reload nginx

  5. For gunicorn (production):
     - cd $APP_DIR
     - sudo -u $USER ./venv/bin/pip install gunicorn
     - Edit /etc/systemd/system/$SERVICE_NAME.service (uncomment gunicorn ExecStart)
     - sudo systemctl daemon-reload && sudo systemctl restart $SERVICE_NAME

EOF
}

main() {
    require_root

    if [[ "${1:-}" == "--update" ]]; then
        log "Update mode"
        clone_or_update_code
        setup_venv
        systemctl restart "$SERVICE_NAME" || true
        show_next_steps
        return
    fi

    install_deps
    create_user
    clone_or_update_code
    setup_venv
    setup_env
    install_systemd
    install_nginx
    start_service
    show_next_steps
}

main "$@"
