"""The expensive end of the suite.

Marked `slow` so the pull-request gate can skip it and the nightly run cannot.
Module 0x02 is where you wire that split up.

Nothing here is unusual — it is the shape of test that always ends up outside
the fast gate: bulk data, sustained load, and anything that waits.
"""

import time

import pytest
from fastapi.testclient import TestClient

from urlshort.api import app
from urlshort.store import Store


@pytest.mark.slow
def test_two_hundred_thousand_links_stay_unique():
    s = Store()
    codes = {s.shorten(f"https://example.com/{i}") for i in range(200_000)}
    assert len(codes) == 200_000
    assert len(s) == 200_000


@pytest.mark.slow
def test_survives_a_sustained_burst():
    client = TestClient(app)
    start = time.time()
    for i in range(5_000):
        r = client.post("/shorten", json={"url": f"https://example.com/burst/{i}"})
        assert r.status_code == 201
    elapsed = time.time() - start
    assert elapsed < 120, f"5000 shortens took {elapsed:.1f}s"


@pytest.mark.slow
def test_redirects_stay_correct_under_repetition():
    client = TestClient(app)
    created = client.post("/shorten", json={"url": "https://example.com/hot"}).json()
    code = created["code"]
    for _ in range(3_000):
        r = client.get(f"/r/{code}", follow_redirects=False)
        assert r.status_code == 307
        assert r.headers["location"] == "https://example.com/hot"
