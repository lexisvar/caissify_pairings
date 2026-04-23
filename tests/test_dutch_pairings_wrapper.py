"""
Regression guard for the ``dutch_pairings()`` convenience wrapper.

Background
----------
``dutch_pairings()`` in ``caissify_pairings.engines.dutch`` is a
public, documented entry point (see the README). Prior to v0.4.3 it
explicitly named only ``bye_value`` and ``max_byes_per_player`` and
silently dropped every other kwarg on the floor — so a caller who did::

    dutch_pairings(..., accelerated=True)

got a ``TypeError: dutch_pairings() got an unexpected keyword argument
'accelerated'`` even though :class:`DutchEngine` itself has accepted
that argument since v0.4.0 (Baku Acceleration).

That bug existed because the wrapper enumerated kwargs by hand. From
v0.4.3 onward the wrapper forwards ``**kwargs`` verbatim to the
engine, and these tests lock that contract down so the same class of
bug can't reappear.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from caissify_pairings.engines.dutch import DutchEngine, dutch_pairings


# ---------------------------------------------------------------- helpers


def _players(n: int) -> List[Dict[str, Any]]:
    """Minimal player dicts accepted by DutchEngine."""
    return [
        {
            "id": i,
            "name": f"P{i}",
            "score": 0.0,
            "rating": 2400 - (i - 1) * 10,
            "starting_number": i,
            "color_hist": [],
            "float_history": [],
            "bye_count": 0,
        }
        for i in range(1, n + 1)
    ]


# --------------------------------------------------- forwarding contract


class TestKwargForwarding:
    """Every kwarg the wrapper receives must reach ``DutchEngine.__init__``."""

    def test_accelerated_is_forwarded(self):
        """``accelerated=True`` was the reported missing case."""
        with patch(
            "caissify_pairings.engines.dutch.DutchEngine",
            wraps=DutchEngine,
        ) as spy:
            dutch_pairings(
                players=_players(8),
                previous_pairings=set(),
                round_number=1,
                total_rounds=9,
                accelerated=True,
            )
        assert spy.call_args.kwargs["accelerated"] is True

    def test_initial_color_is_forwarded(self):
        """``initial_color="black"`` must reach the engine too."""
        with patch(
            "caissify_pairings.engines.dutch.DutchEngine",
            wraps=DutchEngine,
        ) as spy:
            dutch_pairings(
                players=_players(6),
                previous_pairings=set(),
                round_number=1,
                total_rounds=5,
                initial_color="black",
            )
        assert spy.call_args.kwargs["initial_color"] == "black"

    def test_legacy_bye_value_still_works(self):
        """Regression guard for the two kwargs that DID work pre-fix."""
        with patch(
            "caissify_pairings.engines.dutch.DutchEngine",
            wraps=DutchEngine,
        ) as spy:
            dutch_pairings(
                players=_players(5),
                previous_pairings=set(),
                round_number=1,
                total_rounds=5,
                bye_value=0.5,
                max_byes_per_player=2,
            )
        kwargs = spy.call_args.kwargs
        assert kwargs["bye_value"] == 0.5
        assert kwargs["max_byes_per_player"] == 2

    def test_arbitrary_future_kwarg_passes_through(self):
        """
        The whole point of using ``**kwargs`` is that a NEW option added
        to ``DutchEngine`` tomorrow doesn't require touching this
        wrapper. Verify that by passing a kwarg the engine silently
        ignores (via its own ``**kwargs`` sink on BasePairingEngine) and
        asserting the wrapper didn't reject it.
        """
        with patch(
            "caissify_pairings.engines.dutch.DutchEngine",
            wraps=DutchEngine,
        ) as spy:
            # An unknown-but-inert kwarg — the engine's ``**kwargs``
            # sink absorbs it. The wrapper must not raise.
            dutch_pairings(
                players=_players(6),
                previous_pairings=set(),
                round_number=1,
                total_rounds=5,
                some_future_option_we_havent_invented_yet=42,
            )
        assert (
            spy.call_args.kwargs["some_future_option_we_havent_invented_yet"]
            == 42
        )


# ------------------------------------------------------ end-to-end behaviour


class TestAcceleratedEndToEnd:
    """
    Functional sanity check: calling the wrapper with ``accelerated=True``
    must actually produce Baku-shaped round-1 pairings (top half vs top
    half), not just raise. This is the bug as the reporter would hit it.
    """

    def test_round_one_pairs_within_virtual_score_groups(self):
        """
        Round 1 with 8 players + Baku: the +1 virtual point goes to
        players 1–4, so the expected pairs are 1v3, 2v4, 5v7, 6v8 — the
        top half plays itself and the bottom half plays itself, rather
        than the non-accelerated 1v5/2v6/3v7/4v8 split.
        """
        pairings = dutch_pairings(
            players=_players(8),
            previous_pairings=set(),
            round_number=1,
            total_rounds=9,
            accelerated=True,
        )

        assert len(pairings) == 4
        assert all("bye" not in p for p in pairings)

        seen_pairs = {
            frozenset((p["white_id"], p["black_id"])) for p in pairings
        }

        top_half = {1, 2, 3, 4}
        bottom_half = {5, 6, 7, 8}
        for pair in seen_pairs:
            # Each pair must be entirely inside one half — this is the
            # Baku signature that would fail if ``accelerated`` were
            # silently dropped by the wrapper.
            assert pair <= top_half or pair <= bottom_half, (
                f"pair {pair} crosses the top/bottom half split — "
                "accelerated=True did not reach the engine"
            )


class TestRejectsPositionalArgsForKwargs:
    """
    The wrapper's named parameters stop at the four core values so we
    can forward ``**kwargs`` cleanly. Passing engine options
    positionally must therefore fail loudly rather than silently
    mis-binding — no surprise behaviour for current callers.
    """

    def test_extra_positional_is_rejected(self):
        """5th positional arg used to silently bind to ``bye_value``."""
        with pytest.raises(TypeError):
            dutch_pairings(_players(4), set(), 1, 5, 0.5)  # noqa: E501
