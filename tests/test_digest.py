import datetime as dt

import pandas as pd
import pytest

from evescreener.clock import UTC
from evescreener.digest import FENCE, build, isk, pct, render, split_messages
from evescreener.screen import build_screen

AS_OF = dt.datetime(2026, 8, 18, 16, 0, tzinfo=UTC)
SWEEP = dt.datetime(2026, 8, 18, 15, 58, tzinfo=UTC)


def book(type_id, side, best, walk):
    return {
        "type_id": type_id,
        "region_id": 10000002,
        "side": side,
        "sweep_ts": SWEEP,
        "expires_ts": SWEEP,
        "best_price": best,
        "total_volume": 1_000_000,
        "order_count": 50,
        "p5_price": best,
        "top_order_volume_share": 0.1,
        "station_volume_share": 1.0,
        "depth_fill_price_1": walk,
        "depth_fill_qty_1": 1000,
        "depth_fill_price_2": walk,
        "depth_fill_qty_2": 1000,
        "depth_fill_price_3": walk,
        "depth_fill_qty_3": 1000,
    }


def make_result(config, rows, names=None):
    type_ids = sorted({row["type_id"] for row in rows})
    return build_screen(
        config,
        book=pd.DataFrame(rows),
        turnover=pd.DataFrame(
            [
                {
                    "type_id": t,
                    "median_isk_value_30d": 1e11,
                    "median_order_count_30d": 900,
                    "bars": 29,
                }
                for t in type_ids
            ]
        ),
        names=names or {t: f"Type {t}" for t in type_ids},
        type_ids=type_ids,
        as_of=AS_OF,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (4.03, "4.03"),
        (17_160.0, "17.16k"),
        (250_000_000.0, "250.00M"),
        (1_580_000_000_000.0, "1.58T"),
        (float("nan"), "—"),
    ],
)
def test_isk_formatting_spans_twelve_orders_of_magnitude(value, expected):
    assert isk(value) == expected


def test_pct_marks_unknowns_rather_than_printing_nan():
    assert pct(float("nan")) == "—"
    assert pct(-3.5) == "-3.50%"


def test_a_digest_with_no_candidates_says_so_plainly(config):
    result = make_result(
        config, [book(1, "sell", 100.0, 100.0), book(1, "buy", 99.0, 99.0)]
    )
    text = "\n".join(render(config, result))
    assert "Nothing clears costs today" in text
    assert "That is a result, not a gap" in text


def test_a_digest_lists_candidates_when_they_exist(config):
    result = make_result(
        config, [book(1, "sell", 100.0, 100.0), book(1, "buy", 130.0, 130.0)]
    )
    text = "\n".join(render(config, result))
    assert "Nothing clears costs today" not in text
    assert "Type 1" in text


def test_unpriced_rows_are_named_with_their_reason(config):
    result = make_result(config, [book(1, "sell", 100.0, 100.0)])
    text = "\n".join(render(config, result))
    assert "Unpriced — UNKNOWN (1)" in text
    assert "no resting orders on both sides" in text


def test_a_stale_book_is_announced_in_the_header(config):
    result = build_screen(
        config,
        book=pd.DataFrame([book(1, "sell", 100.0, 100.0), book(1, "buy", 99.0, 99.0)]),
        turnover=pd.DataFrame(),
        names={1: "Type 1"},
        type_ids=[1],
        as_of=SWEEP + dt.timedelta(hours=4),
    )
    text = "\n".join(render(config, result))
    assert "STALE" in text


def test_telemetry_calls_out_any_early_fetch(config):
    result = make_result(
        config, [book(1, "sell", 100.0, 100.0), book(1, "buy", 99.0, 99.0)]
    )
    clean = "\n".join(render(config, result, telemetry={"requests": 5}))
    assert "all requests honoured Expires" in clean
    dirty = "\n".join(
        render(config, result, telemetry={"requests": 5, "early_fetches": 2})
    )
    assert "2 EARLY FETCHES" in dirty


def test_messages_respect_the_content_cap_and_are_numbered():
    lines = [f"line {index} " + "x" * 60 for index in range(200)]
    messages, dropped = split_messages(lines, 2000)
    assert dropped == 0
    assert len(messages) > 1
    assert all(len(message) <= 2000 for message in messages)
    for index, message in enumerate(messages, start=1):
        assert message.startswith(f"[{index}/{len(messages)}] ")


def test_a_code_fence_is_closed_and_reopened_across_a_split():
    lines = [FENCE] + [f"row {index} " + "y" * 60 for index in range(120)] + [FENCE]
    messages, _ = split_messages(lines, 2000)
    assert len(messages) > 1
    for message in messages:
        assert message.count(FENCE) % 2 == 0, "every message must balance its fences"


def test_a_line_too_long_to_send_is_reported_never_truncated_silently():
    messages, dropped = split_messages(["x" * 5000], 2000)
    assert dropped == 1
    assert "line dropped" in messages[0]
    assert "x" * 100 not in messages[0]


def test_build_archives_the_full_digest(config):
    import json

    from evescreener.digest import archive

    result = make_result(
        config, [book(1, "sell", 100.0, 100.0), book(1, "buy", 99.0, 99.0)]
    )
    digest = build(config, result, telemetry={"requests": 1})
    archive(config, digest, result, {"kind": "dry_run", "detail": "test"})
    lines = config.paths.digest_archive.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[-1])
    assert record["delivery"]["kind"] == "dry_run"
    assert record["messages"] == digest.messages
    assert record["candidates"] == 0


def test_the_digest_footer_states_that_no_orders_are_placed(config):
    result = make_result(
        config, [book(1, "sell", 100.0, 100.0), book(1, "buy", 99.0, 99.0)]
    )
    assert "no orders are placed" in "\n".join(render(config, result))
