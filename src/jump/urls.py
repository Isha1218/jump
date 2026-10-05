"""URL helpers shared by every agent."""
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

# Standard analytics params, the same on every site. A URL reached from an ad (has an ad-click id) keeps
# no query at all: ad landings pile on made-up campaign params.
TRACKING = re.compile(r"^(utm_.*|_ga|_gl|fbclid|igshid|mc_cid|mc_eid|ref_src)$", re.I)
AD_CLICK = re.compile(r"^(gclid|gclsrc|dclid|gad_source|gad_campaignid|gbraid|wbraid|msclkid|twclid|yclid|li_fat_id)$", re.I)
ACTION = re.compile(r"logout|log_out|signout|sign_out|delete|remove|unsubscribe|/edit\b", re.I)


def normalize(url: str, base: str | None = None) -> str | None:
    """Absolute http(s) URL without fragment, default port or tracking params (any query, if from an ad); None if not web."""
    try:
        raw = urljoin(base, url) if base else url
        p = urlsplit(raw.strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    host = p.hostname.lower()
    if p.port and not ((p.scheme == "http" and p.port == 80) or (p.scheme == "https" and p.port == 443)):
        host = f"{host}:{p.port}"
    params = parse_qsl(p.query, keep_blank_values=True)
    if any(AD_CLICK.match(k) for k, _ in params):
        params = []
    query = urlencode([(k, v) for k, v in params if not TRACKING.match(k)])
    return urlunsplit((p.scheme, host, p.path or "/", query, ""))


def origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def path_segments(url: str) -> list[str]:
    return [s for s in urlsplit(url).path.split("/") if s]


def kind_from_url(url: str) -> str:
    p = urlsplit(url)
    path, host = p.path.lower(), (p.hostname or "")
    if path.endswith(".pdf"):
        return "pdf"
    if re.search(r"\.(pptx?|key)$", path) or (host == "docs.google.com" and path.startswith("/presentation")):
        return "slides"
    if re.search(r"\.(mp4|mov|webm|m4v)$", path) or re.search(r"youtube\.com|youtu\.be|panopto|vimeo", host):
        return "video"
    if re.search(r"\.(docx?|xlsx?|zip|tar|gz|txt|md|ipynb|py|java|go|c|h|cpp|rs|png|jpe?g|gif|svg)$", path):
        return "file"
    return "html"


def url_template(url: str) -> str:
    """Shape of a URL with variable parts blanked: /lectures/l04.html -> host/lectures/l{n}.html"""
    p = urlsplit(url)
    segs = []
    for s in path_segments(url):
        if re.fullmatch(r"[0-9a-f]{12,}|[0-9a-f-]{32,36}", s, re.I):
            segs.append("{id}")
        else:
            segs.append(re.sub(r"\d+", "{n}", s))
    return (p.hostname or "") + "/" + "/".join(segs)


def in_scope(url: str, origin_: str, path_prefix: str) -> bool:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}" == origin_ and (p.path.rstrip("/") + "/").startswith(path_prefix)


def relation(from_url: str, to_url: str, origin_: str, path_prefix: str) -> str:
    """'deeper' (below the linking page's directory), 'sideways' (elsewhere in scope) or 'outside'."""
    if not in_scope(to_url, origin_, path_prefix):
        return "outside"
    from_dir = urlsplit(from_url).path
    from_dir = from_dir if from_dir.endswith("/") else from_dir.rsplit("/", 1)[0] + "/"
    to_path = urlsplit(to_url).path
    return "deeper" if to_path.startswith(from_dir) and to_path != urlsplit(from_url).path else "sideways"


def is_action_url(url: str) -> bool:
    return bool(ACTION.search(url))


def url_words(url: str) -> str:
    """Searchable words from host + path: 'cse452/26au/L04-rpc.pdf' -> 'cse 452 26 au l 04 rpc pdf'."""
    p = urlsplit(url)
    text = f"{p.hostname or ''} {p.path}"
    text = re.sub(r"([a-zA-Z])(\d)", r"\1 \2", text)
    text = re.sub(r"(\d)([a-zA-Z])", r"\1 \2", text)
    return " ".join(w for w in re.split(r"[^a-zA-Z0-9]+", text.lower()) if w)
