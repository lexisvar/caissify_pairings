"""
Engine registry — maps system names to engine classes.

To register a new engine, add it to ENGINES below.
"""

from __future__ import annotations

from typing import Dict, Type

from caissify_pairings.base import BasePairingEngine

# Lazy imports to keep startup fast — engines are imported only when requested.
_REGISTRY: Dict[str, str] = {
    "dutch": "caissify_pairings.engines.dutch",
}

# Populated on first access
_CACHE: Dict[str, Type[BasePairingEngine]] = {}


def get_engine(system: str) -> Type[BasePairingEngine]:
    """Return the engine class for the given system name."""
    if system in _CACHE:
        return _CACHE[system]

    module_path = _REGISTRY.get(system)
    if module_path is None:
        available = ", ".join(sorted(_REGISTRY.keys()))
        raise ValueError(
            f"Unknown pairing system {system!r}. Available: {available}"
        )

    import importlib
    mod = importlib.import_module(module_path)
    engine_cls = mod.Engine  # Convention: each engine module exposes `Engine`
    _CACHE[system] = engine_cls
    return engine_cls


def available_systems() -> list[str]:
    """Return a sorted list of registered system names."""
    return sorted(_REGISTRY.keys())
