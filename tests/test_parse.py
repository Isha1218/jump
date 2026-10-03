from jump.crawl.parse import parse

PAGE = """<html><head><title> CSE 452  Schedule </title><base href="https://x.edu/c/">
<script>var secret = "SCRIPTTEXT";</script><style>.x{}</style></head>
<body>
<nav><a href="/c/">Home</a> <a href="staff.html">Staff</a></nav>
<header>HEADERTEXT</header>
<h1>Schedule</h1><h2>Week 1</h2>
<p>Lectures are on Mondays. See <a href="syllabus.html">the syllabus</a> for details.</p>
<table>
<tr><td>Oct 3</td><td>RPC</td><td><a href="lectures/l05.pdf">slides</a></td></tr>
<tr><td>Oct 5</td><td>Paxos</td><td><a href="lectures/l06.pdf#p2">slides</a></td></tr>
</table>
<ul><li><a href="staff.html"></a></li><li><a href="staff.html">Staff</a></li></ul>
<a href="https://github.com/x/repo" aria-label="Repo"><i></i></a>
<a href="ta.html"><img src="t.png" alt="TA photo"></a>
<a href="mailto:a@x.edu">mail</a>
<footer>FOOTERTEXT <a href="/c/">Home</a></footer>
</body></html>"""


def test_parse_page():
    p = parse(PAGE, "https://x.edu/c/schedule.html")
    assert p.title == "CSE 452 Schedule"
    assert p.headings == ["Schedule", "Week 1"]
    links = {u: (a, c) for u, a, c in p.links}
    assert len(links) == len(p.links)  # deduped
    # base href resolution and fragment stripping
    assert links["https://x.edu/c/lectures/l05.pdf"] == ("slides", "Oct 3 · RPC · slides")
    assert links["https://x.edu/c/lectures/l06.pdf"] == ("slides", "Oct 5 · Paxos · slides")
    assert links["https://x.edu/c/syllabus.html"][1].startswith("Lectures are on Mondays. See the syllabus")
    assert links["https://x.edu/c/staff.html"] == ("Staff", "")  # context equal to anchor -> empty
    assert links["https://github.com/x/repo"][0] == "Repo"
    assert links["https://x.edu/c/ta.html"][0] == "TA photo"
    assert not any(u.startswith("mailto") for u in links)
    assert "https://x.edu/c/" in links


def test_snippet_skips_chrome():
    p = parse(PAGE, "https://x.edu/c/schedule.html")
    assert p.snippet.startswith("CSE 452 Schedule · Schedule · Week 1")
    assert "Lectures are on Mondays" in p.snippet
    for junk in ("SCRIPTTEXT", "HEADERTEXT", "FOOTERTEXT", "Home"):
        assert junk not in p.snippet


def test_parse_long_context_and_heading_cap():
    html = "<h2>h</h2>" * 40 + "<li>" + "word " * 100 + '<a href="/z">z</a></li>'
    p = parse(html, "https://x.edu/")
    assert len(p.headings) == 30
    (_, _, ctx), = p.links
    assert len(ctx) <= 201 and ctx.endswith("…")
