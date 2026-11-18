# Succession

Stewardship of Prior Ledger passes by proof of knowledge, not by
permission. This file is the mechanism.

## The commitment

The founding steward holds a secret phrase. Its SHA-256 hash is published
here; the phrase itself is published nowhere.

```
COMMITMENT: d5e5431cba115e8991d5a59e91319f2ffb15dcafbefc151335c60bb7dd8e2eff
```

To compute a commitment: `printf '%s' "the secret" | sha256sum`

## Claiming stewardship

A claimant who holds the secret proves it by publishing the preimage —
as a signed commit to a fork of this repository, or an issue on the
canonical repository — together with a statement of assumption of the
charter in [GOVERNANCE.md](GOVERNANCE.md).

Verification, by anyone: `sha256(published preimage) == COMMITMENT`.
No authority needs to approve the claim; the hash is the authority.

A successful claimant becomes steward and MUST, as their first acts:

1. Rotate all operational credentials they can reach.
2. Publish a new `COMMITMENT` (their own secret) in this file.
3. Record the succession as a ledger entry.

## Credential escrow (steward's duty)

The secret alone proves succession; it does not open accounts. The
steward keeps the operational credentials — project email recovery codes,
registrar access, repository admin — recoverable by a successor, by
splitting them with Shamir secret sharing (recommended: 2-of-3 shares in
separate physical locations, the secret phrase among the protected
material). The steward maintains this escrow; this file deliberately does
not say where the shares are.

## Abandonment clause

If no steward activity — no commit, merge, release, or signed statement —
occurs on the canonical repository for **24 consecutive months**, the
ledger is abandoned. Any party may then assume stewardship without the
secret, by forking the full history, publishing their own `COMMITMENT`,
and assuming the charter publicly. The earliest such public claim has
legitimacy. Forkability, not the secret, is the final guarantee: the
secret lets a chosen successor take over the *canonical* home; the
abandonment clause ensures the ledger survives even if every secret is
lost.

## What succession is not

Succession transfers duty, never ownership. See GOVERNANCE.md: the
steward maintains the ledger and may not own it. There is nothing here
to inherit except work.
