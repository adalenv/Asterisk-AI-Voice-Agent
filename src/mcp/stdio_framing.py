from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from .errors import MCPProtocolError


_HEADER_SEP = b"\r\n\r\n"


def encode_message(payload: Dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def _parse_headers(raw: bytes) -> Dict[str, str]:
    try:
        text = raw.decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise MCPProtocolError(f"Invalid header encoding: {exc}") from exc

    headers: Dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise MCPProtocolError(f"Malformed header line: {line!r}")
        k, v = line.split(":", 1)
        headers[k.strip().lower()] = v.strip()
    return headers


def decode_frame(buffer: bytearray) -> Tuple[Optional[Dict[str, Any]], int]:
    """Decode a single MCP frame from an in-memory buffer.

    Returns (message_or_none, bytes_consumed).
    """
    idx = buffer.find(_HEADER_SEP)
    if idx < 0:
        return None, 0

    header_bytes = bytes(buffer[:idx])
    headers = _parse_headers(header_bytes)
    if "content-length" not in headers:
        raise MCPProtocolError("Missing Content-Length header")

    try:
        length = int(headers["content-length"])
    except ValueError as exc:
        raise MCPProtocolError(f"Invalid Content-Length: {headers['content-length']!r}") from exc

    start = idx + len(_HEADER_SEP)
    end = start + length
    if len(buffer) < end:
        return None, 0

    body_bytes = bytes(buffer[start:end])
    try:
        message = json.loads(body_bytes.decode("utf-8"))
    except Exception as exc:
        raise MCPProtocolError(f"Invalid JSON body: {exc}") from exc

    return message, end

