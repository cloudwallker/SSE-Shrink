"""Private subprocess entry point for :mod:`sse_shrink.runner`."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _write_result(path: Path, value: bytes) -> int:
    try:
        path.write_bytes(value)
    except OSError:
        return 2
    return 0


def main() -> int:
    if len(sys.argv) != 4:
        return 2

    predicate_path = Path(sys.argv[1])
    function_name = sys.argv[2]
    result_path = Path(sys.argv[3])

    sys.path.insert(0, str(predicate_path.parent))
    try:
        module_spec = importlib.util.spec_from_file_location(
            "_sse_shrink_user_predicate", predicate_path
        )
        if module_spec is None or module_spec.loader is None:
            return _write_result(result_path, b"E:load")
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[module_spec.name] = module
        module_spec.loader.exec_module(module)
    except BaseException:
        return _write_result(result_path, b"E:load")

    try:
        predicate = getattr(module, function_name)
    except BaseException:
        return _write_result(result_path, b"E:target")
    if not callable(predicate):
        return _write_result(result_path, b"E:target")

    try:
        candidate = sys.stdin.buffer.read()
        result = predicate(candidate)
    except BaseException:
        return _write_result(result_path, b"E:call")

    if type(result) is not bool:
        return _write_result(result_path, b"E:type")
    return _write_result(result_path, b"T" if result else b"F")


if __name__ == "__main__":
    raise SystemExit(main())
