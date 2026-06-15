"""The hamblin codec: a postfix opcode stream driven by a value stack.

A tree is serialized in **post-order** -- every child's bytes appear before its
parent's -- so reconstruction is a single left-to-right pass over a value stack:
push a leaf, or pop a node's children and assemble it. There are no balanced
delimiters and no length-prefixed subtrees, so neither encoding nor decoding
recurses: a tree nested a million deep costs stack *memory*, never Python call
frames, and so never a ``RecursionError``. (Named for Charles Hamblin, who gave
us reverse Polish notation and the pushdown store that evaluates it.)

Scalars are LEB128/zigzag varints and length-prefixed bytes -- the boring solved
primitive layer WASM, DWARF, and protobuf all share. The structure on top is
ours.

The encoder needs no schema: it reflects the dataclass fields of every node. The
decoder needs a ``registry`` mapping each struct's class name to its class, so it
can rebuild ``cls(*fields)`` -- the same encode-is-open / decode-needs-a-registry
split a trust boundary wants, since decoded bytes are untrusted and the registry
is the white-list of constructible shapes.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterator
from dataclasses import fields, is_dataclass

# --- opcodes ---------------------------------------------------------------
# One tag byte per record. Leaf tags push a value; container tags pop and build.

_T_STR = 0x01
_T_INT = 0x02  # zigzag + LEB128
_T_BOOL = 0x03
_T_FLOAT = 0x04  # 8-byte IEEE-754 little-endian
_T_BYTES = 0x05
_T_NONE = 0x06
_T_TUPLE = 0x10  # followed by varint count
_T_STRUCT = 0x20  # followed by varint name-length, name bytes, varint field count

MAGIC = b"HMB1"  # format tag + version, so a wrong/old stream is rejected, not misread


class HamblinError(ValueError):
    """A stream is malformed, truncated, or names a shape absent from the registry.

    Subclasses ``ValueError`` so a caller's ``except ValueError`` catches every
    decode failure -- and, crucially, decode *never* raises ``RecursionError``,
    because nothing here recurses."""


# --- LEB128 varints --------------------------------------------------------


def _write_uvarint(out: bytearray, n: int) -> None:
    if n < 0:
        raise HamblinError(f"unsigned varint cannot encode {n}")
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return


def _zigzag(n: int) -> int:
    # Arbitrary-precision zigzag: map signed -> unsigned with no fixed width, so a
    # number wider than 64 bits encodes correctly (the fixed-width `n >> 63` form
    # is WRONG for Python's unbounded ints). 0,-1,1,-2,2 -> 0,1,2,3,4.
    return (n << 1) if n >= 0 else ((-n) << 1) - 1


def _unzigzag(u: int) -> int:
    return (u >> 1) ^ -(u & 1)


# --- a pull reader over an incremental byte source -------------------------


class _Reader:
    """Pulls bytes from a ``read(n) -> bytes`` source, one record at a time, so a
    multi-gigabyte stream is decoded without ever holding more than the value
    stack plus the current record. ``read`` may return short reads (a socket, a
    pipe); we loop until satisfied or the source is exhausted."""

    __slots__ = ("_read",)

    def __init__(self, read: Callable[[int], bytes]) -> None:
        self._read = read

    def take(self, n: int) -> bytes:
        chunks: list[bytes] = []
        need = n
        while need:
            chunk = self._read(need)
            if not chunk:
                raise HamblinError(f"truncated stream: wanted {n} bytes, short by {need}")
            chunks.append(chunk)
            need -= len(chunk)
        return b"".join(chunks) if len(chunks) != 1 else chunks[0]

    def byte(self) -> int:
        return self.take(1)[0]

    def opt_byte(self) -> int:
        """The next byte, or -1 at a clean end of stream (used only between
        complete records, where EOF is legal and means 'no more values')."""
        chunk = self._read(1)
        return -1 if not chunk else chunk[0]

    def uvarint(self) -> int:
        shift = 0
        result = 0
        while True:
            b = self.byte()
            result |= (b & 0x7F) << shift
            if not (b & 0x80):
                return result
            shift += 7
            if shift > 64 * 7:  # a varint this long is a malformed/hostile stream
                raise HamblinError("varint too long")


# --- encode ----------------------------------------------------------------


def _is_node(v: object) -> bool:
    return is_dataclass(v) and not isinstance(v, type)


def _emit_scalar(out: bytearray, v: object) -> None:
    # bool before int: bool IS an int subclass, and we want it round-tripped as bool.
    if isinstance(v, bool):
        out.append(_T_BOOL)
        out.append(1 if v else 0)
    elif isinstance(v, int):
        out.append(_T_INT)
        _write_uvarint(out, _zigzag(v))
    elif isinstance(v, str):
        data = v.encode("utf-8")
        out.append(_T_STR)
        _write_uvarint(out, len(data))
        out += data
    elif isinstance(v, bytes):
        out.append(_T_BYTES)
        _write_uvarint(out, len(v))
        out += v
    elif isinstance(v, float):
        out.append(_T_FLOAT)
        out += struct.pack("<d", v)
    elif v is None:
        out.append(_T_NONE)
    else:
        raise HamblinError(f"cannot serialize scalar of type {type(v).__name__}")


def encode_to(node: object, write: Callable[[bytes], object]) -> None:
    """Encode ``node`` to ``write`` (a ``write(bytes)`` sink), post-order and
    ITERATIVELY. The agenda is an explicit work stack; a ``("close", ...)`` entry
    is pushed *under* a node's fields so the node's own opcode is emitted only
    after every field has been written -- post-order without the call stack."""
    write(MAGIC)
    out = bytearray()
    # work items: ("v", value) to emit a value, or ("struct"/"tuple", meta) to close.
    stack: list[tuple] = [("v", node)]
    while stack:
        kind, payload = stack.pop()
        if kind == "struct":
            name, nfields = payload
            data = name.encode("utf-8")
            out.append(_T_STRUCT)
            _write_uvarint(out, len(data))
            out += data
            _write_uvarint(out, nfields)
            continue
        if kind == "tuple":
            out.append(_T_TUPLE)
            _write_uvarint(out, payload)
            continue
        v = payload
        if _is_node(v):
            fs = fields(v)
            stack.append(("struct", (type(v).__name__, len(fs))))
            for f in reversed(fs):  # reversed so field 0 is emitted first
                stack.append(("v", getattr(v, f.name)))
        elif isinstance(v, (tuple, list)):
            stack.append(("tuple", len(v)))
            for x in reversed(v):
                stack.append(("v", x))
        else:
            _emit_scalar(out, v)
        if len(out) >= 1 << 16:
            write(bytes(out))
            out.clear()
    if out:
        write(bytes(out))


def encode(node: object) -> bytes:
    """Serialize ``node`` (a tree of frozen dataclasses) to bytes."""
    chunks: list[bytes] = []
    encode_to(node, chunks.append)
    return b"".join(chunks)


# --- decode ----------------------------------------------------------------


def _build_struct(registry: dict, name: str, nfields: int, args: list) -> object:
    cls = registry.get(name)
    if cls is None:
        raise HamblinError(f"unknown struct {name!r} (not in registry)")
    if not (is_dataclass(cls) and isinstance(cls, type)):
        raise HamblinError(f"registry[{name!r}] is not a dataclass type")
    expected = len(fields(cls))
    if nfields != expected:
        raise HamblinError(f"{name}: stream has {nfields} fields, class has {expected}")
    return cls(*args)


def decode_from(read: Callable[[int], bytes], registry: dict) -> object:
    """Decode one value from ``read`` (a ``read(n) -> bytes`` source) against
    ``registry`` (struct-name -> dataclass), ITERATIVELY: a single pass that
    pushes leaves and pops-then-builds containers on a value stack. Never
    recurses, so a deeply nested stream decodes (or is cleanly rejected) without
    a ``RecursionError``."""
    r = _Reader(read)
    magic = r.take(len(MAGIC))
    if magic != MAGIC:
        raise HamblinError(f"bad magic {magic!r} (not a hamblin stream)")
    stack: list = []
    while True:
        tag = r.opt_byte()
        if tag == -1:  # clean end of stream between records
            break
        if tag == _T_STR:
            stack.append(r.take(r.uvarint()).decode("utf-8"))
        elif tag == _T_INT:
            stack.append(_unzigzag(r.uvarint()))
        elif tag == _T_BOOL:
            stack.append(r.byte() != 0)
        elif tag == _T_FLOAT:
            stack.append(struct.unpack("<d", r.take(8))[0])
        elif tag == _T_BYTES:
            stack.append(r.take(r.uvarint()))
        elif tag == _T_NONE:
            stack.append(None)
        elif tag == _T_TUPLE:
            count = r.uvarint()
            if count > len(stack):
                raise HamblinError(f"tuple wants {count} values, stack has {len(stack)}")
            items = stack[len(stack) - count :]
            del stack[len(stack) - count :]
            stack.append(tuple(items))
        elif tag == _T_STRUCT:
            name = r.take(r.uvarint()).decode("utf-8")
            nfields = r.uvarint()
            if nfields > len(stack):
                raise HamblinError(
                    f"struct {name!r} wants {nfields} fields, stack has {len(stack)}"
                )
            args = stack[len(stack) - nfields :]
            del stack[len(stack) - nfields :]
            stack.append(_build_struct(registry, name, nfields, args))
        else:
            raise HamblinError(f"unknown opcode 0x{tag:02x}")
    # The stream encodes exactly one root value: post-order means the root is the
    # last record built, and a well-formed stream leaves it alone on the stack.
    if len(stack) != 1:
        raise HamblinError(f"stream did not encode exactly one value (stack holds {len(stack)})")
    return stack[0]


def decode(data: bytes, registry: dict) -> object:
    """Decode a value from a ``bytes`` blob against ``registry``."""
    pos = 0

    def read(n: int) -> bytes:
        nonlocal pos
        if n == 0:
            return b""
        chunk = data[pos : pos + n]
        pos += len(chunk)
        return chunk

    return decode_from(read, registry)


def iter_records(data: bytes) -> Iterator[tuple]:
    """Yield ``(opcode_name, detail)`` for each record -- a debugging window onto
    the flat stream, proving there is no nesting to recurse through."""
    names = {
        _T_STR: "STR", _T_INT: "INT", _T_BOOL: "BOOL", _T_FLOAT: "FLOAT",
        _T_BYTES: "BYTES", _T_NONE: "NONE", _T_TUPLE: "TUPLE", _T_STRUCT: "STRUCT",
    }
    pos = len(MAGIC)
    n = len(data)

    def uvarint() -> int:
        nonlocal pos
        shift = result = 0
        while True:
            b = data[pos]
            pos += 1
            result |= (b & 0x7F) << shift
            if not (b & 0x80):
                return result
            shift += 7

    while pos < n:
        tag = data[pos]
        pos += 1
        if tag == _T_STR or tag == _T_BYTES:
            ln = uvarint()
            pos += ln
            yield (names[tag], ln)
        elif tag == _T_INT:
            yield (names[tag], _unzigzag(uvarint()))
        elif tag == _T_BOOL:
            v = data[pos]
            pos += 1
            yield ("BOOL", bool(v))
        elif tag == _T_FLOAT:
            pos += 8
            yield ("FLOAT", None)
        elif tag == _T_NONE:
            yield ("NONE", None)
        elif tag == _T_TUPLE:
            yield ("TUPLE", uvarint())
        elif tag == _T_STRUCT:
            ln = uvarint()
            name = data[pos : pos + ln].decode("utf-8")
            pos += ln
            yield ("STRUCT", (name, uvarint()))
        else:
            raise HamblinError(f"unknown opcode 0x{tag:02x}")
