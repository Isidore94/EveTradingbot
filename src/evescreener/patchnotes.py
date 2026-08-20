"""Patch-notes watcher — the anchor calendar's tripwire (plan.md §2, §9 R9).

The one piece of the source repo's 12,160-LOC `market_prep/` stack that has an
EVE referent. Equity catalysts (SEC filings, earnings, Fed) have none here;
**patch dates do**, and they are this system's anchor events (§6).

What it does and does not do:

* It **appends candidates** to `config/anchors.jsonl` with `confirmed: false`.
* It **never anchors.** Only the operator flips `confirmed` to `true`, and the
  signal layer ignores unconfirmed rows (§11 D7). A watcher that could anchor
  by itself would let a mistitled news post silently reshape every band.

The feed is third-party XML. Two precautions, because `xml.etree` is not a
hardened parser: the response is size-capped, and any document declaring a
DOCTYPE or ENTITY is rejected outright rather than parsed (that is the
billion-laughs / quadratic-blowup class, which a byte cap does not stop).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from .config import Config
from .signals.anchors import Anchor, append_candidate, load_anchors

__all__ = ["PatchNote", "fetch_patch_notes", "parse_feed", "sync_anchor_candidates"]

PATCH_NOTES_FEED = "https://www.eveonline.com/rss/patch-notes"
MAX_FEED_BYTES = 4 * 1024 * 1024
_DANGEROUS = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)

# Titles that name a market-relevant event. An expansion reshapes items; a
# tournament announcement does not.
_INTERESTING = re.compile(
    r"patch notes|expansion notes|release notes|balance|update", re.IGNORECASE
)


class FeedError(RuntimeError):
    """The feed could not be trusted or parsed. Never silently ignored."""


@dataclass(frozen=True, slots=True)
class PatchNote:
    published: date
    title: str
    link: str

    def as_anchor(self) -> Anchor:
        return Anchor(
            anchor_date=self.published,
            label=self.title,
            scope="global",
            confirmed=False,
            source=self.link or "patch-notes rss",
        )


def parse_feed(payload: bytes) -> list[PatchNote]:
    """Parse an RSS payload into patch notes. Rejects hostile documents."""
    if len(payload) > MAX_FEED_BYTES:
        raise FeedError(f"feed is {len(payload)} bytes, over the {MAX_FEED_BYTES} cap")
    if _DANGEROUS.search(payload):
        raise FeedError(
            "feed declares a DOCTYPE or ENTITY; refusing to parse it "
            "(entity expansion is not bounded by a byte cap)"
        )
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise FeedError(f"malformed feed: {exc}") from exc

    notes: list[PatchNote] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published = (item.findtext("pubDate") or "").strip()
        if not title or not published:
            continue
        try:
            moment = parsedate_to_datetime(published)
        except (TypeError, ValueError):
            continue
        if not isinstance(moment, datetime):
            continue
        notes.append(PatchNote(published=moment.date(), title=title, link=link))
    return sorted(notes, key=lambda note: note.published)


def fetch_patch_notes(
    config: Config, *, url: str = PATCH_NOTES_FEED, client: httpx.Client | None = None
) -> list[PatchNote]:
    owns = client is None
    client = client or httpx.Client(headers=config.headers, timeout=30.0, follow_redirects=True)
    try:
        response = client.get(url)
        response.raise_for_status()
        return parse_feed(response.content)
    except httpx.HTTPError as exc:
        raise FeedError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        if owns:
            client.close()


def sync_anchor_candidates(
    notes: list[PatchNote],
    calendar: Path,
    *,
    market_relevant_only: bool = True,
) -> list[PatchNote]:
    """Append genuinely new notes as UNCONFIRMED candidates. Returns what was added.

    A note already present in the calendar on the same date, or under the
    same article URL, is skipped, so
    running the watcher daily does not grow the file without bound.
    """
    calendar_anchors = load_anchors(calendar)
    existing = {(anchor.anchor_date, anchor.label.strip().lower()) for anchor in calendar_anchors}
    existing_dates = {anchor.anchor_date for anchor in calendar_anchors}
    # The article URL is the identity of the EVENT; the date is only where CCP
    # last filed it. Without this, an article CCP re-dates is appended a second
    # time as a candidate for something that happened once — and the watcher
    # runs daily, so the operator would confirm one patch twice.
    existing_sources = {
        source.strip().lower()
        for source in (getattr(anchor, "source", "") or "" for anchor in calendar_anchors)
        if source.strip()
    }
    added: list[PatchNote] = []
    for note in notes:
        if market_relevant_only and not _INTERESTING.search(note.title):
            continue
        key = (note.published, note.title.strip().lower())
        source = (note.link or "").strip().lower()
        if key in existing or note.published in existing_dates:
            continue
        if source and source in existing_sources:
            continue
        append_candidate(calendar, note.as_anchor())
        existing.add(key)
        existing_dates.add(note.published)
        if source:
            existing_sources.add(source)
        added.append(note)
    return added
