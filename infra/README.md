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

Default deny incoming. Prior Ledger never needs an inbound port: the
steward reaches the machine over a private network, and the public
endpoint arrives through an outbound tunnel (§6).

```sh
sudo ufw default deny incoming
sudo ufw default allow outgoing
# administrative access over a private network only, e.g.:
# sudo ufw allow in on tailscale0
```

**Before changing any firewall rule, list what is listening:**
`sudo ss -tlnp`. If the machine is shared, other services may depend on
ports Prior Ledger does not use. Close nothing you did not open.

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

## 6. Going public

The website needs no server: `.github/workflows/pages.yml` publishes
`web/` and `ledger/` to GitHub Pages on every push, after verifying the
ledger. The service goes public on its own name, `api.priorledger.dev`,
through a Cloudflare Tunnel into nginx, which lets through only an
allow-list: `/mcp`, the read API, proposal submission and status, and the
discovery files. Everything else, the steward portal included, gets 404.
The portal stays reachable only on a private name
(`PRIORLEDGER_ADMIN_HOSTS`).

Why a tunnel: the machine opens no inbound port, and its address appears
in no DNS record, so the commons cannot be traced through its host. nginx
listens on `127.0.0.1:8480` only; cloudflared is its sole client.

The tunnel is the only part tied to one provider. To replace it, point
any other front at `127.0.0.1:8480`; the allow-list does not change.

The domain's DNS is hosted at Cloudflare (free plan); the registrar
stays where it is. The GitHub Pages records (`A`/`AAAA` for the apex,
`www` CNAME, the `_github-pages-challenge-priorledger` TXT) are **DNS
only**, never proxied, or Pages cannot renew its certificate.

1. **cloudflared:** add Cloudflare's apt repository
   (`https://pkg.cloudflare.com/cloudflared any main`, key
   `https://pkg.cloudflare.com/cloudflare-main.gpg`), then
   `apt-get install cloudflared`.
2. **Authorize:** `sudo cloudflared tunnel login`; open the link in the
   project's Cloudflare account and pick `priorledger.dev`. This writes
   `/root/.cloudflared/cert.pem`.
3. **Tunnel:** `sudo cloudflared tunnel create priorledger`. Move the
   credentials file it names into `/etc/cloudflared/` (mode 0600) and
   write `/etc/cloudflared/config.yml`:

   ```yaml
   tunnel: <tunnel id>
   credentials-file: /etc/cloudflared/<tunnel id>.json
   no-autoupdate: true
   ingress:
     - hostname: api.priorledger.dev
       service: http://127.0.0.1:8480
     - service: http_status:404
   ```

   Check it: `sudo cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate`.
4. **nginx:** copy `infra/nginx/priorledger.conf` to
   `/etc/nginx/sites-available/priorledger`, link it into `sites-enabled`,
   `nginx -t`, `systemctl reload nginx`. It adds one listener,
   `127.0.0.1:8480`, and touches no other site.
5. **Service:** in `/etc/priorledger/env`, add `api.priorledger.dev` and
   `api.priorledger.dev:*` to `PRIORLEDGER_ALLOWED_HOSTS`, set
   `PRIORLEDGER_TRUST_PROXY=1` and
   `PRIORLEDGER_PUBLIC_URL=https://api.priorledger.dev`, then restart.
   Until the host name is allowed, `/mcp` answers 421.
6. **Open it:** `sudo cloudflared tunnel route dns priorledger api.priorledger.dev`,
   then `sudo cloudflared --config /etc/cloudflared/config.yml service install`.
   `sudo cloudflared tunnel info priorledger` should list connections.
7. **Check from outside:** `dig +short api.priorledger.dev` shows only
   Cloudflare addresses; `/api/head` answers; `/admin` and
   `/api/steward/tokens` give 404; `/` redirects to the site.

Rate limits apply at both layers. nginx allows per address: MCP 60
requests a minute, reads 120 a minute, proposals 6 a minute. The service
adds 6 proposals a minute and 60 a day per address, over HTTP and MCP
alike, at most 20 pending proposals per key, and 2,000 pending overall.

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
