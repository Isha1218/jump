from jump import db, naming
from jump.agents import namer
from jump.search import pipeline

ED = "https://edstem.org/us/courses/106640"


def test_clean_title_drops_unread_counts_and_site_endings():
    suffixes = naming.site_suffixes(["CSE 123 – Ed Discussion", "CSE 452 – Ed Discussion", "Lab 2 – Ed Discussion"])
    assert suffixes == {"ed discussion"}
    assert naming.clean_title("(886) CSE 123 - 26au – Ed Discussion", suffixes) == "CSE 123 - 26au"
    assert naming.clean_title("Inbox (21,407) - me@gmail.com - Gmail", set()) == "Inbox - me@gmail.com - Gmail"


def test_names_tell_lookalike_pages_apart():
    pages = [
        {"url": f"{ED}/discussion", "title": "(886) CSE 123 - 26au – Ed Discussion", "headings": "[]"},
        {"url": f"{ED}/discussion/8347484", "title": "(883) CSE 123 - 26au – Ed Discussion",
         "headings": '["Pre-class Material Completed before the displayed ddl? #26"]'},
        {"url": "https://edstem.org/us/dashboard", "title": "Dashboard – Ed Discussion", "headings": "[]"},
        {"url": "https://edstem.org/us/courses/106644/discussion", "title": "STAFF CSE 123 - 26au – Ed Discussion",
         "headings": "[]"},
        {"url": "https://x.edu/cse453/api.pdf", "title": "PowerPoint Presentation", "headings": None},
        {"url": "https://x.edu/cse453/l05.pdf", "title": "PowerPoint Presentation", "headings": None},
    ]
    names = naming.name_all(pages, {"https://x.edu/cse453/l05.pdf": ["slides", "Lecture 5: RPC"]})
    assert names[f"{ED}/discussion"] == "CSE 123 - 26au · discussion"          # shared title + no heading
    assert names[f"{ED}/discussion/8347484"] == "Pre-class Material Completed before the displayed ddl? #26"
    assert names["https://edstem.org/us/dashboard"] == "Dashboard"
    assert names["https://x.edu/cse453/api.pdf"] == "api.pdf"                    # default title -> file name
    assert names["https://x.edu/cse453/l05.pdf"] == "Lecture 5: RPC"            # specific link text wins


def test_run_once_writes_names_that_search_shows(conn):
    pid = db.upsert_place(conn, "https://www.amazon.com", "/", status="active", revisit=0.6)
    db.upsert_page(conn, "https://www.amazon.com/gp/css/order-history", place_id=pid, visited=1,
                   title="(2) Your Orders")
    assert namer.run_once(conn) == {"pages": 1, "renamed": 1}
    assert namer.run_once(conn) == {"pages": 1, "renamed": 0}                  # unchanged names aren't rewritten
    assert pipeline.search(conn, "amazon orders", use_jev=False)[0]["label"] == "Your Orders"
