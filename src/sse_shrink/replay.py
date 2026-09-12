"""Offline synchronous HTTPX replay transport."""

from __future__ import annotations

from collections.abc import Iterator

import httpx


class _ReplayStream(httpx.SyncByteStream):
    def __init__(self, data: bytes, chunk_size: int):
        self._data = data
        self._chunk_size = chunk_size
        self._closed = False

    def __iter__(self) -> Iterator[bytes]:
        for start in range(0, len(self._data), self._chunk_size):
            yield self._data[start : start + self._chunk_size]

    def close(self) -> None:
        self._closed = True


class ReplayTransport(httpx.BaseTransport):
    """Return the same finite SSE byte stream for every request, without network access."""

    def __init__(self, data: bytes, *, chunk_size: int = 64, status_code: int = 200):
        if not isinstance(data, bytes):
            raise TypeError("replay data must be bytes")
        if type(chunk_size) is not int or chunk_size <= 0:
            raise ValueError("chunk_size must be a positive integer")

        self._data = data
        self._chunk_size = chunk_size
        self._status_code = status_code

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            self._status_code,
            headers={"content-type": "text/event-stream"},
            stream=_ReplayStream(self._data, self._chunk_size),
        )
