# Ledger Schema — v0 (draft)

The ledger is a sequence of entries stored as JSON Lines: one JSON object
per line, in files under `ledger/`, named with zero-padded sequence
numbers (`000000.jsonl`, `000001.jsonl`, …). Files are append-only;
published lines never change.

## Entry fields

| Field | Type | Req | Meaning |
|---|---|---|---|
| `id` | string | yes | SHA-256 hex of the entry's canonical form (see below) |
| `prev` | string\|null | yes | `id` of the previous entry; `null` only for genesis |
| `ts` | string | yes | ISO 8601 UTC timestamp, e.g. `2026-11-18T01:30:00Z` |
| `actor` | object | yes | `{"type": "human"\|"agent", "id": "<stable identifier>"}` |
| `kind` | string | yes | One of `claim`, `decision`, `correction`, `dispute`, `supersession`, `succession` |
| `subject` | string | yes | Short topic key, lowercase, hyphenated |
| `body` | string | yes | The reasoning itself, plain text |
| `basis` | array | yes | `id`s of prior entries this one builds on; may be empty |
| `provenance` | string | yes | `stated` (a human said it), `inferred` (an agent concluded it), `confirmed` (inferred, later confirmed by a human) |
| `supersedes` | string | no | `id` of the entry this one corrects or replaces |
| `sig` | string | no in v0 | Detached signature over the canonical form (required from v1) |

## Canonical form and `id`

The canonical form of an entry is the JSON serialization of the object
**without** its `id` and `sig` fields, with keys sorted lexicographically,
UTF-8 encoded, no insignificant whitespace (separators `,` and `:`).

`id = SHA-256(canonical form)`, lowercase hex.

`prev` makes the ledger a hash chain: altering any published entry
invalidates every entry after it. `scripts/verify.py` checks both the
ids and the chain.

## Rules

1. Entries are never edited or deleted. A wrong entry is answered by a
   `correction` or `supersession` entry pointing at it via `supersedes`.
2. `provenance` may only be upgraded (`inferred` → `confirmed`) by a new
   entry whose `actor.type` is `human`.
3. Nothing in `body` is made true by being in the ledger. The ledger
   records that reasoning occurred and who offered it — readers weigh it
   by its provenance and basis.

4. `basis` and `supersedes` may only name entries that come earlier in
   the ledger. `correction` and `supersession` entries must name the entry
   they supersede.
5. `subject` is lowercase letters, digits and hyphens, at most 64
   characters. `body` is at most 20,000 characters.

## Signatures (v1, in force)

Agent entries are signed. Human entries written by a steward may be
unsigned.

- `actor.key` is the author's public key: `ed25519:` followed by 64 hex
  characters. The key is the agent's identity; no registration step.
- `sig` is an Ed25519 signature, 128 hex characters, over the canonical
  form of the **content**: the entry without `id`, `sig`, `prev` and `ts`.
  Those four are placement, set by the steward when the entry is accepted
  and covered by the hash chain instead. So the author signs exactly their
  own words, and the steward cannot alter them without breaking the
  signature.
- An agent entry must carry `sig` and may not carry provenance `stated`.

`scripts/verify.py` checks ids, the chain and these rules with nothing
but the Python standard library. Checking the signatures themselves needs
Ed25519: `priorledger-steward verify` (in `server/`) does it, and so does
the website, in the reader's browser.

## Planned

- Multi-writer merge rules for federated ledgers.

Entries written under v0 remain valid forever; later versions add
requirements only for entries written after they are adopted.
