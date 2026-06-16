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

## Binder-naming O(n^2) cleanup (in progress, on top of bfefc51 = user moved format into notation.py)
- Confirmed quadratic in CURRENT code: format nested-forall depth 500/1000/2000 = 543/2404/9889 ms
  (2x depth -> 4x time). Cause: _format_binder_push called binder.body.free_vars() per binder
  (O(subtree) each -> O(n^2)) + _fresh_name scanned growing avoid-set.
- FIX applied in notation.py:
  * _Printer: added free (frozenset, whole-formula free names computed once), depth, _names cache,
    _raw_pos cursor; binder_name(depth) returns depth-th name not in free, O(1) amortized.
    Removed used field + fresh method + _fresh_name function (delete-first).
  * _format_node: printer.free = node.free_vars() once at entry.
  * _format_binder_push: name = printer.binder_name(printer.depth); depth++/restore in combine.
- Correctness: notation round-trip property tests PASS (11 notation tests green after fix).
  Depth-indexed names are collision-free (distinct per depth, all avoid free set) -> alpha-equiv
  round-trips hold.
- Timing AFTER fix: command output not flushing to background task file (tooling hiccup). Need to
  re-measure to PROVE linear before claiming. NOT yet verified-quoted.

## NEXT
- Re-run timing (foreground, maybe write to a file then read) to show linear scaling.
- Full suite + ruff + pyright. Commit cold-start. Then report.

## REMEASURED: STILL QUADRATIC after first fix (500/1000/2000 = 408/2349/11738 ms)
Root cause was misdiagnosed. The dominant O(n^2) is `instantiate(binder, Var(name))` per binder
in _format_binder_push -- each opens an O(subtree) body rebuild. n nested binders -> O(n^2).
(The free_vars()-per-binder I removed was a SECOND quadratic, real but not the dominant one.)

## REAL FIX (redoing): eliminate instantiate from formatting; render BVar via a scope stack.
- _Printer: keep free + _names + _raw_pos (depth-indexed naming); REPLACE bound dict + depth with
  scope: list[(name,sort)] of enclosing binders. depth = len(scope).
- BVar(i) format: render name of scope[len-1-i] (no sort -- binder shows it). dangling if out of range.
- Var format: ALWAYS show its sort (it's genuinely free now; no bound-suppression needed, since
  bound occurrences stay BVar -- we no longer instantiate them into Vars). Output identical.
- _format_binder_push: push (name,sort) on scope, eval binder.body (ORIGINAL, with BVar), NO
  instantiate; combine pops scope. O(1) per binder. Whole format O(n).
- Correctness identical (BVar->name == old opened-Var-with-suppressed-sort). Round-trip tests are spec.

## NEXT
- implement scope-stack render; re-measure (expect linear); full suite+ruff+pyright; commit.

## Scope-stack fix DONE + correct. Full suite 316 pass, pyright clean, ruff 2 errors (fixing).
Measured AFTER fix:
- binders (O(n) build via direct Forall): 1k/2k/4k/8k/16k/32k = 6/7/16/63/232/853 ms
- implications same depths: 4k/8k/16k/32k = 15/32/75/200 ms
=> binder now scales like implication. The binder-SPECIFIC quadratics (per-binder free_vars
   rescan + per-binder instantiate tree-rebuild) are GONE.
- Residual super-linear cost is GENERAL string assembly: building one big output string by
  bottom-up nesting f"...{body}" copies the accumulator each level -> O(n^2) char-copies.
  Affects implications + repr too (NOT binder-specific, pre-existing). Analysis matches numbers
  (binder ~5*n^2 char-copies).

## DECISION
Commit the binder-naming/instantiate fix (what Q asked; correct; eliminates binder-specific O(n^2)).
Report the residual general string-assembly O(n^2) with numbers + offer the emit-to-join rewrite
(would also speed repr). Don't unilaterally rewrite whole formatter -- bigger, affects repr, Q's call.

## NEXT
- fix 2 ruff errors in notation.py; commit cold-start; report with before/after numbers + offer.

## DONE: binder formatting O(n^2) eliminated (cold-start b732bfc)
- scope-stack render (BVar from scope, no instantiate) + depth-indexed names (free computed once).
- before->after (nested forall, format only): 500/1000/2000 = 544/2404/9890 ms -> 6/7/7 ms.
- 317 pass, ruff+pyright clean. Added 20k deep-binder format test under recursionlimit=300.
- RESIDUAL (reported to Q, his call): general string-assembly O(n^2) -- bottom-up f"...{body}"
  copies accumulator per level; affects repr + nested implications too, NOT binder-specific.
  Fix would be emit-to-list + join once (also speeds repr). Offered.

## emit-and-join rewrite (Q said "of course!") -- IN PROGRESS
Goal: make format() AND repr() truly O(n) by emitting string fragments left-to-right into a
list and join() once (no bottom-up f"...{body}" re-wrapping = no O(n^2) char copies).

- notation.py format(): DONE. _format_node -> pre-order emit; work stack of ("emit",node,prec)/
  ("lit",s)/("pop",); _emit + _emit_fun + _push. Replaced all the combine-based _format_*push.
  Parens decided top-down from prec; BVar from scope; binder pushes scope, ("pop",) closes after
  body. pyright clean, 12 notation tests pass. Output identical (parse(format)==x holds).
- syntax.py repr(): IN PROGRESS. Node.__repr__ -> emit-and-join driver (stack of ("emit",node)/
  ("lit",s), out list, join once). Base _repr_with -> _repr_emit(out,stack). Added module helper
  _emit_pieces(stack,pieces)=stack.extend(reversed(pieces)). Converting 8 nodes' _repr_with->
  _repr_emit: Var DONE. REMAINING: Fun(350), BVar(?), Eq, Implies, Bottom, Forall, Exists.
  (grep line numbers shifted +~7 after adding _emit_pieces.)

Output must stay byte-identical (existing repr tests + test_quantifier_repr_uses_the_locally_
nameless_form). Each _repr_emit pushes same literals as old _repr_with f-strings.

## emit-and-join DONE for both format() and repr()
- format() (notation.py) + repr() (syntax.py, all 8 nodes _repr_emit + Node.__repr__ driver +
  _emit_pieces helper). No stale _repr_with/_format_push/combine refs.
- 317 pass, pyright clean, ruff clean. Output identical (round-trip + repr tests green).

## DONE (cold-start b4c7407): format() + repr() now truly O(n)
- emit-and-join: pre-order walk appends fragments to one list, join once. No bottom-up wrapping.
- Measured linear (2x depth -> 2x time): format binders 8/16/32/64k = 19/39/79/156 ms
  (32k was 853ms before -> 79ms). repr nested Fun 8/16/32/64k = 4.9/9.9/20/40 ms.
- 317 pass, pyright + ruff clean. Output byte-identical (round-trip + repr tests green).

## WHOLE ARC COMPLETE
Pipeline fully iterative AND linear end-to-end: hamblin decode -> validate -> derive ->
sort_check -> repr/format. No call-stack recursion, no O(n^2) anywhere on the path. Only bound
is memory.

## Blocker
None. Task complete.
