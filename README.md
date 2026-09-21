# SSE Shrink

Shrink a failure-inducing LLM event stream into a small, reproducible Python test.

[简体中文](README.zh-CN.md)

![SSE Shrink workflow](docs/demo.svg)

SSE Shrink removes whole Server-Sent Events while repeatedly asking your Python
predicate whether the **same target failure** still occurs. It applies the mature
[delta debugging](https://www.st.cs.uni-saarland.de/papers/tse2002/) approach to SSE
failure samples, preserves the original bytes of every retained event, and exports
an auditable HTTPX/pytest reproduction bundle.

It is useful when a long captured response fails in a parser, SDK, gateway, or AI
application, but most events are irrelevant to the failure.

## Quick start

SSE Shrink v0.2.0 requires Python 3.11 or newer. Install the wheel from the
[GitHub Release](https://github.com/cloudwallker/SSE-Shrink/releases/tag/v0.2.0)
or use a source checkout. This project is not published on PyPI.

Create a virtual environment:

```console
python -m venv .venv
```

Activate the virtual environment on macOS/Linux:

```sh
source .venv/bin/activate
```

Or on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, replace `python` in the commands below with
`.\.venv\Scripts\python.exe`.

Download `sse_shrink-0.2.0-py3-none-any.whl` from the Release into the current
directory, then install it and pytest for the exported reproduction test:

```console
python -m pip install ./sse_shrink-0.2.0-py3-none-any.whl "pytest>=8,<10"
```

Alternatively, install the v0.2.0 source checkout with development tools:

```console
git clone --branch v0.2.0 https://github.com/cloudwallker/SSE-Shrink.git
python -m pip install -e "./SSE-Shrink[dev]"
```

With either installation, run the offline demo:

```console
python -m sse_shrink demo --output demo-output --json
python -m pytest demo-output/test_repro.py -q
```

The synthetic demo is deliberately faulty and runs offline. It writes `minimal.sse`,
`report.json`, `test_repro.py`, `predicate.py`, and a bundle README under
`demo-output/`. A verified local run produced:

```json
{"schema_version":1,"status":"complete","original_bytes":1833,"reduced_bytes":155,"original_events":42,"reduced_events":2,"kept_event_indices":[13,30],"calls":70,"cache_hits":5,"minimality_verified":true,"baseline_verified":true,"fixed_prefix_bytes":0,"incomplete_tail":false,"synthetic_demo":true}
```

That synthetic sample went from 42 to 2 events and from 1,833 to 155 bytes, a 91.5%
reduction in file size. Its exported target-preservation test also passed locally
(`1 passed` in 0.36 seconds for that run). These figures describe this bundled sample,
not throughput, performance, or expected results for other predicates. Neither
command prints the stream body.

## Minimize your own failure

Write a predicate that accepts candidate bytes and returns the built-in `bool` value
`True` only when the same target failure is present:

```python
# predicate.py
from my_app.streams import TargetParseError, parse_stream


def fails(candidate: bytes) -> bool:
    try:
        parse_stream(candidate)
    except TargetParseError as exc:
        return exc.code == "missing_usage"
    return False
```

Then minimize a local SSE file:

```console
python -m sse_shrink minimize broken.sse \
  --predicate predicate.py:fails \
  --output shrink-output
```

On PowerShell, write the command on one line or replace `\` with a backtick.
Use `--json` for one machine-readable summary object. Existing bundle files are
protected unless `--force` is explicit.

For a real-project export, the generated test intentionally does not copy your
predicate or its dependencies. Point it at the same predicate at test time:

```console
SSE_SHRINK_PREDICATE=predicate.py:fails python -m pytest shrink-output/test_repro.py -q
```

PowerShell equivalent:

```powershell
$env:SSE_SHRINK_PREDICATE = "predicate.py:fails"
python -m pytest shrink-output/test_repro.py -q
```

The exported assertion proves that the target failure remains. Add a separate
application-level assertion when you want to test the eventual fix.

## What it guarantees

- Framing recognizes LF, CRLF, and CR boundaries without rewriting retained bytes.
- A UTF-8 BOM and leading blank lines are a fixed prefix; complete event blocks own
  their terminators, and an incomplete final block remains independently removable.
- Baseline, accepted reductions, final output, and final deletion checks use fresh
  repeated predicate calls. Exceptions, timeouts, non-boolean results, and
  inconsistent answers are errors rather than successful reproductions.
- A completed result is event-level **1-minimal**: deleting any one remaining
  reducible event no longer preserves the predicate. The fixed prefix is outside
  that claim, and 1-minimal does not mean globally shortest.
- The included synchronous `ReplayTransport` serves the retained bytes through
  HTTPX in memory and does not itself access the network.

Default limits are 10 MiB, 10,000 reducible events, 200 actual predicate calls,
5 seconds per subprocess, and 3 stability repetitions. A budget-exhausted run may
export its last validated candidate, clearly marked `minimality_verified=false`.
If the initial baseline cannot be verified within the budget, no reproduction bundle
is exported. CLI exit codes distinguish success (`0`), input/options (`2`), target
absence or a predicate that still matches after removing all reducible events (`3`),
predicate failure, timeout, or instability (`4`), budget exhaustion (`5`), and
interruption with Ctrl+C (`130`). With `--json`, interruption reports one error
object with `status="error"`, `category="interrupted"`, and a fixed message,
without a traceback. Successful reports retain `schema_version=1`; the Python API
and reduction rules are unchanged in v0.2.0.

## Boundaries and safety

SSE Shrink is a reducer, not a redaction tool. The original and minimized streams
may contain model text, personal data, credentials, or other sensitive material;
inspect every bundle before sharing it. Reports omit stream content, predicate
stdout/stderr, and absolute source paths.

Predicates are user code. Each call runs in a fresh, time-bounded subprocess to
isolate Python state, but that subprocess is **not a security sandbox**. Only run
predicates you trust. The replay transport is offline, but other code imported by
your predicate may still use the network or filesystem.

On timeout, communication failure, or interruption, the runner terminates and
reaps its direct worker and closes its pipes. Library callers still receive
`KeyboardInterrupt`; the CLI translates it to exit code 130. Cleanup does not
manage arbitrary processes started by a predicate, and the timeout is not a hard
bound on operating-system process startup. The worker reads candidate bytes before
loading user code so that an import that hangs cannot block a large Windows stdin
write ahead of timeout handling.

An `.sse` file also cannot preserve original TCP chunk boundaries or timing. HTTPX
chunk sizes in a reproduction are an explicit simulation.

## Why this tool

Recording and mocking tools solve adjacent problems. Projects such as
[llm-mock](https://github.com/autopost/llm-mock) focus on recording and replay;
[AIMock](https://github.com/CopilotKit/aimock) covers broader protocol mocking,
timing, and fault injection; and
[llm-stream-tck](https://github.com/carter51200/llm-stream-tck) provides synthetic
stream conformance and boundary tests. SSE Shrink starts with a failure sample you
already have and reduces its event sequence according to your exact predicate. It
can complement those tools rather than replace them.

## Development

See [component benchmarks](docs/performance.md) for measured framing and memory
improvements, reproducible commands, and input shapes that can be slower.

```console
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
python -m build
python scripts/smoke_wheel.py
```

The smoke check inspects wheel and source archive contents, installs the wheel and
pytest in a fresh temporary environment, then checks both version entry points,
the 42-to-2-event demo and its exported test outside the checkout. Dependency
installation may access the network; subsequent smoke commands block network
access. CI runs these checks on Ubuntu and Windows with Python 3.11 and 3.14.

Contributions and real-world failure cases are welcome. Please read
[CONTRIBUTING.md](CONTRIBUTING.md), especially the guidance about sensitive stream
content. SSE Shrink is licensed under the [MIT License](LICENSE).
