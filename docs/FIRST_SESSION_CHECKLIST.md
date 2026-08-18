# First-session checklist — clone to first digest

Every step names the command and the artifact that proves it. A green test is
not evidence that the pipeline works; each PASS below comes from inspecting a
real artifact after a real run.

This is the *setup* checklist. The **Phase 0 validation gate** — five in-game
spot-checks and the fee arithmetic against a real fill — lives in
`CURRENT_CHECKPOINT.md` and is a separate, later exercise.

Prerequisites: [uv](https://docs.astral.sh/uv/) and network access to
`esi.evetech.net` and `developers.eveonline.com`. Python is installed by uv;
nothing else is required. Total time ≈ 15 minutes, most of it the SDE download.

---

## A. Environment

```bash
uv sync
```

**PASS:** `.venv/` exists and `uv run python -c "import evescreener; print(evescreener.__version__)"`
prints a version. Runtime dependencies should be exactly `httpx`, `pandas`,
`pyarrow`, `numpy` and their transitives (decision 0014).

---

## B. Configuration

```bash
cp config.example.toml config.toml
```

Then edit `config.toml`:

- `[discord] webhook_url` — paste the channel's webhook, or leave empty. Empty
  is a supported state: digests build and archive, and delivery reports
  `unconfigured` rather than failing.
- `[esi] user_agent` — must carry a real contact address.
- `[costs] accounting_level` / `broker_relations_level` — your actual skills.

`config.toml` is gitignored and is the only place a secret lives.

**PASS:** `uv run python -m evescreener selftest` exits 0.

It checks that the config and the example have identical key sets, that the
User-Agent is descriptive, that the compatibility-date pin is safely in the past,
that the data dir is writable, and that the bar contract has not grown an `open`
column. At this point it will note that the SDE is not loaded — expected.

---

## C. Deterministic gate

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
```

**PASS:** tests fully green (the `network`-marked tests are deselected by
design), ruff clean. Run this before every commit.

Optional, and it does hit ESI:

```bash
uv run pytest -q -m network
```

Three tests that assert the live endpoints still match what `plan.md` §0
records. Run it when you suspect CCP changed something.

---

## D. Static data and the watchlist

```bash
uv run python -m evescreener ingest-history
```

First run downloads the SDE bundle (~99 MB, once per build) and loads `types`
and `marketGroups` into `state.db`, then resolves the 50-name seed watchlist and
pulls daily history for each.

**PASS:** the run prints `watchlist: 50/50 names resolved` and a bar count in
the tens of thousands. An unresolvable name is a **loud error listing every
failure** — names drift across patches, and a silently dropped name is a hole in
the screen nobody notices.

```bash
sqlite3 data/state.db "SELECT value FROM sde_meta WHERE key='sde_build';"
ls data/bars/region=10000002/
```

Reference run, 2026-08-18: SDE build 3470007, 52,863 types, 50/50 resolved,
20,192 bars, 0 partial bars carried.

---

## E. One order-book sweep

```bash
uv run python -m evescreener sweep-books
```

**PASS:** ~413 pages, ~400k orders, `duplicate order_ids 0`, and **`tokens
charged 826`** — the figure `plan.md` §3.2 predicted. Takes ~40 s.

```bash
ls data/books/region=10000002/
```

If this reports far fewer pages than ~413, check that it is not a `skipped_fresh`
run: the client refuses to refetch inside the five-minute cache window, which is
correct behaviour, not a failure.

---

## F. First digest

```bash
uv run python -m evescreener digest --dry-run     # renders and archives, posts nothing
uv run python -m evescreener digest               # posts to the webhook
```

**PASS:** the digest prints with a header, a candidate section, a measurement
table, an unpriced section, and a telemetry footer. In the footer, **`all
requests honoured Expires`**.

**Expect the candidate section to be empty**, saying "Nothing clears costs
today". That is the correct and normal output for a 50-name roster netted at
0.25B (decisions 0008, 0012), not a broken run. The measurement table below it
is where the numbers are.

```bash
tail -1 data/streams/digests.jsonl | head -c 400
```

**PASS:** the digest is archived even when delivery is unconfigured or fails.

---

## G. Compliance check

```bash
sqlite3 data/state.db "
  SELECT COUNT(*) requests, SUM(1 - honored_expiry) early_fetches,
         SUM(status >= 400) errors, MAX(ratelimit_used) peak_used
  FROM sweep_ledger;"
```

**PASS:** `early_fetches = 0` and `errors = 0`. Anything else, stop and read
`ESI_CLIENT_RUNBOOK.md` before running again — this is the one failure class in
this repo that can cost the account rather than a number.

---

## Results

| Step | Status | Date | Notes |
|---|---|---|---|
| A. Environment | | | |
| B. Configuration | | | |
| C. Deterministic gate | | | |
| D. SDE + watchlist | | | |
| E. Book sweep | | | |
| F. First digest | | | |
| G. Compliance | | | |

When A–G are all PASS the system is **running**. It is not yet
`LIVE_VALIDATED` — that requires the Phase 0 gate in `CURRENT_CHECKPOINT.md`,
which is the part only the operator, inside the game client, can do.
