import tracemalloc

import pytest

from sse_shrink.errors import InputError
from sse_shrink.framing import frame_stream


def test_preserves_mixed_line_endings_prefix_and_incomplete_tail() -> None:
    data = b"\xef\xbb\xbf\n\ndata: keep\r\n\r\nretry: 3000\n\ndata: tail"

    stream = frame_stream(data)

    assert stream.prefix == b"\xef\xbb\xbf\n\n"
    assert [(frame.raw, frame.start_line, frame.complete) for frame in stream.frames] == [
        (b"data: keep\r\n\r\n", 3, True),
        (b"retry: 3000\n\n", 5, True),
        (b"data: tail", 7, False),
    ]
    assert stream.join() == data
    assert stream.join((1, 2)) == b"\xef\xbb\xbf\n\nretry: 3000\n\ndata: tail"


@pytest.mark.parametrize(
    ("data", "expected_frames"),
    [
        (
            b"event: message\ndata: first\ndata:\ndata: third\n\n",
            [(b"event: message\ndata: first\ndata:\ndata: third\n\n", 1, True)],
        ),
        (
            b": comment\rretry: 250\rdata: \r\rdata: next\r",
            [
                (b": comment\rretry: 250\rdata: \r\r", 1, True),
                (b"data: next\r", 5, False),
            ],
        ),
        (
            "data: 雪\r\n\r\n".encode(),
            [("data: 雪\r\n\r\n".encode(), 1, True)],
        ),
    ],
)
def test_preserves_metadata_multiline_empty_data_utf8_and_cr_variants(
    data: bytes, expected_frames: list[tuple[bytes, int, bool]]
) -> None:
    stream = frame_stream(data)

    assert [(frame.raw, frame.start_line, frame.complete) for frame in stream.frames] == (
        expected_frames
    )
    assert stream.join() == data


def test_bom_without_blank_lines_is_fixed_prefix() -> None:
    data = b"\xef\xbb\xbfdata: value\n\n"

    stream = frame_stream(data)

    assert stream.prefix == b"\xef\xbb\xbf"
    assert stream.frames[0].raw == b"data: value\n\n"
    assert stream.frames[0].start_line == 1
    assert stream.join(()) == b"\xef\xbb\xbf"


def test_empty_and_prefix_only_streams_have_no_frames() -> None:
    empty = frame_stream(b"")
    preamble = frame_stream(b"\xef\xbb\xbf\r\n\n\r")

    assert empty.prefix == b""
    assert empty.frames == ()
    assert preamble.prefix == b"\xef\xbb\xbf\r\n\n\r"
    assert preamble.frames == ()


def test_rejects_size_and_event_limit_overruns() -> None:
    with pytest.raises(InputError, match="size"):
        frame_stream(b"1234", max_bytes=3)
    with pytest.raises(InputError, match="event"):
        frame_stream(b"data: 1\n\ndata: 2\n\n", max_events=1)


@pytest.mark.parametrize(
    ("option", "value"),
    [("max_bytes", 0), ("max_bytes", True), ("max_events", -1), ("max_events", 1.5)],
)
def test_limits_must_be_positive_integers(option: str, value: object) -> None:
    with pytest.raises(InputError, match=option):
        frame_stream(b"", **{option: value})


def test_join_rejects_invalid_indices() -> None:
    stream = frame_stream(b"data: one\n\ndata: two\n\n")

    with pytest.raises(InputError, match="indices"):
        stream.join((1, 0))
    with pytest.raises(InputError, match="indices"):
        stream.join((2,))


def test_control_bytes_other_than_cr_and_lf_do_not_split_lines() -> None:
    data = b"data: x\x0b\x0c\x85\xe2\x80\xa8y\r\r\ndata: z\n\n"

    stream = frame_stream(data)

    assert [(frame.raw, frame.start_line, frame.complete) for frame in stream.frames] == [
        (b"data: x\x0b\x0c\x85\xe2\x80\xa8y\r\r\n", 1, True),
        (b"data: z\n\n", 3, True),
    ]
    assert stream.join() == data


def test_join_with_prefix_avoids_a_second_full_body_allocation() -> None:
    event = b"data: " + b"x" * (2 * 1024 * 1024) + b"\n\n"
    data = b"\xef\xbb\xbf\n" + event + event
    stream = frame_stream(data)

    already_tracing = tracemalloc.is_tracing()
    if not already_tracing:
        tracemalloc.start()
    baseline, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    try:
        result = stream.join((0, 1))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        if not already_tracing:
            tracemalloc.stop()

    assert result == data
    # Leave room for iterator bookkeeping, but not another complete body copy.
    assert peak - baseline < len(data) * 1.5
