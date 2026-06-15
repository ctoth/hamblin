"""Unit, streaming, malformed-input, and depth tests for the hamblin codec."""

from __future__ import annotations

import io
import sys

import pytest
from conftest import DEEP_REGISTRY, REGISTRY, Branch, Chain, Leaf, Tip

import hamblin
from hamblin import HamblinError


def _leaf(i=0):
    return Leaf(s="x", i=i, b=True, f=1.5, raw=b"\x00\x01", opt=None)


# --- round-trips -----------------------------------------------------------


def test_single_leaf_round_trips():
    t = _leaf()
    assert hamblin.decode(hamblin.encode(t), REGISTRY) == t


def test_nested_round_trips():
    t = Branch("root", 3, (_leaf(1), Branch("mid", -2, (_leaf(2),)), _leaf(3)))
    assert hamblin.decode(hamblin.encode(t), REGISTRY) == t


def test_empty_tuple_field():
    t = Branch("leafless", 0, ())
    assert hamblin.decode(hamblin.encode(t), REGISTRY) == t


@pytest.mark.parametrize(
    "value",
    [
        Leaf(s="", i=0, b=False, f=0.0, raw=b"", opt=None),
        Leaf(s="únïçødé 🜍", i=-(10**30), b=True, f=-3.5e10, raw=bytes(range(256)), opt=7),
        Leaf(s="z", i=2**64 + 1, b=False, f=float("inf"), raw=b"\xff", opt=-1),
    ],
)
def test_scalar_edges_round_trip(value):
    assert hamblin.decode(hamblin.encode(value), REGISTRY) == value


def test_bool_stays_bool_not_int():
    t = Leaf(s="", i=1, b=True, f=0.0, raw=b"", opt=None)
    back = hamblin.decode(hamblin.encode(t), REGISTRY)
    assert back.b is True and isinstance(back.b, bool)
    assert isinstance(back.i, int) and not isinstance(back.i, bool)


def test_negative_zero_float():
    t = Leaf(s="", i=0, b=False, f=-0.0, raw=b"", opt=None)
    back = hamblin.decode(hamblin.encode(t), REGISTRY)
    import math

    assert math.copysign(1.0, back.f) == -1.0


def test_starts_with_magic():
    assert hamblin.encode(_leaf()).startswith(hamblin.MAGIC)


# --- streaming -------------------------------------------------------------


def test_encode_to_decode_from_file_like():
    t = Branch("root", 3, (_leaf(1), _leaf(2)))
    buf = io.BytesIO()
    hamblin.encode_to(t, buf.write)
    buf.seek(0)
    assert hamblin.decode_from(buf.read, REGISTRY) == t


def test_streaming_equals_one_shot_under_chunked_reads():
    t = Branch("r", 1, (Branch("a", 2, (_leaf(9),)), _leaf(8)))
    blob = hamblin.encode(t)

    # a reader that hands back at most one byte at a time -- worst-case short reads
    pos = 0

    def dribble(n: int) -> bytes:
        nonlocal pos
        if n == 0:
            return b""
        chunk = blob[pos : pos + 1]
        pos += len(chunk)
        return chunk

    assert hamblin.decode_from(dribble, REGISTRY) == t


# --- malformed / hostile input: always HamblinError, never a crash ---------


def test_empty_input_rejected():
    with pytest.raises(HamblinError):
        hamblin.decode(b"", REGISTRY)


def test_bad_magic_rejected():
    with pytest.raises(HamblinError):
        hamblin.decode(b"XXXXabcd", REGISTRY)


def test_unknown_opcode_rejected():
    with pytest.raises(HamblinError):
        hamblin.decode(hamblin.MAGIC + b"\xff", REGISTRY)


def test_unknown_struct_name_rejected():
    blob = hamblin.encode(_leaf())
    with pytest.raises(HamblinError):
        hamblin.decode(blob, {})  # empty registry: "Leaf" unknown


def test_arity_mismatch_rejected():
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class LeafWrong:  # same name slot, different field count
        a: int

    blob = hamblin.encode(_leaf())
    with pytest.raises(HamblinError):
        hamblin.decode(blob, {"Leaf": LeafWrong})


def test_two_values_rejected_as_one_stream():
    blob = hamblin.MAGIC + b"\x02\x02" + b"\x02\x02"  # two INT records
    with pytest.raises(HamblinError):
        hamblin.decode(blob, REGISTRY)


def test_tuple_underflow_rejected():
    blob = hamblin.MAGIC + b"\x10\x05"  # TUPLE wanting 5 with an empty stack
    with pytest.raises(HamblinError):
        hamblin.decode(blob, REGISTRY)


def test_every_truncation_is_handled():
    # Every proper prefix either cleanly rejects (HamblinError) or happens to be a
    # complete shorter stream that decodes -- but NEVER crashes with anything else.
    blob = hamblin.encode(Branch("r", 1, (_leaf(1), _leaf(2))))
    for k in range(len(blob)):
        try:
            hamblin.decode(blob[:k], REGISTRY)
        except HamblinError:
            pass
        except RecursionError as exc:
            raise AssertionError(f"decode recursed on prefix len {k}") from exc
    assert hamblin.decode(blob, REGISTRY) is not None  # the whole thing still works


# --- the headline guarantee: no recursion, ever ----------------------------


def _build_chain(depth: int):
    cur: object = Tip()
    for _ in range(depth):
        cur = Chain(cur)
    return cur


def test_deep_chain_no_recursion_error():
    """A 100k-deep tree round-trips with the recursion limit set far below the
    depth -- proof that neither encode nor decode uses the call stack. Equality is
    checked via the (iterative) encoder, never Python's recursive dataclass __eq__."""
    depth = 100_000
    t = _build_chain(depth)

    old = sys.getrecursionlimit()
    sys.setrecursionlimit(200)  # < 0.2% of the tree depth
    try:
        blob = hamblin.encode(t)
        back = hamblin.decode(blob, DEEP_REGISTRY)
        # round-trip proven without recursive ==: re-encoding must reproduce bytes
        assert hamblin.encode(back) == blob
    finally:
        sys.setrecursionlimit(old)

    # confirm the decoded structure really is `depth` Chains deep (iterative walk)
    n = 0
    cur = back
    while isinstance(cur, Chain):
        n += 1
        cur = cur.inner
    assert n == depth and isinstance(cur, Tip)
