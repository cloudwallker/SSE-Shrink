from __future__ import annotations

import httpx
import pytest

from sse_shrink.replay import ReplayTransport


def test_streams_exact_bytes_in_configured_chunks_without_preloading() -> None:
    data = b"data: hi\n\n"
    transport = ReplayTransport(data, chunk_size=3)

    with httpx.Client(transport=transport, trust_env=False) as client:
        with client.stream("POST", "https://offline.invalid/v1/chat/completions") as response:
            assert response.is_stream_consumed is False
            assert list(response.iter_raw()) == [b"dat", b"a: ", b"hi\n", b"\n"]


def test_one_byte_chunks_preserve_utf8_bytes() -> None:
    data = "data: 你好\n\n".encode()

    with httpx.Client(transport=ReplayTransport(data, chunk_size=1), trust_env=False) as client:
        with client.stream("GET", "https://offline.invalid/stream") as response:
            assert list(response.iter_raw()) == [bytes([value]) for value in data]


def test_repeated_requests_receive_independent_streams() -> None:
    data = b"data: repeat\n\n"

    with httpx.Client(transport=ReplayTransport(data, chunk_size=5), trust_env=False) as client:
        first = client.get("https://one.invalid/stream")
        second = client.get("https://two.invalid/stream")

    assert first.content == data
    assert second.content == data


def test_response_has_configured_status_and_sse_content_type() -> None:
    with httpx.Client(transport=ReplayTransport(b"", status_code=206), trust_env=False) as client:
        response = client.get("https://offline.invalid/stream")

    assert response.status_code == 206
    assert response.headers["content-type"] == "text/event-stream"


@pytest.mark.parametrize("chunk_size", [0, -1, True, 1.5])
def test_invalid_chunk_size_is_rejected(chunk_size: object) -> None:
    with pytest.raises(ValueError):
        ReplayTransport(b"data: x\n\n", chunk_size=chunk_size)  # type: ignore[arg-type]


def test_response_and_transport_close_cleanly_before_stream_is_consumed() -> None:
    transport = ReplayTransport(b"data: unread\n\n", chunk_size=2)
    client = httpx.Client(transport=transport, trust_env=False)
    response = client.send(
        client.build_request("GET", "https://offline.invalid/stream"), stream=True
    )

    response.close()
    client.close()

    assert response.is_closed is True
