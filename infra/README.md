# Infrastructure

Every server in this project is a disposable view of the repository.
This file is how a steward — or a successor — rebuilds one from nothing.

## Host baseline

Any Debian 13 (or similar) machine with PostgreSQL. The ledger needs
little: 1 vCPU, 4 GB RAM, and 50 GB of disk last for years.

## 1. Dedicated system user

The ledger runs as its own user, with no login shell and no sudo.

```sh
sudo adduser --system --group --home /srv/priorledger --shell /usr/sbin/nologin priorledger
sudo chmod 750 /srv/priorledger
sudo -u priorledger git clone https://github.com/priorledger/priorledger.git /srv/priorledger/repo
sudo -u priorledger python3 /srv/priorledger/repo/scripts/verify.py /srv/priorledger/repo/ledger/
```

The verify step must print `OK` before anything else is built on top.

## 2. Database

The role logs in through the operating system user (peer authentication),
so no password exists to leak. No other role may connect.

```sh
sudo -u postgres psql <<'SQL'
CREATE ROLE priorledger LOGIN;
CREATE DATABASE priorledger OWNER priorledger;
REVOKE ALL ON DATABASE priorledger FROM PUBLIC;
SQL
```

The database is only an index over `ledger/`. It can be dropped and
rebuilt from the repository at any time; it is never the source of truth.

## 3. Network

Default deny incoming. During development the service is reachable only
over a private network. Public ports 80 and 443 open only at launch,
together with a TLS certificate — `.dev` domains are HTTPS-only in every
major browser, so the site cannot be served without one.

```sh
sudo ufw default deny incoming
sudo ufw default allow outgoing
# administrative access over a private network only, e.g.:
# sudo ufw allow in on tailscale0
# at launch:
# sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
```

## Not yet here

The MCP server, its service unit, and the nginx and TLS configuration
will be added as they are built.
