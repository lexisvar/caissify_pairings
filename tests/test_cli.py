"""
Tests for the JSON-over-stdin CLI (``caissify_pairings.__main__``).

These tests exist to lock in the contract that the CLI forwards
**every** non-reserved top-level JSON key to the selected engine as a
keyword argument. Prior to this file, only ``bye_value`` and
``max_byes_per_player`` were passed through, which silently dropped
``accelerated``, ``cycles``, ``initial_color``, ``bye_type``, and
anything else, causing downstream tournament managers to think they
were configuring the engine when they were not.
"""

from __future__ import annotations

import io
import json
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from caissify_pairings.__main__ import main as cli_main


# ---------------------------------------------------------------- helpers


def _make_players(n: int, start_score: float = 0.0) -> List[Dict[str, Any]]:
    """Return ``n`` minimal player dicts suitable for the engines."""
    return [
        {
            "id": i,
            "name": f"P{i}",
            "score": start_score,
            "rating": 2000 - (i - 1) * 10,
            "starting_number": i,
            "color_hist": [],
            "float_history": [],
            "bye_count": 0,
        }
        for i in range(1, n + 1)
    ]


def _run_cli(input_json: Dict[str, Any], capsys: pytest.CaptureFixture) -> List[Dict[str, Any]]:
    """Invoke ``cli_main`` with ``input_json`` on stdin and return the parsed pairings."""
    stdin = io.StringIO(json.dumps(input_json))
    with patch("sys.stdin", stdin):
        cli_main()
    out = capsys.readouterr().out
    return json.loads(out)


# ---------------------------------------------------------------- tests


class TestCLIKwargPassthrough:
    """The CLI must forward arbitrary engine kwargs from the JSON body."""

    def test_accelerated_true_reaches_dutch_engine(self, capsys):
        """``accelerated=true`` must actually flip Baku on, not be silently dropped."""
        payload = {
            "system": "dutch",
            "players": _make_players(8),
            "previous_pairings": [],
            "round_number": 1,
            "total_rounds": 9,
            "accelerated": True,
        }
        with patch("caissify_pairings.__main__.generate_pairings") as gp:
            gp.return_value = []
            _run_cli(payload, capsys)
        gp.assert_called_once()
        kwargs = gp.call_args.kwargs
        assert kwargs["accelerated"] is True
        assert kwargs["system"] == "dutch"

    def test_cycles_reaches_round_robin_engine(self, capsys):
        """``cycles=2`` must reach the round-robin engine for double round-robin."""
        payload = {
            "system": "round_robin",
            "players": _make_players(6),
            "previous_pairings": [],
            "round_number": 1,
            "total_rounds": 10,
            "cycles": 2,
        }
        with patch("caissify_pairings.__main__.generate_pairings") as gp:
            gp.return_value = []
            _run_cli(payload, capsys)
        assert gp.call_args.kwargs["cycles"] == 2

    def test_initial_color_and_bye_type_reach_engine(self, capsys):
        """A mix of non-reserved keys must all go through."""
        payload = {
            "system": "dutch",
            "players": _make_players(4),
            "previous_pairings": [],
            "round_number": 1,
            "total_rounds": 5,
            "initial_color": "black",
            "bye_type": "H",
        }
        with patch("caissify_pairings.__main__.generate_pairings") as gp:
            gp.return_value = []
            _run_cli(payload, capsys)
        kwargs = gp.call_args.kwargs
        assert kwargs["initial_color"] == "black"
        assert kwargs["bye_type"] == "H"

    def test_reserved_keys_are_not_passed_as_kwargs(self, capsys):
        """Core contract fields must arrive as positionals, not be duplicated in kwargs."""
        payload = {
            "system": "dutch",
            "players": _make_players(4),
            "previous_pairings": [[1, 2]],
            "round_number": 2,
            "total_rounds": 5,
            "bye_value": 0.5,
        }
        with patch("caissify_pairings.__main__.generate_pairings") as gp:
            gp.return_value = []
            _run_cli(payload, capsys)
        kwargs = gp.call_args.kwargs
        # These five must arrive as explicit arguments only:
        assert kwargs["system"] == "dutch"
        assert kwargs["round_number"] == 2
        assert kwargs["total_rounds"] == 5
        # previous_pairings comes back as a set
        assert kwargs["previous_pairings"] == {(1, 2)}
        # non-reserved keys are forwarded:
        assert kwargs["bye_value"] == 0.5

    def test_legacy_bye_value_and_max_byes_still_work(self, capsys):
        """Regression guard for the pre-0.4.1 hard-coded pass-through."""
        payload = {
            "system": "casual",
            "players": _make_players(6),
            "previous_pairings": [],
            "round_number": 1,
            "total_rounds": 5,
            "bye_value": 1.0,
            "max_byes_per_player": 2,
        }
        with patch("caissify_pairings.__main__.generate_pairings") as gp:
            gp.return_value = []
            _run_cli(payload, capsys)
        kwargs = gp.call_args.kwargs
        assert kwargs["bye_value"] == 1.0
        assert kwargs["max_byes_per_player"] == 2


class TestCLIEndToEnd:
    """A small end-to-end check that the CLI actually pairs something."""

    def test_dutch_round_one_returns_pairings(self, capsys):
        payload = {
            "system": "dutch",
            "players": _make_players(8),
            "previous_pairings": [],
            "round_number": 1,
            "total_rounds": 7,
        }
        pairings = _run_cli(payload, capsys)
        assert len(pairings) == 4
        assert all("white_id" in p and "black_id" in p for p in pairings)

    def test_accelerated_round_one_is_a_valid_pairing(self, capsys):
        """End-to-end: Baku on round 1 produces a full round of pairings."""
        payload = {
            "system": "dutch",
            "players": _make_players(8),
            "previous_pairings": [],
            "round_number": 1,
            "total_rounds": 9,
            "accelerated": True,
        }
        pairings = _run_cli(payload, capsys)
        assert len(pairings) == 4
        ids_played = {p["white_id"] for p in pairings} | {p["black_id"] for p in pairings}
        assert ids_played == {1, 2, 3, 4, 5, 6, 7, 8}

    def test_round_robin_cycles_two_produces_round_one_of_cycle_two(self, capsys):
        """Round 4 of a 6-player double RR (cycles=2) is round 1 of cycle 2."""
        payload = {
            "system": "round_robin",
            "players": _make_players(6),
            "previous_pairings": [],
            "round_number": 6,
            "total_rounds": 10,
            "cycles": 2,
        }
        pairings = _run_cli(payload, capsys)
        assert len(pairings) == 3
