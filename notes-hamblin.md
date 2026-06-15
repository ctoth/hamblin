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

## DONE
- ruff clean, pyright clean, 26 pytest pass.
- committed (6e98c6c), pushed to https://github.com/ctoth/hamblin (public, default branch master).
- verified `uv add git+https://github.com/ctoth/hamblin` resolves/builds/installs and a
  round-trip works against the installed package (the physgen->bridgman pattern).

## FOLLOW-UP (not yet done -- separate task)
Wire cold-start to depend on hamblin and rip JSON out of the verifier:
- pyproject: dependencies += "hamblin"; [tool.uv.sources] hamblin = { git = "https://github.com/ctoth/hamblin" }
- syntax.py: replace encode_node/decode_node/term_*_dict/formula_*_dict with hamblin.encode/decode + SYNTAX_REGISTRY
- proof.py: to_dict/from_dict/to_json/from_json -> hamblin (bytes), _PROOF_REGISTRY
- verify.py: read bytes (stdin.buffer / 'rb'), drop json import; except (HamblinError=ValueError, TypeError)
  now covers deep input -> clean REJECTED, no RecursionError at the front door.
- add a cold-start test: 50k-deep proof round-trips through hamblin where json.loads died.

## WIRING cold-start (in progress, 2026-06-15)
API rename JSON->bytes (delete-first, no shims):
- syntax.py: DONE. added `import hamblin`; replaced encode_node/_encode_value/decode_node
  /term_*_dict/formula_*_dict with term_to_bytes/term_from_bytes/formula_to_bytes/
  formula_from_bytes (hamblin.encode/decode + SYNTAX_REGISTRY, kept). isinstance guard
  -> ValueError.
- proof.py: IN PROGRESS. drop `import json`, encode_node/decode_node imports; add
  `import hamblin`. Replace to_dict/from_dict/to_json/from_json with to_bytes/from_bytes
  (hamblin + _PROOF_REGISTRY, kept). Also update module docstring "serializable to JSON"
  -> bytes.
- verify.py: TODO. read bytes (open 'rb' / sys.stdin.buffer.read()), from_bytes; drop json.
  except (ValueError,TypeError) already covers HamblinError(=ValueError) -> deep input now
  cleanly REJECTED, no RecursionError at the front door.
- pyproject.toml: TODO. dependencies=["hamblin"]; [tool.uv.sources] hamblin={git=...}; uv sync.

Tests to update (preserve asserted properties; drop format-internal `["k"]` asserts; drop JSON names):
- test_properties.py: imports; *_JSON_EXAMPLES->*_EXAMPLES; 3 "json_kind" tests -> bytes round-trip
  (keep type+eq, drop ["k"]); lines 184/189/194-196/313/340 to_bytes/from_bytes; determinism
  to_bytes(pf)==to_bytes(pf). ADD deep-proof (50k) round-trip+check under low recursionlimit.
- test_checker.py: drop `import json`; import from_bytes/to_bytes; line 329 bytes;
  _run_verify -> bytes (input=bytes, text=False, decode stdout); forged-axiom test -> to_bytes(
  P.Axiom(bad)) instead of json.dumps dict.

## Wiring progress (update 2)
- syntax.py DONE, proof.py DONE, verify.py DONE, pyproject DONE + `uv sync` installed
  hamblin from git OK.
- test_properties.py: imports DONE; *_EXAMPLES renamed DONE; 3 kind tests + term/formula/
  proof roundtrips + determinism rewritten to bytes DONE. REMAINING: lines 307 & 334
  (from_json(to_json)) -> from_bytes(to_bytes); ADD deep-proof test.
- test_checker.py: NOT STARTED (drop json import; from_bytes/to_bytes; _run_verify ->
  bytes input/text=False/decode stdout; forged-axiom -> to_bytes(P.Axiom(bad))).
- THEN: uv run pytest / ruff / pyright; commit cold-start; commit hamblin notes.

## Wiring DONE + verified (update 3)
- All source + tests updated. `uv run pytest`: 312 passed. ruff clean. pyright (cold_start) clean.
- End-to-end CLI proof:
  - truncated blob   -> rc 1, REJECTED, NO traceback (clean)
  - garbage bytes    -> rc 1, REJECTED, NO traceback (clean)  <-- front door fixed
  - deep VALID proof -> CRASHES, but in Fun.__repr__ (syntax.py:304) while PRINTING the
    "VERIFIED: <sequent>" echo. NOT the trust/decode path (decode+check succeed). Pre-existing:
    every __repr__/format() recurses; my fix just made it reachable (json.loads used to die first).
- This is the OUTPUT path, distinct from the wiring goal. Decode+check are iterative & verified.

## REMAINING / FOLLOW-UP (surface to Q, his call)
- Iterative __repr__ + format() across all nodes so the verifier can echo a deep sequent.
  Same disease (recursion), one more place. Sizable (touches every node + notation.py).
- about to commit cold-start wiring (green unit); then report repr finding + offer.

## Iterative output path (in progress, after commit 4bbc1cf)
Goal: make __repr__ (and later format()) non-recursive so the verifier can echo a
deep sequent. Wiring already committed + clean tree.

__repr__ approach (mirrors iterative __hash__):
- Node.__repr__: post-order over children(), build reprs dict by id, each node
  overrides _repr_with(reprs). Base Node._repr_with raises NotImplementedError. DONE.
- Each node dataclass: add repr=False to decorator, __repr__ body -> _repr_with(reprs).
  DONE: Var, Fun, BVar, Eq, Implies, Bottom, Forall. REMAINING: Exists.
- Sequent.__repr__ (sequent.py): NO change needed -- only 1 level deep, calls repr() on
  its formulas which is now iterative.

NEXT:
- Exists._repr_with (last one)
- run pytest/ruff/pyright; verify CLI now prints a deep valid proof (rc 0, VERIFIED, no traceback)
- add a test: repr of a 50k-deep term under low recursionlimit doesn't raise
- commit __repr__ work
- THEN format() iterative -- HARDER (top-down ctx threading, precedence, binder opens body
  with fresh name). Not on verifier path (verify uses __repr__, not format). Assess separately.

## __repr__ DONE + committed (9abb6fe)
- All 8 nodes -> _repr_with + repr=False; Node.__repr__ iterative driver. 313 pass, clean.
- CLI: 20k-deep VALID proof now rc 0 VERIFIED, no traceback. Output path fixed for repr.

## format() iterative (in progress now)
Design: Node.format = explicit DFS. work stack of ("eval",node,pprec) / ("combine",fn,nargs);
parallel values stack. Each node overrides _format_push(ctx,pprec,work): append combine then
children (reversed so arg0 renders first). Binder mutates ctx on push, its combine restores
(enter/exit bracket the subtree -- faithful DFS). Base Node.format + _format_push DONE.
Per-node _format_push: Var DONE, Fun DONE (renamed 2nd closure combine_app to dodge pyright
redeclare). REMAINING: BVar (raise ValueError dangling), Eq, Implies (Not special), Bottom,
_format_binder -> _binder_format_push + Forall/Exists.
Spec to refactor against: tests/test_notation.py has @given round-trips parse(format(x))==x
over terms()/formulas() -- if output identical, they pass.

NEXT after format: add deep-format test (50k under low recursionlimit); pytest/ruff/pyright;
commit. format() NOT on verifier path (verify uses repr) but Q wants iterative-everything.

## format() iterative DONE (not yet committed)
- All node format -> _format_push; Node.format = iterative DFS driver; _format_binder ->
  _binder_format_push (ctx enter on push, restore in combine). Full suite: 313 pass; notation
  round-trip property tests (parse(format(x))==x) green -> output identical to recursive form.
- BUG FOUND + FIXED en route: Forall/Exists decorators were missing repr=False, so dataclass
  generated a recursive __repr__ shadowing _repr_with. No existing test reprs a quantifier, so
  it slipped 313-green. Added repr=False to both.

## NEXT (then commit format work)
- Add 2 regression tests to test_notation.py:
  (a) repr of forall("x","N",Eq(x,x)) == "(forall :N. #0 = #0)"  [guards the repr=False fix]
  (b) deep format: format_formula of a 50k-deep Implies chain under recursionlimit=300 doesn't
      raise. (Use implications NOT nested binders -- binder fresh-naming is O(n^2).)
- run pytest/ruff/pyright; commit syntax.py + test_notation.py.
- Then report to Q: repr + format both iterative; verifier echoes deep sequents; quantifier
  repr bug fixed. Output path fully de-recursed.

## ALL DONE (cold-start commits 4bbc1cf, 9abb6fe, 783daf7)
- hamblin wired in; JSON gone from verifier front door.
- __repr__ iterative (9abb6fe). format() iterative + Forall/Exists repr=False bug fixed (783daf7).
- Full output path de-recursed. 315 pass, ruff + pyright clean. Nested binders verified.
- Whole pipeline now iterative end to end: decode (hamblin) -> validate -> derive -> sort_check
  -> repr/format. The only bound is memory.

## Blocker
None. Task complete.
