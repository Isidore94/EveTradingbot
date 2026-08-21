"""The ported desk workflow: watchlist, per-type brief, observation board.

These surfaces are observation, never opportunity (plan.md §18): the board
shows types that do NOT clear costs with their friction printed, the brief
never calls itself a pick, and the watchlist renders every name every day.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from evescreener.brief import (
    build_board,
    build_brief,
    format_isk,
    render_board,
    render_brief,
    watchlist_summary,
)
from evescreener.cli import HANDLERS, build_parser
from evescreener.digest import build_digest
from evescreener.screen import run_screen
from evescreener.signals.composite import build_composite
from evescreener.universe import add_watch, remove_watch, watchlist_entries

NOW = datetime(2026, 8, 20, 16, 0, tzinfo=UTC)


def bars_for(type_ids, *, bars=200, dip_at=None, flat=False, seed=9):
    rng = np.random.default_rng(seed)
    stamps = pd.date_range("2026-01-01 11:00", periods=bars, freq="D", tz="UTC")
    rows = []
    for offset, type_id in enumerate(type_ids):
        if flat:
            close = np.full(bars, 1_000_000.0)
        else:
            close = 1_000_000 * np.exp(np.cumsum(rng.normal(0.0, 0.01, bars)))
            if dip_at is not None:
                close[dip_at:] = close[dip_at:] * 0.75
        for index, stamp in enumerate(stamps):
            rows.append(
                {
                    "type_id": type_id,
                    "region_id": 10000002,
                    "datetime": stamp,
                    "high": close[index] * 1.01,
                    "low": close[index] * 0.99,
                    "close": close[index],
                    "volume": 100_000.0 + offset,
                    "order_count": 400,
                    "isk_value": close[index] * 100_000.0,
                    "fetched_at": "2026-08-20T00:00:00+00:00",
                }
            )
    return pd.DataFrame(rows)


def book_for(type_ids, *, sweep=NOW, ask=760_000.0, bid=740_000.0):
    rows = []
    for type_id in type_ids:
        for side, fill in (("sell", ask), ("buy", bid)):
            row = {
                "type_id": type_id,
                "region_id": 10000002,
                "side": side,
                "sweep_ts": sweep.isoformat(),
                "expires_ts": None,
                "best_price": fill,
                "total_volume": 1e9,
                "order_count": 40,
                "p5_price": fill,
                "top_order_volume_share": 0.05,
                "station_volume_share": 1.0,
                "partial_sweep": False,
                # R1/S2: a fixture must say where its quotes rested, or every
                # pricing path correctly refuses it.
                "best_location_id": 60003760,
                "exec_location_id": 60003760,
                "exec_price": fill,
                "exec_is_structure": False,
            }
            for index in range(3):
                row[f"depth_fill_price_{index}"] = fill
                row[f"depth_fill_qty_{index}"] = 1e6
            rows.append(row)
    return pd.DataFrame(rows)


@pytest.fixture
def seeded_db(db):
    db.replace_types([(tid, f"Type {tid}", 1857, 1.0, 1.0, 1) for tid in range(34, 50)])
    return db


# -- the watchlist ------------------------------------------------------------


def test_watch_add_list_remove_round_trip(seeded_db):
    record = add_watch(seeded_db, name="Type 34", type_id=34, note="minerals I know")
    assert record["type_id"] == 34
    assert record["note"] == "minerals I know"
    names = [row["name"] for row in watchlist_entries(seeded_db)]
    assert "Type 34" in names
    assert remove_watch(seeded_db, "Type 34") is True
    assert remove_watch(seeded_db, "Type 34") is False, "a second removal has nothing to remove"


def test_re_adding_a_name_updates_never_duplicates(seeded_db):
    add_watch(seeded_db, name="Type 35", type_id=35)
    add_watch(seeded_db, name="Type 35", type_id=35, note="second thoughts")
    entries = [row for row in watchlist_entries(seeded_db) if row["name"] == "Type 35"]
    assert len(entries) == 1
    assert entries[0]["note"] == "second thoughts"


def test_watch_add_with_an_unresolvable_name_is_a_loud_exit(tmp_path, monkeypatch, capsys):
    from evescreener.cli import main

    monkeypatch.setenv("EVESCREENER_DATA_DIR", str(tmp_path / "data"))
    code = main(["--example-config", "watch", "add", "--name", "Nonexistent Widget"])
    assert code == 2
    assert "no type named" in capsys.readouterr().err


# -- the brief ----------------------------------------------------------------


def test_brief_reads_one_type_fully(config, seeded_db):
    ids = list(range(34, 44))
    bars = bars_for(ids, dip_at=150)
    composite = build_composite(bars_for(ids, seed=2), members=10, min_members=5)
    brief = build_brief(
        config, seeded_db, bars[bars["type_id"] == 34], composite.frame, book_for([34]), 34, now=NOW
    )
    assert brief.type_name == "Type 34"
    assert brief.bars == 200
    assert brief.close is not None
    assert brief.band_zone != "UNKNOWN"
    assert set(brief.gates) == {
        "below_anchored_value",
        "relative_strength_intact",
        "participation_intact",
        "measurable",
    }
    assert brief.freshness == "fresh"
    assert len(brief.tier_costs) == 3
    assert brief.friction_pct is not None and brief.friction_pct > 0


def test_stale_bars_turn_every_gate_unknown_however_fresh_the_book(config, seeded_db):
    """§21 R2: freshness was read off the book, so old bars looked current.

    These fixture bars end 2026-07-19 while NOW is 2026-08-20 — 32 completed
    days behind — against a book swept moments ago.
    """
    bars = bars_for([34])
    brief = build_brief(config, seeded_db, bars, None, book_for([34]), 34, now=NOW)
    assert brief.freshness == "fresh", "the book really is current"
    assert brief.bar_freshness == "stale", "the bars really are not"
    assert brief.bar_age_days is not None and brief.bar_age_days > 3
    assert set(brief.gates.values()) == {"UNKNOWN"}, "no gate survives a stale bar"
    assert any("behind" in flag for flag in brief.flags)


def test_current_bars_let_the_gates_be_decided(config, seeded_db):
    """The downgrade must not be unconditional, or it would say nothing."""
    from evescreener.timeutil import iso, last_completed_bar_date

    bars = bars_for([34])
    # Re-date the same series so its newest bar is the last completed EVE day.
    span = pd.date_range(end=last_completed_bar_date(NOW), periods=200, freq="D", tz="UTC")
    bars = bars.copy()
    bars["datetime"] = [stamp.replace(hour=11) for stamp in span]
    bars["fetched_at"] = iso(NOW)
    brief = build_brief(config, seeded_db, bars, None, book_for([34]), 34, now=NOW)
    assert brief.bar_freshness == "fresh"
    assert brief.bar_age_days == 0
    assert set(brief.gates.values()) != {"UNKNOWN"}


def test_brief_on_a_stale_book_is_unknown_not_priced(config, seeded_db):
    bars = bars_for([34])
    stale = book_for([34], sweep=NOW - timedelta(hours=6))
    brief = build_brief(config, seeded_db, bars, None, stale, 34, now=NOW)
    assert brief.freshness == "stale"
    assert brief.friction_pct is None
    assert all(not tier["fillable"] for tier in brief.tier_costs)


def test_brief_with_no_bars_says_what_to_run(config, seeded_db):
    brief = build_brief(config, seeded_db, pd.DataFrame(), None, pd.DataFrame(), 34, now=NOW)
    assert brief.bars == 0
    assert "ingest-history" in brief.note
    assert "ingest-history" in render_brief(brief)


def test_render_brief_is_an_observation_not_a_pick(config, seeded_db):
    bars = bars_for([34], dip_at=150)
    composite = build_composite(bars_for(list(range(34, 44)), seed=2), members=10, min_members=5)
    text = render_brief(
        build_brief(config, seeded_db, bars, composite.frame, book_for([34]), 34, now=NOW)
    )
    assert "observation, not a pick" in text
    assert "gates:" in text
    assert "breakeven" in text


# -- the board ----------------------------------------------------------------


def test_board_shows_types_that_do_not_clear_costs(config, seeded_db):
    """The screen hides what cannot clear costs; the board deliberately does not."""
    ids = list(range(34, 40))
    bars = bars_for(ids, dip_at=150)
    composite = build_composite(bars_for(ids, seed=2), members=10, min_members=5)
    wide = book_for(ids, ask=1_400_000.0, bid=700_000.0)
    screen = run_screen(config, seeded_db, bars, composite, wide, now=NOW)
    assert screen.candidates == [], "the wide spread must clear the screen"
    board = build_board(config, seeded_db, bars, composite.frame, wide, now=NOW)
    assert board.rows, "the same types must still be on the board"
    assert all(row["friction_pct"] > 20 for row in board.rows if row["friction_pct"] is not None)


def test_board_sorts_by_value_with_blanks_at_the_bottom(config, seeded_db):
    ids = list(range(34, 40))
    bars = pd.concat([bars_for(ids, dip_at=150), bars_for([44], flat=True)])
    composite = build_composite(bars_for(ids, seed=2), members=10, min_members=5)
    board = build_board(
        config, seeded_db, bars, composite.frame, book_for(ids + [44]), now=NOW, top=50
    )
    dips = [row["dip_sigma"] for row in board.rows]
    known = [value for value in dips if value is not None]
    assert known == sorted(known), "deepest dip first"
    if None in dips:
        assert dips.index(None) >= len(known), "a blank is unmeasured, not a zero — it sorts last"


def test_board_sorts_by_strength_when_asked(config, seeded_db):
    ids = list(range(34, 40))
    bars = bars_for(ids, dip_at=150)
    composite = build_composite(bars_for(ids, seed=2), members=10, min_members=5)
    board = build_board(
        config, seeded_db, bars, composite.frame, book_for(ids), now=NOW, sort="strength", top=50
    )
    values = [row["rrs"] for row in board.rows if row["rrs"] is not None]
    assert values == sorted(values, reverse=True)


def test_board_caps_rows_and_counts_honestly(config, seeded_db):
    ids = list(range(34, 44))
    bars = bars_for(ids, dip_at=150)
    composite = build_composite(bars_for(ids, seed=2), members=10, min_members=5)
    board = build_board(config, seeded_db, bars, composite.frame, book_for(ids), now=NOW, top=3)
    assert len(board.rows) == 3
    assert board.measured == len(ids)
    text = render_board(board)
    assert "observation, not opportunity" in text
    assert f"3 of {len(ids)} measured shown" in text


def test_board_marks_watchlist_rows(config, seeded_db):
    ids = [34, 35]
    bars = bars_for(ids, dip_at=150)
    composite = build_composite(bars_for(ids, seed=2), members=10, min_members=5)
    board = build_board(
        config, seeded_db, bars, composite.frame, book_for(ids), now=NOW, watch_ids={34}, top=50
    )
    marked = {row["type_id"]: row["watched"] for row in board.rows}
    assert marked.get(34) is True
    assert marked.get(35) is False


def test_board_rejects_an_unknown_sort():
    from evescreener.config import example_config

    with pytest.raises(ValueError):
        build_board(example_config(), None, pd.DataFrame(), None, pd.DataFrame(), sort="momentum")


# -- the watchlist in the digest ----------------------------------------------


def test_watchlist_summary_renders_every_name(config, seeded_db):
    add_watch(seeded_db, name="Type 34", type_id=34)
    seeded_db.conn.execute(
        "INSERT INTO watchlist(name, type_id, added_at) VALUES(?,?,?)",
        ("Ghost Name", None, "2026-08-20T00:00:00+00:00"),
    )
    add_watch(seeded_db, name="Type 35", type_id=35)  # no bars for this one
    bars = bars_for([34], dip_at=150)
    composite = build_composite(bars_for(list(range(34, 44)), seed=2), members=10, min_members=5)
    rows = watchlist_summary(config, seeded_db, bars, composite.frame, book_for([34]), now=NOW)
    by_name = {row["name"]: row for row in rows}
    assert by_name["Ghost Name"]["unresolved"] is True
    assert by_name["Type 35"]["bars"] == 0
    assert by_name["Type 34"]["close"] is not None


def test_digest_watchlist_section_shows_even_on_an_honest_zero(config, seeded_db):
    screen = run_screen(config, seeded_db, pd.DataFrame(), None, pd.DataFrame(), now=NOW)
    assert screen.honest_zero
    text = build_digest(
        config,
        screen,
        watchlist=[
            {
                "name": "Type 34",
                "type_id": 34,
                "bars": 200,
                "close": 750_000.0,
                "day_change_pct": -1.2,
                "dip_sigma": -1.4,
                "band_zone": "LOWER_1_2",
                "rrs": 0.3,
                "friction_pct": 6.1,
                "freshness": "fresh",
                "is_setup": False,
            },
            {"name": "Ghost Name", "unresolved": True},
            {"name": "Type 35", "type_id": 35, "bars": 0},
        ],
    )
    assert "Nothing clears costs today" in text
    assert "Watchlist" in text
    assert "Type 34" in text and "friction 6.10%" in text
    assert "UNRESOLVED" in text
    assert "no bars in the lake yet" in text


def test_an_empty_watchlist_says_how_to_add(config, seeded_db):
    screen = run_screen(config, seeded_db, pd.DataFrame(), None, pd.DataFrame(), now=NOW)
    text = build_digest(config, screen, watchlist=[])
    assert "watch add" in text


def test_no_watchlist_argument_means_no_section(config, seeded_db):
    screen = run_screen(config, seeded_db, pd.DataFrame(), None, pd.DataFrame(), now=NOW)
    assert "Watchlist" not in build_digest(config, screen)


# -- the command surface --------------------------------------------------------


def test_the_new_commands_are_wired():
    assert {"watch", "brief", "board"} <= set(HANDLERS)


def test_watch_requires_a_subcommand_and_add_requires_a_name():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["watch"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["watch", "add"])
    args = build_parser().parse_args(["watch", "add", "--name", "Ishtar", "--note", "doctrine"])
    assert args.name == "Ishtar" and args.note == "doctrine"


def test_board_defaults_are_stated():
    args = build_parser().parse_args(["board"])
    assert args.top == 20 and args.sort == "value"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["board", "--sort", "momentum"])


# -- formatting ----------------------------------------------------------------


def test_format_isk_spans_the_magnitudes():
    assert format_isk(None) == "UNKNOWN"
    assert format_isk(2_500_000_000.0) == "2.50B"
    assert format_isk(350_000_000.0) == "350.00M"
    assert format_isk(5_000.0) == "5,000.00"
