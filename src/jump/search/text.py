"""Text normalization shared by the FTS index and queries, so "lecture 5" meets "L05"."""
import re
import unicodedata

STOPWORDS = frozenset(
    "a an the of for to in on at by with from and or not is are my me i this that page open go show find please".split()
)

# Generic synonym groups (any site, not just courses). Multi-word entries are matched as phrases.
SYNONYM_GROUPS = [
    ["lecture", "lec", "slides", "notes"],
    ["hw", "homework", "assignment", "pset"],
    ["proj", "project"],
    ["settings", "preferences", "config", "configuration"],
    ["billing", "invoices", "payments"],
    ["orders", "purchases"],
    ["video", "recording"],
    ["docs", "documentation", "reference"],
    ["pr", "pull request"],
    ["exam", "midterm", "final"],
    ["schedule", "calendar"],
    ["account", "profile"],
    ["repo", "repository"],
    ["issue", "ticket", "bug"],
]
SYNONYMS = {w: g for g in SYNONYM_GROUPS for w in g}


def normalize_text(s: str | None) -> str:
    """Lowercase, split letter/digit runs, strip leading zeros, drop punctuation: 'CSE452 L04!' -> 'cse 452 l 4'."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"[\W_]+", " ", s)
    s = re.sub(r"(?<=[^\W\d])(?=\d)|(?<=\d)(?=[^\W\d])", " ", s)
    return " ".join(w.lstrip("0") or "0" if w.isdigit() else w for w in s.split())


def tokens(q: str) -> list[str]:
    """Normalized query tokens without stopwords (all tokens if every one is a stopword)."""
    toks = normalize_text(q).split()
    return [t for t in toks if t not in STOPWORDS] or toks


def query_terms(q: str) -> list[list[str]]:
    """Each query term with its synonyms: '452 rpc lecture' -> [['452'], ['rpc'], ['lecture', 'lec', ...]]."""
    toks, out, i = tokens(q), [], 0
    while i < len(toks):
        pair = " ".join(toks[i:i + 2])
        term = pair if i + 1 < len(toks) and pair in SYNONYMS else toks[i]
        i += len(term.split())
        out.append([term, *(w for w in SYNONYMS.get(term, []) if w != term)])
    return out
