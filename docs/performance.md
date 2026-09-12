# Framing and assembly benchmarks

Measured on Windows / CPython 3.13.9 against the pre-optimization implementation.
These are synthetic component measurements, not end-to-end CLI speedups.
The demo still makes 70 fresh predicate calls; subprocess startup and the user's
predicate can dominate total runtime.

The optimization replaces a Python loop over every byte with a compiled,
streaming CR/LF/CRLF matcher. It also joins a nonempty fixed prefix and event bodies
in one allocation. The empty-prefix path stays unchanged, including the single
frame fast path. No input limit, predicate repetition, call budget or minimality
rule changed.

## Framing

Each timing is the median of five rounds, with three calls per round and a warmup.
Memory tracing ran separately from timing.

| Synthetic input | Before | After | Comparison |
| --- | ---: | ---: | ---: |
| One 10 MiB event | 677.274 ms | 95.721 ms | 7.08× faster |
| 10,000 LF events, 1,000,000 bytes | 82.775 ms | 29.557 ms | 2.80× faster |
| 10,000 CRLF events, 1,000,000 bytes | 96.519 ms | 29.152 ms | 3.31× faster |
| 256 KiB of LF-only blank prefix | 147.471 ms | 175.169 ms | 18.8% slower |

Dense blank lines allocate many small match/line objects, so that case can be
slower. Very short streams may also see small overhead. Keep these controls when
evaluating future changes; this optimization primarily helps longer event bodies.

## Candidate assembly

To reduce timing drift, this comparison loaded both implementations in one
process and alternated their order over seven rounds of 25 calls. Input bodies
were 8 MiB. Allocation figures are peak traced Python bytes during one join,
excluding pre-existing input storage and the rest of the application.

| Case | Before | After | Peak bytes before → after |
| --- | ---: | ---: | ---: |
| BOM + 256 frames | 5.7098 ms | 2.7899 ms | 16,777,373 → 8,411,692 |
| Empty prefix + 256 frames | 4.0429 ms | 3.7870 ms | 8,411,561 → 8,411,561 |
| Empty prefix + one frame | 0.0005 ms | 0.0005 ms | 552 → 552 |

The earlier separate-process run showed the empty-prefix control at 2.554 ms
before and 3.086 ms after. Treat these small timing differences as noise, not
evidence of an improvement in that unchanged path. The eliminated full-body copy
is the reliable memory improvement for nonempty prefixes.

## Reproduce and inspect

After installing the project, run from the repository root:

```console
python benchmarks/benchmark_framing.py
```

The script measures the current implementation, prints JSON and includes the
dense-blank-line control. The before/after comparisons and exhaustive equivalence
check below record earlier manual experiments; the script does not rerun those
comparisons or include the earlier implementation. Do not enable
`-X tracemalloc` when comparing timings: the script measures allocation peaks in a
separate phase. Results depend on CPU, allocator, system load and input shape.
There are no wall-clock thresholds in CI.

Raw observations: [before](../benchmarks/results/before.json),
[after](../benchmarks/results/after.json),
[alternating join comparison](../benchmarks/results/join-interleaved.json), and
[dense LF comparison](../benchmarks/results/dense-blank-lf.json).

Validation covered 19,682 cases: every byte string of length 0–8 over `{A, CR, LF}`,
with and without BOM. Prefixes, raw frames, line numbers, completion flags
and representative frame selections matched the original implementation. A new
memory regression failed before the change and passed afterward; its threshold
allows allocation overhead but rejects a second complete body copy. The original
failure-predicate and CLI regression suite also passed.
