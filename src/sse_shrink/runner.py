"""Run user predicates in fresh, bounded Python subprocesses."""

from __future__ import annotations

import math
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from .errors import InputError, PredicateError

_WORKER_PATH = Path(__file__).with_name("_worker.py").resolve()
_ERROR_MESSAGES = {
    b"E:load": "predicate module could not be loaded",
    b"E:target": "predicate target is missing or not callable",
    b"E:call": "predicate raised an exception",
    b"E:type": "predicate must return a boolean",
}


def _reap_worker(process: subprocess.Popen[bytes]) -> KeyboardInterrupt | None:
    """Terminate, reap, and close one direct worker without masking its caller."""
    interruption: KeyboardInterrupt | None = None

    def safely(operation: Callable[[], object]) -> tuple[bool, object | None]:
        nonlocal interruption
        while True:
            try:
                return True, operation()
            except KeyboardInterrupt as error:
                if interruption is None:
                    interruption = error
            except (OSError, ValueError):
                return False, None

    _polled, returncode = safely(process.poll)
    if returncode is None:
        killed, _ignored = safely(process.kill)
        if killed or safely(process.poll)[1] is not None:
            safely(process.wait)
    else:
        safely(process.wait)
    for pipe in (process.stdin, process.stdout, process.stderr):
        if pipe is not None:
            safely(pipe.close)
    return interruption


class SubprocessPredicate:
    """Call a ``file.py:function`` predicate in a fresh process each time."""

    def __init__(self, spec: str, *, timeout: float = 5.0):
        if not isinstance(spec, str) or ":" not in spec:
            raise InputError("predicate must use FILE:FUNCTION syntax")

        path_text, function_name = spec.rsplit(":", 1)
        if not path_text or not function_name:
            raise InputError("predicate must use FILE:FUNCTION syntax")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise InputError("predicate timeout must be a positive number")
        if not math.isfinite(timeout) or timeout <= 0:
            raise InputError("predicate timeout must be a positive number")

        self._path = Path(path_text).expanduser().resolve()
        if not self._path.is_file():
            raise InputError("predicate file is unavailable")
        self._function_name = function_name
        self._timeout = float(timeout)

    def __call__(self, candidate: bytes) -> bool:
        if not self._path.is_file():
            raise PredicateError("predicate file is unavailable")

        with tempfile.TemporaryDirectory(prefix="sse-shrink-") as temporary_dir:
            descriptor, result_name = tempfile.mkstemp(dir=temporary_dir)
            os.close(descriptor)
            result_path = Path(result_name)
            command = [
                sys.executable,
                str(_WORKER_PATH),
                str(self._path),
                self._function_name,
                str(result_path),
            ]

            try:
                process = subprocess.Popen(
                    command,
                    cwd=self._path.parent,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                )
            except (OSError, ValueError):
                raise PredicateError("predicate process could not be started") from None

            try:
                try:
                    process.communicate(input=candidate, timeout=self._timeout)
                except subprocess.TimeoutExpired:
                    raise PredicateError("predicate timed out") from None
                except (OSError, ValueError, TypeError):
                    raise PredicateError("predicate process communication failed") from None
            finally:
                cleanup_interruption = _reap_worker(process)
                if cleanup_interruption is not None and sys.exc_info()[0] is None:
                    raise cleanup_interruption

            if process.returncode != 0:
                raise PredicateError("predicate process failed")

            try:
                result = result_path.read_bytes()
            except OSError:
                raise PredicateError("predicate process did not return a result") from None

        if result == b"T":
            return True
        if result == b"F":
            return False
        if result in _ERROR_MESSAGES:
            raise PredicateError(_ERROR_MESSAGES[result])
        raise PredicateError("predicate process did not return a valid result")
