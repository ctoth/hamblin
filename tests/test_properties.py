"""Property-based tests: round-trip, streaming equivalence, and total decoding.

The strategy builds arbitrary trees of the `Leaf`/`Branch` dataclasses, so the
codec is exercised against deep nesting, every scalar leaf, empty and ragged
tuples, and unicode/binary payloads it never saw hand-written.
"""

from __future__ import annotations

from conftest import REGISTRY, Branch, Leaf
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import hamblin
from hamblin import HamblinError

# scalar leaves: NaN excluded (NaN != NaN would break round-trip equality, not the codec)
_floats = st.floats(allow_nan=False)
_leaves = st.builds(
    Leaf,
    s=st.text(),
    i=st.integers(),
    b=st.booleans(),
    f=_floats,
    raw=st.binary(),
    opt=st.none() | st.integers(),
)

# trees: a Branch carries a ragged tuple of sub-trees (Leaf or Branch)
_trees = st.recursive(
    _leaves,
    lambda children: st.builds(
        Branch,
        label=st.text(),
        n=st.integers(),
        kids=st.lists(children, max_size=4).map(tuple),
    ),
    max_leaves=40,
)


@given(_trees)
@settings(max_examples=400, suppress_health_check=[HealthCheck.too_slow])
def test_round_trip(tree):
    assert hamblin.decode(hamblin.encode(tree), REGISTRY) == tree


@given(_trees)
@settings(max_examples=200)
def test_encode_is_deterministic(tree):
    assert hamblin.encode(tree) == hamblin.encode(tree)


@given(_trees, st.integers(min_value=1, max_value=7))
@settings(max_examples=200)
def test_streaming_equals_one_shot(tree, chunk):
    """Decoding from a source that dribbles `chunk` bytes at a time yields the same
    value as decoding the whole blob -- the codec never depends on read framing."""
    blob = hamblin.encode(tree)
    pos = 0

    def read(n: int) -> bytes:
        nonlocal pos
        if n == 0:
            return b""
        take = min(n, chunk)
        out = blob[pos : pos + take]
        pos += len(out)
        return out

    assert hamblin.decode_from(read, REGISTRY) == tree


@given(_trees)
@settings(max_examples=200)
def test_reencode_is_stable(tree):
    """encode . decode . encode == encode -- the bytes are a fixed point, which
    also certifies round-trip for trees too deep for Python's recursive `==`."""
    blob = hamblin.encode(tree)
    assert hamblin.encode(hamblin.decode(blob, REGISTRY)) == blob


@given(st.binary(max_size=2048))
@settings(max_examples=1000)
def test_arbitrary_bytes_never_crash(data):
    """Decode is TOTAL: hostile/garbage bytes raise HamblinError (a ValueError) and
    nothing else -- never a RecursionError, never an uncaught exception."""
    try:
        hamblin.decode(data, REGISTRY)
    except HamblinError:
        pass  # the only acceptable failure
    except RecursionError as exc:  # pragma: no cover -- would be a real bug
        raise AssertionError("decode recursed on hostile input") from exc


@given(_trees, st.data())
@settings(max_examples=300)
def test_truncated_valid_blob_never_crashes(tree, data):
    """A valid blob cut at an arbitrary point decodes or cleanly rejects -- never
    crashes -- so a partial write or hostile truncation is safe."""
    blob = hamblin.encode(tree)
    cut = data.draw(st.integers(min_value=0, max_value=len(blob)))
    try:
        hamblin.decode(blob[:cut], REGISTRY)
    except HamblinError:
        pass
    except RecursionError as exc:  # pragma: no cover
        raise AssertionError("decode recursed on truncated input") from exc
