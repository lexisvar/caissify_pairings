"""
JSON Schema definitions for caissify_pairings' public contracts.

This subpackage ships the JSON Schema for :func:`generate_pairings` output
so downstream consumers (Rust, TypeScript, Swift, …) can validate or
code-generate against a single source of truth instead of guessing the
shape from library internals.

Example::

    import json
    from importlib.resources import files

    schema = json.loads(
        files("caissify_pairings.schemas")
        .joinpath("engine_output.schema.json")
        .read_text(encoding="utf-8")
    )

Or the convenience helper :func:`engine_output_schema`::

    from caissify_pairings.schemas import engine_output_schema
    schema = engine_output_schema()
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any, Dict

__all__ = ["engine_output_schema", "ENGINE_OUTPUT_SCHEMA_FILENAME"]

ENGINE_OUTPUT_SCHEMA_FILENAME = "engine_output.schema.json"


def engine_output_schema() -> Dict[str, Any]:
    """
    Return the JSON Schema (draft 2020-12) describing the output of
    :meth:`caissify_pairings.base.BasePairingEngine.generate_pairings`.

    The schema is loaded fresh on every call so callers can safely mutate
    the returned dict without affecting other callers.
    """
    raw = (
        files(__package__)
        .joinpath(ENGINE_OUTPUT_SCHEMA_FILENAME)
        .read_text(encoding="utf-8")
    )
    return json.loads(raw)
