import json

from evescreener import notify


def opener_returning(status, headers=None, body=""):
    calls = []

    def opener(url, payload):
        calls.append((url, json.loads(payload)))
        return status, headers or {}, body

    opener.calls = calls
    return opener


def test_no_webhook_is_unconfigured_not_a_failure():
    result = notify.send("", ["hello"])
    assert result.kind == notify.UNCONFIGURED
    assert not result.ok
    assert result.messages_sent == 0


def test_a_successful_post_is_delivered():
    opener = opener_returning(204)
    result = notify.send("https://discord/wh", ["one", "two"], opener=opener)
    assert result.kind == notify.DELIVERED
    assert result.ok
    assert result.messages_sent == 2
    assert len(opener.calls) == 2


def test_mentions_are_suppressed_in_every_payload():
    opener = opener_returning(204)
    notify.send("https://discord/wh", ["@everyone"], opener=opener)
    _, payload = opener.calls[0]
    assert payload["allowed_mentions"] == {"parse": []}
    assert payload["content"] == "@everyone"


def test_a_4xx_is_rejected_and_stops_the_run():
    opener = opener_returning(400, body="bad body")
    result = notify.send("https://discord/wh", ["one", "two"], opener=opener)
    assert result.kind == notify.REJECTED
    assert result.messages_sent == 0
    assert len(opener.calls) == 1


def test_a_429_reports_rate_limited_with_retry_after_from_the_header():
    opener = opener_returning(429, headers={"Retry-After": "4.5"})
    result = notify.send("https://discord/wh", ["one"], opener=opener)
    assert result.kind == notify.RATE_LIMITED
    assert result.retry_after == 4.5


def test_a_429_falls_back_to_retry_after_in_the_body():
    opener = opener_returning(429, body=json.dumps({"retry_after": 2.25}))
    result = notify.send("https://discord/wh", ["one"], opener=opener)
    assert result.kind == notify.RATE_LIMITED
    assert result.retry_after == 2.25


def test_a_5xx_is_ambiguous_never_reported_as_delivered():
    result = notify.send("https://discord/wh", ["one"], opener=opener_returning(502))
    assert result.kind == notify.AMBIGUOUS
    assert not result.ok


def test_a_transport_explosion_is_ambiguous():
    def opener(url, payload):
        raise OSError("connection reset")

    result = notify.send("https://discord/wh", ["one"], opener=opener)
    assert result.kind == notify.AMBIGUOUS
    assert "transport" in result.detail


def test_a_partial_send_reports_how_far_it_got():
    statuses = iter([204, 500])

    def opener(url, payload):
        return next(statuses), {}, ""

    result = notify.send("https://discord/wh", ["a", "b"], opener=opener)
    assert result.kind == notify.AMBIGUOUS
    assert result.messages_sent == 1
    assert result.messages_total == 2
