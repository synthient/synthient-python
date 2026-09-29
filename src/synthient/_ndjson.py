from __future__ import annotations

from synthient._errors import SynthientError

MAX_EVENT_SIZE = 16 << 20


class LineBuffer:
    """Splits NDJSON byte chunks into records.

    Surrounding whitespace, including a CR from CRLF, is stripped, and blank keepalive
    lines are dropped. A line longer than ``limit`` bytes raises SynthientError.
    """

    def __init__(self, limit: int = MAX_EVENT_SIZE) -> None:
        self._limit = limit
        self._pending = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        head, *rest = chunk.split(b"\n")
        self._pending += head
        lines: list[bytes] = []
        if rest:
            lines = [bytes(self._pending), *rest[:-1]]
            self._pending = bytearray(rest[-1])
        if len(self._pending) > self._limit or any(len(line) > self._limit for line in lines):
            raise SynthientError(f"stream event exceeds {self._limit} bytes")
        return [record for line in lines if (record := line.strip())]

    def finish(self) -> bytes | None:
        """Return the final line when the stream ended without a newline."""
        record = bytes(self._pending.strip())
        self._pending.clear()
        return record or None
