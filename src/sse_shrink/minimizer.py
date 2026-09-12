"""Predicate-driven delta debugging for losslessly framed SSE streams."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from .errors import InputError, NotReproducedError, PredicateError
from .framing import FramedStream


@dataclass(frozen=True)
class ShrinkResult:
    """A verified candidate reached within the call budget."""

    data: bytes
    kept_indices: tuple[int, ...]
    calls: int
    cache_hits: int
    status: str
    minimality_verified: bool
    baseline_verified: bool


class _BudgetExhausted(Exception):
    pass


class _Oracle:
    def __init__(
        self,
        predicate: Callable[[bytes], bool],
        *,
        max_calls: int,
        repeat: int,
    ) -> None:
        self._predicate = predicate
        self._max_calls = max_calls
        self._repeat = repeat
        self._cache: dict[tuple[int, bytes], bool] = {}
        self.calls = 0
        self.cache_hits = 0

    @staticmethod
    def _key(candidate: bytes) -> tuple[int, bytes]:
        return len(candidate), hashlib.blake2b(candidate, digest_size=32).digest()

    def _call(self, candidate: bytes) -> bool:
        if self.calls >= self._max_calls:
            raise _BudgetExhausted
        self.calls += 1
        try:
            result = self._predicate(candidate)
        except Exception:
            raise PredicateError("predicate raised an exception") from None
        if type(result) is not bool:
            raise PredicateError("predicate must return bool")
        return result

    def probe(self, candidate: bytes) -> bool:
        """Run or reuse one screening call."""
        key = self._key(candidate)
        if key in self._cache:
            self.cache_hits += 1
            return self._cache[key]
        result = self._call(candidate)
        self._cache[key] = result
        return result

    def stable(self, candidate: bytes) -> bool:
        """Run fresh repetitions and reject disagreement with any known result."""
        key = self._key(candidate)
        known = self._cache.get(key)
        first: bool | None = None
        for _ in range(self._repeat):
            result = self._call(candidate)
            if known is not None and result != known:
                raise PredicateError("predicate returned inconsistent results")
            if first is None:
                first = result
            elif result != first:
                raise PredicateError("predicate returned inconsistent results")
        assert first is not None
        self._cache[key] = first
        return first


def _positive_integer(name: str, value: object) -> int:
    if type(value) is not int or value <= 0:
        raise InputError(f"{name} must be a positive integer")
    return value


def _result(
    stream: FramedStream,
    indices: tuple[int, ...],
    oracle: _Oracle,
    *,
    status: str,
    minimality_verified: bool,
    baseline_verified: bool,
) -> ShrinkResult:
    return ShrinkResult(
        data=stream.join(indices),
        kept_indices=indices,
        calls=oracle.calls,
        cache_hits=oracle.cache_hits,
        status=status,
        minimality_verified=minimality_verified,
        baseline_verified=baseline_verified,
    )


def _chunks(indices: tuple[int, ...], count: int):
    length = len(indices)
    for part in range(count):
        start = part * length // count
        end = (part + 1) * length // count
        if start != end:
            yield start, end


def minimize(
    stream: FramedStream,
    predicate: Callable[[bytes], bool],
    *,
    max_calls: int = 200,
    repeat: int = 3,
) -> ShrinkResult:
    """Reduce frames while retaining a stable target-failure predicate."""
    call_limit = _positive_integer("max_calls", max_calls)
    repetitions = _positive_integer("repeat", repeat)
    if not isinstance(stream, FramedStream):
        raise InputError("stream must be a FramedStream")

    oracle = _Oracle(predicate, max_calls=call_limit, repeat=repetitions)
    original = tuple(range(len(stream.frames)))
    last_verified = original

    try:
        baseline = oracle.stable(stream.join(original))
    except _BudgetExhausted:
        return _result(
            stream,
            original,
            oracle,
            status="budget_exhausted",
            minimality_verified=False,
            baseline_verified=False,
        )
    if not baseline:
        raise NotReproducedError("original input does not reproduce the target failure")

    try:
        empty_reproduces = oracle.stable(stream.join(()))
    except _BudgetExhausted:
        return _result(
            stream,
            last_verified,
            oracle,
            status="budget_exhausted",
            minimality_verified=False,
            baseline_verified=True,
        )
    if empty_reproduces:
        raise NotReproducedError("empty event candidate reproduces the target failure")

    current = original
    granularity = min(2, len(current))
    try:
        while len(current) >= 2 and granularity >= 2:
            reduced = False
            for start, end in _chunks(current, granularity):
                candidate = current[:start] + current[end:]
                candidate_data = stream.join(candidate)
                if oracle.probe(candidate_data):
                    if not oracle.stable(candidate_data):
                        raise PredicateError("predicate returned inconsistent results")
                    current = candidate
                    last_verified = current
                    granularity = min(len(current), max(2, granularity - 1))
                    reduced = True
                    break
            if reduced:
                continue
            if granularity >= len(current):
                break
            granularity = min(len(current), granularity * 2)

        if not oracle.stable(stream.join(current)):
            raise PredicateError("verified candidate no longer reproduces the target failure")
        last_verified = current

        position = 0
        while position < len(current):
            candidate = current[:position] + current[position + 1 :]
            if oracle.stable(stream.join(candidate)):
                current = candidate
                last_verified = current
                position = 0
            else:
                position += 1

        if not oracle.stable(stream.join(current)):
            raise PredicateError("final candidate no longer reproduces the target failure")
        last_verified = current
    except _BudgetExhausted:
        return _result(
            stream,
            last_verified,
            oracle,
            status="budget_exhausted",
            minimality_verified=False,
            baseline_verified=True,
        )

    return _result(
        stream,
        current,
        oracle,
        status="complete",
        minimality_verified=True,
        baseline_verified=True,
    )
