"""Losslessly split SSE bytes into removable event blocks."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import chain

from .errors import InputError

_UTF8_BOM = b"\xef\xbb\xbf"
_LINE_ENDING = re.compile(rb"\r\n|[\r\n]")
_LEADING_LINE_ENDINGS = re.compile(rb"[\r\n]+")


@dataclass(frozen=True)
class Frame:
    """One original byte range and its location in the input."""

    raw: bytes
    start_line: int
    complete: bool


@dataclass(frozen=True)
class FramedStream:
    """A fixed preamble followed by removable frames."""

    prefix: bytes
    frames: tuple[Frame, ...]

    def join(self, indices: tuple[int, ...] | None = None) -> bytes:
        """Reassemble an ordered subsequence without changing any bytes."""
        if indices is None:
            selected = range(len(self.frames))
        else:
            previous = -1
            for index in indices:
                if (
                    type(index) is not int
                    or index <= previous
                    or index < 0
                    or index >= len(self.frames)
                ):
                    raise InputError("indices must be unique, ordered frame indices")
                previous = index
            selected = indices
        parts = (self.frames[index].raw for index in selected)
        if self.prefix:
            return b"".join(chain((self.prefix,), parts))
        return b"".join(parts)


@dataclass(frozen=True)
class _Line:
    start: int
    content_end: int
    end: int

    @property
    def blank(self) -> bool:
        return self.start == self.content_end


def _lines(data: bytes, start: int) -> Iterator[_Line]:
    for ending in _LINE_ENDING.finditer(data, start):
        yield _Line(start, ending.start(), ending.end())
        start = ending.end()
    if start < len(data):
        yield _Line(start, len(data), len(data))


def _positive_integer(name: str, value: object) -> int:
    if type(value) is not int or value <= 0:
        raise InputError(f"{name} must be a positive integer")
    return value


def frame_stream(
    data: bytes,
    *,
    max_bytes: int = 10 * 1024 * 1024,
    max_events: int = 10_000,
) -> FramedStream:
    """Split raw SSE bytes at blank lines while preserving exact byte ranges."""
    byte_limit = _positive_integer("max_bytes", max_bytes)
    event_limit = _positive_integer("max_events", max_events)
    if not isinstance(data, bytes):
        raise InputError("data must be bytes")
    if len(data) > byte_limit:
        raise InputError(f"input size exceeds max_bytes ({byte_limit})")

    bom_end = len(_UTF8_BOM) if data.startswith(_UTF8_BOM) else 0
    prefix_end = bom_end
    start_line = 1

    leading_endings = _LEADING_LINE_ENDINGS.match(data, bom_end)
    if leading_endings is not None:
        prefix_end = leading_endings.end()
        start_line += (
            data.count(b"\r", bom_end, prefix_end)
            + data.count(b"\n", bom_end, prefix_end)
            - data.count(b"\r\n", bom_end, prefix_end)
        )

    scanned_lines = iter(_lines(data, prefix_end))
    first_content_line: _Line | None = None
    try:
        first_content_line = next(scanned_lines)
    except StopIteration:
        pass

    if first_content_line is None:
        return FramedStream(prefix=data[:prefix_end], frames=())

    frames: list[Frame] = []
    frame_start = first_content_line.start
    frame_start_line = start_line
    current_line = first_content_line

    while True:
        if current_line.blank:
            frames.append(
                Frame(
                    raw=data[frame_start : current_line.end],
                    start_line=frame_start_line,
                    complete=True,
                )
            )
            if len(frames) > event_limit:
                raise InputError(f"event count exceeds max_events ({event_limit})")
            frame_start = current_line.end
            frame_start_line = start_line + 1

        start_line += 1
        try:
            current_line = next(scanned_lines)
        except StopIteration:
            break

    if frame_start < len(data):
        frames.append(Frame(raw=data[frame_start:], start_line=frame_start_line, complete=False))
        if len(frames) > event_limit:
            raise InputError(f"event count exceeds max_events ({event_limit})")

    return FramedStream(prefix=data[:prefix_end], frames=tuple(frames))
