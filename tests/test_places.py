from jump import places


def _group(urls_days):
    """urls_days: {url: n_days} -> {scope: Place}"""
    return {p.scope: p for p in places.group({u: set(range(n)) for u, n in urls_days.items()})}


def test_course_offerings_are_separate_places():
    base = "https://courses.cs.washington.edu/courses"
    urls = {}
    for course in ("cse452", "cse451"):
        urls[f"{base}/{course}/26au/"] = 6
        for i in range(1, 6):
            urls[f"{base}/{course}/26au/lectures/l{i:02d}.html"] = 2
            urls[f"{base}/{course}/26au/hw/hw{i}.pdf"] = 1
    got = _group(urls)
    assert set(got) == {f"{base}/cse452/26au/", f"{base}/cse451/26au/"}
    assert got[f"{base}/cse452/26au/"].alias == "cse452"
    assert len(got[f"{base}/cse451/26au/"].urls) == 11


def test_github_repos_are_places():
    urls = {
        "https://github.com/alice/raft/issues/12": 2,
        "https://github.com/alice/raft/pull/40": 1,
        "https://github.com/alice/raft/blob/main/src/node.go": 1,
        "https://github.com/alice/raft": 3,
        "https://github.com/alice/kvstore/issues/3": 1,
        "https://github.com/alice/kvstore/pull/7": 2,
        "https://github.com/bob/paxos/issues/1": 2,
        "https://github.com/bob/paxos/blob/main/README.md": 1,
    }
    got = _group(urls)
    assert set(got) == {"https://github.com/alice/raft/", "https://github.com/alice/kvstore/",
                        "https://github.com/bob/paxos/"}
    assert "https://github.com/alice/raft" in got["https://github.com/alice/raft/"].urls
    assert got["https://github.com/bob/paxos/"].alias == "paxos"


def test_doc_sections_stay_one_place():
    urls = {
        "https://docs.stripe.com/api/charges/create": 3,
        "https://docs.stripe.com/api/customers": 2,
        "https://docs.stripe.com/payments/checkout/how-checkout-works": 2,
        "https://docs.stripe.com/payments/accept-a-payment": 1,
        "https://docs.stripe.com/webhooks": 2,
        "https://docs.stripe.com/webhooks/signatures": 1,
    }
    got = _group(urls)
    assert set(got) == {"https://docs.stripe.com/"}
    assert got["https://docs.stripe.com/"].alias == "stripe"


def test_single_deep_url_is_its_directory():
    got = _group({"https://blog.example.org/2024/05/notes/raft-explained.html": 1})
    assert set(got) == {"https://blog.example.org/2024/05/notes/"}
    assert got["https://blog.example.org/2024/05/notes/"].alias == "notes"


def test_concentration_descends_and_leftovers_stay_at_node():
    urls = {f"https://wiki.example.com/team/infra/runbooks/r{i}": 3 for i in range(5)}
    urls["https://wiki.example.com/"] = 1
    got = _group(urls)
    assert set(got) == {"https://wiki.example.com/team/infra/runbooks/", "https://wiki.example.com/"}


def test_depth_is_capped():
    got = _group({"https://x.org/a/b/c/d/e/f/page.html": 2})
    assert set(got) == {"https://x.org/a/b/c/d/"}


def test_hubs_detected_by_behavior():
    visits = []
    for i, dest in enumerate(["https://a.com/x", "https://b.org/y", "https://c.net/z"]):
        q = f"https://search.example/s?q=term{i}"
        visits += [{"url": q, "from_url": None, "ts": 0}, {"url": dest, "from_url": q, "ts": 1},
                   {"url": q, "from_url": None, "ts": 2}]  # came back to the results the same day
    # a non-hub: same path, many queries, but you never leave the site from it
    for i in range(4):
        visits.append({"url": f"https://shop.example/item?id={i}", "from_url": None})
        visits.append({"url": "https://shop.example/cart", "from_url": f"https://shop.example/item?id={i}"})
    assert places.find_hubs(visits) == {"https://search.example/s"}


def test_alias_from_host():
    assert places.alias("https://www.bbc.co.uk", "/") == "bbc"
    assert places.alias("https://docs.python.org", "/3/") == "python"


def test_opaque_ids_make_communities_tenants():
    got = _group({
        "https://forum.example/r/rust/comments/1n2xyz/borrow_checker_help/": 2,
        "https://forum.example/r/rust/comments/1q9abc/async_traits/": 1,
        "https://forum.example/r/golang/comments/2k3def/generics_question/": 2,
    })
    rust = [s for s in got if s.startswith("https://forum.example/r/rust/")]
    golang = [s for s in got if s.startswith("https://forum.example/r/golang/")]
    assert len(rust) == len(golang) == 1 and len(got) == 2      # split per community, never at /r/


def test_pass_through_page_with_repeated_queries_is_hub():
    visits = []
    for i, dest in enumerate(["https://a.com/", "https://b.org/", "https://c.net/", "https://a.com/x"]):
        login = f"https://sso.example/login?step={i % 2}"            # only 2 distinct queries over 4 days
        visits += [{"url": login, "from_url": None, "ts": i * 86400, "duration_s": 2},
                   {"url": dest, "from_url": login, "ts": i * 86400 + 5}]
    assert places.find_hubs(visits) == {"https://sso.example/login"}
    for v in visits:
        v["duration_s"] = 120 if "sso" in v["url"] else 0
    assert places.find_hubs(visits) == set()                          # same pattern, but you stay: not a hub
