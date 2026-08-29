import pytest
from fastapi.testclient import TestClient

from urlshort.api import app, store


@pytest.fixture(autouse=True)
def _clean_store():
    store.__init__()  # noqa: PLC2801 - reset the module-level store between tests
    yield


@pytest.fixture
def client():
    return TestClient(app)


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_version_reports_the_build(client, monkeypatch):
    monkeypatch.setenv("GIT_SHA", "deadbeef")
    monkeypatch.setenv("IMAGE_TAG", "v9.9.9")
    body = client.get("/version").json()
    assert body["git_sha"] == "deadbeef"
    assert body["image_tag"] == "v9.9.9"
    assert "version" in body


def test_shorten_and_follow(client):
    created = client.post("/shorten", json={"url": "https://example.com/x"})
    assert created.status_code == 201
    code = created.json()["code"]

    r = client.get(f"/r/{code}", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "https://example.com/x"


def test_shorten_rejects_bad_url(client):
    r = client.post("/shorten", json={"url": "javascript:alert(1)"})
    assert r.status_code == 400


def test_unknown_code_is_404(client):
    assert client.get("/r/zzzz", follow_redirects=False).status_code == 404
