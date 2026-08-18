"""Discord webhook transport (plan.md §11 D6).

Ported from the source repo's ntfy ``push_notify``: the transport changes, the
result contract does not. Delivery reports one of

``unconfigured`` — no webhook is set; the digest was still built and archived
``delivered``    — Discord accepted every message
``rejected``     — Discord refused it (4xx that is not 429)
``rate_limited`` — 429 with ``retry_after``; new kind, Discord-specific
``ambiguous``    — the transport failed in a way that leaves delivery unknown

An ambiguous send is never reported as delivered. The opener is injectable so
tests exercise every branch without a network.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

UNCONFIGURED = "unconfigured"
DELIVERED = "delivered"
REJECTED = "rejected"
RATE_LIMITED = "rate_limited"
AMBIGUOUS = "ambiguous"

Opener = Callable[[str, bytes], tuple[int, dict[str, str], str]]


@dataclass(frozen=True)
class DeliveryResult:
    kind: str
    detail: str
    messages_sent: int = 0
    messages_total: int = 0
    retry_after: float | None = None
    statuses: tuple[int, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.kind == DELIVERED


def _default_opener(url: str, body: bytes) -> tuple[int, dict[str, str], str]:
    import httpx

    response = httpx.post(
        url,
        content=body,
        headers={"Content-Type": "application/json"},
        timeout=30.0,
    )
    return response.status_code, dict(response.headers), response.text


def build_payload(content: str) -> bytes:
    """One Discord webhook body. No @here/@everyone in v1 (D6)."""
    return json.dumps({"content": content, "allowed_mentions": {"parse": []}}).encode(
        "utf-8"
    )


def send(
    webhook_url: str,
    messages: list[str],
    *,
    opener: Opener | None = None,
) -> DeliveryResult:
    """Post ``messages`` in order, stopping at the first non-success."""
    total = len(messages)
    if not webhook_url:
        return DeliveryResult(
            kind=UNCONFIGURED,
            detail="discord.webhook_url is empty; digest built but not delivered",
            messages_total=total,
        )
    if not messages:
        return DeliveryResult(
            kind=DELIVERED, detail="nothing to send", messages_total=0
        )

    opener = opener or _default_opener
    statuses: list[int] = []
    for index, content in enumerate(messages, start=1):
        try:
            status, headers, body = opener(webhook_url, build_payload(content))
        except Exception as exc:  # transport failed mid-flight: delivery unknown
            return DeliveryResult(
                kind=AMBIGUOUS,
                detail=f"message {index}/{total} failed in transport: {exc!r}",
                messages_sent=index - 1,
                messages_total=total,
                statuses=tuple(statuses),
            )
        statuses.append(status)

        if status == 429:
            return DeliveryResult(
                kind=RATE_LIMITED,
                detail=f"Discord rate-limited message {index}/{total}",
                messages_sent=index - 1,
                messages_total=total,
                retry_after=_retry_after(headers, body),
                statuses=tuple(statuses),
            )
        if 200 <= status < 300:
            continue
        if 400 <= status < 500:
            return DeliveryResult(
                kind=REJECTED,
                detail=(
                    f"Discord rejected message {index}/{total}: {status} {body[:200]}"
                ),
                messages_sent=index - 1,
                messages_total=total,
                statuses=tuple(statuses),
            )
        return DeliveryResult(
            kind=AMBIGUOUS,
            detail=f"Discord returned {status} on message {index}/{total}",
            messages_sent=index - 1,
            messages_total=total,
            statuses=tuple(statuses),
        )

    return DeliveryResult(
        kind=DELIVERED,
        detail=f"delivered {total} message(s)",
        messages_sent=total,
        messages_total=total,
        statuses=tuple(statuses),
    )


def _retry_after(headers: dict[str, str], body: str) -> float | None:
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is not None:
        try:
            return float(raw)
        except ValueError:
            pass
    try:
        return float(json.loads(body)["retry_after"])
    except (ValueError, KeyError, TypeError):
        return None
