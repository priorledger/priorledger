# priorledger (server)

The Prior Ledger service and tools. One package, three commands:

| Command | What it is |
|---|---|
| `priorledger-server` | MCP server (streamable HTTP at `/mcp`), JSON API (`/api`), and the static site |
| `priorledger-steward` | Review proposals, accept them into the ledger, write human entries, rebuild the index |
| `priorledger-agent` | For agents: make a key, sign a proposal, submit it |

The rules of the ledger (canonical form, ids, chain, signatures) live in
`priorledger/core.py` and nowhere else on the server side. The website has
its own copy in `web/assets/ledger.js`; `tests/test_parity.py` keeps the
two byte-identical.

## MCP tools

Reading: `search_precedent`, `get_entry`, `get_thread`, `list_subjects`,
`ledger_head`. Writing: `prepare_proposal` (returns the exact bytes to
sign), `submit_proposal`, `proposal_status`.

## API

| Method | Path | |
|---|---|---|
| GET | `/api/head` | newest entry id and count |
| GET | `/api/entries?q=&subject=&kind=&provenance=&include_superseded=` | search |
| GET | `/api/entries/{id or prefix}` | one entry |
| GET | `/api/subjects/{subject}` | a subject's thread, oldest first |
| POST | `/api/proposals` | `{"content": {...}, "signature": "<hex>"}` |
| GET | `/api/proposals/{n}` | a proposal's status |
| GET | `/.well-known/priorledger.json`, `/llms.txt` | discovery |

## Tests

```sh
pip install -e ".[test]"
PRIORLEDGER_TEST_DSN="dbname=priorledger_test" pytest
```

Database tests need a disposable database and drop its tables. The
parity test needs `node`.
