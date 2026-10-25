"""Just enough multipart/form-data to read and replace one simple field; file parts are never copied apart."""
from __future__ import annotations

import re

_B = re.compile(r'boundary\s*=\s*(?:"([^"]*)"|([^;\s]+))', re.I)
_NAME = re.compile(rb'(?:^|[;\s])name\s*=\s*(?:"([^"]*)"|([^;\s]+))', re.I)
_FILE = re.compile(rb'[;\s]filename\*?\s*=', re.I)


def boundary(content_type: str | None) -> bytes | None:
    if not content_type or "multipart/" not in content_type.lower():
        return None
    m = _B.search(content_type)
    b = (m.group(1) if m.group(1) is not None else m.group(2)) if m else ""
    return b.encode("latin-1", "replace") if b else None


def _parts(body: bytes, b: bytes) -> tuple[list[tuple[bytes | None, bool, int, int]], int]:
    """([(name, is_file, value_start, value_end)], where the closing delimiter starts or -1)."""
    delim = b"--" + b
    out = []
    pos = body.find(delim)
    while pos >= 0:
        after = pos + len(delim)
        if body[after:after + 2] == b"--":
            return out, pos
        nl = body.find(b"\n", after)
        if nl < 0:
            break
        eol = b"\r\n" if body[nl - 1:nl] == b"\r" else b"\n"
        hs = nl + 1
        if body.startswith(eol, hs):
            head, vs = b"", hs + len(eol)
        else:
            he = body.find(eol + eol, hs)
            if he < 0:
                break
            head, vs = body[hs:he], he + 2 * len(eol)
        nxt = body.find(eol + delim, vs)
        if nxt < 0:
            break
        m = _NAME.search(head)
        name = (m.group(1) if m.group(1) is not None else m.group(2)) if m else None
        out.append((name, bool(_FILE.search(head)), vs, nxt))
        pos = nxt + len(eol)
    return out, -1


def field(body: bytes, content_type: str | None, name: str) -> str | None:
    b = boundary(content_type)
    if not b or not body:
        return None
    want = name.encode()
    for n, is_file, s, e in _parts(body, b)[0]:
        if n == want and not is_file:
            return body[s:e].decode("utf-8", "replace")
    return None


def set_field(body: bytes, content_type: str | None, name: str, value: str) -> bytes:
    """Replace a simple field's value, or add the field before the closing delimiter; other bytes are untouched."""
    b = boundary(content_type)
    if not b:
        return body
    want, val = name.encode(), value.encode()
    parts, close = _parts(body, b)
    for n, is_file, s, e in parts:
        if n == want and not is_file:
            return body[:s] + val + body[e:]
    part = b'--' + b + b'\r\nContent-Disposition: form-data; name="' + want + b'"\r\n\r\n' + val + b"\r\n"
    if close >= 0:
        return body[:close] + part + body[close:]
    return body + part + b"--" + b + b"--\r\n"
