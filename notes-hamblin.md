# hamblin — build notes

## What this is
A binary serialization format + codec for trees of frozen dataclasses, with a
**linear (iterative) reading**: no recursion in encode or decode, so arbitrarily
deep / gigabyte trees never hit a `RecursionError`. Named for Charles Hamblin,
who invented reverse Polish notation and the pushdown stack that evaluates it —
which is exactly the design: **postfix (children-before-parent) opcode stream
driven by a value stack.** Pickle's VM model, WASM's varints, none of JSON's
balanced delimiters.

Built standalone so cold-start can depend on it as a git dep, same as
`physgen` depends on `bridgman` (`{ git = "https://github.com/ctoth/<name>" }`).

## Format (the whole thing)
Post-order opcode stream. Each opcode pushes a leaf or pops-and-assembles:
- scalars: STR/INT(zigzag)/BOOL/FLOAT/BYTES/NONE  -> push value
- TUPLE <count>                                   -> pop count, push tuple
- STRUCT <name> <nfields>                          -> pop nfields, push cls(*args)
Lengths/counts/tags are LEB128 unsigned varints; ints zigzag+LEB128.
Encoder: iterative post-order (explicit work stack, deferred close-op). No registry
needed (reflects dataclass fields). Decoder: linear scan + value stack; takes a
name->class registry. Mirrors cold-start's encode_node/decode_node split.

## API
encode(node)->bytes ; decode(bytes, registry)->node ;
encode_to(node, write) ; decode_from(read, registry)  # streaming

## Property tests (Hypothesis)
- round-trip: decode(encode(t), reg) == t  for random dataclass trees
- depth invariance: 100k-deep skinny tree round-trips, NO RecursionError
- streaming == one-shot
- malformed/truncated bytes -> clean ValueError, never crash/hang/RecursionError
- unknown struct name / arity mismatch -> ValueError

## State / progress
- [x] scaffolded ~/code/hamblin/{src/hamblin,tests}
- [x] confirmed gh auth (account ctoth, ssh), uv 0.8.8, gh 2.87.3
- [x] mirrored bridgman pyproject style (uv_build backend, src layout, MIT, Q author)
- [x] src/hamblin/_codec.py (varints, encode_to/encode, decode_from/decode, iter_records)
- [x] src/hamblin/__init__.py (public API + docstring + version)
- [x] pyproject.toml, README.md, .gitignore
- [x] tests/conftest.py (Leaf/Branch + REGISTRY; Tip/Chain + DEEP_REGISTRY)
- [ ] tests/test_codec.py (units, streaming, malformed, deep-no-recursion)
- [ ] tests/test_properties.py (Hypothesis: round-trip, streaming==oneshot, hostile bytes total)
- [ ] uv run pytest (quote summary), uv run ruff/pyright
- [ ] git init + commit
- [ ] gh repo create ctoth/hamblin --public --source . --push
- [ ] FOLLOW-UP (separate): wire cold-start to depend on hamblin, drop json from verify.py

## Key design notes / gotchas observed
- Fixed end-of-stream bug: decode loops reading opcodes until opt_byte()==-1 (clean
  EOF), then asserts stack==[root]. Earlier len==1 check misfired mid-stream.
- Deep-tree tests must NOT use Python `==` on the result: stock dataclass __eq__
  recurses and would blow the stack (a harness artifact, not hamblin). Compare via
  `encode(decode(blob))==blob` (encode is iterative) + iterative depth walk. Set a
  low recursionlimit in that test to PROVE no recursion.
- Hypothesis floats: allow_nan=False (NaN!=NaN breaks round-trip equality).
- bool checked before int in _emit_scalar (bool is an int subclass).

## Test run 1 results (uv run pytest)
- 26 passed after fixes. pyright: 0 errors/0 warnings.
- BUG FOUND BY HYPOTHESIS: `_zigzag` used fixed-width 64-bit form (`n >> 63`),
  WRONG for Python unbounded ints; -(2**64+1) corrupted. Fixed to arbitrary-precision
  `(n<<1) if n>=0 else ((-n)<<1)-1`. Verified against _unzigzag.
- Test bug: `test_every_truncation_is_clean` asserted EVERY prefix raises, but a
  prefix can end on a complete SUB-value and decode fine. Rewrote -> "raises
  HamblinError or returns, never crashes". Renamed test_every_truncation_is_handled.

## Ruff cleanup (in progress)
- UP035: import Callable/Iterator from collections.abc (done)
- E501 line 260 wrapped (done)
- B904 x3: add `from exc` -- test_codec.py done; test_properties.py:93 done;
  test_properties.py:108 ("truncated input") STILL TODO (replace_all only hit the
  identical-text one).

## NEXT
- fix last B904 (test_properties.py:108), re-run ruff (expect clean) + pytest
- git init + commit; gh repo create ctoth/hamblin --public --source . --push
- FOLLOW-UP: wire cold-start dep, drop json from verify.py

## Blocker
None.
