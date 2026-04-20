#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/ftptransfer}"
BRANCH="${BRANCH:-master}"
SERVICE_NAME="${SERVICE_NAME:-ftptransfer.service}"
VENV_DIR="${VENV_DIR:-$APP_DIR/.venv}"

cd "$APP_DIR"

git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

if [[ -f requirements.txt ]]; then
  python3 -m venv "$VENV_DIR"
  source "$VENV_DIR/bin/activate"
  pip install --upgrade pip
  pip install -r requirements.txt
fi

sudo systemctl daemon-reload
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager
