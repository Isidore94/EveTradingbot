# 0010 — Rank on the depth walk and `p5_price`, never on best price

Date: 2026-08-18

## Context
A one-unit sell order priced at 10× fair value, or a wall priced to lure, makes
any naive top-of-book screen buy garbage. The community defence is Fuzzwork's
5%-percentile: the volume-weighted mean price of the best 5% of resting volume,
robust to any *single* small bait order by construction.

That defence has a known hole: in a thin book, the bait *is* the top 5% — and
thin books are exactly where margins look widest.

## Decision
Never rank on `best_price`. Every `book_summary` row carries, and the screen
ranks on, the **depth-walk fill price at real ISK notionals** (ADR 0008), with
`p5_price` as a secondary read. `top_order_volume_share` is carried and flagged
above 0.5, and `order_count` floors are applied. `best_price` is displayed for
in-game cross-checking and is explicitly labelled as not the ranking input.

Crossed region-wide books (best bid above best ask) are flagged, not shown as a
spread.

## Rationale
The depth walk **prices the bait in and dilutes it** — it is not a filter that
might miss a case, it is arithmetic that cannot be fooled by a small order,
because a small order contributes its own tiny weight to the walk and nothing
more. That closes the hole `p5_price` leaves open, which is why both are carried
rather than either alone.

Phase 0 produced the worked example. Zydrine in The Forge: best ask 1,000 ISK
against a 0.25B depth walk of 1,198 ISK, with 95% of the bid volume resting in a
single order. The bait moved the top of book by 17% and the netted entry price
by nothing. Across the region, 185 of 16,699 types with both sides quoted showed
a crossed book (1.1%) — enough that a top-of-book screener would surface a
handful of pure fictions on any given day.
