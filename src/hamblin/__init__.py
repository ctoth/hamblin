"""hamblin -- a recursion-free binary serialization for frozen-dataclass trees.

A tree is written in postfix (children before parent) and read back by a stack
machine in one linear pass, so encode and decode never recurse: a term nested a
million deep costs heap memory, never Python call frames, and so never a
``RecursionError``. The format owns its structure (a postfix opcode stream, no
balanced delimiters); the scalar layer is LEB128/zigzag varints. Named for
Charles Hamblin, inventor of reverse Polish notation and the pushdown stack.

    >>> from dataclasses import dataclass
    >>> import hamblin
    >>> @dataclass(frozen=True)
    ... class Lit:
    ...     value: int
    >>> @dataclass(frozen=True)
    ... class Add:
    ...     left: object
    ...     right: object
    >>> tree = Add(Lit(1), Add(Lit(2), Lit(3)))
    >>> registry = {"Lit": Lit, "Add": Add}
    >>> hamblin.decode(hamblin.encode(tree), registry) == tree
    True

``encode`` reflects the dataclass fields and needs no schema. ``decode`` takes a
``registry`` mapping each struct's class name to its class -- the white-list of
shapes untrusted bytes are allowed to construct. Fields may be the scalar leaves
``str``/``int``/``bool``/``float``/``bytes``/``None``, other registered
dataclasses, or tuples/lists of those. Use ``encode_to``/``decode_from`` to
stream to/from a file or socket without holding the whole serialized blob.
"""

from __future__ import annotations

from ._codec import (
    MAGIC,
    HamblinError,
    decode,
    decode_from,
    encode,
    encode_to,
    iter_records,
)

__all__ = [
    "MAGIC",
    "HamblinError",
    "decode",
    "decode_from",
    "encode",
    "encode_to",
    "iter_records",
]

__version__ = "0.1.0"
