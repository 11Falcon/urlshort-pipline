"""Pure logic: turning URLs into short codes and back.

No framework, no I/O. This is the part your unit tests can hammer in
milliseconds, which is what makes a fast pull-request gate possible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
ALLOWED_SCHEMES = frozenset({"http", "https"})


class InvalidURL(ValueError):
    """The caller gave us something that is not a fetchable http(s) URL."""


class UnknownCode(KeyError):
    """No such short code."""


def encode(number: int) -> str:
    """Base-62 encode a non-negative integer. encode(0) == 'a'."""
    if number < 0:
        raise ValueError("number must be non-negative")
    if number == 0:
        return ALPHABET[0]
    out: list[str] = []
    while number:
        number, rem = divmod(number, len(ALPHABET))
        out.append(ALPHABET[rem])
    return "".join(reversed(out))


def normalise(url: str) -> str:
    """Validate and canonicalise a URL, or raise InvalidURL."""
    candidate = (url or "").strip()
    if not candidate:
        raise InvalidURL("empty url")
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise InvalidURL(f"scheme {parsed.scheme!r} is not allowed")
    if not parsed.netloc:
        raise InvalidURL("url has no host")
    host = parsed.netloc.lower()
    path = parsed.path or "/"
    rebuilt = f"{parsed.scheme.lower()}://{host}{path}"
    if parsed.query:
        rebuilt += f"?{parsed.query}"
    return rebuilt


@dataclass
class Store:
    """An in-memory link table. Deliberately boring, deliberately testable."""

    _by_code: dict[str, str] = field(default_factory=dict)
    _by_url: dict[str, str] = field(default_factory=dict)
    _counter: int = 0

    def shorten(self, url: str) -> str:
        """Return a short code for `url`, reusing one if we have seen it."""
        target = normalise(url)
        existing = self._by_url.get(target)
        if existing is not None:
            return existing
        code = encode(self._counter)
        self._counter += 1
        self._by_code[code] = target
        self._by_url[target] = code
        return code

    def resolve(self, code: str) -> str:
        try:
            return self._by_code[code]
        except KeyError as exc:
            raise UnknownCode(code) from exc

    def __len__(self) -> int:
        return len(self._by_code)
