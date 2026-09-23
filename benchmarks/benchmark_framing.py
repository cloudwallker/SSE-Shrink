"""Compare bb6b031 and the current framing implementation in one process."""

from __future__ import annotations

import argparse
import importlib
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sse_shrink.framing import frame_stream

_BASELINE_REVISION = "bb6b031"
_UTF8_BOM = b"\xef\xbb\xbf"
_ROUNDS = 7


def _git_show(repository: Path, path: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "safe.directory=*",
            "show",
            f"{_BASELINE_REVISION}:{path}",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=repository,
    )
    return result.stdout


def _load_baseline_frame_stream(repository: Path) -> Callable[[bytes], Any]:
    """Import the baseline under a temporary package so its relative imports work."""
    temporary_directory = tempfile.TemporaryDirectory(prefix="sse-shrink-baseline-")
    package_name = "_sse_shrink_bb6b031"
    package_directory = Path(temporary_directory.name, package_name)
    package_directory.mkdir()
    Path(package_directory, "__init__.py").write_text("", encoding="utf-8")
    for filename in ("framing.py", "errors.py"):
        Path(package_directory, filename).write_text(
            _git_show(repository, f"src/sse_shrink/{filename}"), encoding="utf-8"
        )

    sys.path.insert(0, temporary_directory.name)
    try:
        module = importlib.import_module(f"{package_name}.framing")
    finally:
        sys.path.pop(0)

    # Keep extracted source available for the lifetime of its imported module.
    module._benchmark_temporary_directory = temporary_directory
    return module.frame_stream


def _measure_alternating(
    before: Callable[[], object], after: Callable[[], object], *, calls: int = 1
) -> dict[str, dict[str, float | int | list[float]]]:
    before()
    after()
    samples: dict[str, list[float]] = {"before": [], "after": []}
    for round_number in range(_ROUNDS):
        order = (("before", before), ("after", after))
        if round_number % 2:
            order = tuple(reversed(order))
        for name, function in order:
            started = time.perf_counter()
            for _ in range(calls):
                function()
            samples[name].append((time.perf_counter() - started) * 1_000 / calls)

    return {
        name: {"samples_ms": values, "median_ms": statistics.median(values)}
        for name, values in samples.items()
    }


def _measure_peak(function: Callable[[], object]) -> int:
    already_tracing = tracemalloc.is_tracing()
    if not already_tracing:
        tracemalloc.start()
    baseline, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    try:
        result = function()
        _, peak = tracemalloc.get_traced_memory()
        del result
        return peak - baseline
    finally:
        if not already_tracing:
            tracemalloc.stop()


def _measure_current(function: Callable[[], object]) -> dict[str, float | int | list[float]]:
    function()
    samples: list[float] = []
    for _ in range(_ROUNDS):
        started = time.perf_counter()
        function()
        samples.append((time.perf_counter() - started) * 1_000)
    return {
        "samples_ms": [round(sample, 6) for sample in samples],
        "median_ms": round(statistics.median(samples), 6),
        "peak_python_bytes": _measure_peak(function),
    }


def _framing_inputs() -> dict[str, bytes]:
    byte_limit = 10 * 1024 * 1024
    event_lf = b"data: " + b"x" * 92 + b"\n\n"
    event_crlf = b"data: " + b"x" * 90 + b"\r\n\r\n"
    return {
        "long_line_10_mib": b"data: " + b"x" * (byte_limit - 8) + b"\n\n",
        "lf_10000_events": event_lf * 10_000,
        "crlf_10000_events": event_crlf * 10_000,
        "lf_prefix_1_mib": _UTF8_BOM + b"\n" * (1024 * 1024),
        "cr_prefix_1_mib": _UTF8_BOM + b"\r" * (1024 * 1024),
        "crlf_prefix_1_mib": _UTF8_BOM + b"\r\n" * (512 * 1024),
        "mixed_prefix_1_mib": _UTF8_BOM + b"\r\r\n\n" * (256 * 1024),
        "prefix_plus_10000_events": _UTF8_BOM + b"\r\n" * (512 * 1024) + event_lf * 10_000,
    }


def _record_operation(
    before: Callable[[], object], after: Callable[[], object], input_bytes: int
) -> dict[str, object]:
    timings = _measure_alternating(before, after)
    for name, function in (("before", before), ("after", after)):
        timings[name]["peak_python_bytes"] = _measure_peak(function)
        timings[name]["median_ms"] = round(float(timings[name]["median_ms"]), 6)
        timings[name]["samples_ms"] = [round(sample, 6) for sample in timings[name]["samples_ms"]]
    before_ms = float(timings["before"]["median_ms"])
    after_ms = float(timings["after"]["median_ms"])
    return {
        "input_bytes": input_bytes,
        "before": timings["before"],
        "after": timings["after"],
        "speedup": round(before_ms / after_ms, 3),
    }


def _join_operations() -> dict[str, bytes]:
    event = b"data: " + b"x" * (32 * 1024 - 8) + b"\n\n"
    return {
        "join_empty_prefix": event * 256,
        "join_bom_prefix": _UTF8_BOM + event * 256,
    }


def benchmark_current() -> dict[str, object]:
    framing_measurements: dict[str, object] = {}
    for name, data in _framing_inputs().items():

        def current(data: bytes = data) -> object:
            return frame_stream(data)

        assert current().join() == data
        framing_measurements[name] = {
            "input_bytes": len(data),
            "current": _measure_current(current),
        }
    join_measurements: dict[str, object] = {}
    for name, data in _join_operations().items():
        stream = frame_stream(data)
        assert stream.join() == data
        join_measurements[name] = {
            "input_bytes": len(data),
            "current": _measure_current(stream.join),
        }
    return {
        "mode": "current",
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "timing": {"rounds": _ROUNDS, "calls_per_sample": 1},
        "framing": framing_measurements,
        "join": join_measurements,
    }


def benchmark_comparison(repository: Path) -> dict[str, object]:
    baseline_frame_stream = _load_baseline_frame_stream(repository)
    framing_measurements: dict[str, object] = {}
    for name, data in _framing_inputs().items():

        def before(data: bytes = data) -> object:
            return baseline_frame_stream(data)

        def after(data: bytes = data) -> object:
            return frame_stream(data)

        assert before().join() == data
        assert after().join() == data
        framing_measurements[name] = _record_operation(before, after, len(data))

    join_measurements: dict[str, object] = {}
    for name, data in _join_operations().items():
        before_stream = baseline_frame_stream(data)
        after_stream = frame_stream(data)
        assert before_stream.join() == data
        assert after_stream.join() == data
        join_measurements[name] = _record_operation(
            before_stream.join, after_stream.join, len(data)
        )

    return {
        "baseline_revision": _BASELINE_REVISION,
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "timing": {
            "rounds": _ROUNDS,
            "calls_per_sample": 1,
            "execution": "before/after order alternates by round; allocation measured separately",
        },
        "framing": framing_measurements,
        "join": join_measurements,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--compare-baseline",
        action="store_true",
        help="load bb6b031 from the containing Git checkout and alternate both implementations",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="where to save the complete raw JSON observation (stdout only when omitted)",
    )
    arguments = parser.parse_args()
    if arguments.compare_baseline:
        results = benchmark_comparison(Path(__file__).resolve().parents[1])
    else:
        results = benchmark_current()
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
