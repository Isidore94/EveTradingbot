# 0015 — `X-Compatibility-Date` is pinned to a day that has fully passed

Date: 2026-08-18 (correction to `plan.md` §11 D2, made during Phase 0)

## Context
New-style ESI routes require an `X-Compatibility-Date` header; the legacy
`/latest/` routes were removed on 2026-02-24. `plan.md` §11 D2 originally pinned
it to `2026-08-18`, the date the plan was written.

## Decision
The pin is `2026-08-17`, and more generally **must always name a day that has
already passed everywhere**. `selftest` fails any pin that is not safely in the
past. The underlying decision — pinned, bumped deliberately, never floated — is
unchanged.

## Rationale
The original value could not be transmitted. Every route returned:

```
HTTP 400 {"error":"Compatibility date (2026-08-18) is in the future.
          Current date (UTC-11) is 2026-08-17."}
```

CCP evaluates the header against a **UTC-11** clock, so a date is "in the future"
for up to 11 hours after it begins in UTC. Pinning to "today" therefore fails for
part of every day it is set, which is worse than failing always — it would have
looked like an intermittent outage.

`selftest` enforces the property rather than the value, so the next deliberate
bump cannot reintroduce the bug.

This is recorded as a decision rather than a bug fix because it amends a locked
§11 row. The amendment is narrow: the *value* moved because the locked value was
physically unsendable, and the operator's confirmation is owed at the Phase 0
gate (`CURRENT_CHECKPOINT.md`).
