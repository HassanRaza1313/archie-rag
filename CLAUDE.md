# Archie RAG

## Code

- Reuse before writing.
- Make the smallest complete change, including the callers and tests it breaks. Nothing speculative; deleting beats adding.
- Fix bugs at the root, in shared code.
- Never cut validation at trust boundaries, data-loss handling, security or accessibility.
- New logic gets a test.
- A known shortcut gets a `shortcut: <limit>, <when to upgrade>` comment.
- Comments explain why, not what.
- Run `/simplify` before a PR.
