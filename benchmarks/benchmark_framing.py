"""Measure framing and candidate assembly; run after installing the project."""

from __future__ import annotations

import json
import platform
import statistics
import timeit
import tracemalloc
from collections.abc import Callable

from sse_shrink.framing import frame_stream


def measure(function: Callable[[], object], *, number: int = 3) -> dict[str, float | int]:
    function()
    samples = timeit.repeat(function, number=number, repeat=5)
    already_tracing = tracemalloc.is_tracing()
    if not already_tracing:
        tracemalloc.start()
    baseline, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    try:
        result = function()
        _, peak = tracemalloc.get_traced_memory()
        del result
    finally:
        if not already_tracing:
            tracemalloc.stop()
    return {
        "median_ms": round(statistics.median(samples) * 1000 / number, 3),
        "peak_python_bytes": peak - baseline,
    }


def main() -> None:
    byte_limit = 10 * 1024 * 1024
    inputs = {
        "long_line_10_mib": b"data: " + b"x" * (byte_limit - 8) + b"\n\n",
        "lf_10000_events": (b"data: " + b"x" * 92 + b"\n\n") * 10_000,
        "crlf_10000_events": (b"data: " + b"x" * 90 + b"\r\n\r\n") * 10_000,
        "dense_blank_lf": b"\n" * (256 * 1024),
    }
    measurements = {}
    for name, data in inputs.items():
        assert frame_stream(data).join() == data
        measurements[name] = {
            "input_bytes": len(data),
            **measure(lambda data=data: frame_stream(data)),
        }

    event = b"data: " + b"x" * (32 * 1024 - 8) + b"\n\n"
    for name, prefix in (("join_empty_prefix", b""), ("join_bom", b"\xef\xbb\xbf")):
        data = prefix + event * 256
        stream = frame_stream(data)
        assert stream.join() == data
        measurements[name] = {"input_bytes": len(data), **measure(stream.join, number=15)}

    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "platform": platform.system(),
                "timing": "median of 5 rounds; tracemalloc measured separately",
                "measurements": measurements,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
