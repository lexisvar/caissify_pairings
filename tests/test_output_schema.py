"""
Regression guard for the public output JSON Schema.

The schema at ``caissify_pairings/schemas/engine_output.schema.json`` is the
source of truth that downstream consumers (Rust, TypeScript, Swift, …)
code-generate against. If an engine ever starts emitting a shape that does
not match the schema, one of two things is wrong:

1. The engine changed its output contract (breaking downstreams silently).
2. The schema drifted from reality (lying to downstreams).

Either way, this test must fail loudly. Every engine + every common bye
variant is exercised here so the schema stays honest.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any, Dict, List

import pytest
from jsonschema import Draft202012Validator

from caissify_pairings import generate_pairings
from caissify_pairings.schemas import engine_output_schema


# ------------------------------------------------------------------ fixtures


@pytest.fixture(scope="module")
def schema() -> Dict[str, Any]:
    return engine_output_schema()


@pytest.fixture(scope="module")
def validator(schema) -> Draft202012Validator:
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _players(n: int, *, with_score: bool = False) -> List[Dict[str, Any]]:
    """Minimal player dicts accepted by every engine."""
    return [
        {
            "id": i,
            "name": f"P{i}",
            "score": (n - i) * 0.5 if with_score else 0.0,
            "rating": 2000 - (i - 1) * 10,
            "starting_number": i,
            "color_hist": [],
            "float_history": [],
            "bye_count": 0,
        }
        for i in range(1, n + 1)
    ]


# ------------------------------------------------------------------ schema self-tests


class TestSchemaItself:
    """Sanity-check the schema file ships and is valid JSON Schema 2020-12."""

    def test_schema_file_ships_in_package(self):
        """The JSON file must be present via importlib.resources."""
        path = files("caissify_pairings.schemas").joinpath(
            "engine_output.schema.json"
        )
        assert path.is_file()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["$schema"].startswith("https://json-schema.org/draft/2020-12/")

    def test_helper_returns_equivalent_schema(self):
        """``engine_output_schema()`` must return the on-disk JSON verbatim."""
        direct = json.loads(
            files("caissify_pairings.schemas")
            .joinpath("engine_output.schema.json")
            .read_text(encoding="utf-8")
        )
        assert engine_output_schema() == direct

    def test_schema_is_valid_2020_12(self, schema):
        Draft202012Validator.check_schema(schema)


# ------------------------------------------------------------------ engine outputs


class TestDutchOutput:
    def test_even_field_round_one(self, validator):
        pairings = generate_pairings(
            system="dutch",
            players=_players(8),
            previous_pairings=set(),
            round_number=1,
            total_rounds=5,
        )
        validator.validate(pairings)
        assert all("bye" not in p for p in pairings)

    def test_odd_field_emits_pab_bye_row(self, validator):
        """Odd fields must produce exactly one `bye=true, bye_type="U"` row."""
        pairings = generate_pairings(
            system="dutch",
            players=_players(7),
            previous_pairings=set(),
            round_number=1,
            total_rounds=5,
        )
        validator.validate(pairings)
        byes = [p for p in pairings if p.get("bye")]
        assert len(byes) == 1
        bye = byes[0]
        assert bye["black_id"] is None
        assert bye["bye_type"] == "U"

    def test_accelerated_round_one(self, validator):
        """Baku-accelerated round 1 must also validate."""
        pairings = generate_pairings(
            system="dutch",
            players=_players(8),
            previous_pairings=set(),
            round_number=1,
            total_rounds=9,
            accelerated=True,
        )
        validator.validate(pairings)


class TestRoundRobinOutput:
    def test_even_field(self, validator):
        pairings = generate_pairings(
            system="round_robin",
            players=_players(6),
            previous_pairings=set(),
            round_number=1,
            total_rounds=5,
        )
        validator.validate(pairings)

    def test_odd_field_emits_bye_row(self, validator):
        pairings = generate_pairings(
            system="round_robin",
            players=_players(5),
            previous_pairings=set(),
            round_number=1,
            total_rounds=5,
        )
        validator.validate(pairings)
        byes = [p for p in pairings if p.get("bye")]
        assert len(byes) == 1
        assert byes[0]["black_id"] is None

    def test_configurable_bye_type(self, validator):
        pairings = generate_pairings(
            system="round_robin",
            players=_players(5),
            previous_pairings=set(),
            round_number=1,
            total_rounds=5,
            bye_type="F",
        )
        validator.validate(pairings)
        byes = [p for p in pairings if p.get("bye")]
        assert byes[0]["bye_type"] == "F"


class TestCasualOutput:
    def test_round_one(self, validator):
        pairings = generate_pairings(
            system="casual",
            players=_players(8),
            previous_pairings=set(),
            round_number=1,
            total_rounds=5,
        )
        validator.validate(pairings)

    def test_round_two_with_scores(self, validator):
        pairings = generate_pairings(
            system="casual",
            players=_players(8, with_score=True),
            previous_pairings=set(),
            round_number=2,
            total_rounds=5,
        )
        validator.validate(pairings)

    def test_odd_field_default_bye_type_is_F(self, validator):
        pairings = generate_pairings(
            system="casual",
            players=_players(7),
            previous_pairings=set(),
            round_number=1,
            total_rounds=5,
        )
        validator.validate(pairings)
        byes = [p for p in pairings if p.get("bye")]
        assert len(byes) == 1
        assert byes[0]["bye_type"] == "F"


# ------------------------------------------------------------------ negative cases


class TestSchemaRejectsBadShapes:
    """Prove the schema actually rejects the malformations we care about."""

    def test_missing_black_id_field_is_rejected(self, validator):
        """``black_id`` is required — omitting it must fail validation."""
        bad = [{"white_id": 1, "table": 1}]
        with pytest.raises(Exception):
            validator.validate(bad)

    def test_bye_row_with_non_null_black_id_is_rejected(self, validator):
        """bye=true + black_id=int is inconsistent and must fail."""
        bad = [{"white_id": 1, "black_id": 2, "table": 1, "bye": True, "bye_type": "U"}]
        with pytest.raises(Exception):
            validator.validate(bad)

    def test_null_black_id_without_bye_is_rejected(self, validator):
        """black_id=null without bye=true is inconsistent and must fail."""
        bad = [{"white_id": 1, "black_id": None, "table": 1}]
        with pytest.raises(Exception):
            validator.validate(bad)

    def test_unknown_bye_type_is_rejected(self, validator):
        bad = [
            {
                "white_id": 1,
                "black_id": None,
                "table": 1,
                "bye": True,
                "bye_type": "Q",
            }
        ]
        with pytest.raises(Exception):
            validator.validate(bad)

    def test_table_zero_is_rejected(self, validator):
        bad = [{"white_id": 1, "black_id": 2, "table": 0}]
        with pytest.raises(Exception):
            validator.validate(bad)
