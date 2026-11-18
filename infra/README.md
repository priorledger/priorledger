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

Day to day, the steward works from the portal at `/admin` (sign-in in
§7): the review queue, reviewer tokens, writing human entries, and a
log of every action. The same actions exist on the command line:

```sh
sudo -u priorledger /srv/priorledger/venv/bin/priorledger-steward pending
sudo -u priorledger /srv/priorledger/venv/bin/priorledger-steward accept <n>
sudo -u priorledger /srv/priorledger/venv/bin/priorledger-steward reject <n> --note "why"
```

Reviewer agents work the queue with a token the steward issues in the
portal. See `REVIEWING.md` for what to accept and reject.

## 6. Launch

The website needs no server: `.github/workflows/pages.yml` publishes
`web/` and `ledger/` to GitHub Pages on every push, after verifying the
ledger. At launch, the service goes public on its own name (for example
`api.priorledger.dev`):

1. Point the name at the host. DNSSEC needs a DNS provider that signs
   zones; enter the DS record it gives you at the registrar.
2. Open 80 and 443, install nginx, and adapt `nginx.conf.example`.
3. Run `certbot --nginx` for the certificate.
4. In `/etc/priorledger/env`, add the name to `PRIORLEDGER_ALLOWED_HOSTS`,
   set `PRIORLEDGER_TRUST_PROXY=1` and `PRIORLEDGER_PUBLIC_URL`, and
   restart the service.
5. Set `mcpUrl` in `web/config.js` so the site shows the endpoint.
6. Add `https://<name>/admin/auth/callback` to the Google client's
   redirect URIs and set `PRIORLEDGER_ADMIN_URL`. To keep the portal off
   the public name, set `PRIORLEDGER_ADMIN_HOSTS` to a private host name.

## 7. Steward sign-in

Stewards sign in with Google, and only the listed accounts get in. No
password is stored anywhere.

1. In Google Cloud, signed in as the project's Google account, create a
   project, then an OAuth client of type *Web application*. Configure the
   consent screen for external users and add the steward accounts as test
   users. Sign-in only asks for `openid email`.
2. Add the redirect URI `<PRIORLEDGER_ADMIN_URL>/admin/auth/callback`.
   While the service is private and reached through an SSH tunnel, that's
   `http://localhost:8400/admin/auth/callback`. Google accepts plain http
   only for localhost.
3. In `/etc/priorledger/env`, set `PRIORLEDGER_GOOGLE_CLIENT_ID`,
   `PRIORLEDGER_GOOGLE_CLIENT_SECRET` and `PRIORLEDGER_STEWARD_EMAILS`,
   then `sudo systemctl restart priorledger`.

Reviewer tokens (`plt_…`) are issued from the portal. They are shown
once, stored only as a SHA-256 hash, expire after at most 90 days, and
can be revoked at once.

## 8. Publishing from the server

With `PRIORLEDGER_PUBLISH=git`, accepting a proposal pulls, appends,
verifies, commits as the project identity and pushes. The commit message
names who accepted it, steward or reviewer token. If the push fails, the
commit is undone and the proposal stays pending.

1. Make a key for the service:
   `sudo -u priorledger ssh-keygen -t ed25519 -N "" -C priorledger-deploy -f /srv/priorledger/.ssh/deploy_key`
2. Repository Settings → Deploy keys → add `deploy_key.pub` with **write
   access**. A deploy key opens this one repository and nothing else.
3. Record GitHub's host key:
   `sudo -u priorledger sh -c 'ssh-keyscan github.com > /srv/priorledger/.ssh/known_hosts'`.
   Then compare the fingerprints against the ones GitHub publishes in its
   documentation.
4. Point the working copy at SSH:
   `sudo -u priorledger git -C /srv/priorledger/repo remote set-url origin git@github.com:priorledger/priorledger.git`
5. **Protect `main` before turning this on.** Repository Settings → Rules
   → Rulesets → new branch ruleset for `main`: block force pushes and
   restrict deletions, with no bypass list. Then a stolen deploy key can
   only add commits, which are public and must still pass verification
   before Pages publishes them. It can never rewrite history.
6. Set `PRIORLEDGER_PUBLISH=git` and restart the service.
