#!/bin/sh
# Install or update the Prior Ledger service on a Debian host.
# Assumes infra/README.md steps 1–3 are done: the priorledger user, the clone at
# /srv/priorledger/repo, and the priorledger database. Safe to run again.
set -eu

REPO=/srv/priorledger/repo
VENV=/srv/priorledger/venv

if [ "$(id -u)" -ne 0 ]; then echo "run with sudo" >&2; exit 1; fi
if [ ! -d "$REPO/.git" ]; then echo "no clone at $REPO; see infra/README.md" >&2; exit 1; fi

if ! python3 -c "import venv, ensurepip" 2>/dev/null; then
  apt-get install -y python3-venv
fi

echo "verifying the ledger"
sudo -u priorledger python3 "$REPO/scripts/verify.py" "$REPO/ledger/"

echo "installing the server into $VENV"
[ -d "$VENV" ] || sudo -u priorledger python3 -m venv "$VENV"
sudo -u priorledger "$VENV/bin/pip" install --quiet --upgrade pip
sudo -u priorledger "$VENV/bin/pip" install --quiet "$REPO/server"

if [ ! -f /etc/priorledger/env ]; then
  install -d -m 0750 -o root -g priorledger /etc/priorledger
  install -m 0640 -o root -g priorledger "$REPO/infra/env.example" /etc/priorledger/env
  echo "wrote /etc/priorledger/env from the example; review it"
fi

if grep -q '^PRIORLEDGER_SECRET=$' /etc/priorledger/env; then
  sed -i "s/^PRIORLEDGER_SECRET=$/PRIORLEDGER_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')/" /etc/priorledger/env
  echo "generated the session secret"
fi

install -m 0644 "$REPO/infra/priorledger.service" /etc/systemd/system/priorledger.service
systemctl daemon-reload
systemctl enable --quiet priorledger
systemctl restart priorledger
sleep 2
systemctl --no-pager --lines=5 status priorledger
curl -fsS "http://127.0.0.1:$(sed -n 's/^PRIORLEDGER_PORT=//p' /etc/priorledger/env)/api/head" && echo
