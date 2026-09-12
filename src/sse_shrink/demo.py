"""Offline synthetic stream with a deliberately faulty consumer."""

from __future__ import annotations

import json

import httpx

from sse_shrink.replay import ReplayTransport


def build_synthetic_stream() -> bytes:
    """Return many irrelevant events around the two-event synthetic failure."""

    def event(payload: dict[str, object]) -> bytes:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return b"data: " + encoded + b"\n\n"

    before = [event({"type": "content_block_delta", "index": index}) for index in range(12)]
    start = event(
        {
            "type": "message_start",
            "message": {"id": "synthetic-message", "role": "assistant"},
        }
    )
    middle = [event({"type": "ping", "index": index}) for index in range(16)]
    delta = event({"type": "message_delta", "delta": {"stop_reason": "end_turn"}})
    after = [event({"type": "content_block_stop", "index": index}) for index in range(12)]
    return b"".join((*before, start, *middle, delta, *after))


def _consume_with_deliberate_bug(data: bytes) -> None:
    """Replay the stream into a parser intentionally missing a usage default."""
    message: dict[str, object] | None = None
    transport = ReplayTransport(data, chunk_size=7)
    with httpx.Client(transport=transport, trust_env=False) as client:
        with client.stream("POST", "https://offline.invalid/v1/messages") as response:
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                payload = json.loads(line[6:])
                if payload.get("type") == "message_start":
                    message = payload["message"]
                elif payload.get("type") == "message_delta":
                    if message is None:
                        raise RuntimeError("message_delta before message_start")
                    # Deliberate demo defect: message_start omitted this field.
                    message["usage"]  # noqa: B018


def fails(data: bytes) -> bool:
    """Recognize only the demo's intentional ``KeyError('usage')`` target."""
    try:
        _consume_with_deliberate_bug(data)
    except KeyError as error:
        return error.args == ("usage",)
    except Exception:
        return False
    return False
