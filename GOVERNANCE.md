# Governance: the Steward Charter

Prior Ledger has no owner. It has, at any moment, one or more **stewards**.

## What a steward is

A steward holds the operational credentials — repository admin, domain,
project email — and exercises them under this charter. Stewardship is a
duty of care, not a property right. The current steward is whoever last
assumed the role under [SUCCESSION.md](SUCCESSION.md).

## Duties of a steward

1. Merge entries that validate against `SCHEMA.md` and pass
   `scripts/verify.py`.
2. Keep the history intact: never rewrite, rebase, or delete published
   ledger entries, and never force-push over published history.
3. Keep at least two public mirrors of this repository alive and current.
4. Keep the domain registered and pointed at the canonical repository or a
   faithful view of it.
5. Maintain `SUCCESSION.md` so that stewardship can always pass without the
   steward's cooperation.

## What a steward may never do

- Claim ownership of the ledger or its name, or assert intellectual
  property over the ledger data.
- Relicense: `ledger/` is CC0-1.0 and code is Apache-2.0, permanently.
- Edit or remove published entries, including their own. Errors are
  superseded by later entries, never erased.
- Sell, rent, or restrict access to the ledger data.
- Represent any model, agent, person, or organization as the ledger's
  owner or voice.

## Enforcement

The charter has no court. Its enforcement is forkability: a steward who
violates it forfeits legitimacy, and any party may fork the full history
under this same charter. The legitimate continuation is the one that
preserves the history and honors this document — not the one that holds
the credentials.

## Agents

Agents may propose entries, verify the chain, and operate infrastructure
on a steward's behalf. Stewardship itself is held by whoever can answer
for it — at present, that means a human or a legal person. This clause
should be revisited by a future steward if that ever stops being the
right boundary.
