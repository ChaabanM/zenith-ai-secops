"""Streaming JSONL line reader with a per-line memory bound."""

import hashlib
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Iterator
from dataclasses import dataclass


@dataclass
class Line:
    number: int  # 1-based physical line number
    data: bytes  # at most max_bytes + 1 bytes (prefix when the line is too long)
    size: int  # full line size in bytes (without the newline)
    sha256: str  # hash of the full line

    @property
    def is_blank(self) -> bool:
        return self.size == len(self.data) and not self.data.strip()


class _LineAssembler:
    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self.number = 0
        self._reset()

    def _reset(self) -> None:
        self.buf = bytearray()
        self.size = 0
        self.hash = hashlib.sha256()

    def _append(self, piece: bytes) -> None:
        self.size += len(piece)
        self.hash.update(piece)
        room = self.max_bytes + 1 - len(self.buf)
        if room > 0:
            self.buf += piece[:room]

    def _emit(self) -> Line:
        self.number += 1
        data = bytes(self.buf)
        size, digest = self.size, self.hash.hexdigest()
        if size == len(data) and data.endswith(b"\r"):  # tolerate CRLF line endings
            data = data[:-1]
            size -= 1
            digest = hashlib.sha256(data).hexdigest()
        line = Line(self.number, data, size, digest)
        self._reset()
        return line

    def feed(self, chunk: bytes) -> Iterator[Line]:
        start = 0
        while True:
            newline = chunk.find(b"\n", start)
            if newline == -1:
                self._append(chunk[start:])
                return
            self._append(chunk[start:newline])
            yield self._emit()
            start = newline + 1

    def close(self) -> Iterator[Line]:
        if self.size:
            yield self._emit()


async def aiter_lines(stream: AsyncIterable[bytes], max_bytes: int) -> AsyncIterator[Line]:
    assembler = _LineAssembler(max_bytes)
    async for chunk in stream:
        for line in assembler.feed(chunk):
            yield line
    for line in assembler.close():
        yield line


def iter_lines(chunks: Iterable[bytes], max_bytes: int) -> Iterator[Line]:
    assembler = _LineAssembler(max_bytes)
    for chunk in chunks:
        yield from assembler.feed(chunk)
    yield from assembler.close()
