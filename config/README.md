# `config/` — committed data, never secrets

## `anchors.jsonl`

The anchor calendar (plan.md §11 D7). One JSON object per line:

```json
{"date": "2026-06-09", "label": "Summer expansion", "scope": "global", "confirmed": true, "source": "patch notes"}
```

- `scope` is `global` or a `market_group_id` (as a string) whose subtree the
  anchor applies to.
- `confirmed: false` marks a **candidate**. The patch-notes watcher may append
  candidates; it may never anchor. Only the operator flips `confirmed` to
  `true`, and only a confirmed anchor is used by the signal layer.

**Every seeded row currently ships as `confirmed: false` on purpose.** These
dates are placeholders standing in for the patch calendar the operator
actually considers live; anchoring bands to dates nobody verified would be
exactly the kind of fabricated confirmation this system exists to avoid.
Seeding the real dates is the Phase 2 gate item.

Secrets never live here. `config.toml` at the repo root is gitignored and
holds the Discord webhook.
