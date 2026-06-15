# hamblin

Recursion-free binary serialization for trees of frozen dataclasses.

A tree is written in **postfix** — every child's bytes come before its parent's —
and read back by a **stack machine** in a single left-to-right pass. So neither
encoding nor decoding ever recurses: a value nested a million deep costs heap
memory, never Python call frames, and therefore **never a `RecursionError`**. The
only bound is memory, which already held the data.

Named for [Charles Hamblin](https://en.wikipedia.org/wiki/Charles_Leonard_Hamblin),
who gave us reverse Polish notation and the pushdown stack that evaluates it —
which is exactly the design here.

## Why not JSON / CBOR / protobuf?

They encode nesting as **balanced delimiters** (`{}`/`[]`, `l…e`) or
**length-prefixed subtrees**. Delimiters have no linear reading — you recurse, or
keep an explicit stack to track open containers — and length-prefixed subtrees
force you to buffer each subtree. Either way deep input either blows the call
stack (`RecursionError`) or makes you cap depth. hamblin's grammar has a natural
linear reading: it's a flat opcode stream, like a tiny bytecode.

```
STRUCT/TUPLE/STR/INT/BOOL/FLOAT/BYTES/NONE   # one tag byte per record
```

A leaf opcode pushes a value; `TUPLE n` / `STRUCT name n` pops `n` already-built
children and pushes the assembled node. Lengths and counts are LEB128 varints;
ints are zigzag + LEB128. That's the whole format.

## Use

```python
from dataclasses import dataclass
import hamblin

@dataclass(frozen=True)
class Lit:
    value: int

@dataclass(frozen=True)
class Add:
    left: object
    right: object

tree = Add(Lit(1), Add(Lit(2), Lit(3)))
registry = {"Lit": Lit, "Add": Add}

blob = hamblin.encode(tree)                  # bytes
assert hamblin.decode(blob, registry) == tree
```

`encode` reflects dataclass fields and needs no schema. `decode` takes a
`registry` (class-name → class) — the white-list of shapes untrusted bytes may
construct. A struct whose name is absent, or whose field count disagrees with the
class, is a clean `HamblinError` (a `ValueError`), never a crash.

Fields may be the scalar leaves `str`, `int`, `bool`, `float`, `bytes`, `None`,
other registered dataclasses, or tuples/lists of those.

### Streaming

```python
with open("tree.hmb", "wb") as f:
    hamblin.encode_to(tree, f.write)

with open("tree.hmb", "rb") as f:
    tree = hamblin.decode_from(f.read, registry)
```

`decode_from` holds only the value stack plus the current record, so a
multi-gigabyte stream decodes without ever materializing the whole blob.

## Guarantees (all property-tested with Hypothesis)

- **Round-trip:** `decode(encode(t), reg) == t` for arbitrary dataclass trees.
- **No recursion:** a 100k-deep tree round-trips with no `RecursionError`.
- **Streaming == one-shot:** chunked reads/writes give identical results.
- **Total decode:** malformed, truncated, or hostile bytes raise `HamblinError`
  (a `ValueError`) — never a crash, hang, or `RecursionError`.

## License

MIT
