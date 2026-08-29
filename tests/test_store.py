import pytest

from urlshort.store import InvalidURL, Store, UnknownCode, encode, normalise


def test_encode_is_stable_and_ordered():
    assert encode(0) == "a"
    assert encode(1) == "b"
    assert encode(61) == "9"
    assert encode(62) == "ba"


def test_encode_rejects_negative():
    with pytest.raises(ValueError):
        encode(-1)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Example.COM", "https://example.com/"),
        ("http://example.com/a/b", "http://example.com/a/b"),
        ("  https://example.com/x?q=1  ", "https://example.com/x?q=1"),
    ],
)
def test_normalise(raw, expected):
    assert normalise(raw) == expected


@pytest.mark.parametrize("raw", ["", "ftp://example.com", "not a url", "https://"])
def test_normalise_rejects_junk(raw):
    with pytest.raises(InvalidURL):
        normalise(raw)


def test_shorten_is_idempotent():
    s = Store()
    first = s.shorten("https://example.com/one")
    again = s.shorten("https://EXAMPLE.com/one")
    assert first == again
    assert len(s) == 1


def test_shorten_then_resolve():
    s = Store()
    code = s.shorten("https://example.com/deep/link?a=1")
    assert s.resolve(code) == "https://example.com/deep/link?a=1"


def test_resolve_unknown():
    with pytest.raises(UnknownCode):
        Store().resolve("nope")
