#!/usr/bin/env python3
"""
MOVIE BOX — Stremio addon for netnaija.film + movieboxonline.net
================================================================
Both sites run on the same "oneroom / wefeed" platform (aoneroom.com).
This addon:

  * catalogs  : scrapes the sites' SSR listing pages (movies / tv-series /
                animated-series), resolves titles to IMDb ids
                (IMDb suggestion API, TMDB fallback)
  * streams   : Stremio imdb id -> Cinemeta title/year -> platform
                subject-api search (signed mobile API) -> dub list ->
                play-info/v2 -> CloudFront-signed DASH -> repackaged as
                local HLS master/media playlists whose segment URLs are
                CloudFront *query-signed* (Policy/Signature/Key-Pair-Id as
                query params) pointing straight at the CDN (no proxying,
                CORS *, Range supported).

Env:
  PORT             server port (default 7000)
  MB_PUBLIC_URL    public url; enables anti-sleep keep-alive self-ping
  TMDB_API_KEY     fallback imdb resolver (defaults to shared key)
"""

import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import threading
import time
import uuid
import random
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, urlencode, quote, unquote

import requests

VERSION = "1.1.8"
BRAND = "MOVIE BOX"
PORT = int(os.environ.get("PORT", "7000"))
PUBLIC_URL = os.environ.get("MB_PUBLIC_URL", "").rstrip("/")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "adc48d20c0956934fb224de5c40bb85d")

UA_APP = ("com.community.oneroom/50020042 (Linux; U; Android 13; en_US; Redmi; "
          "Build/TQ2A.230405.003; Cronet/135.0.7012.3)")
SECRET_KEY = "76iRl07s0xSN9jqmEWAt79EBJZulIQIsV64FZr2O"
API_HOSTS = ["https://api6.aoneroom.com", "https://api5.aoneroom.com",
             "https://api4.aoneroom.com", "https://api3.aoneroom.com"]
CINEMETA = "https://v3-cinemeta.strem.io"
IMDB_SUGGEST = "https://v2.sg.media-imdb.com/suggestion"
SITES = {
    "netnaija": "https://netnaija.film",
    "moviebox": "https://movieboxonline.net",
}
# listing paths per site: kind -> (site_path_movie_type, series_type)
LISTING_PATHS = {
    ("netnaija", "movies"):    "/movies",
    ("netnaija", "series"):    "/tv-series",
    ("netnaija", "animated"):  "/animated-series",
    ("moviebox", "movies"):    "/film",
    ("moviebox", "series"):    "/tv-series",
    ("moviebox", "animated"):  "/animated-series",
}
CATALOG_PAGE_SIZE = 36          # subjects per site page
CATALOG_PREFETCH = 3            # pages fetched per catalog refresh
HLS_SESSION_TTL = 6 * 3600      # CloudFront cookies last ~7 days; stay lower
START = time.time()

# --------------------------------------------------------------------------
# tiny per-entry TTL cache with definitive/transient distinction
# --------------------------------------------------------------------------

def _cache_put(store, key, val, ttl):
    store[key] = (val, time.time() + ttl)

def _cache_get(store, key):
    ent = store.get(key)
    if not ent:
        return False, None
    val, exp = ent
    if time.time() > exp:
        store.pop(key, None)
        return False, None
    return True, val

# --------------------------------------------------------------------------
# platform crypto (oneroom request signing)
# --------------------------------------------------------------------------

def _b64d(v):
    """CloudFront policies arrive URL-safe base64 ('_'/'-'); platform secrets
    are standard base64 — urlsafe_b64decode handles both."""
    return base64.urlsafe_b64decode(v + "=" * ((4 - len(v) % 4) % 4))

def _x_client_token(ts):
    return "%d,%s" % (ts, hashlib.md5(str(ts)[::-1].encode()).hexdigest())

def _sorted_query(url):
    p = urlparse(url)
    qs = parse_qs(p.query, keep_blank_values=True)
    if not qs:
        return ""
    return "&".join("%s=%s" % (k, v) for k in sorted(qs) for v in qs[k])

def _x_tr_signature(method, url, body, ts):
    p = urlparse(url)
    q = _sorted_query(url)
    cu = "%s?%s" % (p.path, q) if q else p.path
    bh = hashlib.md5(body.encode()).hexdigest() if body is not None else ""
    bl = str(len(body)) if body is not None else ""
    canon = "\n".join([method.upper(), "application/json", "application/json",
                       bl, str(ts), bh, cu])
    sig = base64.b64encode(
        hmac.new(_b64d(SECRET_KEY), canon.encode(), hashlib.md5).digest()
    ).decode()
    return "%d|2|%s" % (ts, sig)

def _client_info():
    return json.dumps({
        "package_name": "com.community.oneroom",
        "version_name": "3.0.03.0529.03",
        "version_code": 50020042,
        "os": "android", "os_version": "13",
        "install_ch": "ps",
        "device_id": "".join(random.choice("0123456789abcdef") for _ in range(32)),
        "install_store": "ps",
        "gaid": str(uuid.uuid4()),
        "brand": "Redmi", "model": "23078RKD5C",
        "system_language": "en", "net": "NETWORK_WIFI",
        "region": "US", "timezone": "Asia/Kolkata",
        "sp_code": "40401", "X-Play-Mode": "2",
    })

_AUTH_TOKEN = None
_AUTH_LOCK = threading.RLock()

def _absorb_token(resp):
    global _AUTH_TOKEN
    xu = resp.headers.get("x-user", "")
    if not xu:
        return
    try:
        tok = json.loads(xu).get("token")
        if tok:
            with _AUTH_LOCK:
                _AUTH_TOKEN = tok
    except Exception:
        pass

def _bootstrap_token():
    """Anonymous auth token via tab-operating (x-user response header)."""
    try:
        for base in API_HOSTS:
            url = base + "/wefeed-mobile-bff/tab-operating?page=1&tabId=0&version="
            ts = int(time.time() * 1000)
            headers = {
                "User-Agent": UA_APP,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Client-Token": _x_client_token(ts),
                "x-tr-signature": _x_tr_signature("GET", url, None, ts),
                "X-Client-Info": _client_info(),
                "X-Client-Status": "0",
                "X-M-Version": "11.7.0",
            }
            r = requests.get(url, headers=headers, timeout=10)
            _absorb_token(r)
            if r.status_code < 400:
                return
    except Exception:
        pass

def api_call(method, path, body=None, timeout=10):
    """Signed platform call with host rotation + 1 retry. Returns dict|None.
    None => transient failure (never cached by callers)."""
    global _AUTH_TOKEN
    if not _AUTH_TOKEN and not path.startswith("/wefeed-mobile-bff/tab-operating"):
        with _AUTH_LOCK:
            if not _AUTH_TOKEN:
                _bootstrap_token()
    last = None
    for attempt in (1, 2):
        for base in API_HOSTS:
            url = base + path
            ts = int(time.time() * 1000)
            headers = {
                "User-Agent": UA_APP,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Client-Token": _x_client_token(ts),
                "x-tr-signature": _x_tr_signature(method, url, body, ts),
                "X-Client-Info": _client_info(),
                "X-Client-Status": "0",
                "X-M-Version": "11.7.0",
                "X-Forwarded-For": "103.241.224.%d" % random.randint(1, 254),
            }
            if _AUTH_TOKEN:
                headers["Authorization"] = "Bearer " + _AUTH_TOKEN
            try:
                r = requests.request(method, url, headers=headers,
                                     data=body.encode() if body else None,
                                     timeout=timeout)
                _absorb_token(r)
                if r.status_code in (403, 406, 429, 500, 502, 503, 504):
                    last = "http%d" % r.status_code
                    continue
                try:
                    d = r.json()
                except Exception:
                    return None  # transient garbage
                if d.get("code") == 0:
                    return d.get("data") or {}
                # definitive API-level error
                return {"__error__": d.get("message") or d.get("reason") or "api"}
            except requests.RequestException as e:
                last = type(e).__name__
                continue
        if attempt == 1:
            time.sleep(0.4)
    return None

# --------------------------------------------------------------------------
# TEMPORARY: web-play reachability diagnostic (see /debug/webplay route)
# --------------------------------------------------------------------------
def _debug_webplay(sid, se, ep, dpath):
    import urllib.parse as _up
    out = {"version": VERSION}
    try:
        r = requests.get("https://api.ipify.org?format=json", timeout=10)
        out["egress_ip"] = r.json().get("ip")
    except Exception as e:
        out["egress_ip"] = "err:" + str(e)[:40]
    try:
        from curl_cffi import requests as _cr
        S = _cr.Session(impersonate="chrome")
        out["engine"] = "curl_cffi(chrome)"
    except Exception:
        S = requests.Session()
        out["engine"] = "requests"
    H = "https://netnaija.film"
    try:
        r0 = S.get(H + "/", timeout=20)
        out["home"] = r0.status_code
    except Exception as e:
        out["home"] = "err:" + str(e)[:60]
    jwt = None
    try:
        r1 = S.post(H + "/wefeed-h5api-bff/subject/search-suggest",
                    json={"keyword": "our", "perPage": 3},
                    headers={"Accept": "application/json",
                             "X-Client-Info": json.dumps({"timezone": "Asia/Dhaka"}),
                             "X-Request-Lang": "en", "Referer": H + "/"},
                    timeout=20)
        out["suggest"] = r1.status_code
        jwt = json.loads(r1.headers.get("x-user", "{}")).get("token")
    except Exception as e:
        out["suggest"] = "err:" + str(e)[:60]
    out["jwt"] = bool(jwt)
    ck = "netnaija_i18n_lang=%22en%22"
    if jwt:
        ck = "netnaija_token=" + _up.quote(json.dumps(jwt)) + "; " + ck
    try:
        r2 = S.get(H + "/wefeed-h5api-bff/subject/play?subjectId=%s&se=%s&ep=%s&detailPath=%s"
                   % (sid, se, ep, dpath),
                   headers={"Accept": "application/json",
                            "X-Client-Info": json.dumps({"timezone": "Asia/Dhaka"}),
                            "X-Source": "webNetnaijaSite",
                            "Referer": "%s/movieDetail/%s?se=%s&ep=%s&utm_source=webNetnaijaSite"
                                       % (H, dpath, se, ep),
                            "Cookie": ck},
                   timeout=25)
        d = r2.json()
        data = d.get("data") or {}
        out["play_http"] = r2.status_code
        out["play_code"] = d.get("code")
        out["counts"] = {k: len(data.get(k) or []) for k in ("streams", "dash", "hls")}
        out["hasResource"] = data.get("hasResource")
        out["data"] = data
    except Exception as e:
        out["play"] = "err:" + str(e)[:80]
    return out


# --------------------------------------------------------------------------
# platform: search / dubs / play-info
# --------------------------------------------------------------------------

def search_subjects(kw, subject_type):
    """subject_type: 1=movie 2=series. Returns list of subject dicts.
    Filters results to the EXACT requested type (platform search sometimes
    mixes in EPG junk like 'Episode #1.347' of the other type). Fallback
    chain for the platform's flaky multi-word index: v2 -> v1 -> single
    longest word via v1."""
    def _filtered(subs):
        return [s for s in subs if int(s.get("subjectType") or 0) == subject_type]

    def _v2(keyword):
        d = api_call("POST", "/wefeed-mobile-bff/subject-api/search/v2",
                     json.dumps({"keyword": keyword, "page": 1, "perPage": 20,
                                 "subjectType": subject_type, "tabId": "All"}))
        if not (d and "__error__" not in d):
            return []
        res = d.get("results") or []
        return _filtered(res[0].get("subjects", []) if res else [])

    def _v1(keyword):
        d = api_call("POST", "/wefeed-mobile-bff/subject-api/search",
                     json.dumps({"keyword": keyword, "page": 1, "perPage": 20,
                                 "subjectType": subject_type}))
        if not (d and "__error__" not in d):
            return []
        return _filtered(d.get("items") or [])

    subs = _v2(kw)
    if subs:
        return subs
    subs = _v1(kw)
    if subs:
        return subs
    words = [w for w in re.split(r"\W+", kw) if len(w) > 1]
    if len(words) > 1:
        w = max(words, key=len)
        subs = _v1(w) or _v2(w)
        if subs:
            return subs
    return []

def subject_dubs(sid):
    d = api_call("GET", "/wefeed-mobile-bff/subject-api/get?subjectId=%s&update=0&status=0" % sid)
    if d is None or "__error__" in d:
        return []
    return [x for x in (d.get("dubs") or []) if x.get("subjectId")]

def play_info(sid, se=None, ep=None):
    p = "/wefeed-mobile-bff/subject-api/play-info/v2?subjectId=%s&host=%s" % (sid, API_HOSTS[0])
    if se and ep:
        p += "&se=%s&ep=%s" % (se, ep)
    d = api_call("GET", p)
    if d is None or "__error__" in d:
        return None
    return d

# --------------------------------------------------------------------------
# title matching helpers
# --------------------------------------------------------------------------

_TAG = re.compile(r"\[(.*?)\]|\((.*?)\)")

def clean_title(t):
    t = _TAG.sub(" ", t or "")
    t = re.sub(r"\s+[Ss]\d{1,2}\s*-\s*[Ss]?\d{1,2}\s*$", "", t)  # S1-S4 ranges
    t = re.sub(r"\s+[Ss]\d{1,2}\s*$", "", t)                     # single S3
    t = re.sub(r"\s+[Ss]eason\s*\d{1,2}\s*$", "", t, flags=re.I)  # "Season 2"
    return re.sub(r"\s+", " ", t).strip()

def _year_of(s):
    m = re.match(r"(\d{4})", str(s or ""))
    return m.group(1) if m else ""

def _title_tokens(t):
    """Normalized token set for alias matching (×→x, punctuation stripped)."""
    t = clean_title(t or "").lower()
    t = t.replace("×", "x").replace("–", " ").replace("—", " ")
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    return set(w for w in t.split() if len(w) > 1)

def match_subjects(subjects, title, year, subject_type, season=None):
    """Return [(subject, lang_label)] matching cinemeta title/year.
    1) exact cleaned-title match; if none, 2) alias match: every token of the
    platform title (>=3 tokens) is contained in the imdb title — covers
    shortened platform names like "Demon Slayer the Movie: Mugen Train" for
    imdb's "Demon Slayer: Kimetsu no Yaiba - The Movie: Mugen Train".
    Year tolerance: ±1 for movies; if every candidate fails the year check
    but exactly ONE candidate exists, trust it (platform dates are often
    wrong)."""
    want = clean_title(title).lower()
    want_se = "%s s%d" % (want, season) if season else None
    want_stripped = re.sub(r"\s*part\s*\d+$", "", want)
    exact, fuzzy = [], []
    want_toks = _title_tokens(title)
    for s in subjects:
        if int(s.get("subjectType") or 0) != subject_type:
            continue
        st = clean_title(s.get("title") or "").lower()
        if st == want or (want_se and st == want_se) or st == want_stripped:
            exact.append(s)
        elif exact == [] and len(want_toks) >= 4:
            ctoks = _title_tokens(s.get("title"))
            if len(ctoks) >= 3 and ctoks <= want_toks:
                fuzzy.append((len(ctoks), ctoks, s))
    pool = exact or [s for _, _, s in
                     sorted(fuzzy, key=lambda x: -x[0])]
    if not pool:
        return []
    if subject_type == 1 and year:
        in_year = []
        for s in pool:
            sy = _year_of(s.get("releaseDate"))
            if not sy or abs(int(sy) - int(year)) <= 1:
                in_year.append(s)
        if in_year:
            pool = in_year
        elif len(pool) == 1:
            pass  # single exact-title match with odd upload year: trust it
        else:
            return []
    out = []
    for s in pool[:4]:
        label = (s.get("corner") or "").strip() or "Original"
        out.append((s, label))
    return out

# --------------------------------------------------------------------------
# Cinemeta + imdb resolution
# --------------------------------------------------------------------------

def cinemeta(ctype, imdb):
    hit, val = _cache_get(_CINEMETA_CACHE, (ctype, imdb))
    if hit:
        return val
    try:
        r = requests.get("%s/meta/%s/%s.json" % (CINEMETA, ctype, imdb), timeout=10)
        if r.status_code == 200:
            m = (r.json().get("meta") or {})
            name = m.get("name")
            if name:
                year = ""
                ri = str(m.get("releaseInfo") or "")
                mm = re.match(r"^(\d{4})", ri)
                if mm:
                    year = mm.group(1)
                val = {"name": name, "year": year}
                _cache_put(_CINEMETA_CACHE, (ctype, imdb), val, 12 * 3600)
                return val
        if r.status_code in (400, 404):
            _cache_put(_CINEMETA_CACHE, (ctype, imdb), None, 3600)
            return None
    except requests.RequestException:
        return None
    return None

def _imdb_suggest_id(imdb):
    """Keyless id→{name,year} via the IMDb suggestion API (accepts an id as
    query). Fallback when Cinemeta has no meta for an id."""
    hit, val = _cache_get(_IMDB_CACHE, ("byid", imdb))
    if hit:
        return val
    try:
        u = "%s/%s/%s.json" % (IMDB_SUGGEST, quote(imdb[:1]), quote(imdb))
        r = requests.get(u, timeout=8)
        if r.status_code == 200:
            for e in (r.json().get("d") or []):
                if e.get("id") == imdb and e.get("l"):
                    val = {"name": e.get("l"),
                           "year": str(e.get("y") or "")}
                    break
        _cache_put(_IMDB_CACHE, ("byid", imdb), val, 12 * 3600)
        return val
    except Exception:
        return None

def _imdb_suggest(title, year, ctype):
    try:
        u = "%s/%s/%s.json" % (IMDB_SUGGEST, quote(title.lower()[:1]), quote(title))
        r = requests.get(u, timeout=8)
        if r.status_code != 200:
            return None
        want = clean_title(title).lower()
        for e in r.json().get("d", []):
            if (e.get("l") or "").strip().lower() != want:
                continue
            y = str(e.get("y") or "")
            if year and y and abs(int(y) - int(year)) > 1:
                continue
            qid = e.get("qid") or ""
            if ctype == "movie" and qid not in ("movie", "video", "videoGame", ""):
                if qid in ("tvSeries", "tvMiniSeries"):
                    continue
            if ctype == "series" and qid not in ("tvSeries", "tvMiniSeries"):
                continue
            return e.get("id")
    except Exception:
        return None
    return None

def _tmdb_find(title, year, ctype):
    try:
        t = "movie" if ctype == "movie" else "tv"
        r = requests.get("https://api.themoviedb.org/3/search/%s" % t,
                         params={"api_key": TMDB_API_KEY, "query": title,
                                 "year": year if ctype == "movie" else None,
                                 "first_air_date_year": year if ctype == "series" else None},
                         timeout=10)
        if r.status_code != 200:
            return None
        res = (r.json().get("results") or [])
        if not res:
            return None
        want = clean_title(title).lower()
        for cand in res:
            nm = cand.get("title" if t == "movie" else "name") or ""
            if clean_title(nm).lower() != want:
                continue
            tid = cand.get("id")
            r2 = requests.get("https://api.themoviedb.org/3/%s/%d/external_ids" % (t, tid),
                              params={"api_key": TMDB_API_KEY}, timeout=10)
            if r2.status_code == 200:
                return r2.json().get("imdb_id")
    except Exception:
        return None
    return None

def resolve_imdb(title, year, ctype):
    key = (title.lower(), year, ctype)
    hit, val = _cache_get(_IMDB_CACHE, key)
    if hit:
        return val
    val = _imdb_suggest(title, year, ctype) or _tmdb_find(title, year, ctype)
    if val:
        _cache_put(_IMDB_CACHE, key, val, 24 * 3600)
    else:
        _cache_put(_IMDB_CACHE, key, None, 4 * 3600)
    return val

_CINEMETA_CACHE = {}
_IMDB_CACHE = {}

# --------------------------------------------------------------------------
# catalog scraping (SSR Nuxt payload decode)
# --------------------------------------------------------------------------

_SCRAPED = {}      # (site, kind, page) -> (subjects, expiry)

def _deref_all(payload):
    d = payload

    def deref(v, depth=0):
        if depth > 8:
            return v
        if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < len(d):
            item = d[v]
            if isinstance(item, (str, float)) or (isinstance(item, int) and not isinstance(item, bool)):
                return item          # terminal scalar
            if isinstance(item, (dict, list)):
                return deref(item, depth + 1)   # nested structure reference
            return v
        if isinstance(v, dict):
            return {k: deref(x, depth + 1) for k, x in v.items()}
        if isinstance(v, list):
            return [deref(x, depth + 1) for x in v]
        return v

    found, seen = [], set()

    def walk(o):
        if isinstance(o, dict):
            rr = deref(o)
            if isinstance(rr, dict) and rr.get("subjectId") and rr.get("title"):
                k = str(rr.get("subjectId")) + str(rr.get("title"))
                if k not in seen:
                    seen.add(k)
                    found.append(rr)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(d)
    return found

def scrape_subjects(site, kind, page):
    key = (site, kind, page)
    hit, val = _cache_get(_SCRAPED, key)
    if hit:
        return val
    base = SITES[site]
    path = LISTING_PATHS[(site, kind)]
    url = "%s%s%s" % (base, path, ("?page=%d" % page) if page > 1 else "")
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        if r.status_code != 200:
            return []
        ms = re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
                        r.text, re.S)
        if not ms:
            return []
        d = json.loads(max(ms, key=len))
        subs = [s for s in _deref_all(d) if s.get("subjectType") in (1, 2)]
        _cache_put(_SCRAPED, key, subs, 6 * 3600)
        return subs
    except Exception:
        return []

def catalog_pool(site, kind, want_type):
    """type-filtered subject pool across prefetch pages."""
    out = []
    for pg in range(1, CATALOG_PREFETCH + 1):
        subs = scrape_subjects(site, kind, pg)
        out.extend([s for s in subs if int(s.get("subjectType") or 0) == want_type])
    # dedupe by subjectId+title
    seen, dd = set(), []
    for s in out:
        k = str(s.get("subjectId"))
        if k not in seen:
            seen.add(k)
            dd.append(s)
    return dd

def subject_to_meta(s, ctype):
    sid = str(s.get("subjectId"))
    title = s.get("title") or ""
    year = _year_of(s.get("releaseDate"))
    imdb = resolve_imdb(clean_title(title), year, ctype)
    if not imdb or not imdb.startswith("tt"):
        return None
    cover = s.get("cover") if isinstance(s.get("cover"), dict) else {}
    m = {
        "id": imdb,
        "type": ctype,
        "name": clean_title(title) or title,
        "poster": cover.get("url") or "",
        "releaseInfo": year or None,
    }
    rate = s.get("imdbRatingValue")
    if rate:
        try:
            m["imdbRating"] = "%.1f" % float(rate)
        except Exception:
            pass
    g = s.get("genre")
    if g:
        m["genres"] = [x.strip() for x in str(g).split(",") if x.strip()][:4]
    return m

def get_catalog(ctype, cat_id, skip):
    parts = cat_id.split("-", 1)
    if len(parts) != 2 or parts[0] not in SITES or parts[1] not in ("movies", "series", "animated"):
        return {"metas": []}
    site, kind = parts
    want_type = 1 if ctype == "movie" else 2
    pool = catalog_pool(site, kind, want_type)
    metas, seen = [], set()
    with ThreadPoolExecutor(max_workers=10) as ex:
        for m in ex.map(lambda s: subject_to_meta(s, ctype), pool):
            if m and m["id"] not in seen:   # dub/season variants share imdb ids
                seen.add(m["id"])
                metas.append(m)
    return {"metas": metas[skip:skip + 100]}

def search_catalog(ctype, query):
    st = 1 if ctype == "movie" else 2
    subs = search_subjects(query, st)
    metas, seen = [], set()
    for s in subs[:40]:
        m = subject_to_meta(s, ctype)
        if m and m["id"] not in seen:
            seen.add(m["id"])
            metas.append(m)
    return {"metas": metas}

# --------------------------------------------------------------------------
# DASH -> HLS bridge
# --------------------------------------------------------------------------

_HLS_SESSIONS = {}
_SESS_LOCK = threading.Lock()
_MPD_CACHE = {}     # dash_base -> {reps, audio, dur, seg_dur, exp}

def _cf_parts(sign_cookie):
    try:
        parts = {}
        for p in sign_cookie.rstrip(";").split(";"):
            if "=" in p:
                k, v = p.split("=", 1)
                parts[k.strip()] = v
        if not all(k in parts for k in ("CloudFront-Policy", "CloudFront-Signature",
                                        "CloudFront-Key-Pair-Id")):
            return None
        return parts
    except Exception:
        return None

def _dash_base(policy_value):
    try:
        pol = _b64d(policy_value).decode(errors="replace")
        m = re.search(r'Resource"?\s*:\s*"(https://[^"]+)/\*"', pol)
        return m.group(1) if m else None
    except Exception:
        return None

def _signed(base, fname, cf):
    return "%s/%s?%s" % (base, fname, urlencode(
        {"Policy": cf["CloudFront-Policy"],
         "Signature": cf["CloudFront-Signature"],
         "Key-Pair-Id": cf["CloudFront-Key-Pair-Id"]}))

def _parse_mpd(xml_text):
    """-> {video:[{id,height,bandwidth}], audio:[{id,lang,bandwidth}], dur, seg_dur}"""
    ns = {"m": "urn:mpeg:dash:schema:mpd:2011"}
    root = ET.fromstring(xml_text.encode() if isinstance(xml_text, str) else xml_text)
    def dur_to_s(s):
        if not s:
            return 0.0
        m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?", s)
        if not m:
            return 0.0
        h, mi, se = m.groups()
        return int(h or 0) * 3600 + int(mi or 0) * 60 + float(se or 0)
    dur = dur_to_s(root.get("mediaPresentationDuration"))
    video, audio = [], []
    for aset in root.findall(".//m:AdaptationSet", ns):
        ct = aset.get("contentType") or ""
        for rep in aset.findall("m:Representation", ns):
            rid = rep.get("id")
            bw = int(rep.get("bandwidth") or 0)
            if ct == "video" or rep.get("height"):
                video.append({"id": rid, "height": int(rep.get("height") or 0),
                              "width": int(rep.get("width") or 0), "bw": bw,
                              "codecs": rep.get("codecs") or "hev1"})
            elif ct == "audio" or rep.get("audioSamplingRate"):
                audio.append({"id": rid, "lang": aset.get("lang") or "und", "bw": bw,
                              "codecs": rep.get("codecs") or "mp4a.40.2"})
    tmpl = root.find(".//m:SegmentTemplate", ns)
    seg_dur = 5.0
    if tmpl is not None and tmpl.get("duration"):
        ts = float(tmpl.get("timescale") or 1)
        seg_dur = float(tmpl.get("duration")) / ts if ts else 5.0
    return {"video": video, "audio": audio, "dur": dur, "seg_dur": seg_dur or 5.0}

def get_mpd_info(dash_base, cookie):
    hit, val = _cache_get(_MPD_CACHE, dash_base)
    if hit:
        return val
    try:
        r = requests.get(dash_base + "/index.mpd",
                         headers={"Cookie": cookie, "User-Agent": "ExoPlayerLib/2.18.7"},
                         timeout=15)
        if r.status_code == 200 and b"<MPD" in r.content[:600]:
            info = _parse_mpd(r.text)
            if info["video"]:
                _cache_put(_MPD_CACHE, dash_base, info, 30 * 60)
                return info
        if r.status_code in (400, 403, 404):
            _cache_put(_MPD_CACHE, dash_base, None, 15 * 60)
            return None
    except requests.RequestException:
        return None
    return None

def new_hls_session(dash_base, cf, mpd):
    tok = secrets.token_urlsafe(12)
    with _SESS_LOCK:
        now = time.time()
        for k in [k for k, v in _HLS_SESSIONS.items() if v["exp"] < now]:
            _HLS_SESSIONS.pop(k, None)
        _HLS_SESSIONS[tok] = {"dash": dash_base, "cf": cf, "mpd": mpd,
                              "exp": now + HLS_SESSION_TTL}
    return tok

def hls_master(sess):
    mpd = sess["mpd"]
    lines = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-INDEPENDENT-SEGMENTS"]
    auds = mpd["audio"]
    if auds:
        for i, a in enumerate(auds):
            lines.append("#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID=\"aud\",NAME=\"%s\","
                         "DEFAULT=%s,AUTOSELECT=YES,LANGUAGE=\"%s\",URI=\"a%d.m3u8\""
                         % (a["lang"].upper(), "YES" if i == 0 else "NO", a["lang"], i))
    for v in mpd["video"]:
        codecs = v["codecs"] + ("," + ",".join(a["codecs"] for a in auds) if auds else "")
        res = "%dx%d" % (v["width"], v["height"]) if v.get("width") else str(v["height"])
        lines.append("#EXT-X-STREAM-INF:BANDWIDTH=%d,RESOLUTION=%s,CODECS=\"%s\"%s"
                     % (v["bw"], res, codecs, ",AUDIO=\"aud\"" if auds else ""))
        lines.append("v%s.m3u8" % v["id"])
    return "\n".join(lines) + "\n"

_TAIL_CACHE = {}   # (dash, rep) -> last segment index that exists on the CDN

def _seg_exists(dash, rep, i, cf):
    try:
        r = requests.get(_signed(dash, "chunk-stream%s-%05d.m4s" % (rep, i), cf),
                         headers={"Range": "bytes=0-1"}, timeout=8)
        return r.status_code in (200, 206)
    except Exception:
        return False

def _last_good_seg(dash, rep, n, cf):
    """Some platform uploads have an MPD duration inflated ~1.2x — the
    playlist lists segments that don't exist on the CDN, so players die
    ~83% into the episode with a load error. Probe the tail once (cheap:
    heuristic point first, then binary search) and trim the playlist to
    what actually exists, ending it cleanly with ENDLIST."""
    key = (dash, rep)
    hit, val = _cache_get(_TAIL_CACHE, key)
    if hit:
        return val
    last = n
    if n > 1 and not _seg_exists(dash, rep, n, cf):
        # common pattern: exactly ~83.25% of the listed count exists
        k = max(1, int(n * 0.8325))
        if _seg_exists(dash, rep, k, cf) and not _seg_exists(dash, rep, k + 1, cf):
            last = k
        else:
            lo, hi = 1, n - 1
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if _seg_exists(dash, rep, mid, cf):
                    lo = mid
                else:
                    hi = mid - 1
            last = lo
        if last < 2:      # probe looked broken — serve the full list
            last = n
    _cache_put(_TAIL_CACHE, key, last, 1800)
    return last

def hls_media(sess, rep_id, kind):
    mpd = sess["mpd"]
    seg = mpd["seg_dur"] or 5.0
    n = max(1, int(math.ceil((mpd["dur"] or 0) / seg)))
    n = _last_good_seg(sess["dash"], rep_id, n, sess["cf"])
    lines = ["#EXTM3U", "#EXT-X-VERSION:7",
             "#EXT-X-TARGETDURATION:%d" % int(math.ceil(seg)),
             "#EXT-X-PLAYLIST-TYPE:VOD",
             "#EXT-X-MAP:URI=\"%s\"" % _signed(sess["dash"], "init-stream%s.m4s" % rep_id, sess["cf"])]
    for i in range(1, n + 1):
        lines.append("#EXTINF:%.3f," % seg)
        lines.append(_signed(sess["dash"], "chunk-stream%s-%05d.m4s" % (rep_id, i), sess["cf"]))
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"

# --------------------------------------------------------------------------
# stream handler
# --------------------------------------------------------------------------

def _res_label(heights):
    hs = sorted(set(heights), reverse=True)
    if not hs:
        return "HD"
    return "MULTI" if len(hs) > 1 else str(hs[0])

_STREAM_CACHE = {}       # (ctype, imdb, se, ep) -> (card list, expiry)
_STREAM_CACHE_TTL = 600  # results cached 10 min; replays are instant
_PLAY_CACHE = {}         # (sid, se, ep) -> play-info payload (10 min)
_DUB_CACHE = {}          # sid -> dub list (30 min)
_SEARCH_CACHE = {}       # (kw, subject_type) -> subjects (10 min)

def _cached_search(kw, subject_type):
    key = (kw, subject_type)
    hit, val = _cache_get(_SEARCH_CACHE, key)
    if hit:
        return val
    val = search_subjects(kw, subject_type)
    _cache_put(_SEARCH_CACHE, key, val, 600)
    return val

def _cached_dubs(sid):
    hit, val = _cache_get(_DUB_CACHE, sid)
    if hit:
        return val
    val = subject_dubs(sid)
    _cache_put(_DUB_CACHE, sid, val, 1800)
    return val

def _cached_play(sid, se, ep):
    key = (str(sid), se, ep)
    hit, val = _cache_get(_PLAY_CACHE, key)
    if hit:
        return val
    val = play_info(sid, se, ep)
    _cache_put(_PLAY_CACHE, key, val, 600)
    return val

_LABEL_PRETTY = {"esla": "Spanish", "ptbr": "Portuguese (BR)", "pt": "Portuguese",
                 "es": "Spanish", "id": "Indonesian"}

def _pretty_label(nm):
    nm = (nm or "").strip()
    return _LABEL_PRETTY.get(nm.lower(), nm) or "Dub"

def _res_from_pi(pi, pl):
    """Resolution label from play-info (no MPD fetch needed at card time)."""
    raw = pi.get("displayResolutions") or (pl or {}).get("resolutions") or ""
    try:
        heights = [int(x) for x in re.findall(r"\d{3,4}", str(raw))]
    except Exception:
        heights = []
    return _res_label(heights) if heights else "MULTI"

def _resolve_entry(pair, se, ep, ctype, title, year):
    """Stream card for one dub entry. Only play-info is fetched here (cached);
    the MPD + HLS playlists are resolved lazily on first /hls request — this
    keeps card building fast enough for Stremio's ~20s timeout."""
    sid, label = pair
    pi = _cached_play(sid, se if ctype == "series" else None,
                      ep if ctype == "series" else None)
    if not pi:
        return None
    pl = (pi.get("streams") or [None])[0]
    if not pl or not pl.get("signCookie"):
        return None
    cf = _cf_parts(pl["signCookie"])
    if not cf or "CloudFront-Policy" not in cf:
        return None
    if not _dash_base(cf["CloudFront-Policy"]):
        return None
    res = _res_from_pi(pi, pl)
    desc = "%s (%s) (%s) ▣ %s" % (title, year or "----", label, BRAND)
    if ctype == "series":
        desc += " ▣ S%02dE%02d" % (se, ep)
    desc += " ◀ MBCLOUD"
    use_se, use_ep = (se, ep) if ctype == "series" else (0, 0)
    return {
        "name": "𖤍 %s 𖤍" % res,
        "description": desc,
        "url": "/hls/%s/%d/%d/master.m3u8" % (sid, use_se, use_ep),
        "behaviorHints": {"notWebReady": False, "isBingeable": True},
        "bingeGroup": "mbx|%s:%s:%s|%s|%s" % (title, se if ctype == "series" else "",
                                              ep if ctype == "series" else "", label, res),
    }

def _lazy_hls(sid, se, ep, file):
    """Stateless HLS: derive playlists from (sid, se, ep) via cached
    play-info + cached MPD. No session store — survives restarts and keeps
    signing cookies fresh for long playback sessions."""
    pi = _cached_play(sid, se or None, ep or None)
    pl = (pi.get("streams") or [None])[0] if pi else None
    ck = (pl or {}).get("signCookie") or ""
    if not ck:
        return None
    cf = _cf_parts(ck)
    pol = (cf or {}).get("CloudFront-Policy")
    dash = _dash_base(pol) if pol else None
    if not dash:
        return None
    mpd = get_mpd_info(dash, ck)
    if not mpd:
        return None
    sess = {"dash": dash, "cf": cf, "mpd": mpd}
    if file == "master":
        return hls_master(sess)
    kind, idx = file[0], int(file[1:])
    reps = mpd["audio"] if kind == "a" else mpd["video"]
    if idx >= len(reps):
        return None
    return hls_media(sess, reps[idx]["id"], kind)

def build_streams(ctype, imdb, se, ep, _prewarm_next=True):
    key = (ctype, imdb, se, ep)
    hit, val = _cache_get(_STREAM_CACHE, key)
    if hit:
        return {"streams": val}
    meta = cinemeta(ctype, imdb) or _imdb_suggest_id(imdb)
    if not meta:
        return {"streams": [], "message": "no metadata"}
    title, year = meta["name"], meta["year"]
    stype = 1 if ctype == "movie" else 2
    subs = _cached_search(title, stype)
    if not subs:
        return {"streams": []}
    matched = match_subjects(subs, title, year, stype, season=se)
    if not matched:
        return {"streams": []}
    # dub lists for the top matches, fetched in parallel
    with ThreadPoolExecutor(max_workers=2) as ex:
        dub_lists = list(ex.map(lambda m: _cached_dubs(str(m[0].get("subjectId"))),
                                matched[:2]))
    # dedupe by subjectId AND label (clean card list)
    entries, seen, seen_labels = [], set(), set()
    for s, label in matched:
        sid = str(s.get("subjectId"))
        if sid not in seen and label not in seen_labels:
            seen.add(sid)
            seen_labels.add(label)
            entries.append((sid, label))
    for dubs in dub_lists:
        for d in dubs[:6]:
            dsid = str(d.get("subjectId"))
            nm = (d.get("lanName") or "").replace(" dub", "").replace(" Audio", "").strip()
            nm = _pretty_label(nm)
            if dsid not in seen and nm not in seen_labels:
                seen.add(dsid)
                seen_labels.add(nm)
                entries.append((dsid, nm))
    entries = entries[:8]
    # resolve every dub in parallel (play-info + MPD each)
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(lambda p: _resolve_entry(p, se, ep, ctype, title, year),
                              entries))
    streams = [r for r in results if r]
    if streams:
        _cache_put(_STREAM_CACHE, key, streams, _STREAM_CACHE_TTL)
        if _prewarm_next and ctype == "series":
            # background-warm the next episode so binge navigation is instant
            threading.Thread(target=_safe_build, daemon=True,
                             args=(ctype, imdb, se, ep + 1)).start()
        if _prewarm_next:
            # background-warm play-info + MPD so the first PLAY click is instant
            _spawn_warm(entries, se, ep, ctype)
    return {"streams": streams}

def _spawn_warm(entries, se, ep, ctype):
    def _w():
        for sid, _ in entries[:8]:
            try:
                _warm_one(sid, se, ep, ctype)
            except Exception:
                pass
    threading.Thread(target=_w, daemon=True).start()

def _warm_one(sid, se, ep, ctype):
    """Prefetch play-info + MPD for one card (no playlists built)."""
    pi = _cached_play(sid, se if ctype == "series" else None,
                      ep if ctype == "series" else None)
    pl = (pi.get("streams") or [None])[0] if pi else None
    ck = (pl or {}).get("signCookie") or ""
    if not ck:
        return
    cf = _cf_parts(ck)
    pol = (cf or {}).get("CloudFront-Policy")
    dash = _dash_base(pol) if pol else None
    if dash:
        get_mpd_info(dash, ck)

def _safe_build(ctype, imdb, se, ep):
    try:
        build_streams(ctype, imdb, se, ep, _prewarm_next=False)
    except Exception:
        pass

# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------

MANIFEST = {
    "id": "com.movbox.stremio",
    "version": VERSION,
    "name": "MOVIE BOX",
    "description": ("Netnaija.film + MovieBoxOnline.net in Stremio — movies & series "
                    "in up to 1080p, multi-language dubs. HEVC/H.265 streams "
                    "(best on Stremio desktop / Android TV)."),
    "types": ["movie", "series"],
    "idPrefixes": ["tt"],
    "logo": "/logo.png",
    "behaviorHints": {"configurable": False},
    "catalogs": [
        {"type": "movie", "id": "netnaija-movies", "name": "Netnaija • Movies",
         "extra": [{"name": "search", "isRequired": False},
                            {"name": "skip", "isRequired": False}]},
        {"type": "movie", "id": "moviebox-movies", "name": "MovieBox • Movies",
         "extra": [{"name": "search", "isRequired": False},
                            {"name": "skip", "isRequired": False}]},
        {"type": "movie", "id": "netnaija-animated", "name": "Netnaija • Animation",
         "extra": [{"name": "search", "isRequired": False},
                            {"name": "skip", "isRequired": False}]},
        {"type": "movie", "id": "moviebox-animated", "name": "MovieBox • Animation",
         "extra": [{"name": "search", "isRequired": False},
                            {"name": "skip", "isRequired": False}]},
        {"type": "series", "id": "netnaija-series", "name": "Netnaija • Series",
         "extra": [{"name": "search", "isRequired": False},
                            {"name": "skip", "isRequired": False}]},
        {"type": "series", "id": "moviebox-series", "name": "MovieBox • Series",
         "extra": [{"name": "search", "isRequired": False},
                            {"name": "skip", "isRequired": False}]},
        {"type": "series", "id": "netnaija-animated", "name": "Netnaija • Animation",
         "extra": [{"name": "search", "isRequired": False},
                            {"name": "skip", "isRequired": False}]},
        {"type": "series", "id": "moviebox-animated", "name": "MovieBox • Animation",
         "extra": [{"name": "search", "isRequired": False},
                            {"name": "skip", "isRequired": False}]},
    ],
    "resources": ["stream", "catalog"],
}

_LANDING_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MOVIE BOX — Stremio Addon</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:'Segoe UI',system-ui,-apple-system,Roboto,Arial,sans-serif;
       background:#0b0e14;color:#e8eaf0;min-height:100vh}
  .wrap{max-width:880px;margin:0 auto;padding:48px 20px 64px}
  header{text-align:center;margin-bottom:36px}
  .logo{width:96px;height:96px;border-radius:22px;box-shadow:0 8px 32px rgba(255,60,90,.35)}
  h1{font-size:34px;letter-spacing:4px;margin-top:16px;font-weight:800}
  h1 span{background:linear-gradient(90deg,#ff3c5a,#ff9a3c);-webkit-background-clip:text;
          background-clip:text;color:transparent}
  .tag{color:#9aa3b2;margin-top:8px;font-size:15px}
  .install{display:inline-block;margin-top:26px;padding:15px 42px;border-radius:12px;
           background:linear-gradient(90deg,#7b2ff7,#ff3c5a);color:#fff;font-size:18px;
           font-weight:700;text-decoration:none;box-shadow:0 6px 24px rgba(123,47,247,.45);
           transition:transform .15s}
  .install:hover{transform:translateY(-2px)}
  .note{color:#7c8596;font-size:13px;margin-top:12px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px;margin-top:40px}
  .card{background:#141925;border:1px solid #232b3d;border-radius:14px;padding:20px}
  .card h3{font-size:16px;margin-bottom:8px;color:#ffb03c}
  .card p{font-size:14px;color:#aab3c2;line-height:1.55}
  .b{color:#5dd3ff;font-weight:600}
  .steps{margin-top:40px;background:#141925;border:1px solid #232b3d;border-radius:14px;padding:24px}
  .steps h2{font-size:18px;margin-bottom:14px}
  .steps ol{margin-left:20px;color:#aab3c2;font-size:14px;line-height:2}
  .warn{margin-top:18px;padding:12px 16px;border-left:3px solid #ffb03c;background:#1a1f2e;
        border-radius:0 8px 8px 0;font-size:13px;color:#c8b48a}
  footer{margin-top:44px;text-align:center;color:#5c6675;font-size:13px;line-height:2}
  footer a{color:#5dd3ff;text-decoration:none}
</style></head><body><div class="wrap">
<header>
  <img class="logo" src="/logo.png" alt="MOVIE BOX"
       onerror="this.style.display='none'">
  <h1>MOVIE <span>BOX</span></h1>
  <div class="tag">netnaija.film + movieboxonline.net &mdash; movies, series &amp; anime<br>
  in up to <b style="color:#fff">1080p</b> with multi-language dubs (Hindi, English, Tamil, Telugu, Bengali&hellip;)</div>
  <a class="install" id="install" href="#">⬇ Install in Stremio</a>
  <div class="note">works on Stremio desktop, Android, Android TV &amp; Firestick</div>
</header>

<div class="grid">
  <div class="card"><h3>🎞 8 Catalogs</h3>
    <p>Netnaija &amp; MovieBox &mdash; each with <span class="b">Movies</span>,
    <span class="b">Series</span> and <span class="b">Animation</span> shelves,
    searchable straight from Stremio's Discover.</p></div>
  <div class="card"><h3>🗣 Multi-Dub</h3>
    <p>Every title shows one card per language track. Hindi, Original, English,
    Tamil, Telugu, Bengali, Spanish, Portuguese&hellip; whatever the platform hosts.</p></div>
  <div class="card"><h3>⚡ CDN-Direct, Zero Proxy</h3>
    <p>This server only serves tiny text (JSON + m3u8). All video segments stream
    <span class="b">straight from the CDN to your player</span> &mdash; fast and private.</p></div>
  <div class="card"><h3>📅 Always Fresh</h3>
    <p>Catalogs refresh every 6 hours and results are cached 10 minutes,
    so playback starts instantly on repeat.</p></div>
</div>

<div class="steps">
  <h2>How to install</h2>
  <ol>
    <li>Click the <b style="color:#fff">Install in Stremio</b> button above.</li>
    <li>Stremio opens &rarr; press <b style="color:#fff">Install</b>.</li>
    <li>Find any movie/series &mdash; MOVIE BOX streams appear with a
        <b style="color:#fff">▣ MOVIE BOX</b> tag and language name.</li>
  </ol>
  <div class="warn">⚠ Streams are <b>HEVC / H.265</b>. Plays perfectly on Stremio
  desktop, Android &amp; Android TV — but some browsers (e.g. Firefox) can't decode HEVC.</div>
</div>

<footer>
  v__VERSION__ &middot; <a href="/manifest.json">manifest.json</a> &middot;
  <a href="/health">health</a> &middot; video never proxies through this server
  <br>Made for personal use. All content belongs to the original platform.
</footer>
</div>
<script>
  (function(){
    var h = location.host;
    var a = document.getElementById('install');
    a.href = 'stremio://' + (h || 'moviebox-f3hf.onrender.com') + '/manifest.json';
  })();
</script>
</body></html>"""

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MovieBox/" + VERSION

    def log_message(self, fmt, *args):
        print("[%s] %s" % (time.strftime("%H:%M:%S"), fmt % args), flush=True)

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store" if ctype == "application/json" else "public, max-age=300")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _host_base(self):
        host = self.headers.get("Host") or ("127.0.0.1:%d" % PORT)
        scheme = "https" if any(x in host for x in ("onrender.com", ".com", ".app", ".dev", ".io")) else "http"
        base = "%s://%s" % (scheme, host)
        try:
            _note_public_base(base)
        except Exception:
            pass
        return base

    def do_GET(self):
        try:
            self._route()
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self._send(500, json.dumps({"error": str(e)}))
            except Exception:
                pass

    def _route(self):
        u = urlparse(self.path)
        # Some Stremio clients percent-encode the ':' in series ids
        # (tt123:1:1 -> tt123%3A1%3A1) — decode before routing, otherwise
        # EVERY series stream request 404s while movies work fine.
        path, q = unquote(u.path), parse_qs(u.query)

        if path == "/health":
            return self._send(200, json.dumps({
                "ok": True, "version": VERSION, "brand": BRAND,
                "uptime_s": int(time.time() - START),
                "keepalive": bool(PUBLIC_URL or _KEEPALIVE_URL),
                "keepalive_url": PUBLIC_URL or _KEEPALIVE_URL,
                "auth_token": bool(_AUTH_TOKEN),
                "video_proxy": False,
                "segment_routing": "cdn-direct (sacdn CloudFront, query-signed)",
            }))

        if path == "/":
            html = _LANDING_HTML.replace("__VERSION__", VERSION)
            return self._send(200, html, "text/html; charset=utf-8")

        if path == "/manifest.json":
            m = dict(MANIFEST)
            m["logo"] = self._host_base() + "/logo.png"
            return self._send(200, json.dumps(m))

        if path == "/logo.png":
            try:
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png"), "rb") as f:
                    return self._send(200, f.read(), "image/png")
            except Exception:
                return self._send(404, "no logo", "text/plain")

        if path == "/debug/webplay":
            # TEMPORARY diagnostic: replicate the netnaija.film browser play
            # flow from THIS server's IP. Answers whether the web play API is
            # usable from a datacenter (Render) egress at all.
            sid = (q.get("sid") or [""])[0]
            se = (q.get("se") or ["1"])[0]
            ep = (q.get("ep") or ["1"])[0]
            dpath = (q.get("path") or [""])[0]
            if not sid or not dpath:
                return self._send(400, json.dumps({"error": "need sid, se, ep, path"}))
            return self._send(200, json.dumps(_debug_webplay(sid, se, ep, dpath)))

        m = re.match(r"^/catalog/([a-z]+)/([a-z0-9-]+)\.json$", path)
        if m:
            ctype, cid = m.group(1), m.group(2)
            search = (q.get("search") or [""])[0].strip()
            skip = int((q.get("skip") or ["0"])[0] or 0)
            if search:
                return self._send(200, json.dumps(search_catalog(ctype, search)))
            return self._send(200, json.dumps(get_catalog(ctype, cid, skip)))

        m = re.match(r"^/stream/([a-z]+)/(tt\d+|[a-z0-9]+)(?::(\d+):(\d+))?\.json$", path)
        if m:
            ctype, oid = m.group(1), m.group(2)
            if ctype not in ("movie", "series"):
                return self._send(400, json.dumps({"error": "bad type"}))
            se = int(m.group(3) or 1)
            ep = int(m.group(4) or 1)
            if not oid.startswith("tt"):
                return self._send(200, json.dumps({"streams": []}))
            res = build_streams(ctype, oid, se, ep)
            for s in res.get("streams", []):
                if s.get("url", "").startswith("/hls/"):
                    s["url"] = self._host_base() + s["url"]
            return self._send(200, json.dumps(res))

        m = re.match(r"^/hls/(\d{5,25})/(\d{1,3})/(\d{1,5})/(master|v\d+|a\d+)\.m3u8$", path)
        if m:
            sid, use_se, use_ep, file = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
            body = _lazy_hls(sid, use_se, use_ep, file)
            if body is None:
                return self._send(404, "#EXTM3U\n#error no stream for this entry\n",
                                  "application/vnd.apple.mpegurl")
            return self._send(200, body, "application/vnd.apple.mpegurl")

        m = re.match(r"^/hls/([A-Za-z0-9_-]{6,64})/(master|v\d+|a\d+)\.m3u8$", path)
        if m:
            with _SESS_LOCK:
                sess = _HLS_SESSIONS.get(m.group(1))
            if not sess or sess["exp"] < time.time():
                return self._send(404, "#EXTM3U\n#error session expired\n",
                                  "application/vnd.apple.mpegurl")
            if m.group(2) == "master":
                body = hls_master(sess)
            else:
                kind, idx = m.group(2)[0], int(m.group(2)[1:])
                reps = sess["mpd"]["audio"] if kind == "a" else sess["mpd"]["video"]
                if idx >= len(reps):
                    return self._send(404, "bad rep", "text/plain")
                body = hls_media(sess, reps[idx]["id"], kind)
            return self._send(200, body, "application/vnd.apple.mpegurl")

        return self._send(404, json.dumps({"error": "not found"}))

# --------------------------------------------------------------------------
# keep-alive (anti-sleep) — auto-detected from Host header if env not set
# --------------------------------------------------------------------------

_KEEPALIVE_URL = None
_KEEPALIVE_LOCK = threading.Lock()

def _note_public_base(base):
    """Remember the first public-looking Host so we can self-ping."""
    global _KEEPALIVE_URL
    if PUBLIC_URL or not base:
        return
    host = base.split("//", 1)[-1].split(":")[0].lower()
    if (not host or host in ("localhost", "0.0.0.0") or host.startswith("127.")
            or host.startswith("10.") or host.startswith("192.168.")
            or re.match(r"^172\.(1[6-9]|2\d|3[01])\.", host)):
        return
    with _KEEPALIVE_LOCK:
        if _KEEPALIVE_URL:
            return
        _KEEPALIVE_URL = base
        threading.Thread(target=_keepalive_loop, daemon=True).start()
        print("keepalive auto-armed: %s" % base, flush=True)

def _keepalive_loop():
    while True:
        url = PUBLIC_URL or _KEEPALIVE_URL
        if not url:
            return
        try:
            requests.get(url + "/health", timeout=20)
        except Exception:
            pass
        time.sleep(240)

def main():
    if PUBLIC_URL:
        threading.Thread(target=_keepalive_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("MOVIE BOX %s listening on :%d (keepalive=%s)" % (VERSION, PORT, bool(PUBLIC_URL)), flush=True)
    srv.serve_forever()

if __name__ == "__main__":
    main()
