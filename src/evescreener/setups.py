"""The operator setup engine (plan.md §19 Part 3).

The built-in setup in `signals/setup.py` is the *system's* recommendation and
it stays frozen: dips below anchored value with intact demand, no momentum
branch. This module is a different thing. It is the operator's own language
for saying what he is looking for, and §6's "no momentum logic" row does not
constrain it — that row governs what the machine recommends on its own
authority, not what the operator asks it to measure. The machinery's job here
is not to argue with a setup. It is to measure it honestly and, once the
learning loop has enough closed trades, to say which setups earn.

Three properties make that honest rather than merely flexible:

* **The vocabulary is fixed and typed.** A setup is data in
  `config/setups.jsonl`, not code, and every condition it can express is
  listed in `CONDITION_SPECS` below. An unknown condition kind, a misspelled
  parameter or an out-of-range value is a **loud load error naming the file
  and line** — never a condition that silently evaluates to true, and never
  one that silently evaluates to false either, which would be worse because
  the setup would look tested.
* **Evaluation is tri-state.** Every condition returns True, False, or None
  for UNKNOWN, and a setup with any UNKNOWN condition does not fire. "Could
  not measure" is never "measured and passed" (§4). Each condition also
  carries the reason it came out the way it did, so a setup that never fires
  can be debugged without guessing.
* **Everything reads from the same daily bars.** High, low, close, volume,
  order_count. Nothing here needs an open, and nothing here may invent one.

Setups are **long-only**. There is no short side in this system: EVE has no
borrow, and the exit surface is a sell order into a book you already own the
goods for.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .signals.avwap import AvwapBands, classify_band
from .signals.levels import levels_near
from .signals.moving import cloud_state, cross_within, ema, sma

__all__ = [
    "CONDITION_SPECS",
    "Condition",
    "ConditionResult",
    "Setup",
    "SetupContext",
    "SetupError",
    "SetupVerdict",
    "SETUPS_FILE",
    "UNVALIDATED",
    "VALIDATED",
    "describe_condition",
    "evaluate_setup",
    "load_setups",
    "validation_state",
]

SETUPS_FILE = "setups.jsonl"

# A setup is UNVALIDATED until it has a backtest read or enough tagged closed
# trades. The label is information, not a lock: an UNVALIDATED setup still
# scans, still charts and still tags a paper trade. It just says so.
UNVALIDATED = "UNVALIDATED"
VALIDATED = "VALIDATED"
MIN_TRADES_TO_VALIDATE = 20

# Zone names the band condition accepts, plus the two coarse aliases that are
# what an operator actually means most of the time.
BAND_ZONES = tuple(
    dict.fromkeys(
        (
            "ABOVE_UPPER_3",
            "UPPER_2_3",
            "UPPER_1_2",
            "VWAP_UPPER_1",
            "VWAP_LOWER_1",
            "LOWER_1_2",
            "LOWER_2_3",
            "BELOW_LOWER_3",
        )
    )
)
BAND_ALIASES = {
    "below_value": ("VWAP_LOWER_1", "LOWER_1_2", "LOWER_2_3", "BELOW_LOWER_3"),
    "above_value": ("VWAP_UPPER_1", "UPPER_1_2", "UPPER_2_3", "ABOVE_UPPER_3"),
}

LEVEL_KINDS = {"hv": "hv_horizontal", "round": "round_isk", "pivot": "pivot"}

OPS = {
    "at_least": lambda value, threshold: value >= threshold,
    "at_most": lambda value, threshold: value <= threshold,
    "above": lambda value, threshold: value > threshold,
    "below": lambda value, threshold: value < threshold,
}


class SetupError(RuntimeError):
    """A malformed setups.jsonl fails loudly; it is never partially loaded."""


# -- the vocabulary ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConditionSpec:
    """What one condition kind requires, and what it means in English."""

    kind: str
    required: tuple[str, ...]
    optional: dict = field(default_factory=dict)
    summary: str = ""


CONDITION_SPECS: dict[str, ConditionSpec] = {
    spec.kind: spec
    for spec in (
        ConditionSpec(
            kind="price_vs_ma",
            required=("ma", "length", "op"),
            optional={},
            summary="close above/below an SMA or EMA of n bars",
        ),
        ConditionSpec(
            kind="cloud",
            required=("fast", "slow"),
            optional={"position": "any", "slope": "any"},
            summary="where price sits against a two-EMA ribbon, and which way it points",
        ),
        ConditionSpec(
            kind="ma_cross",
            required=("fast", "slow", "direction", "within"),
            optional={"ma": "ema"},
            summary="fast crossed slow up/down within the last k bars",
        ),
        ConditionSpec(
            kind="band_zone",
            required=("zone",),
            optional={},
            summary="the anchored-VWAP zone the close sits in",
        ),
        ConditionSpec(
            kind="dip_sigma",
            required=("op", "value"),
            optional={},
            summary="distance from anchored value in sigma",
        ),
        ConditionSpec(
            kind="rrs",
            required=("op", "value"),
            optional={"scope": "forge"},
            summary="real relative strength vs FORGE or vs the type's own sector",
        ),
        ConditionSpec(
            kind="participation",
            required=("op", "value"),
            optional={},
            summary="volume participation against its own recent window",
        ),
        ConditionSpec(
            kind="near_level",
            required=("level", "within_atr"),
            optional={"side": "any"},
            summary="within j ATR of a high-volume level, round ISK level or pivot",
        ),
        ConditionSpec(
            kind="change",
            required=("bars", "op", "value"),
            optional={},
            summary="percent change over the last n bars",
        ),
    )
}


@dataclass(frozen=True, slots=True)
class Condition:
    kind: str
    params: dict

    def as_dict(self) -> dict:
        return {"kind": self.kind, **self.params}


@dataclass(frozen=True, slots=True)
class Setup:
    name: str
    conditions: tuple[Condition, ...]
    enabled: bool = True
    notes: str = ""
    example: bool = False
    source_line: int | None = None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "example": self.example,
            "notes": self.notes,
            "conditions": [condition.as_dict() for condition in self.conditions],
        }


# -- loading and validation -------------------------------------------------


def _require_number(where: str, key: str, value, *, minimum=None, maximum=None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SetupError(f"{where}: {key!r} must be a number, got {value!r}") from exc
    if not np.isfinite(number):
        raise SetupError(f"{where}: {key!r} must be finite, got {value!r}")
    if minimum is not None and number < minimum:
        raise SetupError(f"{where}: {key!r} must be >= {minimum}, got {number}")
    if maximum is not None and number > maximum:
        raise SetupError(f"{where}: {key!r} must be <= {maximum}, got {number}")
    return number


def _require_choice(where: str, key: str, value, choices) -> str:
    text = str(value).strip().lower()
    if text not in choices:
        raise SetupError(
            f"{where}: {key!r} must be one of {', '.join(sorted(choices))}, got {value!r}"
        )
    return text


def _validate_condition(where: str, raw: dict) -> Condition:
    if not isinstance(raw, dict):
        raise SetupError(f"{where}: each condition must be an object, got {raw!r}")
    if "kind" not in raw:
        raise SetupError(f"{where}: condition is missing 'kind'")
    kind = str(raw["kind"]).strip().lower()
    spec = CONDITION_SPECS.get(kind)
    if spec is None:
        raise SetupError(
            f"{where}: unknown condition kind {kind!r}. Known kinds: "
            + ", ".join(sorted(CONDITION_SPECS))
        )
    supplied = {key: value for key, value in raw.items() if key != "kind"}
    missing = [key for key in spec.required if key not in supplied]
    if missing:
        raise SetupError(f"{where}: {kind} is missing {', '.join(missing)}")
    allowed = set(spec.required) | set(spec.optional)
    unknown = sorted(set(supplied) - allowed)
    if unknown:
        raise SetupError(
            f"{where}: {kind} has unknown parameter(s) {', '.join(unknown)}; "
            f"allowed: {', '.join(sorted(allowed))}"
        )
    params = {**spec.optional, **supplied}

    if kind == "price_vs_ma":
        params["ma"] = _require_choice(where, "ma", params["ma"], {"sma", "ema"})
        params["length"] = int(_require_number(where, "length", params["length"], minimum=1))
        params["op"] = _require_choice(where, "op", params["op"], {"above", "below"})
    elif kind == "cloud":
        params["fast"] = int(_require_number(where, "fast", params["fast"], minimum=1))
        params["slow"] = int(_require_number(where, "slow", params["slow"], minimum=1))
        if params["fast"] >= params["slow"]:
            raise SetupError(
                f"{where}: cloud needs fast < slow, got fast={params['fast']} slow={params['slow']}"
            )
        params["position"] = _require_choice(
            where, "position", params["position"], {"above", "inside", "below", "any"}
        )
        params["slope"] = _require_choice(
            where, "slope", params["slope"], {"rising", "falling", "flat", "any"}
        )
    elif kind == "ma_cross":
        params["ma"] = _require_choice(where, "ma", params["ma"], {"sma", "ema"})
        params["fast"] = int(_require_number(where, "fast", params["fast"], minimum=1))
        params["slow"] = int(_require_number(where, "slow", params["slow"], minimum=1))
        if params["fast"] >= params["slow"]:
            raise SetupError(
                f"{where}: ma_cross needs fast < slow, got fast={params['fast']} "
                f"slow={params['slow']}"
            )
        params["direction"] = _require_choice(
            where, "direction", params["direction"], {"up", "down"}
        )
        params["within"] = int(_require_number(where, "within", params["within"], minimum=1))
    elif kind == "band_zone":
        zones = params["zone"]
        zones = [zones] if isinstance(zones, str) else zones
        if not isinstance(zones, list) or not zones:
            raise SetupError(f"{where}: 'zone' must be a zone name or a non-empty list of them")
        resolved: list[str] = []
        for zone in zones:
            text = str(zone).strip()
            if text.lower() in BAND_ALIASES:
                resolved.extend(BAND_ALIASES[text.lower()])
                continue
            if text.upper() not in BAND_ZONES:
                raise SetupError(
                    f"{where}: unknown zone {zone!r}. Known: "
                    + ", ".join(BAND_ZONES)
                    + ", "
                    + ", ".join(sorted(BAND_ALIASES))
                )
            resolved.append(text.upper())
        params["zone"] = sorted(set(resolved))
    elif kind == "dip_sigma":
        params["op"] = _require_choice(where, "op", params["op"], set(OPS))
        params["value"] = _require_number(where, "value", params["value"])
    elif kind == "rrs":
        params["scope"] = _require_choice(where, "scope", params["scope"], {"forge", "sector"})
        params["op"] = _require_choice(where, "op", params["op"], set(OPS))
        params["value"] = _require_number(where, "value", params["value"])
    elif kind == "participation":
        params["op"] = _require_choice(where, "op", params["op"], set(OPS))
        params["value"] = _require_number(where, "value", params["value"], minimum=0.0)
    elif kind == "near_level":
        params["level"] = _require_choice(where, "level", params["level"], set(LEVEL_KINDS))
        params["within_atr"] = _require_number(
            where, "within_atr", params["within_atr"], minimum=0.0, maximum=20.0
        )
        params["side"] = _require_choice(where, "side", params["side"], {"any", "above", "below"})
    elif kind == "change":
        params["bars"] = int(_require_number(where, "bars", params["bars"], minimum=1))
        params["op"] = _require_choice(where, "op", params["op"], set(OPS))
        params["value"] = _require_number(where, "value", params["value"])

    return Condition(kind=kind, params=params)


def load_setups(path: Path | None = None) -> list[Setup]:
    """Read the operator's setups. A malformed row names itself and stops."""
    path = path or (Path.cwd() / "config" / SETUPS_FILE)
    if not path.exists():
        return []
    setups: list[Setup] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        where = f"{path}:{number}"
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SetupError(f"{where}: not valid JSON — {exc}") from exc
        if not isinstance(record, dict):
            raise SetupError(f"{where}: each line must be a JSON object")
        if "name" not in record:
            raise SetupError(f"{where}: missing 'name'")
        name = str(record["name"]).strip()
        if not name:
            raise SetupError(f"{where}: empty name")
        key = name.casefold()
        if key in seen:
            raise SetupError(f"{where}: duplicate setup name {name!r}")
        unknown = sorted(set(record) - {"name", "enabled", "conditions", "notes", "example"})
        if unknown:
            raise SetupError(
                f"{where}: unknown field(s) {', '.join(unknown)}; allowed: "
                "name, enabled, conditions, notes, example"
            )
        conditions = record.get("conditions")
        if not isinstance(conditions, list) or not conditions:
            raise SetupError(f"{where}: 'conditions' must be a non-empty list")
        seen.add(key)
        setups.append(
            Setup(
                name=name,
                conditions=tuple(_validate_condition(where, condition) for condition in conditions),
                enabled=bool(record.get("enabled", True)),
                notes=str(record.get("notes", "")),
                example=bool(record.get("example", False)),
                source_line=number,
            )
        )
    return setups


def describe_condition(condition: Condition) -> str:
    """One line of English per condition, for charts and the scanner."""
    params = condition.params
    if condition.kind == "price_vs_ma":
        return f"close {params['op']} {params['ma'].upper()}{params['length']}"
    if condition.kind == "cloud":
        bits = [f"EMA cloud {params['fast']}/{params['slow']}"]
        if params["position"] != "any":
            bits.append(f"price {params['position']}")
        if params["slope"] != "any":
            bits.append(params["slope"])
        return ", ".join(bits)
    if condition.kind == "ma_cross":
        way = "golden" if params["direction"] == "up" else "death"
        return (
            f"{params['ma'].upper()}{params['fast']}/{params['slow']} {way} cross "
            f"within {params['within']} bars"
        )
    if condition.kind == "band_zone":
        return "band zone in " + "/".join(params["zone"])
    if condition.kind == "dip_sigma":
        return f"dip sigma {params['op'].replace('_', ' ')} {params['value']:+.2f}"
    if condition.kind == "rrs":
        scope = params["scope"].upper()
        return f"RRS vs {scope} {params['op'].replace('_', ' ')} {params['value']:+.2f}"
    if condition.kind == "participation":
        return f"participation {params['op'].replace('_', ' ')} {params['value']:.2f}"
    if condition.kind == "near_level":
        side = "" if params["side"] == "any" else f" {params['side']}"
        return f"within {params['within_atr']:.2f} ATR of a{side} {params['level']} level"
    if condition.kind == "change":
        return (
            f"{params['bars']}-bar change {params['op'].replace('_', ' ')} {params['value']:+.2f}%"
        )
    return condition.kind  # pragma: no cover - every kind is covered above


# -- evaluation -------------------------------------------------------------


@dataclass(slots=True)
class SetupContext:
    """Everything a condition may read, assembled once per type per scan.

    Assembling it up front is what keeps the scanner from recomputing an EMA
    once per setup per name, and what keeps every setup reading the same
    numbers as the board and the brief.
    """

    frame: pd.DataFrame
    evaluated: pd.DataFrame | None = None
    level_store: dict | None = None
    atr: float | None = None
    rrs_forge: float | None = None
    rrs_sector: float | None = None
    sector_ticker: str | None = None
    bands: AvwapBands | None = None

    @property
    def close(self) -> float | None:
        closes = pd.to_numeric(self.frame["close"], errors="coerce") if len(self.frame) else None
        if closes is None or closes.empty:
            return None
        value = float(closes.iloc[-1])
        return value if np.isfinite(value) else None

    def last(self, column: str) -> float | None:
        if self.evaluated is None or self.evaluated.empty or column not in self.evaluated:
            return None
        value = self.evaluated[column].iloc[-1]
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if np.isfinite(number) else None


@dataclass(frozen=True, slots=True)
class ConditionResult:
    condition: Condition
    passed: bool | None
    detail: str

    @property
    def unknown(self) -> bool:
        return self.passed is None

    def as_dict(self) -> dict:
        return {
            "kind": self.condition.kind,
            "description": describe_condition(self.condition),
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class SetupVerdict:
    setup: Setup
    results: tuple[ConditionResult, ...]

    @property
    def unknown(self) -> bool:
        return any(result.unknown for result in self.results)

    @property
    def fired(self) -> bool:
        """True only when every condition is TRUE. UNKNOWN never fires (§4)."""
        return bool(self.results) and all(result.passed is True for result in self.results)

    @property
    def failed_on(self) -> tuple[str, ...]:
        return tuple(
            describe_condition(result.condition)
            for result in self.results
            if result.passed is False
        )

    @property
    def unknown_on(self) -> tuple[str, ...]:
        return tuple(
            describe_condition(result.condition) for result in self.results if result.unknown
        )

    def as_dict(self) -> dict:
        return {
            "setup": self.setup.name,
            "fired": self.fired,
            "unknown": self.unknown,
            "conditions": [result.as_dict() for result in self.results],
        }


def _compare(op: str, value: float | None, threshold: float) -> bool | None:
    if value is None or not np.isfinite(value):
        return None
    return bool(OPS[op](value, threshold))


def _evaluate_condition(condition: Condition, context: SetupContext) -> ConditionResult:
    kind = condition.kind
    params = condition.params
    frame = context.frame

    if kind == "price_vs_ma":
        series = (
            sma(frame, params["length"]) if params["ma"] == "sma" else ema(frame, params["length"])
        )
        close = context.close
        if series.empty or close is None:
            return ConditionResult(condition, None, "no closes")
        value = series.iloc[-1]
        if not np.isfinite(value):
            return ConditionResult(
                condition,
                None,
                f"{params['ma'].upper()}{params['length']} still warming up ({len(frame)} bars)",
            )
        passed = close > float(value) if params["op"] == "above" else close < float(value)
        return ConditionResult(condition, passed, f"close {close:,.2f} vs {float(value):,.2f}")

    if kind == "cloud":
        state = cloud_state(frame, params["fast"], params["slow"])
        if not state.known:
            return ConditionResult(condition, None, "cloud still warming up")
        if params["position"] != "any" and state.position != params["position"]:
            return ConditionResult(condition, False, f"price {state.position} the cloud")
        if params["slope"] != "any":
            if state.slope is None:
                return ConditionResult(condition, None, "cloud slope not measurable")
            if state.slope != params["slope"]:
                return ConditionResult(condition, False, f"cloud {state.slope}")
        return ConditionResult(
            condition, True, f"price {state.position}, cloud {state.slope or 'flat'}"
        )

    if kind == "ma_cross":
        crossed = cross_within(
            frame,
            fast_kind=params["ma"],
            fast_length=params["fast"],
            slow_kind=params["ma"],
            slow_length=params["slow"],
            bars=params["within"],
            direction=params["direction"],
        )
        if crossed is None:
            return ConditionResult(condition, None, "averages still warming up")
        return ConditionResult(
            condition, crossed, "crossed" if crossed else "no cross in the window"
        )

    if kind == "band_zone":
        zone = None
        if context.bands is not None:
            zone = classify_band(context.close, context.bands)
        if zone is None or zone == "UNKNOWN":
            return ConditionResult(condition, None, "anchored-VWAP bands UNKNOWN")
        return ConditionResult(condition, zone in params["zone"], f"zone {zone}")

    if kind == "dip_sigma":
        value = context.last("dip_sigma")
        passed = _compare(params["op"], value, params["value"])
        if passed is None:
            return ConditionResult(condition, None, "dip sigma UNKNOWN")
        return ConditionResult(condition, passed, f"dip {value:+.2f}σ")

    if kind == "rrs":
        value = context.rrs_forge if params["scope"] == "forge" else context.rrs_sector
        if params["scope"] == "sector" and context.sector_ticker is None:
            # An unresolvable scope is UNKNOWN. It is never silently answered
            # with the market index instead (§6).
            return ConditionResult(condition, None, "type resolves to no sector")
        passed = _compare(params["op"], value, params["value"])
        if passed is None:
            scope = context.sector_ticker if params["scope"] == "sector" else "FORGE"
            return ConditionResult(condition, None, f"RRS vs {scope} UNKNOWN")
        return ConditionResult(condition, passed, f"RRS {value:+.2f}")

    if kind == "participation":
        value = context.last("participation")
        passed = _compare(params["op"], value, params["value"])
        if passed is None:
            return ConditionResult(condition, None, "participation UNKNOWN")
        return ConditionResult(condition, passed, f"participation {value:.2f}")

    if kind == "near_level":
        if context.level_store is None or context.atr is None or not np.isfinite(context.atr):
            return ConditionResult(condition, None, "levels or ATR UNKNOWN")
        close = context.close
        if close is None:
            return ConditionResult(condition, None, "no close")
        near = levels_near(
            context.level_store,
            close,
            context.atr,
            tol_frac=float(params["within_atr"]),
            kinds=[LEVEL_KINDS[params["level"]]],
        )
        if params["side"] != "any":
            near = [level for level in near if level.get("position") == params["side"]]
        return ConditionResult(
            condition,
            bool(near),
            f"{len(near)} {params['level']} level(s) within {params['within_atr']:.2f} ATR",
        )

    if kind == "change":
        closes = pd.to_numeric(frame["close"], errors="coerce").dropna()
        bars = int(params["bars"])
        if len(closes) <= bars:
            return ConditionResult(condition, None, f"needs {bars + 1} bars, has {len(closes)}")
        earlier = float(closes.iloc[-1 - bars])
        latest = float(closes.iloc[-1])
        if earlier <= 0:
            return ConditionResult(condition, None, "earlier close not positive")
        change = (latest / earlier - 1.0) * 100.0
        passed = _compare(params["op"], change, params["value"])
        return ConditionResult(condition, passed, f"{change:+.2f}% over {bars} bars")

    # Unreachable: load_setups refuses any kind not handled above.
    raise SetupError(f"condition kind {kind!r} has no evaluator")  # pragma: no cover


def evaluate_setup(setup: Setup, context: SetupContext) -> SetupVerdict:
    """Evaluate one setup at the last bar. Every condition must be TRUE."""
    return SetupVerdict(
        setup=setup,
        results=tuple(_evaluate_condition(condition, context) for condition in setup.conditions),
    )


def validation_state(*, backtested: bool, closed_trades: int) -> str:
    """UNVALIDATED until a backtest read or >= 20 tagged closed trades.

    Information, not a lock. An UNVALIDATED setup scans, charts and tags
    exactly like any other; the label only says what is not yet known about
    it.
    """
    if backtested or int(closed_trades) >= MIN_TRADES_TO_VALIDATE:
        return VALIDATED
    return UNVALIDATED
