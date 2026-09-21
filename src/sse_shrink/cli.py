"""Command-line interface for SSE Shrink."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .bundle import build_report, export_bundle, validate_output_paths
from .demo import build_synthetic_stream
from .errors import InputError, NotReproducedError, PredicateError
from .framing import FramedStream, frame_stream
from .minimizer import ShrinkResult, minimize
from .runner import SubprocessPredicate


class _UsageError(InputError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        if "positive integer" in message:
            safe_message = "numeric options must be positive integers"
        elif "positive number" in message:
            safe_message = "timeout must be a finite positive number"
        else:
            safe_message = "invalid command-line arguments; use --help"
        raise _UsageError(safe_message)


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer") from None
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _positive_number(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive number") from None
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return parsed


def _add_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", required=True, type=Path, metavar="DIR")
    parser.add_argument("--force", action="store_true", help="replace existing bundle files")
    parser.add_argument("--json", action="store_true", help="print one JSON report object")


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="sse-shrink", description="Reduce an SSE failure reproduction.")
    parser.add_argument("--version", action="version", version=f"sse-shrink {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    minimize_parser = commands.add_parser("minimize", help="shrink a local SSE file")
    minimize_parser.add_argument("input", type=Path, metavar="INPUT")
    minimize_parser.add_argument("--predicate", required=True, metavar="FILE.py:FUNCTION")
    minimize_parser.add_argument("--max-calls", type=_positive_integer, default=200)
    minimize_parser.add_argument("--repeat", type=_positive_integer, default=3)
    minimize_parser.add_argument("--timeout", type=_positive_number, default=5.0)
    minimize_parser.add_argument("--max-bytes", type=_positive_integer, default=10 * 1024 * 1024)
    minimize_parser.add_argument("--max-events", type=_positive_integer, default=10_000)
    _add_output_options(minimize_parser)

    demo_parser = commands.add_parser("demo", help="run the deliberately faulty offline demo")
    _add_output_options(demo_parser)
    return parser


def _read_bounded(path: Path, limit: int) -> bytes:
    if not path.is_file():
        raise InputError("input file is unavailable")
    try:
        with path.open("rb") as source:
            data = source.read(limit + 1)
    except OSError:
        raise InputError("input file could not be read") from None
    if len(data) > limit:
        raise InputError(f"input size exceeds max_bytes ({limit})")
    return data


def _predicate_path(spec: str) -> Path:
    if not isinstance(spec, str) or ":" not in spec:
        raise InputError("predicate must use FILE:FUNCTION syntax")
    path_text, _function_name = spec.rsplit(":", 1)
    return Path(path_text).expanduser().resolve()


def _emit_report(report: dict[str, object], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(report, separators=(",", ":")))
        return
    original_bytes = int(report["original_bytes"])
    reduced_bytes = int(report["reduced_bytes"])
    percentage = (
        0.0 if original_bytes == 0 else 100 * (original_bytes - reduced_bytes) / original_bytes
    )
    minimality = "yes" if report["minimality_verified"] else "no"
    baseline = "yes" if report["baseline_verified"] else "no"
    incomplete_tail = "yes" if report["incomplete_tail"] else "no"
    print(f"{report['original_events']} -> {report['reduced_events']} events")
    print(f"{original_bytes} -> {reduced_bytes} bytes ({percentage:.1f}% smaller)")
    print(f"{report['calls']} predicate calls; {report['cache_hits']} cache hits")
    print(f"incomplete tail: {incomplete_tail}")
    print(f"baseline verified: {baseline}; 1-minimal: {minimality}")
    if report["baseline_verified"]:
        print("bundle: minimal.sse, report.json, test_repro.py, README.md")
        print("reproduce: python -m pytest test_repro.py -q  (from the bundle directory)")


def _run_minimize(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    input_path = args.input.expanduser().resolve()
    predicate_path = _predicate_path(args.predicate)
    predicate = SubprocessPredicate(args.predicate, timeout=args.timeout)
    validate_output_paths(
        args.output,
        force=args.force,
        protected_paths=(input_path, predicate_path),
        synthetic_demo=False,
    )
    data = _read_bounded(input_path, args.max_bytes)
    stream = frame_stream(data, max_bytes=args.max_bytes, max_events=args.max_events)
    result = minimize(stream, predicate, max_calls=args.max_calls, repeat=args.repeat)
    report = build_report(data, stream, result, synthetic_demo=False)
    if not result.baseline_verified:
        return report, 5
    export_bundle(
        args.output,
        original_data=data,
        stream=stream,
        result=result,
        force=args.force,
        protected_paths=(input_path, predicate_path),
        synthetic_demo=False,
        predicate_timeout=args.timeout,
    )
    return report, 5 if result.status == "budget_exhausted" else 0


def _run_demo(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    from . import demo

    demo_path = Path(demo.__file__).resolve()
    validate_output_paths(
        args.output,
        force=args.force,
        protected_paths=(demo_path,),
        synthetic_demo=True,
    )
    data = build_synthetic_stream()
    stream: FramedStream = frame_stream(data)
    predicate = SubprocessPredicate(f"{demo_path}:fails")
    result: ShrinkResult = minimize(stream, predicate)
    report = build_report(data, stream, result, synthetic_demo=True)
    if not result.baseline_verified:
        return report, 5
    try:
        predicate_source = demo_path.read_text(encoding="utf-8")
    except OSError:
        raise InputError("the synthetic demo predicate source is unavailable") from None
    export_bundle(
        args.output,
        original_data=data,
        stream=stream,
        result=result,
        force=args.force,
        protected_paths=(demo_path,),
        synthetic_demo=True,
        predicate_source=predicate_source,
        predicate_timeout=5.0,
    )
    return report, 5 if result.status == "budget_exhausted" else 0


def _emit_error(message: str, *, category: str, json_output: bool) -> None:
    if json_output:
        print(json.dumps({"status": "error", "category": category, "message": message}))
    else:
        print(f"error: {message}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    json_output = "--json" in arguments
    try:
        args = _build_parser().parse_args(arguments)
        if args.command == "minimize":
            report, exit_code = _run_minimize(args)
        else:
            report, exit_code = _run_demo(args)
    except _UsageError as error:
        _emit_error(str(error), category="input", json_output=json_output)
        return 2
    except InputError as error:
        _emit_error(str(error), category="input", json_output=json_output)
        return 2
    except NotReproducedError as error:
        _emit_error(str(error), category="target_absent", json_output=json_output)
        return 3
    except PredicateError as error:
        _emit_error(str(error), category="predicate", json_output=json_output)
        return 4
    except KeyboardInterrupt:
        _emit_error("operation interrupted", category="interrupted", json_output=json_output)
        return 130
    _emit_report(report, json_output=args.json)
    return exit_code
