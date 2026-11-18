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

## 4. The service

```sh
sudo sh /srv/priorledger/repo/infra/install.sh
```

The script verifies the ledger, installs `server/` into a virtualenv
owned by the `priorledger` user, writes `/etc/priorledger/env` from
`env.example` on first run, and starts `priorledger.service`. The unit
runs as the `priorledger` user, can write only `/srv/priorledger`, and
listens on `127.0.0.1:8400`. Running the script again updates the service.

To reach it while it's private, tunnel to the box:
`ssh -L 8400:127.0.0.1:8400 <host>`, then open `http://localhost:8400`.

## 5. Stewarding

```sh
sudo -u priorledger /srv/priorledger/venv/bin/priorledger-steward pending
sudo -u priorledger /srv/priorledger/venv/bin/priorledger-steward accept <n>
sudo -u priorledger /srv/priorledger/venv/bin/priorledger-steward reject <n> --note "why"
```

Accepting appends to the working copy and rebuilds the index. It never
commits. The steward reviews `git diff` in `/srv/priorledger/repo`, then
commits and pushes as the project identity. A GitHub deploy key with
write access, held by the `priorledger` user and scoped to this one
repository, is the intended way to push from the box.

## 6. Launch

The website needs no server: `.github/workflows/pages.yml` publishes
`web/` and `ledger/` to GitHub Pages on every push, after verifying the
ledger. At launch, the service goes public on its own name (for example
`api.priorledger.dev`):

1. Point the name at the host and turn on DNSSEC.
2. Open 80 and 443, install nginx, and adapt `nginx.conf.example`.
3. Run `certbot --nginx` for the certificate.
4. In `/etc/priorledger/env`, add the name to `PRIORLEDGER_ALLOWED_HOSTS`,
   set `PRIORLEDGER_TRUST_PROXY=1` and `PRIORLEDGER_PUBLIC_URL`, and
   restart the service.
5. Set `mcpUrl` in `web/config.js` so the site shows the endpoint.
