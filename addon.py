"""
MovieBox — a Stremio addon for a wefeed-based streaming platform.

STRICT ZERO-BANDWIDTH RULE (v1.9.0, user directive)
    NOTHING but tiny JSON is served from here — no playlists, no
    manifests, no subtitles, no media. Cards point DIRECTLY at the
    platform's CDN: the DASH MPD carries a CloudFront signed COOKIE via
    proxyHeaders (the cookie is not IP-bound — verified cross-IP), and
    subtitles use the caption CDN's open direct URLs. The old
    synthesized /hls, /dash and /sub routes were removed.

SECTIONS
    1. config            constants, branding, hosts
    2. utilities         TTL caches, formatting helpers
    3. platform api      token bootstrap + signed mobile api_call + search
    4. metadata          cinemeta / imdb / tmdb title matching
    5. catalogs          scraped pools -> catalog & search metas
    6. cdn / dash        CloudFront cookie parsing, MPD parsing (quality
                         labels + prewarm verification)
    7. cards             strict-zero direct-CDN stream cards
    8. subtitles         mobile caption endpoint + web fallback (direct URLs)
    9. stream cards      per-dub card building, caching, pre-warming
   10. landing page      install / usage html
   11. http server       routes, gzip, CORS, cache headers
   12. keep-alive        anti-sleep pinger
"""

import base64
import gzip
import hashlib
import io
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
from concurrent.futures import (ThreadPoolExecutor, wait, FIRST_COMPLETED,
                                TimeoutError as FuturesTimeoutError)
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, urlencode, quote, unquote

import requests

# --------------------------------------------------------------------------
# 1. config — branding, hosts, tuning
# --------------------------------------------------------------------------
VERSION   = "1.9.11"
BRAND = "MovieBox"
PORT = int(os.environ.get("PORT", "7000"))
PUBLIC_URL = os.environ.get("MB_PUBLIC_URL", "").rstrip("/")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "adc48d20c0956934fb224de5c40bb85d")
# Optional egress rotation (v1.6.4): set MOVIEBOX_PROXY to a proxy URL —
# typically a *rotating residential gateway* like
# "http://user:pass@gate.provider.tld:7000" — and every platform-API call
# (tab-operating bootstrap / search / play-info / captions) egresses through
# it, so the platform sees the proxy's rotating exit IP instead of Render's
# shared, volume-flagged one. Absent/empty (default) = direct connections,
# behavior identical to v1.6.3. TMDB / Cinemeta / IMDb / media CDN always
# stay direct (they are not flagged), which keeps per-GB proxy cost near
# zero: only small signed JSON + subtitle text ever crosses the proxy
# (media bytes are 302'd straight to the client — zero-media design).
# v1.6.7: MOVIEBOX_PROXY_LIST = comma-separated proxy URLs (e.g. the 10
# Webshare free proxies "http://user:pass@ip:port,..."). Each platform call
# picks a random URL; on failure the retry loop rotates to a fresh exit IP.
# Pool mode overrides MOVIEBOX_PROXY (single) and SCRAPEDO_TOKEN (scrape.do)
# for every non-tab-operating path; tab-operating always stays direct (its
# auth token arrives in a response header proxies must not touch).
_PROXY_RAW_LIST = os.environ.get("MOVIEBOX_PROXY_LIST", "").strip()
_PROXY_URLS = [u.strip() for u in _PROXY_RAW_LIST.split(",") if u.strip()] if _PROXY_RAW_LIST else []
_PROXY_URL = os.environ.get("MOVIEBOX_PROXY", "").strip()
_PLAT_PROXIES = ({"http": _PROXY_URL, "https": _PROXY_URL} if _PROXY_URL else None)
# v1.6.8: auto-refreshed FREE proxy pool — no signup, no key, no bandwidth
# cap. MOVIEBOX_PROXY_SOURCE is a public plain-text list URL (default:
# ProxyScrape v4, ~1800 mixed-protocol proxies the provider refreshes
# continuously). A daemon thread re-fetches it every 10 min, samples the
# http:// entries (socks entries are skipped — no PySocks dependency),
# probes CONNECT-aliveness in parallel and caches up to 12 alive URLs.
# Free proxies are untrusted intermediaries; platform calls are read-only
# catalog fetches and the anonymous token re-bootstraps on failure, which
# bounds the blast radius of a hostile exit.
_FREE_POOL_SRC = os.environ.get(
    "MOVIEBOX_PROXY_SOURCE",
    "https://api.proxyscrape.com/v4/free-proxy-list/get"
    "?request=display_proxies&proxy_format=protocolipport&format=text",
).strip()
_FREE_POOL = [[]]                   # alive free proxies (auto-refreshed)
_FREE_POOL_TS = [0.0]               # last refresh start (throttle: 6 min)
_FREE_POOL_LOCK = threading.Lock()
_FREE_POOL_ON = [False]             # enabled only when run as a server
# v1.6.9 pool learning: dead (connect-failed) exits are benched 10 min,
# platform-blocked (403/406) exits 15 min; a working exit becomes sticky
# for 90s so a stream chain (search -> dubs -> play-info xN) rides ONE
# good exit instead of re-rolling dead dice on every call.
_POOL_BAD = {}                      # url -> benched-until ts
_POOL_STICKY = [None, 0.0]          # last good url, sticky-until ts
_POOL_TLS = threading.local()       # per-thread current pool url + req t0
_POOL_REBUILD_TS = [0.0]            # last dry-pool rebuild spawn
# v1.7.0 trained environment: every exit carries a running record —
# success/fail counts + EWMA latency (ms). Picks prefer reliable-and-fast
# exits; the background trainer keeps records fresh even without traffic.
_POOL_STATS = {}                     # url -> {"ok": n, "fail": n, "lat": ms}
# v1.7.5 per-exit auth: the platform now flags IPs at the AUTH level
# (tab-operating/search answer 401 AUTH_FAIL from a flagged IP even with
# a token bootstrapped elsewhere) — a single global token can no longer
# serve every egress. Each pool exit gets its own token, bootstrapped
# THROUGH that exit, so token and egress IP always match.
_EXIT_TOKENS = {}                    # exit url -> (token, ts)
_EXIT_TOKENS_LOCK = threading.Lock()
_DIRECT_AUTH_FLAG = [0.0]            # direct egress auth-flagged until ts
# v1.7.5 chain budget: one /stream build gets a hard deadline — a slow
# egress must never hold the player hostage (measured: 296s hangs).
_CHAIN_DDL = threading.local()       # per-thread chain deadline (ts)
_EGRESS = threading.local()          # per-thread "this chain rode the pool"
_STREAM_BUDGET = 25.0                # seconds for one /stream build
_STREAM_WALL = 24.0                  # v1.7.8: HARD player-facing wall — the
                                     # budget is thread-local and the fan-out
                                     # waves (alt-search/dubs/play/resolve)
                                     # run on OTHER threads, so a grinding
                                     # egress compounded unbounded off-thread
                                     # waits into 6.5min+ hangs on prod.
                                     # The player always gets an answer now.
_API_CALL_WALL = 14.0                # v1.7.8: per-api_call wall cap — even
                                     # deadline-less threads stop rotating
                                     # hosts after this (2 attempts x 7 hosts
                                     # x (6s req + 6s token bootstrap) used
                                     # to be ~250s per call).
_PLAY_WAIT = 12.0                    # v1.7.8: max wait on a single-flight
                                     # play-info future (was: unbounded)


def _ddl_left():
    """Remaining chain-budget seconds (None = no deadline set)."""
    ddl = getattr(_CHAIN_DDL, "t", None)
    return None if ddl is None else ddl - time.time()


def _direct_auth_ok():
    return time.time() >= _DIRECT_AUTH_FLAG[0]


_EXIT_BOOT_LOCKS = {}
_EXIT_BOOT_LOCKS_GUARD = threading.Lock()


def _exit_token(u, refresh=False):
    """Auth token bound to THIS pool exit. Cached 6h; on demand the token
    is bootstrapped through the exit itself (tab-operating -> x-user).

    v1.8.1: SINGLE-FLIGHT per exit — a parallel wave (search + dubs +
    play-info) that picks the same tokenless exit used to fire one 6s
    bootstrap PER THREAD through the same free proxy, which then choked
    and timed out all of them. Now the first thread bootstraps and the
    rest wait for its result (double-checked under the exit's lock)."""
    if not u:
        return None
    now = time.time()
    got = _EXIT_TOKENS.get(u)
    if got and not refresh and now - got[1] < 6 * 3600:
        return got[0]
    with _EXIT_BOOT_LOCKS_GUARD:
        lk = _EXIT_BOOT_LOCKS.setdefault(u, threading.Lock())
    with lk:
        got = _EXIT_TOKENS.get(u)          # re-check: another thread won
        if got and not refresh and time.time() - got[1] < 6 * 3600:
            return got[0]
        tok = _bootstrap_via_exit(u)
        return tok or (got[0] if got else None)


def _bootstrap_via_exit(u, timeout=6):
    """tab-operating GET through exit u -> its own anonymous token."""
    try:
        url = API_HOSTS[0] + \
            "/wefeed-mobile-bff/tab-operating?page=1&tabId=0&version="
        ts = int(time.time() * 1000)
        r = requests.get(url, timeout=timeout,
                         proxies={"http": u, "https": u},
                         headers={
                             "User-Agent": UA_APP,
                             "Accept": "application/json",
                             "Content-Type": "application/json",
                             "X-Client-Token": _x_client_token(ts),
                             "x-tr-signature": _x_tr_signature("GET", url, None, ts),
                             "X-Client-Info": _client_info(),
                             "X-Client-Status": "0",
                             "X-M-Version": "11.7.0",
                         })
        if r.status_code < 400:
            xu = r.headers.get("x-user", "")
            try:
                tok = json.loads(xu).get("token") if xu else None
            except Exception:
                tok = None
            if tok:
                with _EXIT_TOKENS_LOCK:
                    _EXIT_TOKENS[u] = (tok, time.time())
                return tok
    except Exception:
        pass
    return None

def _pool_all():
    """Fallback pool: the auto-refreshed FREE pool is PRIMARY (user
    preference — no bandwidth caps, self-refreshing); env MOVIEBOX_PROXY_LIST
    URLs are an emergency backup used only while the free pool is empty."""
    free = [u for u in _FREE_POOL[0] if u]
    if free:
        return list(dict.fromkeys(free))
    seen, out = set(), []
    for u in _PROXY_URLS:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out

def _pool_healthy():
    now = time.time()
    return [u for u in _pool_all() if _POOL_BAD.get(u, 0.0) <= now]

def _pool_pick():
    """Pool URL as a requests proxies= dict, TRAINED: prefers the sticky
    good exit, then the best-scored healthy exits (Laplace-smoothed
    success rate blended with EWMA latency), avoids benched URLs, falls
    back to anything when the whole pool is benched."""
    now = time.time()
    with _FREE_POOL_LOCK:
        sticky = _POOL_STICKY[0] if _POOL_STICKY[1] > now else None
        bad = {u for u, t in _POOL_BAD.items() if t > now}
    allp = _pool_all()
    healthy = [u for u in allp if u not in bad]

    def _score(u):
        st = _POOL_STATS.get(u) or {}
        ok, fail = st.get("ok", 0), st.get("fail", 0)
        lat = st.get("lat") or 4000
        quality = (ok + 1.0) / (ok + fail + 2.0)
        s = quality * (4000.0 / max(lat, 250))
        # v1.7.8: prefer exits that already carry a fresh platform token —
        # a tokenless exit pays a ~6s tab-operating bootstrap on its first
        # call, and the pool refreshes every 240s (faster than the 120s
        # stickiness), so cold exits kept re-paying it on prod.
        got = _EXIT_TOKENS.get(u)
        if got and time.time() - got[1] < 6 * 3600:
            s *= 1.5
        return s

    u = None
    # v1.8.1: really ride the FASTEST exit. random.choice(ranked[:3])
    # sent 2/3 of every parallel wave to slower exits and let one free
    # proxy carry the whole wave (it then timed out at 6s). Now: prefer
    # the sticky exit, then the best-scored exit with <2 requests in
    # flight — the wave spreads across exits instead of piling up.
    if sticky and (sticky in healthy or (sticky in allp and not healthy)) \
            and _EXIT_BUSY.get(sticky, 0) < 2:
        u = sticky
    elif healthy:
        ranked = sorted(healthy, key=_score, reverse=True)
        u = next((x for x in ranked if _EXIT_BUSY.get(x, 0) < 2),
                 None) or ranked[0]
    elif allp:
        u = random.choice(allp)     # everything benched: try anyway
    else:
        u = "http://127.0.0.1:9"
    _POOL_TLS.url = u
    _POOL_TLS.t_req = time.time()
    return {"http": u, "https": u}


_EXIT_BUSY = {}                     # v1.8.1: url -> in-flight request count


def _exit_busy_inc(u):
    if u:
        _EXIT_BUSY[u] = _EXIT_BUSY.get(u, 0) + 1


def _exit_busy_dec(u):
    if u:
        n = _EXIT_BUSY.get(u, 0) - 1
        if n > 0:
            _EXIT_BUSY[u] = n
        else:
            _EXIT_BUSY.pop(u, None)

def _pool_note(kind, ms=None):
    """Learn from the outcome of this thread's last pool transport and
    update its training record. kind: "good" | "dead" | "block"."""
    u = getattr(_POOL_TLS, "url", None)
    if not u:
        return
    if ms is None:
        ms = int((time.time() - getattr(_POOL_TLS, "t_req", time.time())) * 1000)
    _POOL_TLS.url = None
    now = time.time()
    st = _POOL_STATS.setdefault(u, {"ok": 0, "fail": 0, "lat": None})
    if kind == "good":
        st["ok"] += 1
        st["lat"] = ms if st["lat"] is None else int(0.6 * st["lat"] + 0.4 * ms)
        with _FREE_POOL_LOCK:
            _POOL_BAD.pop(u, None)
            _POOL_STICKY[0], _POOL_STICKY[1] = u, now + 120
        return
    st["fail"] += 1
    dur = 600 if kind == "dead" else 1800   # blocked exits: 30 min
    with _FREE_POOL_LOCK:
        _POOL_BAD[u] = max(_POOL_BAD.get(u, 0.0), now + dur)
        if _POOL_STICKY[0] == u:
            _POOL_STICKY[0] = None
    # pool running dry -> rebuild it soon in the background (throttled)
    if (_FREE_POOL_ON[0] and len(_pool_healthy()) < 3
            and now - _POOL_REBUILD_TS[0] > 30):
        _POOL_REBUILD_TS[0] = now
        threading.Thread(target=_free_pool_refresh, daemon=True).start()

def _platform_probe(u, timeout=4):
    """Light signed tab-operating GET through candidate exit `u`.
    Returns (kind, latency_ms): ("good", ms) | ("block", None) |
    ("dead", None) | (None, None = answered but unusable status)."""
    t0 = time.time()
    try:
        purl = API_HOSTS[0] + "/wefeed-mobile-bff/tab-operating?page=1&tabId=0&version="
        ts = int(time.time() * 1000)
        rr = requests.get(purl, timeout=timeout, proxies={"http": u, "https": u},
                          headers={
                              "User-Agent": UA_APP,
                              "Accept": "application/json",
                              "Content-Type": "application/json",
                              "X-Client-Token": _x_client_token(ts),
                              "x-tr-signature": _x_tr_signature("GET", purl, None, ts),
                              "X-Client-Info": _client_info(),
                              "X-Client-Status": "0",
                              "X-M-Version": "11.7.0",
                          })
        if rr.status_code == 401:
            return "block", None            # exit IP is auth-flagged too
        if rr.status_code < 400:
            xu = rr.headers.get("x-user", "")
            try:
                tok = json.loads(xu).get("token") if xu else None
            except Exception:
                tok = None
            if not tok:
                return None, None           # header dropped: unusable exit
            with _EXIT_TOKENS_LOCK:
                _EXIT_TOKENS[u] = (tok, time.time())
            return "good", int((time.time() - t0) * 1000)
        if rr.status_code in (403, 406):
            return "block", None
        return None, None
    except Exception:
        return "dead", None

def _free_pool_refresh():
    """Fetch the public list, sample http:// entries, platform-probe them,
    cache the 20 FASTEST platform-good exits and seed their training
    records with the measured probe latency."""
    now = time.time()
    if (not _FREE_POOL_SRC) or (now - _FREE_POOL_TS[0] < 240):
        return
    _FREE_POOL_TS[0] = now
    try:
        r = requests.get(_FREE_POOL_SRC, timeout=20)
        cand = [u.strip() for u in r.text.replace("\r", "").splitlines()
                if u.strip().startswith("http://")]
        cand = random.sample(cand, min(150, len(cand))) if cand else []

        def _probe_batch(batch):
            got = []
            with ThreadPoolExecutor(max_workers=25) as ex:
                for u, res in zip(batch, ex.map(lambda x: _platform_probe(x, 5), batch)):
                    kind, ms = res
                    if kind == "good":
                        got.append((u, ms))
            return got

        # v1.8.1: MERGE, never replace. A refresh whose sample found few
        # good exits used to SHRINK the pool wholesale (prod was seen at
        # 8 members / 4 healthy) and throw away trained exits, their
        # tokens and the sticky pick. Snapshot the known-healthy members
        # FIRST — the wave-1 publish below must not eat them either.
        with _FREE_POOL_LOCK:
            prev = [u for u in _FREE_POOL[0]
                    if _POOL_BAD.get(u, 0.0) <= now]

        def _publish(members):
            with _FREE_POOL_LOCK:
                _FREE_POOL[0] = list(members)[:20]

        # v1.7.1 wave probing: publish the first wave's exits immediately
        # (a usable pool in ~half the time), then refine with wave 2.
        alive = _probe_batch(cand[:60])
        best = sorted(alive, key=lambda x: x[1])[:20]
        _publish(dict.fromkeys(prev + [u for u, _ in best]))
        if len(cand) > 60:
            alive += _probe_batch(cand[60:])
        alive.sort(key=lambda x: x[1])          # fastest first
        probe_lat = {u: ms for u, ms in alive}

        def _lat(u):
            st = _POOL_STATS.get(u) or {}
            return st.get("lat") or probe_lat.get(u, 9999)

        merged = sorted(dict.fromkeys(prev + [u for u, _ in alive]), key=_lat)
        _publish(merged)
        with _FREE_POOL_LOCK:
            for u, ms in alive:
                st = _POOL_STATS.setdefault(u, {"ok": 0, "fail": 0, "lat": None})
                st["ok"] += 1
                st["lat"] = ms if st["lat"] is None else int(0.6 * st["lat"] + 0.4 * ms)
                _POOL_BAD.pop(u, None)
            # v1.8.1: bound the learning dicts — a long-lived instance
            # accumulated 1.5k tokens for exits that left the pool ages
            # ago. Keep only current members + freshly benched ones.
            now2 = time.time()
            keep = set(_FREE_POOL[0]) | {u for u, t in _POOL_BAD.items()
                                         if t > now2}
            for d in (_EXIT_TOKENS, _POOL_STATS):
                for k in [k for k in list(d) if k not in keep]:
                    d.pop(k, None)
    except Exception:
        pass                            # keep the previous pool; retry next cycle

def _pool_train_once():
    """One training pass: platform-probe every current pool member in
    parallel, update stats, bench the dead/blocked. Keeps records fresh
    and the pick order improving even with zero user traffic."""
    members = _pool_all()
    if not members:
        return
    with ThreadPoolExecutor(max_workers=20) as ex:
        for u, res in zip(members, ex.map(_platform_probe, members)):
            kind, ms = res
            if not kind:
                continue
            _POOL_TLS.url = u
            _POOL_TLS.t_req = time.time()
            try:
                _pool_note(kind, ms)
            except Exception:
                pass
            _POOL_TLS.url = None

def _pool_train_loop():
    """Trainer daemon: one pass every 90s."""
    while True:
        try:
            if _FREE_POOL_ON[0] and _FREE_POOL_SRC:
                _pool_train_once()
        except Exception:
            pass
        time.sleep(90)

def _free_pool_loop():
    while True:
        try:
            _free_pool_refresh()
        except Exception:
            pass
        time.sleep(240)
# Scrape.do egress fallback (v1.6.5): platform calls go DIRECT first; on the
# IP-flag signature (HTTP 403/406) the endpoint family is re-routed through
# Scrape.do's rotating residential exits for 30 minutes, after which direct
# is probed again — so we automatically return to free egress once the
# platform's flag on our own IP decays. Scrape.do charges per *successful*
# request (free tier: 1000/month), therefore:
#   - tab-operating (bootstrap) is NEVER routed through it: it works direct
#     and its auth token arrives in a response header scrape.do drops;
#   - prewarm batches are skipped while the search family rides scrape.do.
# Verified: platform search rejects datacenter IPs (406) but accepts
# scrape.do residential exits (code:0), with customHeaders=true forwarding
# every signed header + POST body.
_SCRAPEDO_TOKEN = os.environ.get("SCRAPEDO_TOKEN", "").strip()
_SD_TTL = 1800.0
_SD_FALLBACK = {}                     # endpoint family -> until-ts
_SD_LOCK = threading.Lock()
_SD_CREDITS = [None]                  # last seen "scrape-do-remaining-credits"

def _sd_family(path):
    seg = path.split("?")[0].strip("/").split("/")
    return "/" + "/".join(seg[:3]) if len(seg) >= 3 else "/" + "/".join(seg)

def _sd_forced(path):
    # v1.6.8: the family flag now routes through the proxy pool too (free
    # pool first, scrape.do only if no pool), so it no longer requires
    # SCRAPEDO_TOKEN to be set.
    fam = _sd_family(path)
    with _SD_LOCK:
        until = _SD_FALLBACK.get(fam)
        if not until:
            return False
        if time.time() >= until:
            _SD_FALLBACK.pop(fam, None)
            return False
    return True

def _sd_mark(path):
    with _SD_LOCK:
        _SD_FALLBACK[_sd_family(path)] = time.time() + _SD_TTL

class _SDResp:
    """Shim so Scrape.do answers flow through the existing api_call logic."""
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.headers = {}
        self._payload = payload
    def json(self):
        if self._payload is None:
            raise ValueError("scrape.do: non-json response")
        return self._payload

def _sd_fetch(method, url, headers, body, timeout=45):
    """One platform call via Scrape.do (rotating residential egress)."""
    r = requests.request(method, "https://api.scrape.do/",
                         params={"token": _SCRAPEDO_TOKEN, "url": url,
                                 "customHeaders": "true"},
                         headers=headers,
                         data=body.encode() if body else None,
                         timeout=timeout)
    if r.status_code == 200:
        try:                          # free credit telemetry from response header
            _SD_CREDITS[0] = int(r.headers.get("scrape-do-remaining-credits"))
        except (TypeError, ValueError):
            pass
        try:
            return _SDResp(200, r.json())
        except Exception:
            return _SDResp(502, None)
    return _SDResp(r.status_code, None)

UA_APP = ("com.community.oneroom/50020042 (Linux; U; Android 13; en_US; Redmi; "
          "Build/TQ2A.230405.003; Cronet/135.0.7012.3)")
SECRET_KEY = "76iRl07s0xSN9jqmEWAt79EBJZulIQIsV64FZr2O"
API_HOSTS = ["https://api6.aoneroom.com", "https://api5.aoneroom.com",
             "https://api4.aoneroom.com", "https://api3.aoneroom.com",
             "https://api4sg.aoneroom.com", "https://api6sg.aoneroom.com",
             "https://api.inprovider.com"]

# v1.7.7: direct-host health. api_call used to walk ALL 7 hosts per
# attempt (a sick host = its full timeout, several sick hosts = 10s+ for
# ONE call — measured: 8.8s dubs+play wave for Parasite, 13.8s before the
# timeout clamp). Now: the last host that answered code=0 is tried FIRST
# (sticky), and a host that threw a transport exception is benched 3 min.
_HOST_LOCK = threading.Lock()
_HOST_STICKY = [None]            # last host that answered code=0
_HOST_BAD = {}                   # host -> benched-until timestamp


def _api_hosts():
    """API_HOSTS reordered: benched hosts skipped, sticky host first."""
    now = time.time()
    with _HOST_LOCK:
        bad = {h for h, t in _HOST_BAD.items() if t > now}
        sticky = _HOST_STICKY[0]
    hosts = [h for h in API_HOSTS if h not in bad] or list(API_HOSTS)
    if sticky in hosts:
        hosts.remove(sticky)
        hosts.insert(0, sticky)
    return hosts


def _host_note_ok(base):
    with _HOST_LOCK:
        _HOST_STICKY[0] = base


def _host_note_bad(base):
    with _HOST_LOCK:
        _HOST_BAD[base] = time.time() + 180
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

# --------------------------------------------------------------------------
# 2. utilities — TTL caches
# --------------------------------------------------------------------------
def _cache_put(store, key, val, ttl):
    # v1.9.3: expired entries used to be evicted only when their OWN key
    # was read again — a long-lived instance accumulated every title it
    # ever served (slow memory leak on the 512MB Render dyno). Opportunistic
    # sweep once a store reaches the cap keeps them bounded.
    if len(store) >= _CACHE_SWEEP_AT:
        now = time.time()
        for k in [k for k, ent in store.items() if ent[1] < now]:
            store.pop(k, None)
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

_CACHE_SWEEP_AT = 512      # v1.9.3: prune expired entries when a store grows to this

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

# --------------------------------------------------------------------------
# 3. platform api — token bootstrap, signed api_call, search
# --------------------------------------------------------------------------
_AUTH_TOKEN = None
_AUTH_LOCK = threading.RLock()
_AUTH_REAUTH_TS = 0.0          # last forced re-auth (throttle: 1 per 30s)

# platform circuit breaker: the API aggressively flags IPs by volume
# (403 "Service not available" -> 401 AUTH_FAIL cascades). When calls keep
# failing we go QUIET for 5 minutes so the flag decays; hammering makes
# every endpoint fail harder, including token issuance.
_PLAT_FAILS = 0
_PLAT_CB_UNTIL = 0.0
_PLAT_LOCK = threading.Lock()

def _plat_ok():
    return time.time() >= _PLAT_CB_UNTIL

def _note_plat(ok):
    global _PLAT_FAILS, _PLAT_CB_UNTIL
    with _PLAT_LOCK:
        if ok:
            _PLAT_FAILS = 0
            return
        _PLAT_FAILS += 1
        if _PLAT_FAILS >= 4:
            _PLAT_CB_UNTIL = time.time() + 300   # 5 min of silence
            _PLAT_FAILS = 0
_AUTH_ERR_RE = re.compile(r"token|auth|login|expire|sign", re.I)

def _force_reauth():
    """Drop a server-side-expired token and pull a fresh one (throttled)."""
    global _AUTH_REAUTH_TS, _AUTH_TOKEN
    with _AUTH_LOCK:
        now = time.time()
        if now - _AUTH_REAUTH_TS < 30:
            return False
        _AUTH_REAUTH_TS = now
        _AUTH_TOKEN = None
        _bootstrap_token()
        return bool(_AUTH_TOKEN)

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
    if not _plat_ok():
        return
    if _FREE_POOL_ON[0] and not _pool_all():
        _free_pool_refresh()      # cold start: build the free pool now
    try:
        # direct first; if no direct attempt yields a token, rotate pool
        # exits (up to 4 picks — free proxies are often dead). On an
        # IP-flagged host the pool is what actually yields the token.
        # v1.7.5: while the direct egress is AUTH-flagged (401 on
        # tab-operating), don't waste time on direct attempts at all.
        attempts = ([("direct", b) for b in API_HOSTS[:2]]
                    if _direct_auth_ok() else []) + \
                   ([("pool", None)] * 4 if _pool_all() else [])
        for kind, pbase in attempts:
            url = (pbase or API_HOSTS[0]) + "/wefeed-mobile-bff/tab-operating?page=1&tabId=0&version="
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
            try:
                if kind == "pool":
                    r = requests.get(url, headers=headers, timeout=6,
                                     proxies=_pool_pick())
                else:
                    _POOL_TLS.url = None
                    kw = {"proxies": _PLAT_PROXIES} if _PLAT_PROXIES else {}
                    r = requests.get(url, headers=headers, timeout=10, **kw)
            except requests.RequestException:
                if kind == "pool":
                    _pool_note("dead")
                continue
            _absorb_token(r)
            if r.status_code < 400:
                if kind == "pool":
                    _pool_note("good")
                return
            if kind == "direct" and r.status_code in (401, 403):
                # v1.7.5: the IP flag now shows up as 401 AUTH_FAIL —
                # remember it so direct attempts stop for 10 min.
                _DIRECT_AUTH_FLAG[0] = time.time() + 600
            if kind == "pool" and r.status_code in (401, 403, 406):
                _pool_note("block")
    except Exception:
        pass

def api_call(method, path, body=None, timeout=10):
    """Signed platform call with host rotation + 1 retry. Returns dict|None.
    None => transient failure (never cached by callers)."""
    global _AUTH_TOKEN
    if not _plat_ok():
        return None                    # circuit breaker: stay quiet, let the IP cool
    # v1.7.5 chain budget: once the deadline is spent, stop the whole
    # chain honestly (None = transient) instead of grinding for minutes.
    left = _ddl_left()
    if left is not None and left < 0.5:
        return None
    if not _AUTH_TOKEN and not path.startswith("/wefeed-mobile-bff/tab-operating"):
        with _AUTH_LOCK:
            if not _AUTH_TOKEN:
                _bootstrap_token()
    last = None
    _att_t0 = time.time()        # v1.7.7: a SLOW first attempt means the
    # host is sick — retrying just doubles the stall (measured: one bad
    # api host put a 13.8s dent in the dubs+play wave for Parasite).
    # v1.6.8 fallback-only egress: calls go DIRECT first. On the IP-flag
    # signature (403/406) the endpoint family is re-routed through the
    # rotating proxy pool (env MOVIEBOX_PROXY_LIST + the auto-refreshed
    # free pool) when available, else scrape.do, for 30 minutes — after
    # which direct is probed again. tab-operating always stays direct
    # (its auth token arrives in a response header proxies must not touch).
    fb = None
    if _sd_forced(path):
        fb = "pool" if _pool_all() else ("sd" if _SCRAPEDO_TOKEN else None)
    elif not _direct_auth_ok() and _pool_all():
        fb = "pool"    # v1.7.5: direct egress auth-flagged — ride the pool
    rode_pool = False          # circuit breaker must not trip on proxy fails
    for attempt in (1, 2):
        for base in (API_HOSTS[:1] if fb == "sd" else _api_hosts()):
            # v1.7.8: per-call wall — bound the host-rotation grind even on
            # threads that carry no chain deadline (executor workers).
            if time.time() - _att_t0 > _API_CALL_WALL:
                last = "wall"
                break
            url = base + path
            ts = int(time.time() * 1000)
            _to = timeout          # per-iteration default (sd branch sets none)
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
                if fb == "sd":
                    _POOL_TLS.url = None
                    r = _sd_fetch(method, url, headers, body)
                elif fb == "pool":
                    rode_pool = True
                    _EGRESS.pool = True          # pool answers may be proxy lies
                    px = _pool_pick()
                    _bu = getattr(_POOL_TLS, "url", None)
                    _exit_busy_inc(_bu)          # v1.8.1: spread the wave
                    try:
                        etok = _exit_token(_bu)
                        if etok:
                            # v1.7.5: the token must come from THIS exit's IP
                            headers["Authorization"] = "Bearer " + etok
                        _to = min(timeout, 6)
                        if left is not None:
                            _to = min(_to, max(0.5, left))
                        r = requests.request(method, url, headers=headers,
                                             data=body.encode() if body else None,
                                             timeout=_to, proxies=px)
                    finally:
                        _exit_busy_dec(_bu)
                else:
                    _POOL_TLS.url = None
                    kw = {"proxies": _PLAT_PROXIES} if _PLAT_PROXIES else {}
                    _to = timeout if left is None else min(timeout, max(0.5, left))
                    r = requests.request(method, url, headers=headers,
                                         data=body.encode() if body else None,
                                         timeout=_to, **kw)
                    _absorb_token(r)
                    if (r.status_code in (401, 403, 406) and fb is None
                            and not path.startswith("/wefeed-mobile-bff/tab-operating")):
                        # IP-flag signature on our direct egress: route this
                        # endpoint family through the fallback for 30 min and
                        # retry the call immediately — free pool preferred,
                        # scrape.do when no pool is available.
                        # v1.7.5: the platform switched the flag signature
                        # to 401 AUTH_FAIL — treat it exactly like 403/406
                        # and bench the direct egress for 10 minutes.
                        if r.status_code == 401:
                            _DIRECT_AUTH_FLAG[0] = time.time() + 600
                        if _pool_all():
                            fb = "pool"
                        elif _SCRAPEDO_TOKEN:
                            fb = "sd"
                        if fb:
                            _sd_mark(path)
                            if fb == "pool":
                                rode_pool = True
                                _EGRESS.pool = True
                                px = _pool_pick()
                                _bu = getattr(_POOL_TLS, "url", None)
                                _exit_busy_inc(_bu)
                                try:
                                    etok = _exit_token(_bu)
                                    if etok:
                                        headers["Authorization"] = "Bearer " + etok
                                    _to2 = min(timeout, 6)
                                    if left is not None:
                                        _to2 = min(_to2, max(0.5, left))
                                    r = requests.request(
                                        method, url, headers=headers,
                                        data=body.encode() if body else None,
                                        timeout=_to2, proxies=px)
                                finally:
                                    _exit_busy_dec(_bu)
                            else:
                                r = _sd_fetch(method, url, headers, body)
                if r.status_code == 401 and fb == "pool":
                    # v1.7.5: exit token stale/flagged — refresh it through
                    # the same exit once; if that fails, bench and rotate.
                    last = "http401"
                    exu = getattr(_POOL_TLS, "url", None)
                    if exu:
                        with _EXIT_TOKENS_LOCK:
                            _EXIT_TOKENS.pop(exu, None)
                        if not _bootstrap_via_exit(exu):
                            _pool_note("block")
                    continue
                if r.status_code in (403, 406, 429, 500, 502, 503, 504):
                    last = "http%d" % r.status_code
                    if fb == "pool" and r.status_code in (403, 406):
                        _pool_note("block")   # this exit is platform-blocked
                    if fb == "sd":
                        break   # scrape.do transport trouble: host rotation gains nothing
                    continue    # pool: next iteration picks a fresh exit IP
                try:
                    d = r.json()
                except Exception:
                    _note_plat(False)
                    return None  # transient garbage
                if d.get("code") == 0:
                    _pool_note("good")       # sticky: keep riding this exit
                    if fb != "pool":
                        _host_note_ok(base)  # v1.7.7: sticky healthy host
                    _note_plat(True)
                    return d.get("data") or {}
                msg = str(d.get("message") or d.get("reason") or "api")
                # server-side token expiry ("Token is invalid") self-heals:
                # drop the stale token, bootstrap a fresh one, retry
                if _AUTH_ERR_RE.search(msg) and fb == "pool":
                    # exit-token trouble: drop this exit's token and rotate
                    exu = getattr(_POOL_TLS, "url", None)
                    if exu:
                        with _EXIT_TOKENS_LOCK:
                            _EXIT_TOKENS.pop(exu, None)
                        if not _bootstrap_via_exit(exu):
                            _pool_note("block")
                    continue
                if _AUTH_TOKEN and _AUTH_ERR_RE.search(msg) and _force_reauth():
                    continue          # same call again, now with a fresh token
                # definitive API-level error (bad id, not found, ...) — the
                # service answered, so this is not an IP-health problem
                _pool_note("good")     # transport itself proved healthy
                return {"__error__": msg}
            except requests.RequestException as e:
                last = type(e).__name__
                if fb == "pool":
                    _pool_note("dead")       # bench this exit for 10 min
                elif _to >= 2.0:
                    # v1.7.7: bench sick direct host — but never for a
                    # budget-clamped sub-2s timeout (that's OUR deadline
                    # expiring, not the host being sick)
                    _host_note_bad(base)
                if fb == "sd":
                    break
                continue    # pool mode: next iteration picks a fresh exit IP
        if last == "wall":
            break                    # v1.7.8: budget spent, stop the grind
        if attempt == 1:
            if time.time() - _att_t0 > 3.5 and not rode_pool:
                break             # slow attempt: host sick, retry doubles the stall
            time.sleep(0.4)
    if not rode_pool:
        _note_plat(False)    # circuit breaker: only direct-egress failures count
    return None

# --------------------------------------------------------------------------
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
                                 "subjectType": subject_type, "tabId": "All"}),
                     timeout=4)
        if d is None:
            return None                     # transient (transport) failure
        if "__error__" in d:
            return []                       # platform answered: not found
        res = d.get("results") or []
        return _filtered(res[0].get("subjects", []) if res else [])

    def _v1(keyword):
        d = api_call("POST", "/wefeed-mobile-bff/subject-api/search",
                     json.dumps({"keyword": keyword, "page": 1, "perPage": 20,
                                 "subjectType": subject_type}), timeout=4)
        if d is None:
            return None
        if "__error__" in d:
            return []
        return _filtered(d.get("items") or [])

    # v1.7.0: returns None when every strategy hit a transient transport
    # failure (callers must NOT cache that), [] only when the platform
    # definitively answered "no results".
    answered = False
    for strat in (lambda: _v2(kw), lambda: _v1(kw)):
        subs = strat()
        if subs is None:
            continue          # flaky exit — next strategy rolls a fresh one
        answered = True
        if subs:
            return subs
    words = [w for w in re.split(r"\W+", kw) if len(w) > 1]
    if len(words) > 1:
        w = max(words, key=len)
        for strat in (lambda: _v1(w), lambda: _v2(w)):
            subs = strat()
            if subs is None:
                continue
            answered = True
            if subs:
                return subs
    return [] if answered else None

def subject_dubs(sid):
    d = api_call("GET", "/wefeed-mobile-bff/subject-api/get?subjectId=%s&update=0&status=0" % sid,
                 timeout=6)
    if d is None:
        return None                     # transient — caller must not cache
    if "__error__" in d:
        return []
    return [x for x in (d.get("dubs") or []) if x.get("subjectId")]

def play_info(sid, se=None, ep=None):
    p = "/wefeed-mobile-bff/subject-api/play-info/v2?subjectId=%s&host=%s" % (sid, API_HOSTS[0])
    if se and ep:
        p += "&se=%s&ep=%s" % (se, ep)
    d = api_call("GET", p, timeout=4)
    if d is None or "__error__" in d:
        return None
    return d

# --------------------------------------------------------------------------
# title matching helpers
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# 4. metadata — title matching + imdb/tmdb resolution
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
    # v1.7.4: leading articles ("The/A/An") differ constantly between
    # sources (TMDB: "East Palace" vs the platform's "The East Palace") —
    # compare both raw and article-stripped forms.
    art = re.compile(r"^(the|a|an)\s+")
    want_art = art.sub("", want_stripped)
    exact, fuzzy = [], []
    want_toks = _title_tokens(title)
    want_art_toks = _title_tokens(art.sub("", title or ""))
    for s in subjects:
        if int(s.get("subjectType") or 0) != subject_type:
            continue
        st = clean_title(s.get("title") or "").lower()
        st_art = art.sub("", st)
        if (st == want or (want_se and st == want_se) or st == want_stripped
                or st_art == want_art or st == want_art or st_art == want_stripped):
            exact.append(s)
        elif exact == []:
            ctoks = _title_tokens(s.get("title"))
            ctoks_art = _title_tokens(art.sub("", s.get("title") or ""))
            if (len(want_toks) >= 4 and len(ctoks) >= 3 and ctoks <= want_toks):
                fuzzy.append((len(ctoks), ctoks, s))
            elif (ctoks_art and want_art_toks
                  and (ctoks_art == want_art_toks        # only the article differs
                       or (len(ctoks_art) >= 3 and ctoks_art <= want_art_toks))):
                # "the east palace" ({east,palace}) vs "east palace" ({east,palace})
                fuzzy.append((len(ctoks_art), ctoks_art, s))
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
        r = requests.get("%s/meta/%s/%s.json" % (CINEMETA, ctype, imdb), timeout=4)
        if r.status_code == 200:
            m = (r.json().get("meta") or {})
            name = m.get("name")
            if name:
                year = ""
                ri = str(m.get("releaseInfo") or "")
                mm = re.match(r"^(\d{4})", ri)
                if mm:
                    year = mm.group(1)
                val = {"name": name, "year": year,
                       "tmdb": str(m.get("moviedb_id") or "")}
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
        r = requests.get(u, timeout=4)
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
        r = requests.get(u, timeout=4)
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

def _tmdb_find_id(ctype, imdb):
    """id -> {name, year, tmdb} via TMDB /find (external_source=imdb_id).
    Fastest of the three sources and returns the tmdb id we need for the
    alt-title rescue for free."""
    try:
        r = requests.get("https://api.themoviedb.org/3/find/%s" % imdb,
                         params={"api_key": TMDB_API_KEY,
                                 "external_source": "imdb_id"}, timeout=4)
        if r.status_code != 200:
            return None
        d = r.json()
        rec = ((d.get("movie_results") or [None])[0] if ctype == "movie"
               else (d.get("tv_results") or [None])[0])
        if not rec:
            return None
        name = rec.get("title") or rec.get("name")
        if not name:
            return None
        date = rec.get("release_date") or rec.get("first_air_date") or ""
        return {"name": name, "year": date[:4], "tmdb": str(rec.get("id") or "")}
    except Exception:
        return None

_META_EX = ThreadPoolExecutor(max_workers=6)

def _meta_any(ctype, imdb):
    """id -> {name, year, tmdb}: CINEMETA / TMDB / IMDb-suggest race in
    parallel, first valid answer wins (each is ~0.1-0.4s warm, but any of
    them can hit a slow spell — the old serial chain could block a cold
    stream build for 18s). Result cached 12h; a total loss caches nothing
    so the next request retries."""
    hit, val = _cache_get(_CINEMETA_CACHE, (ctype, imdb))
    if hit:
        return val
    futs = [_META_EX.submit(cinemeta, ctype, imdb),
            _META_EX.submit(_tmdb_find_id, ctype, imdb),
            _META_EX.submit(_imdb_suggest_id, imdb)]
    winner = None
    deadline = time.time() + 7
    while futs and not winner:
        wait(futs, timeout=max(0.2, deadline - time.time()),
             return_when=FIRST_COMPLETED)
        for f in list(futs):
            if not f.done():
                continue
            futs.remove(f)
            try:
                v = f.result()
            except Exception:
                v = None
            if v and v.get("name"):
                winner = v
                break
    if winner:
        winner.setdefault("tmdb", "")
        _cache_put(_CINEMETA_CACHE, (ctype, imdb), winner, 12 * 3600)
        return winner
    # definitive miss (cinemeta 404 caches None itself); otherwise transient
    hit2, val2 = _cache_get(_CINEMETA_CACHE, (ctype, imdb))
    return val2 if hit2 else None

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

# --------------------------------------------------------------------------
# 5. catalogs — scraped pools -> catalog & search metas
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
    with ThreadPoolExecutor(max_workers=3) as ex:   # v1.7.1: pages in parallel
        pages = list(ex.map(lambda pg: scrape_subjects(site, kind, pg),
                            range(1, CATALOG_PREFETCH + 1)))
    out = []
    for subs in pages:
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

_MPD_CACHE = {}     # dash_base -> {reps, audio, dur, seg_dur, exp}

# --------------------------------------------------------------------------
# 6. cdn / dash — CloudFront cookies, MPD parsing, DASH manifests
# --------------------------------------------------------------------------
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
    # per-kind expanded SegmentTimeline (real per-segment durations, seconds)
    tl = {}
    for aset in root.findall(".//m:AdaptationSet", ns):
        ct = aset.get("contentType") or ("audio" if aset.get("lang") else "video")
        if ct in tl:
            continue
        t = aset.find(".//m:SegmentTemplate", ns)
        if t is None:
            continue
        ts = float(t.get("timescale") or 1)
        stl = t.find("m:SegmentTimeline", ns)
        if stl is None:
            continue
        durs = []
        for s in stl.findall("m:S", ns):
            d = s.get("d")
            if not d:
                continue
            rep = int(s.get("r") or 0) + 1
            durs.extend([float(d) / ts] * rep)
        if durs:
            tl[ct] = durs
    seg_dur = 5.0
    if tl:
        k = "video" if "video" in tl else list(tl.keys())[0]
        seg_dur = sum(tl[k]) / len(tl[k])
    if not tl:
        tmpl = root.find(".//m:SegmentTemplate", ns)
        if tmpl is not None and tmpl.get("duration"):
            ts = float(tmpl.get("timescale") or 1)
            seg_dur = float(tmpl.get("duration")) / ts if ts else 5.0
    return {"video": video, "audio": audio, "dur": dur, "seg_dur": seg_dur or 5.0, "tl": tl}

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
                _cache_put(_MPD_CACHE, dash_base, info, 60 * 60)
                return info
        if r.status_code in (400, 403, 404):
            _cache_put(_MPD_CACHE, dash_base, None, 15 * 60)
            return None
    except requests.RequestException:
        return None
    return None




def _res_label(heights):
    hs = sorted(set(heights), reverse=True)
    if not hs:
        return "HD"
    return "MULTI" if len(hs) > 1 else str(hs[0])

_STREAM_CACHE = {}       # (ctype, imdb, se, ep) -> (card list, expiry)
_STREAM_CACHE_TTL = 10800  # 3h: a prewarmed next-episode outlives the current one
_STREAM_STALE = {}        # key -> (expiry, streams): served instantly while a
                          # background rebuild refreshes the fresh cache
_STREAM_STALE_TTL = 24 * 3600
_STALE_SWEEP_AT = 256     # v1.9.3: prune expired stale entries at this size

def _stale_put(key, streams):
    """v1.9.3: _STREAM_STALE was never pruned (entries survived forever,
    each holding a full card list) — sweep expired ones on write."""
    if len(_STREAM_STALE) >= _STALE_SWEEP_AT:
        now = time.time()
        for k in [k for k, ent in _STREAM_STALE.items() if ent[0] < now]:
            _STREAM_STALE.pop(k, None)
    _STREAM_STALE[key] = (time.time() + _STREAM_STALE_TTL, streams)

_STREAM_REFRESHING = set()
_REFRESH_LOCK = threading.Lock()
_PLAY_CACHE = {}         # (sid, se, ep) -> play-info payload (10 min)
_DUB_CACHE = {}          # sid -> dub list (30 min)
_SEARCH_CACHE = {}       # (kw, subject_type) -> subjects (10 min)

def _cached_search(kw, subject_type):
    key = (kw, subject_type)
    hit, val = _cache_get(_SEARCH_CACHE, key)
    if hit:
        return val
    val = search_subjects(kw, subject_type)
    # v1.7.0: None = transient transport failure — NEVER cached (the next
    # request retries). [] = the platform definitively answered "not in
    # catalog" — safe to cache for the full TTL.
    if val is None:
        return None
    _cache_put(_SEARCH_CACHE, key, val, 600)
    return val

def _cached_dubs(sid):
    hit, val = _cache_get(_DUB_CACHE, sid)
    if hit:
        return val
    val = subject_dubs(sid)
    if val is None:
        return None
    _cache_put(_DUB_CACHE, sid, val, 1800)
    return val

_PLAY_INFLIGHT = {}                 # v1.7.7: single-flight play-info
_PLAY_INFLIGHT_LOCK = threading.Lock()


def _cached_play(sid, se, ep):
    key = (str(sid), se, ep)
    hit, val = _cache_get(_PLAY_CACHE, key)
    if hit:
        return val
    with _PLAY_INFLIGHT_LOCK:
        fut = _PLAY_INFLIGHT.get(key)
        if fut is None:
            fut = _META_EX.submit(play_info, sid, se, ep)
            _PLAY_INFLIGHT[key] = fut
            fut.add_done_callback(lambda _f, _k=key: _PLAY_INFLIGHT.pop(_k, None))
    try:       # v1.7.8: bounded wait — a grinding play-info must never hold
        # a /stream build hostage (was: unbounded fut.result(), one of the
        # two compounding causes of the 6.5min+ prod hangs).
        val = fut.result(timeout=max(0.25, min(_PLAY_WAIT,
                                              _ddl_left() or _PLAY_WAIT)))
    except Exception:
        val = None            # transient (timeout included): not cached
    if val is None:
        return None
    _cache_put(_PLAY_CACHE, key, val, 3600)
    return val

# --------------------------------------------------------------------------
# web API (netnaija.film / h5api-bff): subtitles
# The site's play endpoint is IP-gated against servers, but its caption
# endpoint is not — it serves signed subtitle URLs (~7-day CloudFront
# wildcard policy on cacdn.hakunaymatata.com/subtitle/*) for every dub.
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# 8. subtitles — caption endpoints, SRT->VTT
# --------------------------------------------------------------------------
_WEB_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_WEB_JWT = None
_WEB_JWT_TS = 0.0
_SUB_CACHE = {}   # (sid, stream_id) -> captions list (1h)
_LANG3 = {"ar": "ara", "en": "eng", "es": "spa", "fil": "fil", "fr": "fra",
          "in_id": "ind", "id": "ind", "ms": "msa", "pt": "por", "ru": "rus",
          "bn": "ben", "hi": "hin", "ur": "urd", "pa": "pan", "zh": "zho",
          "ko": "kor", "ja": "jpn", "th": "tha", "vi": "vie", "tr": "tur",
          "de": "deu", "ita": "ita", "it": "ita"}

# v1.9.10: reverse map (3-letter source -> ISO-639-1) for sub langs
_LANG1 = {v: k for k, v in _LANG3.items() if len(k) == 2}

_LANG_NAME = {"ar": "Arabic", "bn": "Bangla", "en": "English", "es": "Spanish",
              "fil": "Filipino", "fr": "French", "in_id": "Indonesian", "id": "Indonesian",
              "ms": "Malay", "pt": "Portuguese", "ru": "Russian", "hi": "Hindi",
              "ur": "Urdu", "pa": "Punjabi", "zh": "Chinese", "ko": "Korean",
              "ja": "Japanese", "th": "Thai", "vi": "Vietnamese", "tr": "Turkish",
              "de": "German", "it": "Italian"}



def _web_jwt():
    """Anonymous web JWT via the site's search-suggest (x-user response
    header). Ungated — works from datacenter IPs, unlike subject/play."""
    global _WEB_JWT, _WEB_JWT_TS
    if _WEB_JWT and time.time() - _WEB_JWT_TS < 6 * 3600:
        return _WEB_JWT
    try:
        r = requests.post("https://netnaija.film/wefeed-h5api-bff/subject/search-suggest",
                          json={"keyword": "a", "perPage": 1},
                          headers={"Accept": "application/json",
                                   "Content-Type": "application/json",
                                   "X-Client-Info": json.dumps({"timezone": "Asia/Dhaka"}),
                                   "X-Request-Lang": "en", "User-Agent": _WEB_UA,
                                   "Origin": "https://netnaija.film",
                                   "Referer": "https://netnaija.film/"},
                          timeout=8)
        xu = r.headers.get("x-user") or ""
        if xu:
            try:
                tok = json.loads(xu).get("token") or ""
                if tok:
                    _WEB_JWT, _WEB_JWT_TS = tok, time.time()
            except Exception:
                pass
    except Exception:
        pass
    return _WEB_JWT

# v1.9.6 (user rule: "sub badh dile render bandwidth na kome tahole sub
# back ano" — measured: the filter saved only ~111B per /stream resolve
# (1.2% of a playback session) because SRT bytes NEVER pass through
# Render (direct cacdn URLs since v1.9.0). ALL languages are back.
# MOVIEBOX_SUBS="en,hi" re-enables the filter if ever wanted; default "" = all.
_SUB_LANGS = tuple(x.strip() for x in
                   os.environ.get("MOVIEBOX_SUBS", "").split(",") if x.strip())

def _direct_subs(caps):
    """v1.9.0 strict zero: the platform's caption CDN (cacdn…) serves
    the raw SRT files to ANY ip with no cookie (verified 2026-09-10) —
    subtitle objects point straight at them instead of our /sub route.
    Players (mpv/ExoPlayer) sniff SRT fine even without an extension.
    v1.9.6: _SUB_LANGS defaults to () = ALL languages (sub filter
    reverted — it saved no meaningful Render bandwidth)."""
    # v1.9.10: lang is normalized to ISO-639-1 ('ar','en') — Stremio
    # players don't match 3-letter codes ('ara' broke sub selection).
    # The platform mixes 2-letter ('en') and 3-letter ('ara') sources.
    def _l1(lan):
        lan = {"in_id": "id"}.get(lan, lan or "")   # Indonesian quirk
        return lan if len(lan) == 2 else _LANG1.get(lan, lan)
    return [{"url": c["url"], "lang": _l1(c.get("lan")),
             "id": "mbx-%s" % _l1(c.get("lan"))}
            for c in (caps or [])
            if c.get("lan") and c.get("url")
            and (not _SUB_LANGS or c.get("lan") in _SUB_LANGS)]


def fetch_captions(sid, stream_id):
    """Subtitle tracks for one stream. Primary: the platform's own mobile
    caption endpoint (get-stream-captions — same signed API as play-info,
    discovered in phisher98's CloudStream MovieBoxProvider). Fallback: the
    site's web caption endpoint (netnaija.film h5api-bff; ungated).
    Returns [{id, lan, lanName, url}, ...]; empty on failure."""
    if not stream_id:
        return []
    key = (str(sid), str(stream_id))
    hit, val = _cache_get(_SUB_CACHE, key)
    if hit:
        return val
    def _mobile():
        d = api_call("GET", "/wefeed-mobile-bff/subject-api/get-stream-captions"
                     "?subjectId=%s&streamId=%s" % (sid, stream_id), timeout=4)
        return (d.get("extCaptions") or []) if (d and "__error__" not in d) else []
    _t0 = time.time()
    caps = _mobile()
    if len(caps) < 2 and time.time() - _t0 < 2.0:
        # flaky endpoint — one quick retry, but only when the first call
        # FAILED FAST (a slow call means the family is sick: retrying
        # would just stack a second multi-second stall)
        time.sleep(0.15)
        retry = _mobile()
        if len(retry) > len(caps):
            caps = retry
    if len(caps) < 2 and time.time() - _t0 < 2.5:
        # still thin — try the web endpoint, but only when the mobile
        # family answered FAST (a slow mobile call means platform-side
        # sickness; the web fallback would just stack another stall)
        web = _web_captions(sid, stream_id)
        if len(web) > len(caps):
            caps = web
    _cache_put(_SUB_CACHE, key, caps, 3600 if caps else 180)
    return caps

def _web_captions(sid, stream_id):
    """Fallback: the netnaija.film web caption endpoint (needs a web JWT)."""
    for attempt in (1, 2):
        tok = _web_jwt()
        if not tok:
            return []
        try:
            r = requests.get("https://netnaija.film/wefeed-h5api-bff/subject/caption"
                             "?format=HLS&id=%s&subjectId=%s" % (stream_id, sid),
                             headers={"Accept": "application/json",
                                      "X-Client-Info": json.dumps({"timezone": "Asia/Dhaka"}),
                                      "X-Request-Lang": "en",
                                      "Authorization": "Bearer " + tok,
                                      "User-Agent": _WEB_UA,
                                      "Referer": "https://netnaija.film/"},
                             timeout=4)
        except requests.RequestException:
            return []
        if r.status_code in (401, 403):
            global _WEB_JWT_TS
            _WEB_JWT_TS = 0.0          # force a fresh token, retry once
            continue
        try:
            return ((r.json().get("data") or {}).get("captions")) or []
        except Exception:
            return []
    return []



_CODEC_LABEL = {"hevc": "HEVC", "h265": "HEVC", "h264": "H.264", "avc": "H.264",
                "av1": "AV1"}
_SUB_DISP = {"in_id": "id"}          # nicer code shown in the card sub line


def _fmt_size(n):
    """Bytes -> human readable; blank for junk values."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    if n < 10 * 1024 * 1024:         # < 10 MB is not worth a card slot
        return ""
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024.0
        if n < 1024 or unit == "TB":
            return "%.1f %s" % (n, unit)
    return ""

def _fmt_dur(secs):
    try:
        secs = int(secs)
    except (TypeError, ValueError):
        return ""
    if secs < 60:
        return ""
    if secs >= 3600:
        return "%dh%02dm" % (secs // 3600, (secs % 3600) // 60)
    return "%d min" % (secs // 60)

def _res_range(pi, pl):
    """Max quality only (user directive): '480p-1080p' used to be shown —
    the card now carries just the top resolution the entry serves."""
    raw = pi.get("displayResolutions") or (pl or {}).get("resolutions") or ""
    try:
        heights = sorted({int(x) for x in re.findall(r"\d{3,4}", str(raw))})
    except Exception:
        heights = []
    if not heights:
        return "MULTI"
    return "%dp" % heights[-1]

_CARD_GROUP = BRAND   # v1.9.8: ⌗ carries the addon name

def _ql_label(qtxt):
    """'1080p'/'480p' -> 'FHD 1080p'-style label (user card spec)."""
    m = re.search(r"(2160|1440|1080|960|720|576|480|360)", qtxt or "")
    if not m:
        return "HLS"
    h = int(m.group(1))
    if h >= 2160:
        return "UHD 2160p"
    if h >= 1080:
        return "FHD 1080p"
    return ("HD %dp" if h >= 720 else "SD %dp") % h

def _fmt_card_desc(ql_txt, codec, size, dur, ctype, se, ep, year,
                   label, subs, via="Netnaija"):
    """v1.9.7 unified stream-card format (user spec):

        ♧ FHD 1080p  ✹ Title (Dub)
        ◫ S01 E05 ◇ 863.7 MB ▧ HEVC ◷ 58m
        ◈ WEB-DL
        ◈ Hindi
        ⌗ MovieBox
        ⌬ Netnaija  ◴ 2026 ⟡ 16 SUB · ar, bn, en +13
    """
    t1 = ["◫ S%02d E%02d" % (se, ep) if ctype == "series" else "◫ MOVIE"]
    if size:
        t1.append("◇ %s" % size)
    if codec:
        t1.append("▧ %s" % codec)
    if dur:
        t1.append("◷ %s" % dur)
    t4 = ["⌬ %s" % via]
    if year:
        y = str(year)[:4]
        if y.isdigit():
            t4.append("◴ %s" % y)
    sl = _sub_line(subs)
    if sl:
        t4.append("⟡ " + sl[2:])               # strip the old ▣ prefix
    lines = [" ".join(t1), "◈ WEB-DL"]
    if label:
        lines.append("◈ %s" % label)           # audio langs (glass line)
    lines += ["⌗ %s" % BRAND, "  ".join(t4)]
    return "\n".join(lines)

def _sub_line(subs):
    """Third card line: subtitle count + languages."""
    if not subs:
        return "▣ NO SUB"
    codes = [_SUB_DISP.get(s["id"][4:], s["id"][4:]) for s in subs]
    more = " +%d" % (len(codes) - 3) if len(codes) > 3 else ""
    return "▣ %d SUB · %s%s" % (len(subs), ", ".join(codes[:3]), more)

_LABEL_PRETTY = {"esla": "Spanish", "ptbr": "Portuguese (BR)", "pt": "Portuguese",
                 "es": "Spanish", "id": "Indonesian"}

def _pretty_label(nm):
    nm = (nm or "").strip()
    return _LABEL_PRETTY.get(nm.lower(), nm) or "Dub"

# --------------------------------------------------------------------------
# 5b. v1.9.1 web multi-quality direct MP4s — the site's own per-resolution
# streams (360/480/720/1080), minted from the platform's WEB play API and
# served as DIRECT cards (sign embedded in the URL, ~17h validity, zero
# bytes through Render). Flow (reverse-engineered from the site player):
#   POST /wefeed-h5api-bff/subject/search-suggest  -> JWT (x-user header)
#   POST /wefeed-h5api-bff/subject/search          -> items (detailPath!)
#   GET  /videoPlayPage/{detailPath}               -> warm (REQUIRED: without
#        this visit + Referer, play silently returns hasResource=false)
#   GET  /subject/play?subjectId&se&ep&detailPath  -> per-resolution MP4s
# Dub variants exist as their own web subjects ("Title [Hindi]").
# --------------------------------------------------------------------------
# v1.9.2: DEFAULT OFF — the user confirmed the HEVC DASH cards play on
# their device, while the web MP4 CDN (bcdnxw) also refused their player
# (and every server-side probe), so those cards were unverifiable phantoms.
# Set MOVIEBOX_WEB_MP4=1 to re-enable the per-resolution web cards.
WEB_MP4_ON = os.environ.get("MOVIEBOX_WEB_MP4", "0").strip().lower() \
    in ("1", "true", "on")
_WEB_SITE = "https://netnaija.film"
_WEB_MP4_TTL = 40 * 60              # signed URLs live ~17h; 40min freshness
_WEB_MP4_NEG = 10 * 60
_WEB_LANG_CACHE = {}                # (ctype, norm_title) -> (ts, {lang:(sid,dp)})
_WEB_MP4_CACHE = {}                 # (sid, se, ep) -> (ts, streams|None)
_WEB_MP4_LOCK = threading.Lock()

def _web_norm_t(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())

def _web_hdrs(referer=None):
    h = {"Accept": "application/json",
         "X-Client-Info": json.dumps({"timezone": "Asia/Dhaka"}),
         "User-Agent": _WEB_UA, "Origin": _WEB_SITE, "X-Source": ""}
    if referer:
        h["Referer"] = referer
    return h

def _web_lang_map(title, ctype):
    """One web search for the title -> {lang: (sid, detailPath)} for exact
    matches ('' = original). Cached; empty dict on miss/absence."""
    key = (ctype, _web_norm_t(title))
    hit, val = _cache_get(_WEB_LANG_CACHE, key)
    if hit:
        return val or {}
    langs = {}
    try:
        jwt = _web_jwt()
        if jwt:
            r = requests.post(
                _WEB_SITE + "/wefeed-h5api-bff/subject/search",
                json={"keyword": title, "page": 1, "perPage": 20,
                      "subjectType": 1 if ctype == "movie" else 2,
                      "tabId": "All"},
                headers={"Accept": "application/json",
                         "Content-Type": "application/json",
                         "X-Client-Info": json.dumps({"timezone": "Asia/Dhaka"}),
                         "X-Request-Lang": "en", "User-Agent": _WEB_UA,
                         "Origin": _WEB_SITE, "Referer": _WEB_SITE + "/",
                         "X-Source": "h5",
                         "Authorization": "Bearer %s" % jwt},
                timeout=5)
            items = (((r.json() or {}).get("data") or {}).get("items")) or []
            for it in items:
                raw = (it.get("title") or "").strip()
                sid, dp = it.get("subjectId"), it.get("detailPath")
                if not sid or not dp:
                    continue
                bare = re.sub(r"\s*\[[^\]]*\]", "", raw).strip()
                if _web_norm_t(bare) != _web_norm_t(title):
                    continue
                m = re.search(r"\[([^\]]+)\]", raw)
                lang = (m.group(1).strip() if m else "").lower()
                langs.setdefault(lang, (str(sid), dp))
    except Exception:
        pass
    _cache_put(_WEB_LANG_CACHE, key, langs or None,
               _WEB_MP4_TTL if langs else _WEB_MP4_NEG)
    return langs

def _web_mp4_streams(sid, dp, se, ep):
    """Signed per-resolution MP4s for one web (dub) subject.
    Returns [(res_int, url, size_bytes, codec, duration)] desc, or []."""
    if not WEB_MP4_ON:
        return []
    key = (sid, se, ep)
    hit, val = _cache_get(_WEB_MP4_CACHE, key)
    if hit:
        return val or []
    out = []
    try:
        if _ddl_left() is not None and _ddl_left() < 2.5:
            return []                      # too late in the budget — skip
        s = requests.Session()
        s.get(_WEB_SITE + "/videoPlayPage/" + dp,
              headers={"User-Agent": _WEB_UA, "Accept": "text/html"},
              timeout=4)
        r = s.get(_WEB_SITE + "/wefeed-h5api-bff/subject/play"
                  "?subjectId=%s&se=%s&ep=%s&detailPath=%s" % (sid, se, ep, dp),
                  headers=_web_hdrs(_WEB_SITE + "/videoPlayPage/" + dp),
                  timeout=5)
        d = (r.json() or {}).get("data") or {}
        if d.get("hasResource") or d.get("streams"):
            for st in d.get("streams") or []:
                u = str(st.get("url") or "")
                if not u.startswith("http") or ".mp4" not in u.lower():
                    continue
                try:
                    res = int(re.findall(r"\d{3,4}", str(st.get("resolutions")))[0])
                except Exception:
                    continue
                out.append((res, u, int(st.get("size") or 0),
                            str(st.get("codecName") or ""),
                            int(st.get("duration") or 0)))
            out.sort(key=lambda x: -x[0])
    except Exception:
        out = []
    _cache_put(_WEB_MP4_CACHE, key, out or None,
               _WEB_MP4_TTL if out else _WEB_MP4_NEG)
    return out


def _res_from_pi(pi, pl):
    """Resolution label from play-info (no MPD fetch needed at card time)."""
    raw = pi.get("displayResolutions") or (pl or {}).get("resolutions") or ""
    try:
        heights = [int(x) for x in re.findall(r"\d{3,4}", str(raw))]
    except Exception:
        heights = []
    return _res_label(heights) if heights else "MULTI"


# --------------------------------------------------------------------------
# v1.9.4: HLS quality-menu layer (restored from the v1.4-v1.8.1 design,
# user directive: 'quality switch korte partam, ekhon parchi na').
# Direct DASH MPD cards lock desktop players (mpv) to ONE representation —
# no quality menu. Serving ONLY the master + variant PLAYLISTS from here
# (tiny text, gzip) brings the 240p-1080p Stremio quality menu back, while
# every segment stays a self-signed ABSOLUTE CloudFront URL (query params,
# no cookie/headers needed — verified cookie-less 206 on 2026-09-11) so
# media bytes still NEVER touch this server (multimovies-v2.2.0-class
# 'tiny playlist relay', user-approved; zero VIDEO bytes through Render).
# Kill switch: MOVIEBOX_HLS=0 -> direct MPD cards (v1.9.0-1.9.3 behaviour).
# --------------------------------------------------------------------------
HLS_ON = os.environ.get("MOVIEBOX_HLS", "1") != "0"

def _signed_url(base, fname, cf):
    """CloudFront signed URL from the play-info cookie parts (self-contained
    query signature — no Cookie header required by the player)."""
    return "%s/%s?%s" % (base, fname, urlencode(
        {"Policy": cf["CloudFront-Policy"],
         "Signature": cf["CloudFront-Signature"],
         "Key-Pair-Id": cf["CloudFront-Key-Pair-Id"]}))

def hls_master(sess):
    """Master playlist: one variant per video representation + audio group."""
    mpd = sess["mpd"]
    lines = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-INDEPENDENT-SEGMENTS"]
    auds = mpd["audio"]
    for i, a in enumerate(auds):
        lines.append('#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="%s",'
                     'DEFAULT=%s,AUTOSELECT=YES,LANGUAGE="%s",URI="a%d.m3u8"'
                     % (a["lang"].upper(), "YES" if i == 0 else "NO", a["lang"], i))
    for v in mpd["video"]:
        vcodecs = v["codecs"].replace("hev1", "hvc1")   # hvc1: Safari-friendly
        codecs = vcodecs + ("," + ",".join(a["codecs"] for a in auds) if auds else "")
        res = "%dx%d" % (v["width"], v["height"]) if v.get("width") else str(v["height"])
        extra = ',AUDIO="aud"' if auds else ""
        lines.append('#EXT-X-STREAM-INF:BANDWIDTH=%d,RESOLUTION=%s,CODECS="%s"%s'
                     % (v["bw"], res, codecs, extra))
        lines.append("v%s.m3u8" % v["id"])
    return "\n".join(lines) + "\n"

_TAIL_CACHE = {}   # (dash, rep) -> last segment index that exists on the CDN

def _seg_exists(dash, rep, i, cf):
    try:
        r = requests.get(_signed_url(dash, "chunk-stream%s-%05d.m4s" % (rep, i), cf),
                         headers={"Range": "bytes=0-1"}, timeout=8)
        return r.status_code in (200, 206)
    except Exception:
        return False

def _last_good_seg(dash, rep, n, cf):
    """Some platform uploads have an MPD duration inflated ~1.2x — the
    playlist would list segments that don't exist and players die ~83% in.
    Probe the tail once (2-byte ranges, cached 30min) and trim to reality."""
    key = (dash, rep)
    hit, val = _cache_get(_TAIL_CACHE, key)
    if hit:
        return val
    last = n
    if n > 1 and not _seg_exists(dash, rep, n, cf):
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
    """Media playlist for one representation: init map + signed direct
    segment URLs (absolute CloudFront — zero media bytes through Render)."""
    mpd = sess["mpd"]
    tl = (mpd.get("tl") or {}).get("video" if kind == "v" else "audio")
    if tl:
        durs, n = tl, len(tl)
        tgt = int(math.ceil(max(durs)))
    else:
        seg = mpd["seg_dur"] or 5.0
        durs, n = None, max(1, int(math.ceil((mpd["dur"] or 0) / seg)))
        tgt = int(math.ceil(seg))
    n = _last_good_seg(sess["dash"], rep_id, n, sess["cf"])
    use = durs[:n] if durs else None
    if use:
        tgt = int(math.ceil(max(use)))
    lines = ["#EXTM3U", "#EXT-X-VERSION:7",
             "#EXT-X-TARGETDURATION:%d" % tgt,
             "#EXT-X-PLAYLIST-TYPE:VOD",
             '#EXT-X-MAP:URI="%s"' % _signed_url(sess["dash"],
                                                 "init-stream%s.m4s" % rep_id,
                                                 sess["cf"])]
    for i in range(1, n + 1):
        d = use[i - 1] if use else (mpd["seg_dur"] or 5.0)
        lines.append("#EXTINF:%.1f," % d)
        lines.append(_signed_url(sess["dash"],
                                 "chunk-stream%s-%05d.m4s" % (rep_id, i), sess["cf"]))
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"

def _lazy_hls(sid, se, ep, file):
    """Stateless HLS: playlists derived from (sid, se, ep) via cached
    play-info + cached MPD. No session store — survives restarts and keeps
    signatures fresh for long playback sessions."""
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


def _resolve_entry(pair, se, ep, ctype, title, year, caps=None, web_langs=None):
    """Stream cards for one dub entry. Only play-info is fetched here
    (cached); the DASH card points DIRECTLY at the platform's MPD with
    the signCookie via proxyHeaders (v1.9.0 strict zero-bandwidth).
    v1.9.1: ALSO mints the site's per-resolution direct MP4s from the
    WEB play API (signed URLs, no cookies) — one card per quality,
    ahead of the DASH card."""
    sid, label = pair
    pi = _cached_play(sid, se if ctype == "series" else None,
                      ep if ctype == "series" else None)
    if not pi:
        # web MP4s can still exist even when the mobile play-info is
        # transiently sick — try them before giving up on this dub
        web_cards = _web_cards_for(title, label, ctype, se, ep, sid, web_langs)
        return web_cards or None
    pl = (pi.get("streams") or [None])[0]
    web_cards = _web_cards_for(title, label, ctype, se, ep, sid, web_langs)
    if not pl or not pl.get("signCookie"):
        return web_cards or None
    cf = _cf_parts(pl["signCookie"])
    if not cf or "CloudFront-Policy" not in cf:
        return web_cards or None
    if not _dash_base(cf["CloudFront-Policy"]):
        return web_cards or None
    res = _res_from_pi(pi, pl)
    use_se, use_ep = (se, ep) if ctype == "series" else (0, 0)
    # --- v1.9.7: unified stream-card format (user spec) ---
    ran = _res_range(pi, pl)
    codec = _CODEC_LABEL.get(str(pl.get("codecName") or "").lower())
    card_name = "♧ %s  ✹ %s" % (_ql_label(ran), title)   # v1.9.9: no dub bracket
    # line 3: subtitle tracks. build_streams passes the title-wide caption
    # set (fetched once, concurrently); a standalone call fetches its own.
    if caps is None:
        try:
            caps = fetch_captions(sid, pl.get("id")) or []
        except Exception:
            caps = []
    subs = _direct_subs(caps)
    cf2 = _cf_parts(pl["signCookie"])
    dash = _dash_base(cf2["CloudFront-Policy"])
    # v1.9.4 quality-menu layer: when the MPD parses (one cached text
    # fetch — also the no-phantom verification), the card points at OUR
    # master.m3u8 listing every representation, so Stremio shows the
    # 240p-1080p quality menu again (user: 'quality switch korte partam
    # ekhon parchi na'). Segments inside the variant playlists are
    # self-signed ABSOLUTE CloudFront URLs — media bytes still never
    # touch this server, only tiny playlist text does.
    hls_url = None
    if HLS_ON:
        mpd_info = get_mpd_info(dash, pl["signCookie"])
        if mpd_info and mpd_info.get("video"):
            hls_url = "/hls/%s/%d/%d/master.m3u8" % (sid, use_se, use_ep)
    card = {
        "name": card_name,
        "description": _fmt_card_desc(ran, codec, _fmt_size(pl.get("size")),
                                      _fmt_dur(pl.get("duration")),
                                      ctype, se, ep, year, label, subs),
        "url": hls_url or ("%s/index.mpd" % dash),
        "behaviorHints": {"notWebReady": not bool(hls_url),
                          "isBingeable": True,
                          **({} if hls_url else {"proxyHeaders": {"request": {
                              "Cookie": pl["signCookie"],
                              "User-Agent": "ExoPlayerLib/2.18.7"}}})},
        # (hls_url=None -> the v1.9.0-1.9.3 direct-MPD fallback card:
        #  DASH manifest + signCookie via proxyHeaders, cookie NOT
        #  IP-bound — MPD + segments 200/206 from other IPs verified.)
        "bingeGroup": "mbx|%s:%s:%s|%s|%s" % (title, se if ctype == "series" else "",
                                              ep if ctype == "series" else "", label, res),
        "subtitles": subs,
    }
    return web_cards + [card]


def _web_cards_for(title, label, ctype, se, ep, mob_sid, web_langs):
    """v1.9.1: per-resolution DIRECT MP4 cards from the platform's WEB
    play API (the site player's own multi-quality sources). One card per
    resolution; sign embedded in the URL (~17h), no cookies, plays in
    Stremio Web too. Returns [] when the web catalog lacks this dub."""
    if not WEB_MP4_ON or web_langs is None:
        return []
    lang = "" if (label or "").lower() in ("", "original", "default") \
        else (label or "").lower()
    ent = web_langs.get(lang)
    if not ent:
        # never guess: a web card labeled (Hindi) must be the HINDI dub's
        # own web subject — mapping it to the original audio would mislabel
        return []
    wsid, wdp = ent
    use_se, use_ep = (se, ep) if ctype == "series" else (0, 0)
    st = _web_mp4_streams(wsid, wdp, use_se, use_ep)
    if not st:
        return []
    cards = []
    for res_i, url, size, codec, dur in st:
        cl = _CODEC_LABEL.get((codec or "").lower())
        cards.append({
            "name": "♧ %dp  ✹ %s" % (res_i, title),   # v1.9.9: no dub bracket
            "description": _fmt_card_desc(
                "%dp" % res_i, cl, _fmt_size(size), _fmt_dur(dur),
                ctype, se, ep, year, label, [], via="Netnaija WEB"),
            # signed DIRECT URL — zero bytes through Render, no headers
            "url": url,
            "behaviorHints": {"notWebReady": False, "isBingeable": True},
            "bingeGroup": "mbxw|%s:%s:%s|%s|%dp" % (
                title, se if ctype == "series" else "",
                ep if ctype == "series" else "", label, res_i),
        })
    return cards



_ALT_CACHE = {}          # (ctype, tmdb_id) -> alternative titles (24h)
_JUNK_RE = re.compile(r"\b(review|trailer|teaser|recap|explained|full movie|cam)\b", re.I)


def _alt_titles(ctype, tmdb_id):
    """Latin-script alternative titles from TMDB (localised-name rescue:
    IMDb calls a show "Back to Work!" while the platform lists it as
    "See You at Work Tomorrow!")."""
    tmdb_id = str(tmdb_id or "").strip()
    if not tmdb_id:
        return []
    key = (ctype, tmdb_id)
    hit, val = _cache_get(_ALT_CACHE, key)
    if hit:
        return val
    try:
        u = "%s/%s/%s/alternative_titles?api_key=%s" % (
            "https://api.themoviedb.org/3",
            "movie" if ctype == "movie" else "tv", tmdb_id, TMDB_API_KEY)
        d = requests.get(u, timeout=10).json()
        en, other, seen = [], [], set()
        for t in (d.get("titles") or d.get("results") or []):
            ttl = (t.get("title") or t.get("name") or "").strip()
            if (not ttl or ttl in seen or not ttl.isascii()
                    or not re.search(r"[a-z]", ttl, re.I)):
                continue
            seen.add(ttl)
            # English-market titles first; romanizations ("Nae-il-do...")
            # are weak platform-search terms, keep them as last resort
            (en if (t.get("iso_3166_1") or "").upper() in ("US", "GB", "WW")
             else other).append(ttl)
        out = (en + other)[:6]
    except Exception:
        out = []
    _cache_put(_ALT_CACHE, key, out, 24 * 3600)
    return out

def _ftokens(t):
    """Punctuation-free token set ("Tomorrow's Work!" -> {tomorrow, s, work})
    — punctuation would otherwise break token equality."""
    return set(re.findall(r"[a-z0-9]+", clean_title(t or "").lower()))

def _fuzzy_match(subjects, query, year, subject_type):
    """Junk-guarded fuzzy match for the alt-title rescue: >=2 shared title
    tokens, >=50% of the query covered, year within +/-1, and the candidate
    must not be review/trailer/recap junk."""
    q = _ftokens(query)
    hits = []
    for s in subjects:
        if int(s.get("subjectType") or 0) != subject_type:
            continue
        t = s.get("title") or ""
        if _JUNK_RE.search(t):
            continue
        ctoks = _ftokens(t)
        shared = {w for w in (q & ctoks) if len(w) > 2}
        if len(shared) < 2 or len(shared) / max(len(q), 1) < 0.5:
            continue
        sy = _year_of(s.get("releaseDate"))
        if year and sy and abs(int(sy) - int(year)) > 1:
            continue
        label = (s.get("corner") or "").strip() or "Original"
        hits.append((s, label))
    return hits[:2]

# ---------------------------------------------------------------- stream build
_PHASES = threading.local()      # v1.7.7: per-thread phase timing recorder


def _ph(label, t0):
    """Append 'label ms' to this thread's phase record (best effort)."""
    try:
        rec = getattr(_PHASES, "rec", None)
        if rec is not None:
            rec.append("%s %d" % (label, int((time.time() - t0) * 1000)))
    except Exception:
        pass


_PHASE_RING = []                     # v1.7.8: last build phase records
_BUILD_EX = ThreadPoolExecutor(max_workers=4, thread_name_prefix="build")


def build_streams(ctype, imdb, se, ep, _prewarm_next=True):
    key = (ctype, imdb, se, ep)
    _PHASES.rec = []             # fresh record (thread may be reused)
    hit, val = _cache_get(_STREAM_CACHE, key)
    if hit:
        return {"streams": val}
    if _prewarm_next:
        # v1.7.8: the WALL. The 25s chain budget is thread-local, but the
        # build fans out to executor threads (alt searches, dubs, play-info
        # single-flight, resolve wave) that never see it — on prod a
        # grinding egress compounded those unbounded waits into 6.5min+
        # HANGS with no answer at all (only cached titles responded). The
        # player now always gets an answer within _STREAM_WALL; the build
        # keeps running in the background and lands in the cache, so a
        # retry a minute later usually hits the finished result.
        fut = _BUILD_EX.submit(_build_guarded, ctype, imdb, se, ep, key,
                               _prewarm_next)
        try:
            res, rec = fut.result(timeout=_STREAM_WALL)
            _PHASES.rec = rec      # v1.7.8: keep the reqlog phase breakdown
            return res
        except FuturesTimeoutError:
            _PHASE_RING.append({"t": time.strftime("%H:%M:%S"),
                                "wall": "hit",
                                "phases": list(getattr(_PHASES, "rec", None)
                                               or [])})
            del _PHASE_RING[:-24]
            return {"streams": [],
                    "message": "platform slow — tap streams again in a "
                               "minute (the list is being built)"}
    res, rec = _build_guarded(ctype, imdb, se, ep, key, _prewarm_next)
    _PHASES.rec = rec
    return res


def _build_guarded(ctype, imdb, se, ep, key, _prewarm_next):
    """v1.7.5 hard chain budget, armed on WHICHEVER thread runs the build.
    Returns (result, phase-record) so the caller can mirror the record."""
    _prev_ddl = getattr(_CHAIN_DDL, "t", None)
    _CHAIN_DDL.t = time.time() + _STREAM_BUDGET
    _EGRESS.pool = False
    _PHASES.rec = []       # arm the recorder on THIS (worker) thread too
    try:
        return _build_streams_inner(ctype, imdb, se, ep, key, _prewarm_next), \
            list(getattr(_PHASES, "rec", None) or [])
    finally:
        try:
            rec = getattr(_PHASES, "rec", None)
            if rec:
                _PHASE_RING.append({"t": time.strftime("%H:%M:%S"),
                                    "phases": list(rec)})
                del _PHASE_RING[:-24]
        except Exception:
            pass
        _CHAIN_DDL.t = _prev_ddl
        _EGRESS.pool = False


def _ddl_inherit(fn):
    """v1.7.8: run fn on another thread with THIS thread's chain deadline.

    The budget lives in a thread-local; executor workers submitted from a
    request used to run deadline-less, which is how off-thread grinds
    escaped the 25s budget entirely."""
    ddl = getattr(_CHAIN_DDL, "t", None)

    def _w(*a, **kw):
        _CHAIN_DDL.t = ddl
        try:
            return fn(*a, **kw)
        finally:
            _CHAIN_DDL.t = None
    return _w


def _neg_ttl():
    """How long an EMPTY stream answer may be cached. Answers that rode
    the free-proxy pool can be proxy lies (mangled body / geo catalog /
    flagged exit) — never let one blank a title for long (v1.6.9 lesson,
    tightened for pool egress in v1.7.5)."""
    return 60 if getattr(_EGRESS, "pool", False) else 600


def _build_streams_inner(ctype, imdb, se, ep, key, _prewarm_next):
    if not _prewarm_next:          # background rebuild (SWR / prewarm path)
        _STREAM_STALE.pop(key, None)
    else:                          # stale-while-revalidate: instant answer,
        stale = _STREAM_STALE.get(key)     # refreshed behind the curtain
        if stale and stale[0] > time.time() and stale[1]:
            with _REFRESH_LOCK:
                if key not in _STREAM_REFRESHING:
                    _STREAM_REFRESHING.add(key)
                    threading.Thread(target=_bg_refresh, daemon=True,
                                     args=(ctype, imdb, se, ep, key)).start()
            return {"streams": stale[1]}
    _t = time.time()
    meta = _meta_any(ctype, imdb)
    _ph("meta", _t)
    if not meta:
        return {"streams": [], "message": "no metadata"}
    title, year = meta["name"], meta["year"]
    stype = 1 if ctype == "movie" else 2
    # v1.7.6: alt titles are fetched CONCURRENTLY with the primary search
    # (TMDB is cheap, 24h-cached). The old flow paid a serial TMDB call at
    # rescue time, then SERIAL per-alt platform searches — up to ~10s on
    # localized-name titles before any card appeared.
    alt_fut = (_META_EX.submit(_alt_titles, ctype, meta.get("tmdb"))
               if meta.get("tmdb") else None)
    _t = time.time()
    subs = _cached_search(title, stype)
    _ph("search", _t)
    if subs is None:
        # transient egress failure — NOT cached; the player may retry at once
        return {"streams": [], "message": "platform busy — try again"}
    _t = time.time()
    matched = match_subjects(subs, title, year, stype, season=se) if subs else []
    _ph("match", _t)
    _rt = time.time()            # rescue span (≈0 when the primary matched)
    if not matched:
        # v1.7.2 localised-name rescue: IMDb/cinemeta and the platform often
        # use different English names for the same show. v1.7.6: alt-title
        # searches run in PARALLEL (3 at a time) and are evaluated in
        # priority order (EN-market first) — was one search at a time.
        try:
            alts = (alt_fut.result(timeout=6) if alt_fut
                    else _alt_titles(ctype, meta.get("tmdb"))) or []
        except Exception:
            alts = []
        alts = [a for a in alts
                if a and clean_title(a).lower() != clean_title(title).lower()][:6]
        if alts:
            with ThreadPoolExecutor(max_workers=min(3, len(alts))) as aex:
                afuts = [aex.submit(_ddl_inherit(_cached_search), a, stype)
                         for a in alts]
                for alt, af in zip(alts, afuts):     # priority order
                    try:            # v1.7.8: bounded wait, deadline-aware
                        alt_subs = af.result(
                            timeout=max(0.5, min(8.0, _ddl_left() or 8.0)))
                    except FuturesTimeoutError:
                        continue
                    if not alt_subs:
                        continue
                    matched = _fuzzy_match(alt_subs, alt, year, stype)
                    if matched:
                        break
    _ph("rescue", _rt)
    if not subs and not matched:
        _cache_put(_STREAM_CACHE, key, [], _neg_ttl())
        return {"streams": [], "message": "not in platform catalog"}
    if not matched:
        _cache_put(_STREAM_CACHE, key, [], _neg_ttl())
        return {"streams": [], "message": "no matching subject"}
    # v1.7.6: dub lists AND play-info for the top matches run in the SAME
    # wave — play-info results land in the shared cache, so _resolve_entry
    # below picks them up for free (was: dubs wave, then a play-info wave).
    _t = time.time()
    m_sids = [str(m[0].get("subjectId")) for m in matched[:4]]
    # v1.7.7: play-info prefetch is fire-and-forget on the shared pool —
    # _cached_play is single-flight, so the resolve wave below joins the
    # very same in-flight future; a slow prefetch can no longer hold the
    # dubs wave (measured: 7.3s dubs+play outliers from one sick host).
    for s in m_sids:
        _META_EX.submit(_cached_play, s,
                        se if ctype == "series" else None,
                        ep if ctype == "series" else None)
    with ThreadPoolExecutor(max_workers=2) as ex:
        dub_f = [ex.submit(_ddl_inherit(_cached_dubs), s) for s in m_sids[:2]]
        dub_lists = [f.result() for f in dub_f]   # bounded by _API_CALL_WALL
    _ph("dubs+play", _t)
    # dedupe by subjectId AND label (clean card list)
    entries, seen, seen_labels = [], set(), set()
    for s, label in matched:
        sid = str(s.get("subjectId"))
        if sid not in seen and label not in seen_labels:
            seen.add(sid)
            seen_labels.add(label)
            entries.append((sid, label))
    for dubs in dub_lists:
        if not dubs:                # None (transient) or [] — nothing to add
            continue
        for d in dubs[:6]:
            dsid = str(d.get("subjectId"))
            nm = (d.get("lanName") or "").replace(" dub", "").replace(" Audio", "").strip()
            nm = _pretty_label(nm)
            if dsid not in seen and nm not in seen_labels:
                seen.add(dsid)
                seen_labels.add(nm)
                entries.append((dsid, nm))
    entries = entries[:8]

    def _title_caps():
        """ONE caption fetch for the whole title (dubs share the same set;
        sub URLs embed the source sid and resolve on /sub/). Tries at most
        two sids. Runs concurrently with play-info resolution below."""
        caps, src_sid = [], None
        for sid, _ in entries[:2]:
            pi = _cached_play(sid, se if ctype == "series" else None,
                              ep if ctype == "series" else None)
            pl = (pi.get("streams") or [None])[0] if pi else None
            if not pl or not pl.get("id"):
                continue
            try:
                caps = fetch_captions(sid, pl["id"]) or []
            except Exception:
                caps = []
            if caps:
                src_sid = sid
            if len(caps) >= 2:
                break
        return src_sid, caps

    # resolve every dub in parallel (play-info only — no per-dub caption
    # round trips any more), while the shared captions are fetched once.
    # v1.9.1: ONE web search for the title maps dubs to the site's web
    # subjects, so each dub can mint its per-resolution direct MP4s.
    _t = time.time()
    web_langs = _web_lang_map(title, ctype) if WEB_MP4_ON else None
    with ThreadPoolExecutor(max_workers=8) as ex:
        cap_fut = ex.submit(_ddl_inherit(_title_caps))
        results = list(ex.map(_ddl_inherit(
            lambda p: _resolve_entry(p, se, ep, ctype, title, year, caps=[],
                                     web_langs=web_langs)),
            entries))
        cap_sid, caps = cap_fut.result()   # bounded by _PLAY_WAIT caps
    _ph("resolve", _t)
    streams = [c for r in results if r for c in r]
    # attach the shared subtitle set to every card (direct cacdn URLs,
    # no cookie needed — v1.9.0 strict zero)
    if caps and cap_sid:
        use_se, use_ep = (se, ep) if ctype == "series" else (0, 0)
        shared = _direct_subs(caps)
        if shared:
            for s in streams:
                s["subtitles"] = shared
                # v1.9.7: swap the ⟡ sub tag inside the ⌬ line
                head = s["description"].rsplit("\n", 1)[0]
                base = s["description"].rsplit("\n", 1)[1]
                base = re.sub(r"\s*⟡ [^⟡]*$", "", base).rstrip()
                s["description"] = (head + "\n" + base + "  ⟡ " +
                                    _sub_line(shared)[2:]).rstrip()
    if streams:
        _cache_put(_STREAM_CACHE, key, streams, _STREAM_CACHE_TTL)
        _stale_put(key, streams)   # v1.9.3: sweeps expired entries on write
        if _prewarm_next and ctype == "series":
            # background-warm the next episode so binge navigation is instant
            threading.Thread(target=_safe_build, daemon=True,
                             args=(ctype, imdb, se, ep + 1)).start()
        if _prewarm_next:
            # background-warm play-info + MPD so the first PLAY click is instant
            _spawn_warm(entries, se, ep, ctype)
    if not streams:
        # matched on the platform but no playable video came back — usually
        # an unreleased film's placeholder entry (v1.7.5: say so honestly
        # instead of a blank list)
        _cache_put(_STREAM_CACHE, key, [], _neg_ttl())
        return {"streams": [], "message":
                "platform entry has no video yet (upcoming release?)"}
    return {"streams": streams}

_WARM_TS = [0.0]                  # last prewarm batch (module-level, mutable)

def _spawn_warm(entries, se, ep, ctype):
    """Background prewarm of the next episode — heavily throttled: one batch
    per 10 minutes and never while the platform breaker is open (prewarm is
    the biggest source of platform call volume on a busy instance)."""
    if not _plat_ok():
        return
    if _sd_forced("/wefeed-mobile-bff/subject-api/search/v2") and (_pool_all() or _SCRAPEDO_TOKEN or _PLAT_PROXIES):
        return  # credit conservation: no prefetch while ANY proxy egress is active
    now = time.time()
    if now - _WARM_TS[0] < 600:
        return
    _WARM_TS[0] = now
    def _w():
        for sid, _ in entries[:8]:
            if not _plat_ok():
                return
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

def _bg_refresh(ctype, imdb, se, ep, key):
    """Background SWR rebuild; always releases the in-flight marker."""
    try:
        _safe_build(ctype, imdb, se, ep)
    finally:
        with _REFRESH_LOCK:
            _STREAM_REFRESHING.discard(key)

# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------

MANIFEST = {
    "id": "com.movbox.stremio",
    "version": VERSION,
    "name": "MovieBox",
    "description": ("Stream-only addon for Netnaija.film + MovieBoxOnline.net — "
                    "open any movie or series (up to 1080p, multi-language "
                    "dubs, HEVC) and direct CDN streams appear. No catalogs."),
    "types": ["movie", "series"],
    "idPrefixes": ["tt"],
    "logo": "/logo.png",
    "behaviorHints": {"configurable": False},
    # v1.8.0 (user directive): STREAM-ONLY. No catalogs — the addon now
    # supplies streams for whatever the user opens from their own catalogs
    # (IMDb, Trakt, ...), Torrentio-style. Less platform volume at boot
    # (no six-catalog prewarm) = less IP flagging = faster stream builds.
    "catalogs": [],
    # v1.9.11: subtitles declared as a resource (Nuvio-style players
    # fetch /subtitles/... instead of reading stream-embedded subs).
    "resources": ["stream", "subtitles"],
}

# --------------------------------------------------------------------------
# 10. landing page — install / usage
# --------------------------------------------------------------------------
_LANDING_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MovieBox — Stremio Addon</title>
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
  <img class="logo" src="/logo.png" alt="MovieBox"
       onerror="this.style.display='none'">
  <h1>MOVIE <span>BOX</span></h1>
  <div class="tag">netnaija.film + movieboxonline.net &mdash; movies, series &amp; anime<br>
  in up to <b style="color:#fff">1080p</b> with multi-language dubs (Hindi, English, Tamil, Telugu, Bengali&hellip;)</div>
  <a class="install" id="install" href="#">⬇ Install in Stremio</a>
  <div class="note">works on Stremio desktop, Android, Android TV &amp; Firestick</div>
</header>

<div class="grid">
  <div class="card"><h3>⚡ Stream-only</h3>
    <p>Install it next to any catalog addon (IMDb, Trakt&hellip;) and open any
    movie or series &mdash; direct CDN streams appear, Torrentio-style.
    No catalogs of its own (v1.8.0) = less platform traffic = faster
    stream builds.</p></div>
  <div class="card"><h3>🗣 Multi-Dub</h3>
    <p>Every title shows one card per language track. Hindi, Original, English,
    Tamil, Telugu, Bengali, Spanish, Portuguese&hellip; whatever the platform hosts.</p></div>
  <div class="card"><h3>⚡ CDN-Direct, Zero Proxy</h3>
    <p><span class="b">Strictly zero bandwidth</span>: this server serves
    nothing but tiny JSON. Manifests, subtitles and all media flow
    <span class="b">straight from the CDN to your player</span>.</p></div>
  <div class="card"><h3>📅 Always Fresh</h3>
    <p>Stream lists are cached and replay in ~0.3s; slow builds answer
    honestly and finish in the background for the retry.</p></div>
</div>

<div class="steps">
  <h2>How to install</h2>
  <ol>
    <li>Click the <b style="color:#fff">Install in Stremio</b> button above.</li>
    <li>Stremio opens &rarr; press <b style="color:#fff">Install</b>.</li>
    <li>Find any movie/series &mdash; MovieBox streams appear with a
        <b style="color:#fff">▣ MovieBox</b> tag and language name.</li>
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

# --------------------------------------------------------------------------
# 11. http server — routes, gzip, CORS, cache headers
# --------------------------------------------------------------------------
_REQLOG = []          # v1.6.11: last ~300 served requests (player diagnostics)

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MovieBox/" + VERSION

    def log_message(self, fmt, *args):
        print("[%s] %s" % (time.strftime("%H:%M:%S"), fmt % args), flush=True)

    def _log_req(self, code, nbytes):
        """Record served requests so player-side playback failures can be
        diagnosed from the outside (/debug/reqlog). v1.7.7: /stream entries
        carry the phase breakdown (meta/search/match/rescue/dubs+play/
        resolve, in ms) so slow builds can be attributed from prod."""
        try:
            p = (self.path or "")[:160]
            if not p.startswith(("/health", "/debug")):
                ent = {"t": time.strftime("%H:%M:%S"), "path": p,
                       "code": code,
                       "ms": int((time.time() - getattr(self, "_t0", time.time())) * 1000),
                       "ua": (self.headers.get("User-Agent") or "")[:70],
                       "bytes": nbytes}
                if p.startswith("/stream/"):
                    ent["phases"] = " | ".join(getattr(_PHASES, "rec", None) or [])
                _REQLOG.append(ent)
                if len(_REQLOG) > 400:
                    del _REQLOG[:200]
        except Exception:
            pass

    def _send(self, code, body, ctype="application/json", extra=None):
        """JSON-only responses; gzip when the client allows (keeps Render's
        free-plan egress tiny: stream lists shrink ~5-10x)."""
        if isinstance(body, str):
            body = body.encode()
        self._log_req(code, len(body))
        if len(body) > 512 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
                gz.write(body)
            packed = buf.getvalue()
            if len(packed) < len(body):
                body = packed
                extra = dict(extra or {})
                extra["Content-Encoding"] = "gzip"
                extra["Vary"] = "Accept-Encoding"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if ctype.startswith("text/vtt"):
            cache = "public, max-age=3600"          # subs are immutable
        elif ctype == "application/json":
            cache = "no-store"                      # fresh stream results
        elif "mpegurl" in ctype:
            # v1.9.5: VOD playlists are immutable for the signature's life
            # (hours) and rebuilt statelessly on miss — cacheable for 30min
            cache = "public, max-age=1800"
        else:
            cache = "public, max-age=300"           # landing / misc
        self.send_header("Cache-Control", cache)
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

    def _host_base(self):
        """Public base of THIS server, as the requesting client sees it."""
        host = self.headers.get("Host") or ""
        if host:
            return "https://" + host
        return PUBLIC_URL or _KEEPALIVE_URL or "http://localhost:%d" % PORT

    def do_GET(self):
        self._t0 = time.time()
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
                "platform_circuit": ("cooling_down" if not _plat_ok() else "closed"),
                "platform_proxy": ("pool(%d free%s)" % (len(_FREE_POOL[0]),
                                 ("+%d env" % len(_PROXY_URLS)) if _PROXY_URLS else "")
                                 if _pool_all() else bool(_PLAT_PROXIES)),
                "free_pool": len(_FREE_POOL[0]),
                "exit_tokens": len(_EXIT_TOKENS),
                "direct_auth_flag_s": round(max(0.0, _DIRECT_AUTH_FLAG[0] - time.time())),
                "scrape_do": bool(_SCRAPEDO_TOKEN),
                "scrape_do_credits": _SD_CREDITS[0],
                "video_proxy": False, "web_mp4": WEB_MP4_ON, "egress": "text-only (json/playlists/manifests/subtitles, gzip)",
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

        # v1.7.5: Stremio's canonical search URL is
        # /catalog/{type}/{id}/search={query}.json (path segment!) — the
        # router only knew the ?search= query form and 404'd every
        # in-app catalog search (seen in /debug/reqlog).
        m = re.match(r"^/catalog/([a-z]+)/([a-z0-9-]+?)(?:/search=([^/]*))?\.json$",
                     path)
        if m:
            ctype, cid = m.group(1), m.group(2)
            search = m.group(3)
            if search is None:
                search = (q.get("search") or [""])[0]
            search = (search or "").strip()
            skip = int((q.get("skip") or ["0"])[0] or 0)
            if search:
                return self._send(200, json.dumps(search_catalog(ctype, search)))
            return self._send(200, json.dumps(get_catalog(ctype, cid, skip)))

        if path == "/debug/phases":
            k = (q.get("k") or [""])[0] if q else ""
            if k != "mbx-dbg-7f3a":
                return self._send(404, json.dumps({"error": "not found"}))
            return self._send(200, json.dumps({
                "version": VERSION,
                "note": "last stream builds, newest last; wall=hit means the "
                        "player got the honest slow-retry answer at "
                        "%ds while the build finished in the background"
                        % int(_STREAM_WALL),
                "pool": {"size": len(_pool_all()),
                         "healthy": len(_pool_healthy()),
                         "tokens": len(_EXIT_TOKENS),
                         "direct_auth_flag_s":
                             round(max(0.0, _DIRECT_AUTH_FLAG[0] - time.time())),
                         "cb_quiet_s":
                             round(max(0.0, _PLAT_CB_UNTIL - time.time()))},
                "phases": list(_PHASE_RING[-12:])}))
        if path == "/debug/reqlog":
            k = (q.get("k") or [""])[0] if q else ""
            if k != "mbx-dbg-7f3a":
                return self._send(404, json.dumps({"error": "not found"}))
            return self._send(200, json.dumps({"version": VERSION,
                                               "free_pool": len(_FREE_POOL[0]),
                                               "entries": _REQLOG[-120:]}))

        if path == "/debug/search":
            k = (q.get("k") or [""])[0] if q else ""
            if k != "mbx-dbg-7f3a":
                return self._send(404, json.dumps({"error": "not found"}))
            kw = ((q.get("kw") or ["moana"])[0] or "moana")[:40]
            out = {"version": VERSION, "kw": kw,
                   "token_head": (_AUTH_TOKEN or "")[:14],
                   "direct_auth_flag_s": round(max(0.0, _DIRECT_AUTH_FLAG[0] - time.time())),
                   "exit_tokens": len(_EXIT_TOKENS),
                   "pool": len(_pool_all()),
                   "direct": None, "exits": []}

            def _dbg_search(proxies, token):
                sp = "/wefeed-mobile-bff/subject-api/search/v2"
                sbody = json.dumps({"keyword": kw, "page": 1, "perPage": 20,
                                    "subjectType": 1, "tabId": "All"})
                surl = API_HOSTS[0] + sp
                ts = int(time.time() * 1000)
                hd = {"User-Agent": UA_APP, "Accept": "application/json",
                      "Content-Type": "application/json",
                      "X-Client-Token": _x_client_token(ts),
                      "x-tr-signature": _x_tr_signature("POST", surl, sbody, ts),
                      "X-Client-Info": _client_info(), "X-Client-Status": "0",
                      "X-M-Version": "11.7.0"}
                if token:
                    hd["Authorization"] = "Bearer " + token
                t0 = time.time()
                try:
                    r = requests.post(surl, headers=hd, data=sbody, timeout=10,
                                      proxies=proxies or {})
                    res = {"s": r.status_code,
                           "ms": int((time.time() - t0) * 1000),
                           "xuser": bool(r.headers.get("x-user"))}
                    try:
                        d = r.json()
                        res["code"] = d.get("code")
                        res["msg"] = str(d.get("message") or d.get("reason") or "")[:50]
                        rr = ((d.get("data") or {}).get("results") or [{}])[0]
                        res["hits"] = len(rr.get("subjects") or [])
                    except Exception:
                        res["body"] = (r.text or "")[:60]
                    return res
                except Exception as e:
                    return {"exc": type(e).__name__,
                            "ms": int((time.time() - t0) * 1000)}

            def _dbg_exit_token(u):
                turl = API_HOSTS[0] + "/wefeed-mobile-bff/tab-operating?page=1&tabId=0&version="
                ts = int(time.time() * 1000)
                hd = {"User-Agent": UA_APP, "Accept": "application/json",
                      "Content-Type": "application/json",
                      "X-Client-Token": _x_client_token(ts),
                      "x-tr-signature": _x_tr_signature("GET", turl, None, ts),
                      "X-Client-Info": _client_info(), "X-Client-Status": "0",
                      "X-M-Version": "11.7.0"}
                try:
                    r = requests.get(turl, timeout=8, proxies={"http": u, "https": u},
                                     headers=hd)
                    xu = r.headers.get("x-user", "")
                    tok = ""
                    if xu:
                        try:
                            tok = json.loads(xu).get("token") or ""
                        except Exception:
                            pass
                    return {"tab_s": r.status_code, "xuser": bool(tok)}, tok
                except Exception as e:
                    return {"tab_exc": type(e).__name__}, ""

            out["direct"] = _dbg_search(None, _AUTH_TOKEN)
            for u in _pool_all()[:4]:
                diag, tok = _dbg_exit_token(u)
                own = _dbg_search({"http": u, "https": u}, tok) if tok \
                    else {"note": "no x-user through this exit"}
                glob = _dbg_search({"http": u, "https": u}, _AUTH_TOKEN)
                out["exits"].append({"exit": u, "tab": diag,
                                     "own_token": own, "global_token": glob})
            return self._send(200, json.dumps(out))

        if path == "/debug/ping":
            k = (q.get("k") or [""])[0] if q else ""
            if k != "mbx-dbg-7f3a":
                return self._send(404, json.dumps({"error": "not found"}))
            out = {"version": VERSION, "token": bool(_AUTH_TOKEN),
                   "token_head": (_AUTH_TOKEN or "")[:16]}
            # per-host tab-operating: status + x-user presence (with & without XFF)
            hosts = []
            for base in API_HOSTS:
                for xff in (False, True):
                    url = base + "/wefeed-mobile-bff/tab-operating?page=1&tabId=0&version="
                    ts = int(time.time() * 1000)
                    hd = {"User-Agent": UA_APP, "Accept": "application/json",
                          "Content-Type": "application/json",
                          "X-Client-Token": _x_client_token(ts),
                          "x-tr-signature": _x_tr_signature("GET", url, None, ts),
                          "X-Client-Info": json.dumps(_client_info()),
                          "X-Client-Status": "0", "X-M-Version": "11.7.0"}
                    if xff:
                        hd["X-Forwarded-For"] = "103.241.224.%d" % random.randint(1, 254)
                    try:
                        r = requests.get(url, headers=hd, timeout=8)
                        hosts.append({"h": base.split("//")[1][:14], "xff": xff,
                                      "s": r.status_code,
                                      "tok": bool(r.headers.get("x-user")),
                                      "b": r.text[:40]})
                    except Exception as e:
                        hosts.append({"h": base.split("//")[1][:14], "xff": xff,
                                      "s": type(e).__name__})
            out["tab_hosts"] = hosts
            t0 = time.time()
            try:
                subs = search_subjects("Our Sticky Love", 2)
                out["search_subjects"] = len(subs)
            except Exception as e:
                out["search_subjects"] = "EXC " + str(e)[:80]
            out["search_s"] = round(time.time() - t0, 2)
            return self._send(200, json.dumps(out))

        # v1.9.11: subtitles-resource route — reuses the cached card
        # build, so a cache hit costs nothing extra.
        m = re.match(r"^/subtitles/([a-z]+)/(tt\d+|[a-z0-9]+)(?::(\d+):(\d+))?\.json$", path)
        if m:
            ctype, oid = m.group(1), m.group(2)
            if ctype not in ("movie", "series"):
                return self._send(400, json.dumps({"error": "bad type"}))
            se = int(m.group(3) or 1)
            ep = int(m.group(4) or 1)
            res = build_streams(ctype, oid, se, ep) if oid.startswith("tt") \
                else {"streams": []}
            subs, seen = [], set()
            for c in (res.get("streams") or []):
                for s in (c.get("subtitles") or []):
                    u = s.get("url")
                    if u and u not in seen:
                        seen.add(u)
                        subs.append({"url": u, "lang": s.get("lang", "en"),
                                     "id": s.get("id", "mbx-en")})
            return self._send(200, json.dumps(
                {"subtitles": subs, "cacheMaxAge": 300}))

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
            # v1.9.4: HLS cards carry a relative /hls/... url — absolutize
            # against the request Host so the player can reach the master
            base = self._host_base()
            for s in res.get("streams") or []:
                if s.get("url", "").startswith("/hls/"):
                    s["url"] = base + s["url"]
            return self._send(200, json.dumps(res))

        # v1.9.4: quality-menu HLS layer — ONLY master/variant playlist
        # TEXT is served from here (gzip; stateless rebuild from cached
        # play-info/MPD keeps signatures fresh). Segments in the variant
        # playlists are absolute self-signed CloudFront URLs — zero media
        # bytes pass through this server.
        m = re.match(r"^/hls/(\d{5,25})/(\d{1,3})/(\d{1,5})/(master|v\d+|a\d+)\.m3u8$", path)
        if m:
            body = _lazy_hls(m.group(1), int(m.group(2)), int(m.group(3)), m.group(4))
            if body is None:
                return self._send(404, "#EXTM3U\n#error no stream for this entry\n",
                                  "application/vnd.apple.mpegurl")
            return self._send(200, body, "application/vnd.apple.mpegurl")

        # v1.9.0 STRICT zero-bandwidth: the /hls, /dash and /sub routes
        # were REMOVED — cards now point directly at the platform CDN
        # (DASH MPD via proxyHeaders Cookie, captions via cacdn direct
        # URLs). Nothing but tiny JSON is served from here.
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
    # v1.7.1: NEVER exit. The public URL is learned from the first real
    # request's Host header (_note_public_base -> _KEEPALIVE_URL), so the
    # free-tier dyno can't silently lose its keepalive just because
    # PUBLIC_URL was never configured — no more 50s cold boots after idle.
    while True:
        url = PUBLIC_URL or _KEEPALIVE_URL
        if url:
            try:
                requests.get(url + "/health", timeout=20)
            except Exception:
                pass
        time.sleep(240)

def _boot_prewarm():
    """Background warm-up after boot: wait for the first pool and bootstrap
    the token, so the first real /stream request never pays the cold cost.
    v1.8.0: the six-catalog prewarm is GONE (stream-only manifest) — it was
    the biggest source of platform call volume on a fresh boot, which fed
    the very IP flagging that made stream builds slow."""
    try:
        for _ in range(8):                 # up to ~80s for the first pool
            if _pool_all():
                break
            time.sleep(10)
        _bootstrap_token()
    except Exception:
        pass

def main():
    _FREE_POOL_ON[0] = True
    threading.Thread(target=_free_pool_loop, daemon=True).start()
    threading.Thread(target=_pool_train_loop, daemon=True).start()
    threading.Thread(target=_keepalive_loop, daemon=True).start()
    threading.Thread(target=_boot_prewarm, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("MovieBox %s listening on :%d (keepalive=%s)" % (VERSION, PORT, bool(PUBLIC_URL)), flush=True)
    srv.serve_forever()

if __name__ == "__main__":
    main()
