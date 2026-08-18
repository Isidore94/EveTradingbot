import datetime as dt

import pytest

from evescreener.clock import UTC
from evescreener.watchlist import (
    SEED_WATCHLIST,
    UnresolvedTypeNames,
    resolve_seed,
    seed_watchlist,
)


def test_seed_roster_is_fifty_unique_names():
    assert len(SEED_WATCHLIST) == 50
    assert len(set(SEED_WATCHLIST)) == 50


def _load(store, names):
    store.replace_sde_types(
        [(1000 + index, name, 1, 1, 1, 1.0, 1.0, 1) for index, name in enumerate(names)]
    )


def test_every_seed_name_resolves_when_the_sde_has_it(store):
    _load(store, SEED_WATCHLIST)
    assert len(resolve_seed(store)) == 50


def test_an_unresolvable_name_is_a_loud_error_never_a_silent_skip(store):
    _load(store, [name for name in SEED_WATCHLIST if name != "Morphite"])
    with pytest.raises(UnresolvedTypeNames) as excinfo:
        resolve_seed(store)
    assert excinfo.value.names == ["Morphite"]
    assert "Morphite" in str(excinfo.value)


def test_unpublished_types_do_not_satisfy_a_name(store):
    store.replace_sde_types([(9, "Morphite", 0, 1, 1, 1.0, 1.0, 1)])
    with pytest.raises(UnresolvedTypeNames):
        resolve_seed(store)


def test_watchlist_rows_are_never_auto_removed(store):
    _load(store, SEED_WATCHLIST)
    seed_watchlist(store)
    store.upsert_watchlist(
        [(999_999, "Operator Pick", "operator")], dt.datetime(2026, 8, 18, tzinfo=UTC)
    )
    seed_watchlist(store)  # a second seeding must not prune the operator's entry
    assert (999_999, "Operator Pick") in store.watchlist()
