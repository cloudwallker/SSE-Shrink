import traceback
import tracemalloc
from collections.abc import Callable

import pytest

from sse_shrink.errors import InputError, NotReproducedError, PredicateError
from sse_shrink.framing import frame_stream
from sse_shrink.minimizer import minimize


def test_reduces_to_ordered_failure_inducing_blocks() -> None:
    stream = frame_stream(b"data: noise\n\ndata: A\n\ndata: noise2\n\ndata: B\n\n")

    result = minimize(
        stream,
        lambda data: b"data: A" in data and b"data: B" in data,
        repeat=2,
    )

    assert result.data == b"data: A\n\ndata: B\n\n"
    assert result.kept_indices == (1, 3)
    assert result.status == "complete"
    assert result.minimality_verified is True
    assert result.baseline_verified is True
    assert result.calls > 0


def test_non_monotone_predicate_reaches_fixed_point_after_deletion() -> None:
    stream = frame_stream(b"data: A\n\ndata: B\n\ndata: C\n\ndata: D\n\n")

    reproducing_sets = {
        frozenset({b"A", b"B", b"C", b"D"}),
        frozenset({b"A", b"C", b"D"}),
        frozenset({b"A", b"D"}),
        frozenset({b"D"}),
    }

    def predicate(data: bytes) -> bool:
        present = frozenset(
            token for token in (b"A", b"B", b"C", b"D") if b"data: " + token in data
        )
        return present in reproducing_sets

    result = minimize(stream, predicate, repeat=2)

    assert result.data == b"data: D\n\n"
    assert result.kept_indices == (3,)
    assert result.minimality_verified is True


def test_rejects_absent_target_and_overbroad_empty_candidate() -> None:
    stream = frame_stream(b"data: event\n\n")

    with pytest.raises(NotReproducedError, match="original"):
        minimize(stream, lambda _data: False, repeat=2)
    with pytest.raises(NotReproducedError, match="empty"):
        minimize(stream, lambda _data: True, repeat=2)


@pytest.mark.parametrize(
    "predicate",
    [
        pytest.param(lambda _data: 1, id="integer-return"),
        pytest.param(lambda _data: None, id="none-return"),
    ],
)
def test_rejects_non_boolean_predicate_results(predicate: Callable[[bytes], object]) -> None:
    stream = frame_stream(b"data: event\n\n")

    with pytest.raises(PredicateError, match="bool"):
        minimize(stream, predicate)


def test_wraps_predicate_exception_without_exposing_message() -> None:
    stream = frame_stream(b"data: event\n\n")

    def predicate(_data: bytes) -> bool:
        raise RuntimeError("CANARY_PRIVATE_STREAM_TEXT")

    with pytest.raises(PredicateError) as caught:
        minimize(stream, predicate)

    assert "CANARY_PRIVATE_STREAM_TEXT" not in str(caught.value)
    assert "CANARY_PRIVATE_STREAM_TEXT" not in "".join(traceback.format_exception(caught.value))


def test_inconsistent_fresh_repetitions_are_predicate_errors() -> None:
    stream = frame_stream(b"data: event\n\n")
    answers = iter([True, False])

    with pytest.raises(PredicateError, match="inconsistent"):
        minimize(stream, lambda _data: next(answers), repeat=2)


def test_budget_counts_only_actual_calls_and_returns_last_verified_candidate() -> None:
    stream = frame_stream(b"data: required\n\ndata: noise\n\n")

    result = minimize(
        stream,
        lambda data: b"data: required" in data,
        max_calls=7,
        repeat=2,
    )

    assert result.data == stream.join()
    assert result.kept_indices == (0, 1)
    assert result.calls == 7
    assert result.status == "budget_exhausted"
    assert result.minimality_verified is False
    assert result.baseline_verified is True


def test_budget_exhaustion_during_baseline_is_not_reported_as_verified() -> None:
    stream = frame_stream(b"data: required\n\n")

    result = minimize(stream, lambda _data: True, max_calls=1, repeat=2)

    assert result.calls == 1
    assert result.status == "budget_exhausted"
    assert result.baseline_verified is False
    assert result.minimality_verified is False


def test_known_result_conflict_preempts_budget_exhaustion() -> None:
    stream = frame_stream(b"data: event\n\n")
    nonempty_calls = 0

    def predicate(data: bytes) -> bool:
        nonlocal nonempty_calls
        if not data:
            return False
        nonempty_calls += 1
        return nonempty_calls <= 3

    with pytest.raises(PredicateError, match="inconsistent"):
        minimize(stream, predicate, max_calls=7, repeat=3)


def test_duplicate_byte_candidates_use_cache_without_spending_call_budget() -> None:
    stream = frame_stream(b"data: same\n\ndata: same\n\n")

    result = minimize(
        stream,
        lambda data: data.count(b"data: same") == 2,
        max_calls=80,
        repeat=2,
    )

    assert result.data == stream.join()
    assert result.cache_hits >= 1
    assert result.calls < 80


def test_cache_does_not_retain_every_large_candidate_body() -> None:
    frame_count = 128
    data = b"".join(
        b"data: " + str(index).encode() + b" " + (b"x" * 1_500) + b"\n\n"
        for index in range(frame_count)
    )
    stream = frame_stream(data)

    tracemalloc.start()
    try:
        result = minimize(
            stream,
            lambda candidate: candidate.count(b"\n\n") == frame_count,
            max_calls=132,
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert result.status == "budget_exhausted"
    assert result.calls == 132
    assert peak < len(data) * 20


def test_fresh_checks_do_not_reuse_cached_answer_and_detect_flakiness() -> None:
    stream = frame_stream(b"data: required\n\ndata: noise\n\n")
    candidate_calls: dict[bytes, int] = {}

    def predicate(data: bytes) -> bool:
        candidate_calls[data] = candidate_calls.get(data, 0) + 1
        if data == b"data: required\n\n":
            return candidate_calls[data] == 1
        return b"data: required" in data

    with pytest.raises(PredicateError, match="inconsistent"):
        minimize(stream, predicate, repeat=2)

    assert candidate_calls[b"data: required\n\n"] >= 2


@pytest.mark.parametrize(
    ("option", "value"),
    [("max_calls", 0), ("max_calls", True), ("repeat", -1), ("repeat", 1.5)],
)
def test_numeric_options_must_be_positive_integers(option: str, value: object) -> None:
    stream = frame_stream(b"data: event\n\n")

    with pytest.raises(InputError, match=option):
        minimize(stream, lambda _data: False, **{option: value})
