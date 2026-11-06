import httpx
import pytest
import respx

from rk.net.http import BudgetExhausted, HttpClient, HttpError


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept: list[float] = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def client(tmp_path, clock):
    c = HttpClient(tmp_path / "http.sqlite", user_agent="rk-test", sleep=clock.sleep, now=clock.now, wall=clock.now)
    yield c
    c.close()


@respx.mock
def test_cache_hit_no_network(client):
    route = respx.get("https://api.crossref.org/works/x").mock(return_value=httpx.Response(200, json={"a": 1}))
    a = client.get("https://api.crossref.org/works/x")
    b = client.get("https://api.crossref.org/works/x")
    assert route.call_count == 1 and not a.from_cache and b.from_cache and b.json() == {"a": 1}


@respx.mock
def test_404_returned_not_cached(client):
    route = respx.get("https://api.crossref.org/works/missing").mock(return_value=httpx.Response(404))
    assert client.get("https://api.crossref.org/works/missing").status == 404
    assert client.get("https://api.crossref.org/works/missing").status == 404
    assert route.call_count == 2


@respx.mock
def test_get_json_non_404_errors_raise(client):
    """A rejected key (403) must not look like a missing work (which would mark a real paper 'not_found')."""
    from rk.sources import crossref

    respx.get("https://api.crossref.org/works/10.1000/x").mock(return_value=httpx.Response(403))
    respx.get("https://api.crossref.org/works/10.1000/gone").mock(return_value=httpx.Response(404))
    with pytest.raises(HttpError) as ei:
        crossref.get_work(client, "10.1000/x", None)
    assert ei.value.status == 403
    assert crossref.get_work(client, "10.1000/gone", None) is None


@respx.mock
def test_api_key_not_in_cache_key_or_url(client):
    respx.get("https://api.openalex.org/works/W1").mock(return_value=httpx.Response(200, json={}))
    res = client.get("https://api.openalex.org/works/W1", params={"api_key": "SECRET", "select": "id"})
    assert "SECRET" not in res.url
    rows = client._db.execute("SELECT key, url FROM http_cache").fetchall()
    assert rows and all("SECRET" not in (r[0] + r[1]) for r in rows)
    # a different key value hits the same cache entry
    assert client.get("https://api.openalex.org/works/W1", params={"api_key": "OTHER", "select": "id"}).from_cache


@respx.mock
def test_retry_after_and_budget(client, clock):
    respx.get("https://api.openalex.org/works").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json={"results": []}, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "3600"}),
        ]
    )
    res = client.get("https://api.openalex.org/works", params={"search": "x"}, budget_key="openalex", ttl_s=0)
    assert res.status == 200 and 2.0 in clock.slept
    with pytest.raises(BudgetExhausted):
        client.get("https://api.openalex.org/works", params={"search": "y"}, budget_key="openalex")
    clock.t += 4000
    respx.get("https://api.openalex.org/works").mock(return_value=httpx.Response(200, json={}))
    assert client.get("https://api.openalex.org/works", params={"search": "z"}, budget_key="openalex").status == 200


@respx.mock
def test_gives_up_after_retries(client):
    respx.get("https://api.crossref.org/works/y").mock(return_value=httpx.Response(503))
    with pytest.raises(HttpError) as ei:
        client.get("https://api.crossref.org/works/y")
    assert ei.value.status == 503


@respx.mock
def test_per_host_pacing(client, clock):
    respx.get(url__regex=r"https://export\.arxiv\.org/api/query.*").mock(return_value=httpx.Response(200, text="<feed/>"))
    client.get("https://export.arxiv.org/api/query", params={"id_list": "1"})
    clock.t += 1.0
    client.get("https://export.arxiv.org/api/query", params={"id_list": "2"})
    assert clock.slept and abs(clock.slept[-1] - 2.0) < 1e-9
