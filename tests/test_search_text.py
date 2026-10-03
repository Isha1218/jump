from jump.search.text import normalize_text, query_terms


def test_normalize_text():
    assert normalize_text("L04") == "l 4"
    assert normalize_text("CSE452: Lecture 05 (RPC)!") == "cse 452 lecture 5 rpc"
    assert normalize_text("0 007 2026") == "0 7 2026"
    assert normalize_text("snake_case-name") == "snake case name"
    assert normalize_text(None) == ""


def test_query_terms_synonyms_and_stopwords():
    terms = query_terms("the 452 RPC lecture")
    assert terms[0] == ["452"] and terms[1] == ["rpc"]
    assert terms[2][0] == "lecture" and "slides" in terms[2]
    assert query_terms("open pull request 12") == [["pull request", "pr"], ["12"]]
    assert ["billing", "invoices", "payments"] == sorted(query_terms("billing")[0])
    assert query_terms("and") == [["and"]]          # all-stopword query keeps its words
    assert query_terms('"*()') == []
