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
def test_get_json_non_json_200_is_a_source_error_and_not_cached(client):
    """A 200 with an error page instead of JSON (seen from OpenAlex) is a transient source error, not a JSONDecodeError
    that fails the run, and the bad body is not served from the cache next time."""
    from rk.discovery.boundary import transient_source_error
    from rk.net.http import BadBody

    route = respx.get("https://api.openalex.org/works").mock(side_effect=[
        httpx.Response(200, text="<html>Service busy</html>"), httpx.Response(200, json={"results": []})])
    with pytest.raises(HttpError) as ei:
        client.get_json("https://api.openalex.org/works", {"search": "x"})
    assert isinstance(ei.value, BadBody) and ei.value.status == 200 and "not JSON" in str(ei.value)
    assert transient_source_error(ei.value)
    data, _ = client.get_json("https://api.openalex.org/works", {"search": "x"})
    assert data == {"results": []} and route.call_count == 2


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
def test_a_429_that_spends_the_budget_or_asks_for_a_long_wait_raises_instead_of_sleeping(client, clock):
    """Task 9: OpenAlex's anonymous daily budget answers 429 with retry-after ~6 h; the request must not sleep for hours
    (the run hung in landscape) but raise BudgetExhausted, which pauses the run (resumed after the reset)."""
    spent = {"Retry-After": "21529", "x-ratelimit-remaining": "0", "x-ratelimit-reset": "21529"}
    respx.get("https://api.openalex.org/works").mock(return_value=httpx.Response(429, headers=spent))
    with pytest.raises(BudgetExhausted) as ei:
        client.get("https://api.openalex.org/works", params={"search": "x"}, budget_key="openalex", ttl_s=0)
    assert ei.value.reset_at == pytest.approx(1000.0 + 21529) and max(clock.slept, default=0) < 60
    # a 429 that spends the recorded budget raises at once, even with a short Retry-After (no retry burns it again)
    clock.slept.clear()
    short = {"Retry-After": "1", "x-ratelimit-remaining": "0", "x-ratelimit-reset": "30"}
    respx.get("https://api.openalex.org/works/W9").mock(return_value=httpx.Response(429, headers=short))
    with pytest.raises(BudgetExhausted) as ei:
        client.get("https://api.openalex.org/works/W9", budget_key="openalex-w")
    assert ei.value.budget_key == "openalex-w" and max(clock.slept, default=0) < 1  # host pacing only, no retry wait
    # no budget header: a long Retry-After alone raises too, keyed by the host
    respx.get("https://api.crossref.org/works/z").mock(return_value=httpx.Response(429, headers={"Retry-After": "7200"}))
    with pytest.raises(BudgetExhausted) as ei:
        client.get("https://api.crossref.org/works/z")
    assert ei.value.budget_key == "api.crossref.org" and max(clock.slept, default=0) < 60
    # a server error with a long Retry-After is no budget: retried with capped waits, then the transient HttpError
    clock.slept.clear()
    respx.get("https://api.semanticscholar.org/down").mock(return_value=httpx.Response(503, headers={"Retry-After": "3600"}))
    with pytest.raises(HttpError) as ei:  # another host: api.crossref.org is now waited out (_check_host_wait)
        client.get("https://api.semanticscholar.org/down")
    assert ei.value.status == 503 and clock.slept and max(clock.slept) == 60.0


@respx.mock
def test_a_host_that_asked_for_a_long_wait_is_not_asked_again_before_it(client, clock):
    """Review of oa-host-429: a host whose 429 asked for longer than a request waits (no budget key) is remembered until
    its reset: later requests to it raise at once without a request, so each later record's URL on that host does not
    knock again; after the reset it is asked again. Another host is unaffected."""
    first = respx.get("https://repo.example.org/a.pdf").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "3600"}))
    with pytest.raises(BudgetExhausted):
        client.get("https://repo.example.org/a.pdf", ttl_s=0)
    later = respx.get("https://repo.example.org/b.pdf").mock(return_value=httpx.Response(200, content=b"%PDF-"))
    with pytest.raises(BudgetExhausted) as ei:
        client.get("https://repo.example.org/b.pdf", ttl_s=0)
    assert later.call_count == 0 and ei.value.budget_key == "repo.example.org"
    assert first.call_count == 1 and ei.value.reset_at == pytest.approx(1000.0 + 3600, abs=5)
    respx.get("https://example.org/c.pdf").mock(return_value=httpx.Response(200, content=b"%PDF-"))
    assert client.get("https://example.org/c.pdf", ttl_s=0).status == 200
    clock.t += 3700
    assert client.get("https://repo.example.org/b.pdf", ttl_s=0).status == 200 and later.call_count == 1


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
