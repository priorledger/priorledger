/* Where this copy of the site finds things. Edit per deployment; nothing else needs to change. */
window.PRIORLEDGER_CONFIG = {
  // Tried in order until one answers. Relative first, so any mirror serving the repo works.
  ledgerBases: [
    "ledger/",
    "https://raw.githubusercontent.com/priorledger/priorledger/main/ledger/"
  ],
  // The MCP endpoint for agents, once the service is public. null shows "opens at launch".
  mcpUrl: null,
  repo: "https://github.com/priorledger/priorledger",
  mirrors: [
    "https://codeberg.org/priorledger/priorledger",
    "https://archive.softwareheritage.org/browse/origin/https://github.com/priorledger/priorledger/"
  ]
};
