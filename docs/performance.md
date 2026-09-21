# Framing benchmarks

These are component measurements of byte framing and candidate assembly, not
end-to-end CLI timings. Predicate execution, subprocess startup, and the user's
predicate can dominate an actual reduction.

## v0.2.0 leading-prefix scan

`frame_stream` recognizes a UTF-8 BOM only at byte zero. It then uses one regular
expression match for the consecutive CR/LF prefix, and counts CR plus LF minus
CRLF over that original byte range to obtain its line count. The remaining frame
splitter is unchanged. Spaces, other control bytes, and a second BOM stop the
fixed prefix.

The comparison below ran on Windows 11 / CPython 3.13.9. It loaded the exact
`bb6b031` source and the current source into one process, alternated their order
over seven one-call rounds, and measured allocation separately. Complete samples
and peak allocations are saved in
[the raw observation](../benchmarks/results/v0.2.0-framing-alternating.json).

| Input | bb6b031 median | v0.2.0 median | bb6b031 / v0.2.0 |
| --- | ---: | ---: | ---: |
| One 10 MiB event | 155.767 ms | 148.024 ms | 1.052x |
| 10,000 LF events | 61.457 ms | 62.265 ms | 0.987x |
| 10,000 CRLF events | 62.596 ms | 66.939 ms | 0.935x |
| 1 MiB LF prefix after BOM | 1,280.518 ms | 9.426 ms | 135.855x |
| 1 MiB CR prefix after BOM | 1,280.957 ms | 6.972 ms | 183.718x |
| 1 MiB CRLF prefix after BOM | 688.526 ms | 8.046 ms | 85.574x |
| 1 MiB mixed CR/CRLF/LF prefix after BOM | 1,123.163 ms | 9.538 ms | 117.760x |
| 1 MiB CRLF prefix + 10,000 LF events | 776.322 ms | 77.877 ms | 9.969x |

The dense-prefix cases exceed the 2x target by a wide margin. Long-line,
event-heavy, and assembly controls were not changed by this optimization.
Their alternating samples overlap; small median differences above are timing
noise, not a claimed improvement or regression. No wall-clock threshold belongs
in CI.

Candidate assembly controls (8 MiB body, 256 frames) measured 5.464 ms versus
5.407 ms with an empty prefix and 4.692 ms versus 4.801 ms with a BOM prefix.
Both versions had the same traced peaks: 8,411,561 bytes and 8,411,692 bytes,
respectively.

Correctness was checked separately against an independent byte-at-a-time scanner:
all 19,682 strings of length 0 through 8 over `{A, CR, LF}`, each with and
without a leading BOM. Prefix bytes, frame bytes, line numbers, completion flags,
full joins, and representative valid frame selections agreed. Targeted cases also
cover spaces, a non-line-ending control byte, a second BOM, mixed `CR CRLF LF`
line counts, and prefix-only streams.

## Run the benchmark

The default command needs only the installed/current package, so it also works
from a source distribution without Git history. It prints seven raw samples,
their median, and a separately measured allocation peak for each input.

```console
python benchmarks/benchmark_framing.py
```

For a historical comparison, use a full Git checkout that contains `bb6b031`.
The explicit flag extracts that revision to a temporary package and alternates it
with the current implementation in the same process. Save the complete JSON when
recording an observation.

```console
python benchmarks/benchmark_framing.py --compare-baseline --output benchmarks/results/local.json
```

Do not compare wall-clock measurements across machines or enable `-X tracemalloc`
while timing; the script measures allocation in a separate phase.

## Historic v0.1 observations

The older [before](../benchmarks/results/before.json),
[after](../benchmarks/results/after.json),
[dense LF](../benchmarks/results/dense-blank-lf.json), and
[assembly](../benchmarks/results/join-interleaved.json) files record prior v0.1
work. They are retained for provenance only and are not v0.2.0 performance claims.
