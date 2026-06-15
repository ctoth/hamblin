"""Shared dataclass node fixtures used across the test modules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Leaf:
    s: str
    i: int
    b: bool
    f: float
    raw: bytes
    opt: object  # None or an int -- exercises the object-typed / nullable field


@dataclass(frozen=True)
class Branch:
    label: str
    n: int
    kids: tuple  # tuple of Leaf | Branch


# A two-class registry covering every scalar leaf, tuples, and nesting.
REGISTRY = {"Leaf": Leaf, "Branch": Branch}


# A minimal pair for deep-chain tests, with NO recursive __eq__/__hash__ used.
@dataclass(frozen=True)
class Tip:
    pass


@dataclass(frozen=True)
class Chain:
    inner: object


DEEP_REGISTRY = {"Tip": Tip, "Chain": Chain}
