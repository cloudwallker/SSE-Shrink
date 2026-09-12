"""Create portable reproduction bundles without embedding private source paths."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from .errors import InputError
from .framing import FramedStream
from .minimizer import ShrinkResult

_COMMON_NAMES = ("minimal.sse", "report.json", "test_repro.py", "README.md")


def reserved_paths(output_dir: Path, *, synthetic_demo: bool) -> tuple[Path, ...]:
    names = (*_COMMON_NAMES, "predicate.py") if synthetic_demo else _COMMON_NAMES
    return tuple(output_dir / name for name in names)


def _aliases(left: Path, right: Path) -> bool:
    try:
        if left.resolve(strict=False) == right.resolve(strict=False):
            return True
    except OSError:
        pass
    if not os.path.lexists(left) or not os.path.lexists(right):
        return False
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def validate_output_paths(
    output_dir: Path,
    *,
    force: bool,
    protected_paths: Iterable[Path],
    synthetic_demo: bool,
) -> tuple[Path, ...]:
    """Validate every reserved destination before any output is written."""
    destination = output_dir.expanduser()
    if os.path.lexists(destination) and not destination.is_dir():
        raise InputError("output must be a directory")

    protected = tuple(path.expanduser() for path in protected_paths)
    targets = reserved_paths(destination, synthetic_demo=synthetic_demo)
    for index, target in enumerate(targets):
        if any(_aliases(target, other) for other in targets[index + 1 :]):
            raise InputError("reserved output paths must be distinct")
        if any(_aliases(target, source) for source in protected):
            raise InputError("output would overwrite the input or predicate file")
        if os.path.lexists(target):
            if not target.is_file() or target.is_symlink() and not target.exists():
                raise InputError("a reserved output path is not a regular file")
            if not force:
                raise InputError("output files already exist; use --force to replace them")
    return targets


def build_report(
    original_data: bytes,
    stream: FramedStream,
    result: ShrinkResult,
    *,
    synthetic_demo: bool,
) -> dict[str, object]:
    """Build the stable, path-free report schema."""
    return {
        "schema_version": 1,
        "status": result.status,
        "original_bytes": len(original_data),
        "reduced_bytes": len(result.data),
        "original_events": len(stream.frames),
        "reduced_events": len(result.kept_indices),
        "kept_event_indices": [index + 1 for index in result.kept_indices],
        "calls": result.calls,
        "cache_hits": result.cache_hits,
        "minimality_verified": result.minimality_verified,
        "baseline_verified": result.baseline_verified,
        "fixed_prefix_bytes": len(stream.prefix),
        "incomplete_tail": bool(stream.frames and not stream.frames[-1].complete),
        "synthetic_demo": synthetic_demo,
    }


def _test_source(*, synthetic_demo: bool, predicate_timeout: float) -> str:
    default = (
        "    default = f\"{Path(__file__).with_name('predicate.py')}:fails\"\n"
        if synthetic_demo
        else "    default = None\n"
    )
    return (
        "from __future__ import annotations\n\n"
        "import os\n"
        "from pathlib import Path\n\n"
        "from sse_shrink.runner import SubprocessPredicate\n\n\n"
        "def test_target_failure_is_preserved() -> None:\n"
        + default
        + '    spec = os.environ.get("SSE_SHRINK_PREDICATE", default)\n'
        + "    assert spec, (\n"
        + '        "set SSE_SHRINK_PREDICATE to FILE.py:FUNCTION for the target predicate"\n'
        + "    )\n"
        + '    data = Path(__file__).with_name("minimal.sse").read_bytes()\n'
        + f"    predicate = SubprocessPredicate(spec, timeout={predicate_timeout!r})\n"
        + "    assert predicate(data) is True\n"
    )


def _readme_source(*, synthetic_demo: bool) -> str:
    predicate_setup = (
        "The included `predicate.py` recognizes a deliberately introduced "
        "`KeyError('usage')`; it does not describe a defect in a current SDK.\n\n"
        if synthetic_demo
        else "Set `SSE_SHRINK_PREDICATE` to your original `FILE.py:FUNCTION` predicate. "
        "The predicate and its private dependencies were deliberately not copied.\n\n"
    )
    env_instruction = (
        "You may override the included predicate with `SSE_SHRINK_PREDICATE`.\n\n"
        if synthetic_demo
        else "Example (PowerShell):\n\n"
        "```powershell\n"
        "$env:SSE_SHRINK_PREDICATE = 'path/to/predicate.py:fails'\n"
        "```\n\n"
    )
    return (
        "# SSE Shrink reproduction\n\n"
        "Review `minimal.sse` before sharing it. Shrinking is not redaction.\n\n"
        + predicate_setup
        + env_instruction
        + "Run from any working directory:\n\n"
        + "```console\npython -m pytest path/to/bundle/test_repro.py -q\n```\n\n"
        + "The test asserts that the selected target failure is still present. It does not assert "
        + "that the application bug is fixed. The predicate executes local code; subprocess "
        + "isolation and timeout are not a security sandbox.\n"
    )


def export_bundle(
    output_dir: Path,
    *,
    original_data: bytes,
    stream: FramedStream,
    result: ShrinkResult,
    force: bool,
    protected_paths: Iterable[Path],
    synthetic_demo: bool,
    predicate_source: str | None = None,
    predicate_timeout: float = 5.0,
) -> Mapping[str, object]:
    """Write a validated reproduction bundle and return its report."""
    destination = output_dir.expanduser()
    validate_output_paths(
        destination,
        force=force,
        protected_paths=protected_paths,
        synthetic_demo=synthetic_demo,
    )
    report = build_report(original_data, stream, result, synthetic_demo=synthetic_demo)
    contents: dict[str, bytes] = {
        "minimal.sse": result.data,
        "report.json": (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        "test_repro.py": _test_source(
            synthetic_demo=synthetic_demo, predicate_timeout=predicate_timeout
        ).encode("utf-8"),
        "README.md": _readme_source(synthetic_demo=synthetic_demo).encode("utf-8"),
    }
    if synthetic_demo:
        if predicate_source is None:
            raise InputError("the synthetic demo predicate source is unavailable")
        contents["predicate.py"] = predicate_source.encode("utf-8")

    try:
        destination.mkdir(parents=True, exist_ok=True)
        for name, content in contents.items():
            (destination / name).write_bytes(content)
    except OSError:
        raise InputError("bundle files could not be written") from None
    return report
