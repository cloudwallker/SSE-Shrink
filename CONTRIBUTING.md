# Contributing to SSE Shrink

Thank you for helping improve SSE Shrink. Bug reports, small reproduction cases,
documentation corrections, and focused code changes are all useful.

## Protect private data

Shrinking is not redaction. Before attaching an SSE file, reproduction bundle, log,
or predicate output to an issue, inspect it for model text, personal information,
credentials, internal URLs, and proprietary data. Prefer a synthetic sample that
preserves the behavior. Never publish data you are not authorized to share.

Predicates execute as local Python code in a subprocess, not a security sandbox.
Review contributed predicates before running them.

## Set up a development checkout

Python 3.11 or newer is required. Create and activate a virtual environment, then
run the following from the repository root:

```console
python -m pip install -e ".[dev]"
```

Run the same checks used by CI:

```console
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
python -m build
```

Run the offline end-to-end example when changing the CLI or exported bundle:

```console
python -m sse_shrink demo --output demo-output
python -m pytest demo-output/test_repro.py -q
```

Use a new output directory or pass `--force` deliberately. Do not commit generated
outputs containing private samples.

## Report a bug

Include your operating system, Python version, installation method, exact command,
exit code, and the smallest synthetic reproduction you can share. State what target
failure the predicate is intended to recognize. If the result is unstable, describe
external state, timing, randomness, or network access used by the predicate.

Do not paste the original stream body or predicate stdout/stderr by default. Counts,
the content-free `report.json`, and a redacted directory listing are usually better
starting points.

## Submit a change

Keep changes focused and add tests for behavior that can regress. Preserve event
bytes exactly, keep public error messages content-free, and do not weaken file
collision or alias checks. Documentation must distinguish event-level 1-minimality
from a globally shortest result and subprocess isolation from sandboxing.

By submitting a contribution, you agree that it may be distributed under the
project's MIT License.
