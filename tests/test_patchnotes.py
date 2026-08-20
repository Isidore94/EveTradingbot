"""The patch-notes watcher appends candidates and never anchors."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from evescreener.patchnotes import (
    FeedError,
    PatchNote,
    fetch_patch_notes,
    parse_feed,
    sync_anchor_candidates,
)
from evescreener.signals.anchors import applicable_anchors, load_anchors

FEED = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
<item><title>Patch Notes - Version 24.01</title>
<link>https://www.eveonline.com/news/view/a</link>
<pubDate>Wed, 19 Aug 2026 11:00:00 GMT</pubDate></item>
<item><title>Cradle of War: Expansion Notes</title>
<link>https://www.eveonline.com/news/view/b</link>
<pubDate>Fri, 05 Jun 2026 16:30:00 GMT</pubDate></item>
<item><title>Alliance Tournament Sign-ups</title>
<link>https://www.eveonline.com/news/view/c</link>
<pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_parses_real_feed_shape():
    notes = parse_feed(FEED)
    assert [note.published for note in notes] == [
        date(2026, 6, 1),
        date(2026, 6, 5),
        date(2026, 8, 19),
    ]
    assert notes[-1].title == "Patch Notes - Version 24.01"


def test_a_doctype_or_entity_declaration_is_refused_not_parsed():
    hostile = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><rss></rss>'
    with pytest.raises(FeedError, match="DOCTYPE or ENTITY"):
        parse_feed(hostile)


def test_an_oversized_feed_is_refused():
    with pytest.raises(FeedError, match="over the"):
        parse_feed(b"<rss/>" + b"x" * (4 * 1024 * 1024))


def test_malformed_xml_is_a_loud_error():
    with pytest.raises(FeedError, match="malformed"):
        parse_feed(b"<rss><channel>")


def test_only_market_relevant_posts_become_candidates(tmp_path):
    calendar = tmp_path / "anchors.jsonl"
    added = sync_anchor_candidates(parse_feed(FEED), calendar)
    titles = [note.title for note in added]
    assert "Alliance Tournament Sign-ups" not in titles
    assert len(added) == 2


def test_appended_rows_are_candidates_the_signal_layer_ignores(tmp_path):
    calendar = tmp_path / "anchors.jsonl"
    sync_anchor_candidates(parse_feed(FEED), calendar)
    anchors = load_anchors(calendar)
    assert anchors, "the rows must be written"
    assert all(not anchor.confirmed for anchor in anchors)
    assert applicable_anchors(anchors, as_of=date(2026, 12, 1)) == [], (
        "an unconfirmed candidate must never reach a computation"
    )


def test_running_twice_does_not_grow_the_calendar(tmp_path):
    calendar = tmp_path / "anchors.jsonl"
    first = sync_anchor_candidates(parse_feed(FEED), calendar)
    second = sync_anchor_candidates(parse_feed(FEED), calendar)
    assert first and second == []
    assert len(load_anchors(calendar)) == len(first)


def test_an_operator_confirmed_date_is_never_re_added(tmp_path):
    calendar = tmp_path / "anchors.jsonl"
    calendar.write_text(
        '{"date": "2026-08-19", "label": "my own name for it", '
        '"scope": "global", "confirmed": true}\n',
        encoding="utf-8",
    )
    added = sync_anchor_candidates(parse_feed(FEED), calendar)
    assert all(note.published != date(2026, 8, 19) for note in added)
    anchors = load_anchors(calendar)
    confirmed = [anchor for anchor in anchors if anchor.confirmed]
    assert len(confirmed) == 1
    assert confirmed[0].label == "my own name for it", "the operator's row is never rewritten"


def test_fetch_surfaces_transport_failures(config):
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(FeedError, match="ConnectError"):
        fetch_patch_notes(config, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_fetch_surfaces_http_errors(config):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(FeedError):
        fetch_patch_notes(config, client=client)


def test_the_committed_calendar_carries_real_dates_and_no_confirmations(repo_root):
    anchors = load_anchors(repo_root / "config" / "anchors.jsonl")
    assert len(anchors) >= 5
    assert all(not anchor.confirmed for anchor in anchors)
    assert all(anchor.source for anchor in anchors), "every row cites where it came from"
    assert any("Expansion Notes" in anchor.label for anchor in anchors)


def test_note_to_anchor_is_never_confirmed():
    note = PatchNote(published=date(2026, 1, 1), title="Patch Notes", link="x")
    assert note.as_anchor().confirmed is False


def test_a_rearticled_patch_is_not_appended_twice(tmp_path):
    """CCP re-dates an article; it is still one event.

    Measured on the operator's desk 2026-08-20: `config/anchors.jsonl` ended up
    holding *Patch Notes - Version 24.01* on both 08-19 and 08-20 with an
    identical source URL, because the dedup key was (date, label) only. The
    watcher runs daily, so the operator would have been asked to confirm one
    patch twice — and confirming both would anchor twice on one event.
    """
    from evescreener.patchnotes import PatchNote, sync_anchor_candidates
    from evescreener.signals.anchors import load_anchors

    calendar = tmp_path / "anchors.jsonl"
    url = "https://www.eveonline.com/news/view/patch-notes-version-24-01"
    first = PatchNote(published=date(2026, 8, 19), title="Patch Notes - Version 24.01", link=url)
    assert len(sync_anchor_candidates([first], calendar)) == 1

    redated = PatchNote(published=date(2026, 8, 20), title="Patch Notes - Version 24.01", link=url)
    assert sync_anchor_candidates([redated], calendar) == []
    assert len(load_anchors(calendar)) == 1


def test_a_genuinely_new_article_still_lands(tmp_path):
    """Guards the guard: dedup must not become a mute."""
    from evescreener.patchnotes import PatchNote, sync_anchor_candidates

    calendar = tmp_path / "anchors.jsonl"
    sync_anchor_candidates(
        [PatchNote(date(2026, 8, 19), "Patch Notes - Version 24.01", "https://x/one")],
        calendar,
    )
    added = sync_anchor_candidates(
        [PatchNote(date(2026, 9, 2), "Patch Notes - Version 24.02", "https://x/two")],
        calendar,
    )
    assert [note.title for note in added] == ["Patch Notes - Version 24.02"]
