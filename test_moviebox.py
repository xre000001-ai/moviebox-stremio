#!/usr/bin/env python3
"""Unit tests for MovieBox addon. Run: python3 test_moviebox.py"""
import base64
import importlib
import json
import os
import re
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import addon

# v1.9.1: web MP4 minting does live calls — disabled for unit tests;
# the dedicated web tests below re-enable it with everything mocked.
addon.WEB_MP4_ON = False

PASS = 0
FAIL = 0

def run(test):
    global PASS, FAIL
    name = test.__name__
    try:
        test()
        PASS += 1
        print("  ok  %s" % name)
    except Exception as e:
        FAIL += 1
        import traceback
        print("FAIL  %s: %s" % (name, e))
        traceback.print_exc()

# ---------------------------------------------------------------- fixtures

MPD_FIX = """<?xml version="1.0" encoding="utf-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" profiles="urn:mpeg:dash:profile:isoff-live:2011"
 type="static" mediaPresentationDuration="PT1H0M43.6S" maxSegmentDuration="PT5.0S" minBufferTime="PT10.2S">
 <Period id="0" start="PT0.0S">
  <AdaptationSet id="0" contentType="video" startWithSAP="1" segmentAlignment="true" bitstreamSwitching="true" frameRate="24000/1001" maxWidth="1920" maxHeight="1080" par="16:9">
   <Representation id="0" mimeType="video/mp4" codecs="hev1" bandwidth="972531" width="1920" height="1080" sar="1:1">
    <SegmentTemplate timescale="1000000" duration="5000000" initialization="init-stream$RepresentationID$.m4s" media="chunk-stream$RepresentationID$-$Number%05d$.m4s" startNumber="1"></SegmentTemplate>
   </Representation>
   <Representation id="1" mimeType="video/mp4" codecs="hev1" bandwidth="550468" width="1280" height="720" sar="1:1">
    <SegmentTemplate timescale="1000000" duration="5000000" initialization="init-stream$RepresentationID$.m4s" media="chunk-stream$RepresentationID$-$Number%05d$.m4s" startNumber="1"></SegmentTemplate>
   </Representation>
   <Representation id="2" mimeType="video/mp4" codecs="hev1" bandwidth="336732" width="854" height="480" sar="1280:1281">
    <SegmentTemplate timescale="1000000" duration="5000000" initialization="init-stream$RepresentationID$.m4s" media="chunk-stream$RepresentationID$-$Number%05d$.m4s" startNumber="1"></SegmentTemplate>
   </Representation>
  </AdaptationSet>
  <AdaptationSet id="1" contentType="audio" startWithSAP="1" segmentAlignment="true" bitstreamSwitching="true" lang="hin">
   <Representation id="3" mimeType="audio/mp4" codecs="mp4a.40.2" bandwidth="128000" audioSamplingRate="48000">
    <AudioChannelConfiguration schemeIdUri="urn:mpeg:dash:23003:3:audio_channel_configuration:2011" value="2"/>
    <SegmentTemplate timescale="1000000" duration="5000000" initialization="init-stream$RepresentationID$.m4s" media="chunk-stream$RepresentationID$-$Number%05d$.m4s" startNumber="1"></SegmentTemplate>
   </Representation>
  </AdaptationSet>
 </Period>
</MPD>
"""

POLICY_JSON = json.dumps({
    "Statement": [{
        "Resource": "https://sacdn.hakunaymatata.com/dash/999888_1_1_1080_h265_299/*",
        "Condition": {"DateLessThan": {"AWS:EpochTime": 4102444800}}
    }]
})
POLICY_B64 = base64.b64encode(POLICY_JSON.encode()).decode().rstrip("=")
FAKE_COOKIE = ("CloudFront-Policy=%s;CloudFront-Signature=SIGabc123~_-; "
               "CloudFront-Key-Pair-Id=KP123" % POLICY_B64)

# REAL platform format: CloudFront policies are URL-SAFE base64 ('_' chars).
# Captured live: Squid Game Hindi S1E1 policy (standard b64decode fails on it).
URLSAFE_POLICY = ("eyJTdGF0ZW1lbnQiOlt7IlJlc291cmNlIjoiaHR0cHM6Ly9zYWNkbi5oYWt1"
                  "bmF5bWF0YXRhLmNvbS9kYXNoLzk3MzA0MTUyNTc4MzQ5NjQ4MF8xXzFfMTA4"
                  "MF9oMjY1XzI5OS8qIiwiQ29uZGl0aW9uIjp7IkRhdGVMZXNzVGhhbiI6eyJB"
                  "V1M6RXBvY2hUaW1lIjoxNzg5MTkxNDk4fX19XX0_")
URLSAFE_COOKIE = ("CloudFront-Policy=%s;CloudFront-Signature=sig;CloudFront-Key-Pair-Id=KP9"
                  % URLSAFE_POLICY)

SUBJ_SQUID_HI = {"subjectId": "973041525783496480", "subjectType": 2,
                 "title": "Squid Game [Hindi] S1", "releaseDate": "2021-09-17",
                 "corner": "Hindi"}
SUBJ_SQUID_ORIG = {"subjectId": "3089349649006742360", "subjectType": 2,
                   "title": "Squid Game", "releaseDate": "2021-09-17", "corner": ""}
SUBJ_SQUID_S3 = {"subjectId": "3089349649006742360", "subjectType": 2,
                 "title": "Squid Game S3", "releaseDate": "2025-06-27", "corner": ""}
SUBJ_3IDIOTS = {"subjectId": "977486567826752424", "subjectType": 1,
                "title": "Inception [Hindi]", "releaseDate": "2010-09-01", "corner": "Hindi"}
SUBJ_INCEPTION = {"subjectId": "6391474290696802080", "subjectType": 1,
                  "title": "Inception", "releaseDate": "2010-07-16", "corner": ""}

PLAY_INFO_FIX = {
    "streams": [{
        "format": "MP4", "id": "61", "resolutions": "1080,720,480",
        "size": "905665380", "duration": 3643, "codecName": "hevc",
        "url": "https://macdn.aoneroom.com/other/x.mp4",
        "signCookie": FAKE_COOKIE, "idType": ""
    }],
    "title": "Red Light, Green Light", "displayResolutions": "1080,720,480",
}

NUXT_PAYLOAD = [
    "app", 1,
    {"subjectId": 3, "title": 4, "subjectType": 5, "releaseDate": 6,
     "cover": {"url": 7}, "imdbRatingValue": 8, "genre": 9},
    "1111774575987245152", "Mayday", 1, "2026-01-15",
    "https://pbcdnw.aoneroom.com/image/x.jpg", "6.6", "Action,Thriller"
]

# cover stored as an INDEX to a nested dict (real netnaija payload shape)
NUXT_PAYLOAD_IDX_COVER = [
    "app", 1,
    {"subjectId": 3, "title": 4, "subjectType": 5, "releaseDate": 6, "cover": 7},
    "1111774575987245152", "Mayday", 1, "2026-01-15",
    {"url": 8, "width": 9, "height": 10},
    "https://pbcdnw.aoneroom.com/image/cover.jpg", 535, 755
]

# ------------------------------------------------------------------ tests

def test_x_client_token():
    tok = addon._x_client_token(1789183000000)
    ts, h = tok.split(",")
    assert ts == "1789183000000"
    expect = __import__("hashlib").md5("000003819871"[::-1].encode()).hexdigest() if False else None
    # reversed ts:
    rev = "1789183000000"[::-1]
    import hashlib
    assert h == hashlib.md5(rev.encode()).hexdigest()

def test_sorted_query():
    url = "https://api/x/path?b=2&a=1&c="
    assert addon._sorted_query(url) == "a=1&b=2&c="

def test_tr_signature_format():
    sig = addon._x_tr_signature("GET", "https://api6.aoneroom.com/wefeed-mobile-bff/subject-api/play-info/v2?subjectId=1&host=h", None, 1789183000000)
    ts, ver, b64 = sig.split("|")
    assert ts == "1789183000000" and ver == "2"
    assert len(base64.b64decode(b64 + "==")) == 16  # HMAC-MD5 digest

def test_tr_signature_deterministic():
    a = addon._x_tr_signature("POST", "https://api/x?a=1", '{"k":1}', 42)
    b = addon._x_tr_signature("POST", "https://api/x?a=1", '{"k":1}', 42)
    assert a == b

def test_clean_title():
    assert addon.clean_title("Squid Game [Hindi] S3") == "Squid Game"
    assert addon.clean_title("Movie (2019) (Hindi)") == "Movie"
    assert addon.clean_title("  Plain   Title  ") == "Plain Title"
    assert addon.clean_title("Attack on Titan S1-S4") == "Attack on Titan"
    assert addon.clean_title("Attack on Titan [Hindi] S1-S6") == "Attack on Titan"
    assert addon.clean_title("Some Show Season 2") == "Some Show"
    assert addon.clean_title("Lucifer S1 - S5") == "Lucifer"

def test_year_of():
    assert addon._year_of("2021-09-17") == "2021"
    assert addon._year_of("") == ""

def test_match_movie_year():
    subs = [SUBJ_INCEPTION, SUBJ_3IDIOTS]
    got = addon.match_subjects(subs, "Inception", "2010", 1)
    ids = [s["subjectId"] for s, _ in got]
    assert "6391474290696802080" in ids and "977486567826752424" in ids

def test_match_movie_year_reject():
    # two same-title candidates BOTH outside ±1 -> ambiguous, rejected
    a = dict(SUBJ_INCEPTION)
    b = {"subjectId": "99", "subjectType": 1, "title": "Inception",
         "releaseDate": "2003-01-01"}
    assert addon.match_subjects([a, b], "Inception", "1999", 1) == []

def test_match_single_exact_wrong_year_trusted():
    # platform upload dates are often wrong; a single exact-title match is
    # trusted even when its year is off
    odd = {"subjectId": "77", "subjectType": 1, "title": "Inception",
           "releaseDate": "2016-08-01", "corner": ""}
    got = addon.match_subjects([odd], "Inception", "2010", 1)
    assert [s["subjectId"] for s, _ in got] == ["77"]

def test_match_multiple_wrong_year_rejected():
    odd1 = {"subjectId": "77", "subjectType": 1, "title": "Inception",
            "releaseDate": "2016-08-01"}
    odd2 = {"subjectId": "78", "subjectType": 1, "title": "Inception",
            "releaseDate": "2003-01-01"}
    assert addon.match_subjects([odd1, odd2], "Inception", "2010", 1) == []

def test_match_alias_subset_mugen_train():
    # imdb: "Demon Slayer: Kimetsu no Yaiba - The Movie: Mugen Train"
    # platform title is shortened -> every platform token inside imdb title
    want = "Demon Slayer: Kimetsu no Yaiba - The Movie: Mugen Train"
    subs = [
        {"subjectId": "m1", "subjectType": 1,
         "title": "Demon Slayer the Movie: Mugen Train", "releaseDate": "2021-06-01"},
        {"subjectId": "m2", "subjectType": 1,
         "title": "Demon Slayer the Movie: Mugen Train [English]",
         "releaseDate": "2021-06-01", "corner": "English"},
        {"subjectId": "bt", "subjectType": 1, "title": "Bullet Train",
         "releaseDate": "2022-08-01"},  # shares "train" only -> rejected
    ]
    got = addon.match_subjects(subs, want, "2020", 1)
    ids = [s["subjectId"] for s, _ in got]
    assert "m1" in ids and "m2" in ids and "bt" not in ids

def test_match_alias_rejects_two_token_subset():
    # a 2-token platform title must not fuzzy-match a longer different movie
    subs = [{"subjectId": "sm", "subjectType": 1, "title": "Spider-Man",
             "releaseDate": "2002-05-01"}]
    got = addon.match_subjects(subs, "Spider-Man: No Way Home", "2021", 1)
    assert got == []

def test_match_alias_prefers_longer_candidate():
    want = "Demon Slayer: Kimetsu no Yaiba - The Movie: Mugen Train"
    subs = [
        {"subjectId": "short", "subjectType": 1, "title": "Demon Slayer Mugen Train",
         "releaseDate": "2021-01-01"},
        {"subjectId": "long", "subjectType": 1,
         "title": "Demon Slayer the Movie Mugen Train", "releaseDate": "2021-01-01"},
    ]
    got = addon.match_subjects(subs, want, "2020", 1)
    assert got[0][0]["subjectId"] == "long"

def test_imdb_suggest_id_fallback():
    with mock.patch.object(addon.requests, "get") as g:
        g.return_value.status_code = 200
        g.return_value.json.return_value = {"d": [
            {"id": "tt11032374", "l": "Demon Slayer: Kimetsu no Yaiba - The Movie: Mugen Train", "y": 2020},
            {"id": "tt9999999", "l": "other", "y": 1999},
        ]}
        val = addon._imdb_suggest_id("tt11032374")
    assert val == {"name": "Demon Slayer: Kimetsu no Yaiba - The Movie: Mugen Train",
                   "year": "2020"}

def test_match_series_season():
    subs = [SUBJ_SQUID_ORIG, SUBJ_SQUID_HI, SUBJ_SQUID_S3]
    got = addon.match_subjects(subs, "Squid Game", "", 2, season=3)
    labels = [l for _, l in got]
    assert "Hindi" in labels and "Original" in labels

def test_match_excludes_other_titles():
    subs = [SUBJ_INCEPTION, {"subjectId": "1", "subjectType": 1, "title": "Shutter Island", "releaseDate": "2010-02-19"}]
    got = addon.match_subjects(subs, "Inception", "2010", 1)
    assert all("Inception" in s["title"] for s, _ in got)

def test_match_rejects_wrong_subject_type():
    # a MOVIE subject named like the series must not match a series request
    movie_named_same = {"subjectId": "9", "subjectType": 1, "title": "Attack on Titan",
                        "releaseDate": "2023-01-02"}
    subs = [movie_named_same, {"subjectId": "8", "subjectType": 2,
                               "title": "Attack on Titan S1-S4", "releaseDate": "2013-09-28"}]
    got = addon.match_subjects(subs, "Attack on Titan", "2013", 2, season=1)
    assert [s["subjectId"] for s, _ in got] == ["8"]

def test_cf_parts():
    cf = addon._cf_parts(FAKE_COOKIE)
    assert cf["CloudFront-Key-Pair-Id"] == "KP123"
    assert cf["CloudFront-Signature"] == "SIGabc123~_-"
    assert cf["CloudFront-Policy"] == POLICY_B64

def test_cf_parts_bad():
    assert addon._cf_parts("garbage") is None

def test_dash_base():
    assert addon._dash_base(POLICY_B64) == "https://sacdn.hakunaymatata.com/dash/999888_1_1_1080_h265_299"

def test_dash_base_urlsafe_policy():
    # real platform cookies: URL-safe base64 with '_' — old parser failed here,
    # dropping ~half of the dub streams
    assert addon._dash_base(URLSAFE_POLICY) == \
        "https://sacdn.hakunaymatata.com/dash/973041525783496480_1_1_1080_h265_299"

def test_cf_parts_urlsafe():
    cf = addon._cf_parts(URLSAFE_COOKIE)
    assert cf is not None and cf["CloudFront-Key-Pair-Id"] == "KP9"
    assert cf["CloudFront-Policy"] == URLSAFE_POLICY

def test_b64d_handles_both_alphabets():
    assert addon._b64d("aGk=") == b"hi"                       # standard, padded
    assert addon._b64d("aGk") == b"hi"                        # standard, unpadded
    assert addon._b64d(base64.urlsafe_b64encode(b"hi-/").decode().rstrip("=")) == b"hi-/"

def test_parse_mpd():
    info = addon._parse_mpd(MPD_FIX)
    assert [v["height"] for v in info["video"]] == [1080, 720, 480]
    assert info["audio"][0]["lang"] == "hin"
    assert abs(info["dur"] - 3643.6) < 0.01
    assert abs(info["seg_dur"] - 5.0) < 0.001

def test_parse_mpd_empty_audio():
    xml = MPD_FIX.replace('contentType="audio"', 'contentType="video"')
    info = addon._parse_mpd(xml)
    assert info["audio"] == [] and len(info["video"]) == 4

def test_res_label():
    assert addon._res_label([1080, 720, 480]) == "MULTI"
    assert addon._res_label([480]) == "480"
    assert addon._res_label([]) == "HD"









def test_cache_put_get():
    store = {}
    addon._cache_put(store, "k", "v", 60)
    hit, val = addon._cache_get(store, "k")
    assert hit and val == "v"

def test_cache_expiry():
    store = {}
    addon._cache_put(store, "k", "v", -1)
    hit, _ = addon._cache_get(store, "k")
    assert not hit

def test_nuxt_deref():
    subs = addon._deref_all(NUXT_PAYLOAD)
    assert len(subs) == 1
    s = subs[0]
    assert s["subjectId"] == "1111774575987245152"
    assert s["title"] == "Mayday"
    assert s["cover"]["url"].startswith("https://pbcdnw")
    assert s["imdbRatingValue"] == "6.6"

def test_nuxt_deref_index_cover():
    # real payload: cover is an int index pointing to a nested dict
    subs = addon._deref_all(NUXT_PAYLOAD_IDX_COVER)
    assert len(subs) == 1
    s = subs[0]
    assert s["cover"]["url"] == "https://pbcdnw.aoneroom.com/image/cover.jpg"
    assert s["cover"]["width"] == 535 and s["cover"]["height"] == 755
    assert s["subjectId"] == "1111774575987245152"

def test_search_subjects_v2_primary():
    with mock.patch.object(addon, "api_call") as ac:
        ac.return_value = {"results": [{"subjects": [SUBJ_INCEPTION]}]}
        subs = addon.search_subjects("Inception", 1)
    assert subs == [SUBJ_INCEPTION]
    assert ac.call_count == 1
    assert "search/v2" in ac.call_args[0][1]

def test_search_subjects_v1_fallback():
    with mock.patch.object(addon, "api_call") as ac:
        ac.side_effect = [
            {"results": []},                      # v2 empty
            {"items": [SUBJ_INCEPTION, SUBJ_3IDIOTS]},  # v1 works
        ]
        subs = addon.search_subjects("john wick", 1)
    assert subs == [SUBJ_INCEPTION, SUBJ_3IDIOTS]
    assert ac.call_args_list[1][0][1].endswith("/subject-api/search")

def test_search_subjects_single_word_last_resort():
    with mock.patch.object(addon, "api_call") as ac:
        ac.side_effect = [
            {"results": []},                  # v2 empty
            {"items": []},                    # v1 empty
            {"items": [SUBJ_INCEPTION]},      # v1 single word
        ]
        subs = addon.search_subjects("john wick", 1)
    assert subs == [SUBJ_INCEPTION]
    body = json.loads(ac.call_args_list[2][0][2])
    assert body["keyword"] == "john"  # longest word

def test_search_subjects_filters_junk_types():
    junk = {"subjectId": "9", "subjectType": 6, "title": "MIXTAPE 2024", "releaseDate": "2024-01-01"}
    with mock.patch.object(addon, "api_call") as ac:
        ac.return_value = {"results": [{"subjects": [junk, SUBJ_INCEPTION]}]}
        subs = addon.search_subjects("Inception", 1)
    assert subs == [SUBJ_INCEPTION]

def test_search_subjects_drops_wrong_type_junk():
    # EPG junk ('Episode #1.347', series-type) must be dropped for a MOVIE
    # query and count as "no results" so the fallback chain can kick in
    epg = {"subjectId": "e1", "subjectType": 2, "title": "Episode #1.347"}
    with mock.patch.object(addon, "api_call") as ac:
        ac.side_effect = [
            {"results": [{"subjects": [epg]}]},        # v2: junk only
            {"items": [epg]},                          # v1: junk only
            {"items": [SUBJ_INCEPTION, epg]},          # v1 single word
        ]
        subs = addon.search_subjects("Avengers Endgame", 1)
    assert subs == [SUBJ_INCEPTION]
    body = json.loads(ac.call_args_list[2][0][2])
    assert body["keyword"] in ("Avengers", "Endgame")
    assert body["subjectType"] == 1 and "tabId" not in body

def test_get_catalog_dedupes_imdb():
    m1 = {"subjectId": "a", "subjectType": 1, "title": "John Wick", "releaseDate": "2014-10-24",
          "cover": {"url": "https://x/1.jpg"}}
    m2 = {"subjectId": "b", "subjectType": 1, "title": "John Wick [Hindi]", "releaseDate": "2014-11-01",
          "cover": {"url": "https://x/2.jpg"}}
    m3 = {"subjectId": "c", "subjectType": 1, "title": "Nowhere", "releaseDate": "2023-09-11",
          "cover": {"url": "https://x/3.jpg"}}
    with mock.patch.object(addon, "scrape_subjects", return_value=[m1, m2, m3]), \
         mock.patch.object(addon, "resolve_imdb",
                           side_effect=lambda t, y, c: {"John Wick": "tt2911666", "Nowhere": "tt23178568"}.get(t)):
        res = addon.get_catalog("movie", "netnaija-movies", 0)
    assert [m["id"] for m in res["metas"]] == ["tt2911666", "tt23178568"]

def test_search_catalog_dedupes_imdb():
    with mock.patch.object(addon, "search_subjects",
                           return_value=[SUBJ_INCEPTION, SUBJ_3IDIOTS, SUBJ_SQUID_ORIG]), \
         mock.patch.object(addon, "resolve_imdb",
                           side_effect=lambda t, y, c: {"Inception": "tt1375666", "Squid Game": "tt10919420"}.get(t)):
        res = addon.search_catalog("movie", "inception")
    ids = [m["id"] for m in res["metas"]]
    assert ids == ["tt1375666", "tt10919420"] and len(ids) == len(set(ids))

def test_catalog_poster_from_cover():
    s = {"subjectId": "1", "subjectType": 1, "title": "The Old Guard",
         "releaseDate": "2020-07-10",
         "cover": {"url": "https://pbcdnw.aoneroom.com/p.jpg"}}
    with mock.patch.object(addon, "resolve_imdb", return_value="tt3675440"):
        m = addon.subject_to_meta(s, "movie")
    assert m["poster"] == "https://pbcdnw.aoneroom.com/p.jpg"

def test_imdb_suggest_match():
    with mock.patch("requests.get") as g:
        g.return_value.status_code = 200
        g.return_value.json.return_value = {"d": [
            {"l": "Mayday", "y": 2026, "qid": "movie", "id": "tt29000001"},
            {"l": "Mayday", "y": 2019, "qid": "movie", "id": "tt99999999"},
        ]}
        assert addon._imdb_suggest("Mayday", "2026", "movie") == "tt29000001"

def test_imdb_suggest_year_filter():
    with mock.patch("requests.get") as g:
        g.return_value.status_code = 200
        g.return_value.json.return_value = {"d": [
            {"l": "Mayday", "y": 2019, "qid": "movie", "id": "tt99999999"}]}
        assert addon._imdb_suggest("Mayday", "2026", "movie") is None

def test_imdb_suggest_series_qid():
    with mock.patch("requests.get") as g:
        g.return_value.status_code = 200
        g.return_value.json.return_value = {"d": [
            {"l": "Lucifer", "y": 2016, "qid": "tvSeries", "id": "tt4052886"}]}
        assert addon._imdb_suggest("Lucifer", "2016", "series") == "tt4052886"
        assert addon._imdb_suggest("Lucifer", "2016", "movie") is None

def test_resolve_imdb_caching():
    addon._IMDB_CACHE.clear()
    with mock.patch.object(addon, "_imdb_suggest", return_value="tt1234567"), \
         mock.patch.object(addon, "_tmdb_find", return_value=None):
        assert addon.resolve_imdb("Foo", "2020", "movie") == "tt1234567"
    with mock.patch.object(addon, "_imdb_suggest", return_value=None), \
         mock.patch.object(addon, "_tmdb_find", return_value=None):
        # cached hit
        assert addon.resolve_imdb("Foo", "2020", "movie") == "tt1234567"

def test_subject_to_meta():
    s = {"subjectId": "1", "subjectType": 1, "title": "The Old Guard [Hindi]",
         "releaseDate": "2020-07-10",
         "cover": {"url": "https://pbcdnw.aoneroom.com/x.jpg"},
         "imdbRatingValue": "6.7", "genre": "Action,Adventure"}
    with mock.patch.object(addon, "resolve_imdb", return_value="tt3675440"):
        m = addon.subject_to_meta(s, "movie")
    assert m["id"] == "tt3675440" and m["name"] == "The Old Guard"
    assert m["poster"].startswith("https://pbcdnw") and m["releaseInfo"] == "2020"
    assert m["imdbRating"] == "6.7" and "Action" in m["genres"]

def test_subject_to_meta_no_imdb():
    s = {"subjectId": "1", "subjectType": 1, "title": "Unknown Film Xyzzy",
         "releaseDate": "2026-01-01"}
    with mock.patch.object(addon, "resolve_imdb", return_value=None):
        assert addon.subject_to_meta(s, "movie") is None

def test_get_catalog_filters_type_and_slices():
    movie = {"subjectId": "m1", "subjectType": 1, "title": "Movie A", "releaseDate": "2020-01-01"}
    junk = {"subjectId": "j1", "subjectType": 6, "title": "MIXTAPE", "releaseDate": "2022-01-01"}
    ser = {"subjectId": "s1", "subjectType": 2, "title": "Series B", "releaseDate": "2021-01-01"}
    with mock.patch.object(addon, "scrape_subjects", return_value=[movie, junk, ser]), \
         mock.patch.object(addon, "resolve_imdb",
                           side_effect=lambda t, y, c: "tt0000001" if t == "Movie A" else None):
        res = addon.get_catalog("movie", "netnaija-movies", 0)
    assert [m["id"] for m in res["metas"]] == ["tt0000001"]

def test_get_catalog_bad_id():
    assert addon.get_catalog("movie", "unknown-what", 0) == {"metas": []}

def test_build_streams_series_happy_path():
    addon._MPD_CACHE.clear()
    addon._STREAM_CACHE.clear(); addon._STREAM_STALE.clear(); addon._PLAY_CACHE.clear(); addon._DUB_CACHE.clear(); addon._SEARCH_CACHE.clear()
    dubs = [{"subjectId": "973041525783496480", "lanName": "Hindi dub"},
            {"subjectId": "3089349649006742360", "lanName": "Original"}]
    with mock.patch.object(addon, "_meta_any",
                           return_value={"name": "Squid Game", "year": "2021"}), \
         mock.patch.object(addon, "search_subjects",
                           return_value=[SUBJ_SQUID_ORIG, SUBJ_SQUID_HI]), \
         mock.patch.object(addon, "subject_dubs", return_value=dubs), \
         mock.patch.object(addon, "play_info", return_value=PLAY_INFO_FIX), \
         mock.patch.object(addon, "fetch_captions", return_value=[]), \
         mock.patch.object(addon, "_safe_build"), \
         mock.patch.object(addon, "_spawn_warm"), \
         mock.patch.object(addon.requests, "get") as g:
        r = mock.Mock(status_code=200, content=b"<MPD" + b"x" * 50)
        r.text = MPD_FIX
        g.return_value = r
        res = addon.build_streams("series", "tt10919420", 1, 1)
    assert len(res["streams"]) >= 2
    s0 = res["streams"][0]
    # multi-line card: bold "title (dub)" name + 3 description lines
    assert "Squid Game (" in s0["name"]      # v1.9.7 ♧/✹ format
    assert s0["description"].count("\n") == 4    # v1.9.8 5-line card
    assert "1080p" in s0["name"]                # v1.9.7 ♧ MAX quality
    assert "–" not in s0["description"].split("\n")[0]   # no more 480p–1080p range
    assert "HEVC" in s0["description"] and "863.7 MB" in s0["description"]
    assert "◫ S01 E01" in s0["description"] and "⌗ MovieBox" in s0["description"]
    assert "NO SUB" in s0["description"]          # captions mocked empty
    # v1.9.4 quality-menu HLS card: relative /hls/... master url (the
    # /stream route absolutizes it against the request Host). No
    # proxyHeaders — variant playlists carry self-signed CloudFront
    # segment URLs, so the player needs no headers at all.
    assert s0["url"].startswith("/hls/") and s0["url"].endswith("/master.m3u8")
    assert s0["behaviorHints"]["notWebReady"] is False
    assert "proxyHeaders" not in s0["behaviorHints"]
    assert s0["bingeGroup"].startswith("mbx|Squid Game")
    # the MPD IS fetched at card time now — once per (sid,se,ep): the
    # no-phantom verification doubles as the variant-list source
    assert g.call_count >= 1

def test_build_streams_movie_no_dubs():
    addon._MPD_CACHE.clear()
    addon._STREAM_CACHE.clear(); addon._STREAM_STALE.clear(); addon._PLAY_CACHE.clear(); addon._DUB_CACHE.clear(); addon._SEARCH_CACHE.clear()
    with mock.patch.object(addon, "_meta_any",
                           return_value={"name": "Inception", "year": "2010"}), \
         mock.patch.object(addon, "search_subjects",
                           return_value=[SUBJ_INCEPTION]), \
         mock.patch.object(addon, "subject_dubs", return_value=[]), \
         mock.patch.object(addon, "play_info", return_value=PLAY_INFO_FIX), \
         mock.patch.object(addon, "fetch_captions", return_value=[]), \
         mock.patch.object(addon, "_spawn_warm"), \
         mock.patch.object(addon.requests, "get") as g:
        r = mock.Mock(status_code=200, content=b"<MPD" + b"x" * 50)
        r.text = MPD_FIX
        g.return_value = r
        res = addon.build_streams("movie", "tt1375666", 1, 1)
    assert len(res["streams"]) == 1
    assert "(Original)" in res["streams"][0]["name"]
    assert res["streams"][0]["description"].count("\n") == 4  # v1.9.8
    assert "◴ 2010" in res["streams"][0]["description"]   # v1.9.7 ◴ year
    assert "S01E01" not in res["streams"][0]["description"]
    # v1.9.4: movies get the quality-menu HLS card too (movies use se=0/ep=0)
    assert res["streams"][0]["url"] == "/hls/%s/0/0/master.m3u8" % SUBJ_INCEPTION["subjectId"]
    assert g.call_count >= 1      # MPD verified at card time (no phantoms)

def test_build_streams_result_cached():
    addon._MPD_CACHE.clear()
    addon._STREAM_CACHE.clear(); addon._STREAM_STALE.clear(); addon._PLAY_CACHE.clear(); addon._DUB_CACHE.clear(); addon._SEARCH_CACHE.clear()
    calls = {"search": 0}
    def counting_search(kw, st):
        calls["search"] += 1
        return [SUBJ_INCEPTION]
    with mock.patch.object(addon, "_meta_any",
                           return_value={"name": "Inception", "year": "2010"}), \
         mock.patch.object(addon, "search_subjects", side_effect=counting_search), \
         mock.patch.object(addon, "subject_dubs", return_value=[]), \
         mock.patch.object(addon, "play_info", return_value=PLAY_INFO_FIX), \
         mock.patch.object(addon, "fetch_captions", return_value=[]), \
         mock.patch.object(addon.requests, "get") as g:
        r = mock.Mock(status_code=200, content=b"<MPD" + b"x" * 50)
        r.text = MPD_FIX
        g.return_value = r
        r1 = addon.build_streams("movie", "tt1375666", 1, 1)
        r2 = addon.build_streams("movie", "tt1375666", 1, 1)
    assert r1 == r2 and len(r2["streams"]) == 1
    assert calls["search"] == 1  # second call served from cache

def test_cached_play_dedupes():
    addon._PLAY_CACHE.clear()
    calls = {"n": 0}
    def fake_pi(sid, se=None, ep=None):
        calls["n"] += 1
        return PLAY_INFO_FIX
    with mock.patch.object(addon, "play_info", side_effect=fake_pi):
        a = addon._cached_play("123", 1, 2)
        b = addon._cached_play("123", 1, 2)
    assert a == b and calls["n"] == 1
    addon._PLAY_CACHE.clear()


def test_sub_playlist_404_on_unknown_lan():
    caps = [{"lan": "en", "url": "https://c/e.srt"}]
    with mock.patch.object(addon, "play_info", return_value=PLAY_INFO_FIX), \
         mock.patch.object(addon, "fetch_captions", return_value=caps):
        c = _http_get("/hls/3089349649006742360/1/1/sub-zz.m3u8")
    assert c["code"] == 404
    addon._PLAY_CACHE.clear()


def test_stream_route_accepts_encoded_colons():
    # many Stremio clients send series ids percent-encoded: tt...%3A1%3A1
    addon._STREAM_CACHE.clear(); addon._STREAM_STALE.clear(); addon._PLAY_CACHE.clear(); addon._DUB_CACHE.clear(); addon._SEARCH_CACHE.clear()
    with mock.patch.object(addon, "_meta_any",
                           return_value={"name": "Squid Game", "year": "2021"}), \
         mock.patch.object(addon, "search_subjects",
                           return_value=[SUBJ_SQUID_ORIG]), \
         mock.patch.object(addon, "subject_dubs", return_value=[]), \
         mock.patch.object(addon, "play_info", return_value=PLAY_INFO_FIX), \
         mock.patch.object(addon, "fetch_captions", return_value=[]), \
         mock.patch.object(addon, "_safe_build"), \
         mock.patch.object(addon, "_spawn_warm"), \
         mock.patch.object(addon.requests, "get"):
        for p in ["/stream/series/tt10919420%3A1%3A1.json",
                  "/stream/series/tt10919420%3a2%3a7.json",
                  "/stream/series/tt10919420:1:1.json"]:
            c = _http_get(p)
            assert c["code"] == 200, p
            assert len(json.loads(c["body"])["streams"]) >= 1, p
    addon._STREAM_CACHE.clear(); addon._STREAM_STALE.clear(); addon._PLAY_CACHE.clear()

def test_build_streams_no_match():
    addon._STREAM_CACHE.clear(); addon._STREAM_STALE.clear(); addon._PLAY_CACHE.clear(); addon._DUB_CACHE.clear(); addon._SEARCH_CACHE.clear()
    with mock.patch.object(addon, "_meta_any",
                           return_value={"name": "Zzz Nothing", "year": "1990"}), \
         mock.patch.object(addon, "search_subjects", return_value=[SUBJ_INCEPTION]):
        res = addon.build_streams("movie", "tt0000001", 1, 1)
    assert res["streams"] == []

def test_build_streams_play_info_transparent_on_none():
    addon._STREAM_CACHE.clear(); addon._STREAM_STALE.clear(); addon._PLAY_CACHE.clear(); addon._DUB_CACHE.clear(); addon._SEARCH_CACHE.clear()
    addon._MPD_CACHE.clear()
    with mock.patch.object(addon, "_meta_any",
                           return_value={"name": "Inception", "year": "2010"}), \
         mock.patch.object(addon, "search_subjects", return_value=[SUBJ_INCEPTION]), \
         mock.patch.object(addon, "subject_dubs", return_value=[]), \
         mock.patch.object(addon, "play_info", return_value=None):
        res = addon.build_streams("movie", "tt1375666", 1, 1)
    assert res["streams"] == []


def test_search_catalog_uses_platform_search():
    with mock.patch.object(addon, "search_subjects", return_value=[SUBJ_INCEPTION]), \
         mock.patch.object(addon, "resolve_imdb", return_value="tt1375666"):
        res = addon.search_catalog("movie", "inception")
    assert res["metas"][0]["id"] == "tt1375666"

def test_pretty_label():
    assert addon._pretty_label("esla") == "Spanish"
    assert addon._pretty_label("ptbr") == "Portuguese (BR)"
    assert addon._pretty_label("Hindi") == "Hindi"
    assert addon._pretty_label("") == "Dub"

def test_api_call_signs_headers():
    captured = {}
    def fake_request(method, url, **kw):
        captured.update(kw["headers"])
        captured["url"] = url
        resp = mock.Mock(status_code=200)
        resp.headers = {}
        resp.json = lambda: {"code": 0, "message": "ok", "data": {"x": 1}}
        return resp
    with mock.patch.object(addon, "_bootstrap_token"), \
         mock.patch.object(addon.requests, "request", side_effect=fake_request):
        addon._AUTH_TOKEN = "tok"
        d = addon.api_call("GET", "/wefeed-mobile-bff/tab-operating?page=1&tabId=0&version=")
    assert d == {"x": 1}
    assert captured["X-Client-Token"].count(",") == 1
    assert "|2|" in captured["x-tr-signature"]
    assert captured["User-Agent"].startswith("com.community.oneroom/")
    assert "X-M-Version" in captured
    assert captured.get("Authorization") == "Bearer tok"

def test_api_call_error_definitive():
    resp = mock.Mock(status_code=200)
    resp.headers = {}
    resp.json = lambda: {"code": 400, "reason": "PARAMS_ERROR", "message": "bad"}
    with mock.patch.object(addon, "_bootstrap_token"), \
         mock.patch.object(addon.requests, "request", return_value=resp):
        addon._AUTH_TOKEN = "tok"
        d = addon.api_call("GET", "/x")
    assert d == {"__error__": "bad"}  # message preferred over reason

def test_api_call_retry_on_500():
    calls = {"n": 0}
    def flaky(method, url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise addon.requests.ConnectionError("boom")
        resp = mock.Mock(status_code=200)
        resp.headers = {}
        resp.json = lambda: {"code": 0, "message": "ok", "data": {"ok": True}}
        return resp
    with mock.patch.object(addon, "_bootstrap_token"), \
         mock.patch.object(addon.requests, "request", side_effect=flaky):
        addon._AUTH_TOKEN = "tok"
        d = addon.api_call("GET", "/x")
    assert d == {"ok": True} and calls["n"] == 2

def test_api_call_bootstraps_when_no_token():
    addon._AUTH_TOKEN = None
    called = {}
    with mock.patch.object(addon, "_bootstrap_token", side_effect=lambda: called.update(n=1)):
        resp = mock.Mock(status_code=200)
        resp.headers = {}
        resp.json = lambda: {"code": 0, "message": "ok", "data": {}}
        with mock.patch.object(addon.requests, "request", return_value=resp):
            addon.api_call("GET", "/x")
    assert called.get("n") == 1
    addon._AUTH_TOKEN = "tok"

def test_manifest_shape():
    # v1.8.0 (user directive): STREAM-ONLY — no catalogs of its own; the
    # addon supplies streams for titles opened from other catalog addons.
    assert addon.MANIFEST["catalogs"] == []
    assert set(addon.MANIFEST["types"]) == {"movie", "series"}
    assert "type" not in addon.MANIFEST           # must be 'types' (Stremio protocol)
    assert addon.MANIFEST["idPrefixes"] == ["tt"]
    assert addon.MANIFEST["resources"] == ["stream"]

def test_listing_paths_covered():
    for site in addon.SITES:
        for kind in ("movies", "series", "animated"):
            assert (site, kind) in addon.LISTING_PATHS

# ------------------------------------------------------------- HTTP tests

class _Resp:
    def __init__(self, handler):
        self.h = handler
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def getcode(self):
        return self.h["code"]
    def read(self):
        return self.h["body"]

def _http_get(path):
    import io
    captured = {}
    buf = io.BytesIO()

    class H(addon.Handler):
        def __init__(self):
            self.headers = {"Host": "127.0.0.1:7000"}
            self.command = "GET"
            self.wfile = buf
        def send_response(self, code):
            captured["code"] = code
        def send_header(self, k, v):
            captured.setdefault("headers", {})[k] = v
        def end_headers(self):
            pass
        @property
        def path(self):
            return path
    h = H()
    h._route()
    captured["body"] = buf.getvalue()
    return captured

def test_http_health():
    c = _http_get("/health")
    assert c["code"] == 200
    d = json.loads(c["body"])
    assert d["ok"] is True and d["brand"] == "MovieBox"

def test_note_public_base_ignores_private():
    for bad in ["http://localhost:7000", "http://127.0.0.1:7000",
                "http://0.0.0.0:7000", "http://192.168.1.5:7000"]:
        addon._note_public_base(bad)
    assert addon._KEEPALIVE_URL is None

def test_health_reports_keepalive_off_for_localhost():
    c = _http_get("/health")
    d = json.loads(c["body"])
    assert d["ok"] is True and "keepalive" in d and "version" in d

def test_http_manifest():
    c = _http_get("/manifest.json")
    assert c["code"] == 200
    m = json.loads(c["body"])
    assert m["id"] == "com.movbox.stremio"
    assert m["logo"].endswith("/logo.png")

def test_manifest_catalogs_use_official_extra_key():
    # official stremio protocol: catalogs declare "extra" (NOT "extraSupported")
    for cat in addon.MANIFEST["catalogs"]:
        assert "extra" in cat, cat["id"]
        names = {e["name"] for e in cat["extra"]}
        assert {"search", "skip"} <= names, cat["id"]
        assert "extraSupported" not in cat

def test_http_landing_page():
    c = _http_get("/")
    assert c["code"] == 200
    assert c["headers"]["Content-Type"].startswith("text/html")
    body = c["body"].decode()
    assert "MOVIE" in body and "Install in Stremio" in body
    assert "manifest.json" in body and addon.VERSION in body

def test_http_hls_404_bad_token():
    c = _http_get("/hls/definitelybadtoken00/master.m3u8")
    assert c["code"] == 404

def test_http_stream_bad_id():
    c = _http_get("/stream/movie/xyz123.json")
    assert c["code"] == 200
    assert json.loads(c["body"]) == {"streams": []}

def test_http_not_found():
    c = _http_get("/nope")
    assert c["code"] == 404



def test_fetch_captions_mobile_and_cache():
    addon._SUB_CACHE.clear()
    caps_fix = [{"id": "1", "lan": "en", "lanName": "English",
                 "url": "https://cacdn.hakunaymatata.com/subtitle/x.srt?Policy=P"},
                {"id": "2", "lan": "bn", "lanName": "Bangla",
                 "url": "https://cacdn.hakunaymatata.com/subtitle/y.srt?Policy=P"}]
    calls = {"n": 0}
    def fake_api(method, path, body=None, timeout=10):
        calls["n"] += 1
        return {"extCaptions": caps_fix}
    with mock.patch.object(addon, "api_call", side_effect=fake_api):
        c1 = addon.fetch_captions("123", "456")
        c2 = addon.fetch_captions("123", "456")
    assert c1 == caps_fix and c2 == caps_fix
    assert calls["n"] == 1          # cached second time
    with mock.patch.object(addon, "api_call", return_value={"__error__": "x"}):
        assert addon.fetch_captions("123", "456") == caps_fix  # still cached
    addon._SUB_CACHE.clear()

def test_fetch_captions_falls_back_to_web():
    addon._SUB_CACHE.clear()
    caps_fix = [{"lan": "bn", "url": "https://c/b.srt?P=1"}]
    class R:
        status_code = 200
        def json(self):
            return {"code": 0, "data": {"captions": caps_fix}}
    with mock.patch.object(addon, "api_call", return_value={"__error__": "api"}), \
         mock.patch.object(addon, "_web_jwt", return_value="tok"), \
         mock.patch.object(addon.requests, "get", return_value=R()):
        caps = addon.fetch_captions("777", "888")
    assert caps == caps_fix
    addon._SUB_CACHE.clear()

def test_fetch_captions_all_fail_empty():
    addon._SUB_CACHE.clear()
    with mock.patch.object(addon, "api_call", return_value=None), \
         mock.patch.object(addon, "_web_jwt", return_value=None), \
         mock.patch.object(addon.requests, "get") as g:
        assert addon.fetch_captions("111", "222") == []
    assert g.call_count == 0        # no web attempt without a web jwt
    assert addon.fetch_captions("111", None) == []   # no stream id

def test_resolve_entry_attaches_subtitles():
    pi = {"streams": [{"id": "42", "signCookie": FAKE_COOKIE,
                       "url": "https://macdn.aoneroom.com/other/notice.mp4",
                       "resolutions": "1080,720,480", "size": "1", "duration": 1,
                       "codecName": "hevc", "format": "MP4", "idType": ""}]}
    caps = [{"lan": "en", "url": "https://c/s.srt?P=1"}, {"lan": "hi", "url": "https://c/h.srt?P=1"},
            {"lan": "ar", "url": "https://c/a.srt?P=1"}]   # v1.9.5: ar dropped
    with mock.patch.object(addon, "_cached_play", return_value=pi), \
         mock.patch.object(addon, "fetch_captions", return_value=caps):
        cards = addon._resolve_entry(("111", "Hindi"), 1, 5, "series", "Our Sticky Love", "2026")
    assert isinstance(cards, list) and len(cards) == 1
    assert cards[0]["name"].endswith("Our Sticky Love (Hindi)")  # v1.9.7
    assert cards[0]["url"].startswith("https://sacdn.hakunaymatata.com/dash/999888")  # from the cookie policy
    assert cards[0]["url"].endswith("/index.mpd")
    assert cards[0]["behaviorHints"]["proxyHeaders"]["request"]["Cookie"]
    for card in cards:
        assert card.get("subtitles")
        langs = [s["lang"] for s in card["subtitles"]]
        assert "eng" in langs and "hin" in langs and "ara" in langs  # v1.9.6 all
        assert card["subtitles"][0]["url"].startswith("https://")   # direct
        assert "⟡ 3 SUB · en, hi, ar" in card["description"]  # v1.9.7 ⟡



def test_sub_route_404_unknown_lang():
    addon._PLAY_CACHE.clear(); addon._SUB_CACHE.clear()
    pi = {"streams": [{"id": "42", "signCookie": FAKE_COOKIE, "url": "", "resolutions": "480",
                       "size": "1", "duration": 1, "codecName": "hevc", "format": "MP4", "idType": ""}]}
    with mock.patch.object(addon, "play_info", return_value=pi), \
         mock.patch.object(addon, "fetch_captions", return_value=[]):
        c = _http_get("/sub/3089349649006742360/1/1/zz.vtt")
    assert c["code"] == 404
    addon._PLAY_CACHE.clear(); addon._SUB_CACHE.clear()

MPD_TL = """<?xml version="1.0" encoding="utf-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static"
 mediaPresentationDuration="PT11.85S" maxSegmentDuration="PT6.0S" minBufferTime="PT2.0S">
 <Period id="0" start="PT0.0S">
  <AdaptationSet id="0" contentType="video" bitstreamSwitching="true">
   <Representation id="0" mimeType="video/mp4" codecs="hev1" bandwidth="1600000" width="1920" height="1080">
    <SegmentTemplate timescale="24000" initialization="init-stream$RepresentationID$.m4s" media="chunk-stream$RepresentationID$-$Number%05d$.m4s" startNumber="1">
     <SegmentTimeline>
      <S t="0" d="142142" />
      <S d="142142" />
      <S d="141120" />
     </SegmentTimeline>
    </SegmentTemplate>
   </Representation>
  </AdaptationSet>
  <AdaptationSet id="1" contentType="audio" lang="hin">
   <Representation id="3" mimeType="audio/mp4" codecs="mp4a.40.2" bandwidth="128000" audioSamplingRate="48000">
    <SegmentTemplate timescale="48000" initialization="init-stream$RepresentationID$.m4s" media="chunk-stream$RepresentationID$-$Number%05d$.m4s" startNumber="1">
     <SegmentTimeline>
      <S t="0" d="239576" />
      <S d="240640" r="1" />
     </SegmentTimeline>
    </SegmentTemplate>
   </Representation>
  </AdaptationSet>
 </Period>
</MPD>
"""

def test_parse_mpd_timeline():
    info = addon._parse_mpd(MPD_TL)
    tl = info["tl"]
    assert len(tl["video"]) == 3                      # 3 S entries, no r
    assert abs(tl["video"][0] - 142142 / 24000) < 1e-6
    assert len(tl["audio"]) == 3                      # r="1" expands to 2
    assert abs(tl["audio"][1] - 240640 / 48000) < 1e-6
    assert abs(info["seg_dur"] - sum(tl["video"]) / 3) < 1e-6





def test_fetch_captions_retry_and_fallback():
    addon._SUB_CACHE.clear()
    cap = lambda i: {"lan": "en%d" % i, "url": "https://c/%d.srt" % i}
    nine = [cap(i) for i in range(9)]
    # case A: flaky mobile returns 1 cap, retry returns 9 -> keep 9
    with mock.patch.object(addon, "api_call",
                           side_effect=[{"extCaptions": [cap(0)]}, {"extCaptions": nine}]):
        got = addon.fetch_captions("900", "st1")
    assert len(got) == 9
    # case B: mobile stuck at 1 cap, web fallback has 3 -> keep web's 3
    addon._SUB_CACHE.clear()
    with mock.patch.object(addon, "api_call", return_value={"extCaptions": [cap(0)]}), \
         mock.patch.object(addon, "_web_captions", return_value=[cap(1), cap(2), cap(3)]):
        got = addon.fetch_captions("901", "st2")
    assert len(got) == 3
    addon._SUB_CACHE.clear()

def test_cross_dub_subtitle_rescue():
    addon._MPD_CACHE.clear(); addon._STREAM_CACHE.clear(); addon._STREAM_STALE.clear(); addon._PLAY_CACHE.clear()
    addon._DUB_CACHE.clear(); addon._SEARCH_CACHE.clear()
    caps = [{"lan": "en", "url": "https://c/e.srt"}, {"lan": "hi", "url": "https://c/h.srt"}]
    def caps_by_sid(sid, stream_id):
        return caps if sid == "6391474290696802080" else [{"lan": "ar", "url": "https://c/a.srt"}]
    dubs = [{"subjectId": "973041525783496480", "lanName": "Hindi dub"}]
    with mock.patch.object(addon, "_meta_any",
                           return_value={"name": "Inception", "year": "2010"}), \
         mock.patch.object(addon, "search_subjects", return_value=[SUBJ_INCEPTION]), \
         mock.patch.object(addon, "subject_dubs", return_value=dubs), \
         mock.patch.object(addon, "play_info", return_value=PLAY_INFO_FIX), \
         mock.patch.object(addon, "fetch_captions", side_effect=caps_by_sid), \
         mock.patch.object(addon, "_spawn_warm"), \
         mock.patch.object(addon.requests, "get"):
        res = addon.build_streams("movie", "tt1375666", 1, 1)
    assert len(res["streams"]) == 2               # Original + Hindi dub
    orig, hindi = res["streams"]
    assert len(orig.get("subtitles") or []) == 2  # its own captions
    assert len(hindi.get("subtitles") or []) == 2 # thin (1-cap) dub rescued by the sibling
    assert hindi["subtitles"][0]["url"].startswith("https://c/")   # direct CDN (v1.9.0)
    assert "⟡ 2 SUB" in hindi["description"]   # v1.9.7 ⟡ tag
    assert "NO SUB" not in hindi["description"]
    addon._STREAM_CACHE.clear(); addon._STREAM_STALE.clear()

def test_captions_fetched_once_per_title():
    addon._MPD_CACHE.clear(); addon._STREAM_CACHE.clear(); addon._STREAM_STALE.clear(); addon._PLAY_CACHE.clear()
    addon._DUB_CACHE.clear(); addon._SEARCH_CACHE.clear()
    calls = {"caps": 0}
    nine = [{"lan": "en", "url": "https://c/e.srt"}, {"lan": "hi", "url": "https://c/h.srt"}]
    nine += [{"lan": "x%d" % i, "url": "https://c/%d.srt" % i} for i in range(7)]  # filtered
    def counting_caps(sid, stream_id):
        calls["caps"] += 1
        return nine
    dubs = [{"subjectId": "973041525783496480", "lanName": "Hindi dub"},
            {"subjectId": "1111111111111111111", "lanName": "Tamil dub"}]
    with mock.patch.object(addon, "_meta_any",
                           return_value={"name": "Inception", "year": "2010"}), \
         mock.patch.object(addon, "search_subjects", return_value=[SUBJ_INCEPTION]), \
         mock.patch.object(addon, "subject_dubs", return_value=dubs), \
         mock.patch.object(addon, "play_info", return_value=PLAY_INFO_FIX), \
         mock.patch.object(addon, "fetch_captions", side_effect=counting_caps), \
         mock.patch.object(addon, "_spawn_warm"), \
         mock.patch.object(addon.requests, "get"):
        res = addon.build_streams("movie", "tt1375666", 1, 1)
    assert len(res["streams"]) == 3                       # 3 dubs
    assert calls["caps"] == 1                             # ONE caption fetch, not 3
    for s in res["streams"]:                              # every card shares it
        assert len(s.get("subtitles") or []) == 9   # v1.9.6: ALL langs back
        assert "⟡ 9 SUB" in s["description"]       # v1.9.7 ⟡ tag
        assert s["subtitles"][0]["url"].startswith("https://c/")   # direct CDN (v1.9.0)
    addon._STREAM_CACHE.clear(); addon._STREAM_STALE.clear()

def test_stream_stale_while_revalidate():
    addon._MPD_CACHE.clear(); addon._STREAM_CACHE.clear(); addon._STREAM_STALE.clear()
    addon._PLAY_CACHE.clear(); addon._DUB_CACHE.clear(); addon._SEARCH_CACHE.clear()
    with mock.patch.object(addon, "WEB_MP4_ON", False), \
         mock.patch.object(addon, "_meta_any",
                           return_value={"name": "Inception", "year": "2010"}), \
         mock.patch.object(addon, "search_subjects", return_value=[SUBJ_INCEPTION]), \
         mock.patch.object(addon, "subject_dubs", return_value=[]), \
         mock.patch.object(addon, "play_info", return_value=PLAY_INFO_FIX), \
         mock.patch.object(addon, "fetch_captions", return_value=[]), \
         mock.patch.object(addon, "_spawn_warm"), \
         mock.patch.object(addon.requests, "get"):
        r1 = addon.build_streams("movie", "tt1375666", 1, 1)
    assert len(r1["streams"]) == 1
    addon._STREAM_CACHE.clear()               # simulate the 3h fresh TTL expiring
    class FakeThread:                         # no background refresh in the test
        def __init__(self, *a, **k): pass
        def start(self): pass
    import time as _t
    with mock.patch.object(addon.threading, "Thread", FakeThread):
        t0 = _t.time()
        r2 = addon.build_streams("movie", "tt1375666", 1, 1)
    assert _t.time() - t0 < 0.5               # instant: stale list served
    assert r2 == {"streams": r1["streams"]}
    addon._STREAM_CACHE.clear(); addon._STREAM_STALE.clear()
    addon._STREAM_REFRESHING.clear()

def test_api_call_refreshes_stale_token():
    """Server-side token expiry must self-heal: drop, re-bootstrap, retry."""
    addon._AUTH_TOKEN = "stale"
    addon._AUTH_REAUTH_TS = 0.0
    made = {"n": 0}
    class Resp:
        status_code = 200
        headers = {}
        def __init__(self, payload):
            self._p = payload
        def json(self):
            return self._p
    def fake_request(method, url, headers=None, data=None, timeout=None):
        made["n"] += 1
        if (headers or {}).get("Authorization", "").endswith("stale"):
            return Resp({"code": -1, "message": "Token is invalid"})
        return Resp({"code": 0, "data": {"ok": True}})
    def fake_bootstrap():
        addon._AUTH_TOKEN = "fresh"
    try:
        with mock.patch.object(addon.requests, "request", side_effect=fake_request), \
             mock.patch.object(addon, "_bootstrap_token", side_effect=fake_bootstrap):
            d = addon.api_call("GET", "/wefeed-mobile-bff/subject-api/get?subjectId=1")
        assert d == {"ok": True}                 # recovered with the fresh token
        assert made["n"] >= 2                    # the stale attempt + the retry
        assert addon._AUTH_TOKEN == "fresh"
    finally:
        addon._AUTH_TOKEN = None
        addon._AUTH_REAUTH_TS = 0.0

def test_gzip_response():
    import gzip as gz
    captured, buf = {}, __import__("io").BytesIO()
    class H(addon.Handler):
        def __init__(self):
            self.headers = {"Host": "127.0.0.1:7000", "Accept-Encoding": "gzip"}
            self.command = "GET"
            self.wfile = buf
        def send_response(self, code):
            captured["code"] = code
        def send_header(self, k, v):
            captured.setdefault("headers", {})[k] = v
        def end_headers(self):
            pass
        @property
        def path(self):
            return "/"
    H()._route()
    assert captured["code"] == 200
    assert captured["headers"].get("Content-Encoding") == "gzip"
    body = gz.decompress(buf.getvalue()).decode()
    assert "MovieBox" in body
    assert len(buf.getvalue()) < len(body.encode())   # actually compressed

# --- v1.6.4: optional platform egress proxy (MOVIEBOX_PROXY) ----------------

def test_plat_proxy_default_off():
    # No env var set => no proxying at all (default production behavior).
    assert addon._PLAT_PROXIES is None

def test_api_call_uses_proxy_when_configured():
    addon._PLAT_CB_UNTIL = 0.0
    captured = {}
    def fake_request(method, url, **kw):
        captured["proxies"] = kw.get("proxies")
        resp = mock.Mock(status_code=200)
        resp.headers = {}
        resp.json = lambda: {"code": 0, "message": "ok", "data": {"x": 1}}
        return resp
    gate = {"http": "http://gate.example:7000", "https": "http://gate.example:7000"}
    saved = addon._PLAT_PROXIES
    try:
        addon._PLAT_PROXIES = gate
        with mock.patch.object(addon, "_bootstrap_token"), \
             mock.patch.object(addon.requests, "request", side_effect=fake_request):
            addon._AUTH_TOKEN = "tok"
            d = addon.api_call("GET", "/wefeed-mobile-bff/v2/search?keyword=x")
        assert d == {"x": 1}
        assert captured["proxies"] == gate
    finally:
        addon._PLAT_PROXIES = saved

def test_bootstrap_uses_proxy_when_configured():
    addon._PLAT_CB_UNTIL = 0.0
    addon._DIRECT_AUTH_FLAG[0] = 0.0     # v1.7.5: don't inherit other tests' flag
    captured = {}
    def fake_get(url, **kw):
        captured["proxies"] = kw.get("proxies")
        resp = mock.Mock(status_code=200)
        resp.headers = {}
        resp.json = lambda: {"code": 0, "data": {}}
        return resp
    gate = {"http": "http://gate.example:7000", "https": "http://gate.example:7000"}
    saved_px, saved_tok = addon._PLAT_PROXIES, addon._AUTH_TOKEN
    try:
        addon._PLAT_PROXIES = gate
        with mock.patch.object(addon.requests, "get", side_effect=fake_get):
            addon._bootstrap_token()
        assert captured["proxies"] == gate
    finally:
        addon._PLAT_PROXIES = saved_px
        addon._AUTH_TOKEN = saved_tok

# --- v1.6.5: scrape.do egress fallback (SCRAPEDO_TOKEN) ----------------------

SD_PATH = "/wefeed-mobile-bff/subject-api/search/v2"

def test_sd_family_mark_and_expiry():
    addon._SCRAPEDO_TOKEN = "tok"
    addon._SD_FALLBACK.clear()
    try:
        assert not addon._sd_forced(SD_PATH)
        addon._sd_mark(SD_PATH)
        assert addon._sd_forced(SD_PATH)                    # same family
        assert addon._sd_forced("/wefeed-mobile-bff/subject-api/search")
        assert not addon._sd_forced("/wefeed-mobile-bff/get-stream-captions?x=1")
        assert not addon._sd_forced("/wefeed-mobile-bff/tab-operating?page=1")
        # expired -> not forced, entry dropped
        addon._SD_FALLBACK[addon._sd_family(SD_PATH)] = time.time() - 1
        assert not addon._sd_forced(SD_PATH)
        assert addon._SD_FALLBACK == {}
    finally:
        addon._SCRAPEDO_TOKEN = ""
        addon._SD_FALLBACK.clear()

def test_api_call_via_scrapedo_when_forced():
    addon._PLAT_CB_UNTIL = 0.0
    addon._PLAT_FAILS = 0
    calls = []
    def fake_request(method, url, **kw):
        calls.append(url)
        if "api.scrape.do" in url:
            assert kw.get("params", {}).get("token") == "tok"
            assert kw.get("params", {}).get("customHeaders") == "true"
            resp = mock.Mock(status_code=200)
            resp.headers = {"scrape-do-remaining-credits": "777"}
            resp.json = lambda: {"code": 0, "message": "ok", "data": {"x": 9}}
            return resp
        raise AssertionError("direct platform call made while scrape.do forced")
    saved_tok, saved_fb = addon._SCRAPEDO_TOKEN, dict(addon._SD_FALLBACK)
    try:
        addon._SCRAPEDO_TOKEN = "tok"
        addon._SD_FALLBACK.clear()
        addon._sd_mark(SD_PATH)
        with mock.patch.object(addon, "_bootstrap_token"), \
             mock.patch.object(addon.requests, "request", side_effect=fake_request):
            addon._AUTH_TOKEN = "tok"
            d = addon.api_call("POST", SD_PATH, "{}")
        assert d == {"x": 9}
        assert calls and all("api.scrape.do" in u for u in calls)
        assert addon._SD_CREDITS[0] == 777     # credit telemetry captured
    finally:
        addon._SCRAPEDO_TOKEN = saved_tok
        addon._SD_FALLBACK.clear()
        addon._SD_FALLBACK.update(saved_fb)

def test_api_call_direct_403_falls_back_to_scrapedo():
    addon._PLAT_CB_UNTIL = 0.0
    addon._PLAT_FAILS = 0
    seq = []
    def fake_request(method, url, **kw):
        seq.append(url)
        if "api.scrape.do" in url:
            resp = mock.Mock(status_code=200)
            resp.headers = {}
            resp.json = lambda: {"code": 0, "message": "ok", "data": {"ok": 1}}
            return resp
        resp = mock.Mock(status_code=403)
        resp.headers = {}
        return resp
    saved_tok = addon._SCRAPEDO_TOKEN
    try:
        addon._SCRAPEDO_TOKEN = "tok"
        addon._SD_FALLBACK.clear()
        with mock.patch.object(addon, "_bootstrap_token"), \
             mock.patch.object(addon.requests, "request", side_effect=fake_request):
            addon._AUTH_TOKEN = "tok"
            d = addon.api_call("POST", SD_PATH, "{}")
        assert d == {"ok": 1}
        assert any("api.scrape.do" in u for u in seq)
        assert any("aoneroom" in u for u in seq)       # direct was tried first
        assert addon._sd_forced(SD_PATH)               # family remembered
    finally:
        addon._SCRAPEDO_TOKEN = saved_tok
        addon._SD_FALLBACK.clear()

def test_scrapedo_not_used_for_tab_operating():
    addon._PLAT_CB_UNTIL = 0.0
    addon._PLAT_FAILS = 0
    seq = []
    def fake_request(method, url, **kw):
        seq.append(url)
        resp = mock.Mock(status_code=403)
        resp.headers = {}
        return resp
    saved_tok = addon._SCRAPEDO_TOKEN
    try:
        addon._SCRAPEDO_TOKEN = "tok"
        addon._SD_FALLBACK.clear()
        with mock.patch.object(addon, "_bootstrap_token"), \
             mock.patch.object(addon.requests, "request", side_effect=fake_request):
            addon._AUTH_TOKEN = "tok"
            d = addon.api_call("GET", "/wefeed-mobile-bff/tab-operating?page=1&tabId=0&version=")
        assert d is None                        # all direct 403s -> transient None
        assert seq and all("api.scrape.do" not in u for u in seq)
        assert not addon._sd_forced("/wefeed-mobile-bff/tab-operating?page=1")
    finally:
        addon._SCRAPEDO_TOKEN = saved_tok
        addon._SD_FALLBACK.clear()
        addon._PLAT_FAILS = 0

def test_warm_skipped_while_search_family_on_scrapedo():
    saved_tok = addon._SCRAPEDO_TOKEN
    try:
        addon._SCRAPEDO_TOKEN = "tok"
        addon._SD_FALLBACK.clear()
        addon._sd_mark(SD_PATH)
        saved = addon._WARM_TS[0]
        addon._WARM_TS[0] = 0.0
        try:
            addon._spawn_warm([], None, None, "movie")
            assert addon._WARM_TS[0] == 0.0    # untouched -> skipped pre-throttle
        finally:
            addon._WARM_TS[0] = saved
    finally:
        addon._SCRAPEDO_TOKEN = saved_tok
        addon._SD_FALLBACK.clear()

# --- v1.6.7: multi-proxy pool (MOVIEBOX_PROXY_LIST) ------------------------

def test_pool_parsing():
    try:
        os.environ["MOVIEBOX_PROXY"] = "http://u:p@h:1"
        importlib.reload(addon)
        assert addon._PLAT_PROXIES == {"http": "http://u:p@h:1",
                                       "https": "http://u:p@h:1"}
        os.environ.pop("MOVIEBOX_PROXY")
        importlib.reload(addon)
        assert addon._PLAT_PROXIES is None
        os.environ["MOVIEBOX_PROXY_LIST"] = "http://u:p@h:1,http://u:p@h:2"
        importlib.reload(addon)
        assert addon._PROXY_URLS == ["http://u:p@h:1", "http://u:p@h:2"]
        assert addon._PLAT_PROXIES is None      # single-URL var not set
        # whitespace + trailing comma tolerated
        os.environ["MOVIEBOX_PROXY_LIST"] = " http://a:1 , http://b:2 ,"
        importlib.reload(addon)
        assert addon._PROXY_URLS == ["http://a:1", "http://b:2"]
    finally:
        os.environ.pop("MOVIEBOX_PROXY", None)
        os.environ.pop("MOVIEBOX_PROXY_LIST", None)
        importlib.reload(addon)
    assert addon._PLAT_PROXIES is None

def test_api_call_rotates_proxy_on_pool_failure():
    # v1.6.8: direct first; on the 403 IP-flag the pool engages and rotates
    # exits until one answers.
    addon._PLAT_CB_UNTIL = 0.0
    addon._PLAT_FAILS = 0
    saved_urls = list(addon._PROXY_URLS)
    try:
        addon._PROXY_URLS = ["http://p1:1", "http://p2:2"]
        addon._SD_FALLBACK.clear()
        picks = []
        def fake_request(method, url, **kw):
            px = kw.get("proxies") or {}
            picks.append(px.get("http"))
            if px.get("http") is None:
                resp = mock.Mock(status_code=403)      # direct egress IP-flagged
                resp.headers = {}
                resp.json = lambda: {}
                return resp
            if px.get("http") == "http://p1:1":
                raise addon.requests.ConnectionError("p1 dead")
            resp = mock.Mock(status_code=200)
            resp.headers = {}
            resp.json = lambda: {"code": 0, "message": "ok", "data": {"x": 9}}
            return resp
        with mock.patch.object(addon, "_bootstrap_token"), \
             mock.patch.object(addon, "_pool_pick",
                               side_effect=[{"http": "http://p1:1", "https": "http://p1:1"},
                                           {"http": "http://p2:2", "https": "http://p2:2"}]), \
             mock.patch.object(addon.requests, "request", side_effect=fake_request):
            addon._AUTH_TOKEN = "tok"
            d = addon.api_call("POST", "/wefeed-mobile-bff/subject-api/search/v2", "{}")
        assert d == {"x": 9}
        assert picks == [None, "http://p1:1", "http://p2:2"]  # direct 403 -> pool: died on p1, won on p2
    finally:
        addon._PROXY_URLS = saved_urls
        addon._SD_FALLBACK.clear()
        addon._PLAT_FAILS = 0

def test_bootstrap_uses_pool():
    # v1.6.8: direct attempts first (IP-flagged here -> 403), then pool
    # rotation (p1 403 -> p2 ok).
    addon._PLAT_CB_UNTIL = 0.0
    addon._PLAT_FAILS = 0
    saved_urls = list(addon._PROXY_URLS)
    try:
        addon._PROXY_URLS = ["http://p1:1", "http://p2:2"]
        picked = []
        def fake_get(url, **kw):
            px = kw.get("proxies") or {}
            picked.append(px.get("http"))
            resp = mock.Mock(status_code=403 if (px.get("http") in (None, "http://p1:1")) else 200)
            resp.headers = {}
            return resp
        with mock.patch.object(addon, "_pool_pick",
                               side_effect=[{"http": "http://p1:1", "https": "http://p1:1"},
                                           {"http": "http://p2:2", "https": "http://p2:2"}]), \
             mock.patch.object(addon.requests, "get", side_effect=fake_get):
            addon._AUTH_TOKEN = None
            addon._bootstrap_token()
        assert picked == [None, None, "http://p1:1", "http://p2:2"]  # 2 direct hosts, then pool rotation
    finally:
        addon._PROXY_URLS = saved_urls
        addon._DIRECT_AUTH_FLAG[0] = 0.0   # v1.7.5: direct 403 now sets the flag

def test_warm_skipped_while_pool_active():
    # v1.6.8: pool is fallback-only, so warm is skipped only while the
    # search family is flagged (warm calls would ride the pool).
    saved_urls = list(addon._PROXY_URLS)
    try:
        addon._PROXY_URLS = ["http://p:1"]
        addon._SD_FALLBACK.clear()
        addon._sd_mark(SD_PATH)
        saved = addon._WARM_TS[0]
        addon._WARM_TS[0] = 0.0
        try:
            addon._spawn_warm([], None, None, "movie")
            assert addon._WARM_TS[0] == 0.0    # untouched -> skipped pre-throttle
        finally:
            addon._WARM_TS[0] = saved
            addon._SD_FALLBACK.clear()
    finally:
        addon._PROXY_URLS = saved_urls

def main():
    global PASS, FAIL
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        run(t)
    print("\n%d/%d passed" % (PASS, len(tests)))
    sys.exit(0 if FAIL == 0 else 1)


# --- v1.6.8: auto-refreshed free proxy pool -------------------------------

def test_free_pool_source_default_and_env():
    try:
        os.environ.pop("MOVIEBOX_PROXY_SOURCE", None)
        importlib.reload(addon)
        assert "proxyscrape.com" in addon._FREE_POOL_SRC   # public default
        assert addon._FREE_POOL_SRC.startswith("https://")
        os.environ["MOVIEBOX_PROXY_SOURCE"] = "https://example.com/list.txt"
        importlib.reload(addon)
        assert addon._FREE_POOL_SRC == "https://example.com/list.txt"
        os.environ["MOVIEBOX_PROXY_SOURCE"] = ""            # disable
        importlib.reload(addon)
        assert addon._FREE_POOL_SRC == ""
    finally:
        os.environ.pop("MOVIEBOX_PROXY_SOURCE", None)
        importlib.reload(addon)
    assert "proxyscrape.com" in addon._FREE_POOL_SRC

def test_free_pool_refresh_probes_and_caches():
    saved_ts, saved_pool = addon._FREE_POOL_TS[0], list(addon._FREE_POOL[0])
    try:
        addon._FREE_POOL_TS[0] = 0.0
        addon._FREE_POOL[0] = []
        list_text = ("http://a:1\nsocks5://x:2\nhttp://b:2\nhttp://c:3\r\nhttp://d:4\n")
        def fake_get(url, **kw):
            if url == addon._FREE_POOL_SRC:            # list fetch
                r = mock.Mock(status_code=200)
                r.text = list_text
                return r
            # v1.6.10: platform probe (tab-operating via the candidate)
            # v1.7.5: the probe also captures the exit's own x-user token —
            # exits that drop the header are no longer "good".
            assert "tab-operating" in url
            px = kw.get("proxies") or {}
            u = px.get("http")
            good = u in ("http://a:1", "http://c:3")
            r = mock.Mock(status_code=200 if good else 403)
            r.headers = {"x-user": '{"token": "T"}'} if good else {}
            return r
        with mock.patch.object(addon.requests, "get", side_effect=fake_get):
            addon._free_pool_refresh()
        assert sorted(addon._FREE_POOL[0]) == ["http://a:1", "http://c:3"]  # socks skipped, platform-blocked dropped
        # throttled: a second refresh within 10 min must not re-fetch
        with mock.patch.object(addon.requests, "get", side_effect=AssertionError("re-fetched")):
            addon._free_pool_refresh()
    finally:
        addon._FREE_POOL_TS[0] = saved_ts
        addon._FREE_POOL[0] = saved_pool
        for u in ("http://a:1", "http://c:3"):
            addon._POOL_STATS.pop(u, None)

def test_api_call_falls_back_to_free_pool():
    # free pool only (no env pool): direct 403 -> free-pool retry succeeds
    addon._PLAT_CB_UNTIL = 0.0
    addon._PLAT_FAILS = 0
    saved_urls = list(addon._PROXY_URLS)
    saved_fp = list(addon._FREE_POOL[0])
    try:
        addon._PROXY_URLS = []
        addon._FREE_POOL[0] = ["http://f1:1"]
        addon._SD_FALLBACK.clear()
        picks = []
        def fake_request(method, url, **kw):
            px = kw.get("proxies") or {}
            picks.append(px.get("http"))
            if px.get("http") is None:
                resp = mock.Mock(status_code=403)   # direct egress IP-flagged
            else:
                resp = mock.Mock(status_code=200)   # free proxy works
            resp.headers = {}
            resp.json = lambda: {"code": 0, "message": "ok", "data": {"x": 5}}
            return resp
        with mock.patch.object(addon, "_bootstrap_token"), \
             mock.patch.object(addon.requests, "request", side_effect=fake_request):
            addon._AUTH_TOKEN = "tok"
            d = addon.api_call("POST", SD_PATH, "{}")
        assert d == {"x": 5}
        assert picks[0] is None                     # direct tried first
        assert picks[1] == "http://f1:1"            # free pool engaged after 403
        assert addon._sd_forced(SD_PATH)            # family remembered for 30 min
    finally:
        addon._PROXY_URLS = saved_urls
        addon._FREE_POOL[0] = saved_fp
        addon._SD_FALLBACK.clear()
        addon._PLAT_FAILS = 0

def test_health_reports_free_pool():
    saved_fp = list(addon._FREE_POOL[0])
    try:
        addon._FREE_POOL[0] = ["http://f1:1", "http://f2:2"]
        c = _http_get("/health")
        assert c["code"] == 200
        d = json.loads(c["body"])
        assert d["free_pool"] == 2
        assert "free" in d["platform_proxy"] and "2" in d["platform_proxy"]
    finally:
        addon._FREE_POOL[0] = saved_fp


# --- v1.6.9: pool learning + transient-cache healing ----------------------

def _pool_state_reset():
    addon._POOL_BAD.clear()
    addon._POOL_STICKY[0], addon._POOL_STICKY[1] = None, 0.0
    addon._POOL_TLS.url = None

def test_pool_learning_benches_and_sticks():
    saved_urls, saved_fp = list(addon._PROXY_URLS), list(addon._FREE_POOL[0])
    try:
        _pool_state_reset()
        addon._PROXY_URLS = ["http://a:1", "http://b:2", "http://c:3"]
        addon._FREE_POOL[0] = []
        # all healthy: any pick
        p = addon._pool_pick()
        assert p["http"] in ("http://a:1", "http://b:2", "http://c:3")
        # bench a dead + a blocked exit -> never picked again
        addon._POOL_BAD["http://a:1"] = time.time() + 600
        addon._POOL_BAD["http://b:2"] = time.time() + 900
        for _ in range(20):
            assert addon._pool_pick()["http"] == "http://c:3"
        # good note -> sticky for subsequent picks
        addon._POOL_TLS.url = "http://c:3"
        addon._pool_note("good")
        assert addon._POOL_STICKY[0] == "http://c:3"
        assert addon._pool_pick()["http"] == "http://c:3"   # sticky preferred
        # block note on the sticky -> cleared + benched
        addon._POOL_TLS.url = "http://c:3"
        addon._pool_note("block")
        assert addon._POOL_STICKY[0] is None
        assert "http://c:3" in addon._POOL_BAD
        # everything benched -> still returns something (try anyway)
        pick = addon._pool_pick()["http"]
        assert pick in ("http://a:1", "http://b:2", "http://c:3")
    finally:
        _pool_state_reset()
        addon._PROXY_URLS = saved_urls
        addon._FREE_POOL[0] = saved_fp

def test_api_call_pool_notes_block_on_406():
    addon._PLAT_CB_UNTIL = 0.0
    addon._PLAT_FAILS = 0
    saved_urls, saved_fp = list(addon._PROXY_URLS), list(addon._FREE_POOL[0])
    try:
        _pool_state_reset()
        addon._PROXY_URLS = []
        addon._FREE_POOL[0] = ["http://f1:1"]
        addon._SD_FALLBACK.clear()
        calls = []
        def fake_request(method, url, **kw):
            px = kw.get("proxies") or {}
            u = px.get("http")
            calls.append(u)
            resp = mock.Mock(status_code=403 if u in (None, "http://f1:1") else 200)
            resp.headers = {}
            resp.json = lambda: {}
            return resp
        with mock.patch.object(addon, "_bootstrap_token"), \
             mock.patch.object(addon.requests, "request", side_effect=fake_request):
            addon._AUTH_TOKEN = "tok"
            d = addon.api_call("POST", SD_PATH, "{}")
        assert d is None                                # everything 403'd
        assert calls[0] is None                         # direct tried first
        assert "http://f1:1" in calls                   # pool engaged after 403
        assert "http://f1:1" in addon._POOL_BAD         # benched after its 403
    finally:
        _pool_state_reset()
        addon._PROXY_URLS = saved_urls
        addon._FREE_POOL[0] = saved_fp
        addon._SD_FALLBACK.clear()
        addon._PLAT_FAILS = 0

def test_circuit_not_tripped_by_pool_failures():
    # 4 consecutive pool-exhausted calls must NOT open the platform circuit
    saved_cb, saved_f = addon._PLAT_CB_UNTIL, addon._PLAT_FAILS
    saved_urls, saved_fp = list(addon._PROXY_URLS), list(addon._FREE_POOL[0])
    try:
        _pool_state_reset()
        addon._PLAT_CB_UNTIL = 0.0
        addon._PLAT_FAILS = 0
        addon._PROXY_URLS = ["http://p:1"]
        addon._FREE_POOL[0] = []
        addon._SD_FALLBACK.clear()
        addon._sd_mark(SD_PATH)          # family flagged -> rides the pool
        with mock.patch.object(addon, "_bootstrap_token"), \
             mock.patch.object(addon.requests, "request",
                               side_effect=addon.requests.ConnectionError("dead")):
            addon._AUTH_TOKEN = "tok"
            for _ in range(4):
                assert addon.api_call("POST", SD_PATH, "{}") is None
        assert addon._PLAT_CB_UNTIL == 0.0             # circuit stayed closed
    finally:
        _pool_state_reset()
        addon._PLAT_CB_UNTIL, addon._PLAT_FAILS = saved_cb, saved_f
        addon._PROXY_URLS = saved_urls
        addon._FREE_POOL[0] = saved_fp
        addon._SD_FALLBACK.clear()

def test_transient_answers_never_cached():
    # v1.7.0: None (transient transport failure) is NEVER cached — the next
    # call retries immediately; [] (definitive "not in catalog") keeps the
    # full TTL.
    addon._SEARCH_CACHE.clear()
    addon._DUB_CACHE.clear()
    addon._PLAY_CACHE.clear()
    calls = {"n": 0}
    def fake_search(kw, stype):
        calls["n"] += 1
        return None if calls["n"] == 1 else [{"subjectId": "1", "title": kw}]
    with mock.patch.object(addon, "search_subjects", side_effect=fake_search):
        assert addon._cached_search("kw", 1) is None        # transient
        assert ("kw", 1) not in addon._SEARCH_CACHE         # not cached at all
        assert addon._cached_search("kw", 1) == [{"subjectId": "1", "title": "kw"}]
        assert calls["n"] == 2                              # re-queried
    with mock.patch.object(addon, "search_subjects", return_value=[]):
        assert addon._cached_search("kw2", 1) == []         # definitive
        _, exp = addon._SEARCH_CACHE[("kw2", 1)]
        assert exp - time.time() > 300                      # full TTL
    dcalls = {"n": 0}
    def fake_dubs(sid):
        dcalls["n"] += 1
        return None if dcalls["n"] == 1 else [{"subjectId": "9"}]
    with mock.patch.object(addon, "subject_dubs", side_effect=fake_dubs):
        assert addon._cached_dubs("7") is None
        assert "7" not in addon._DUB_CACHE
        assert addon._cached_dubs("7") == [{"subjectId": "9"}]
    with mock.patch.object(addon, "play_info", return_value=None):
        assert addon._cached_play("3", None, None) is None
        assert ("3", None, None) not in addon._PLAY_CACHE


# --- v1.6.11: free-pool primary + hvc1 + reqlog ------------------------------

def test_pool_free_first_precedence():
    # free pool is PRIMARY; env MOVIEBOX_PROXY_LIST only when free is empty
    saved_urls, saved_fp = list(addon._PROXY_URLS), list(addon._FREE_POOL[0])
    try:
        addon._PROXY_URLS = ["http://env1:1", "http://env2:2"]
        addon._FREE_POOL[0] = ["http://f1:1", "http://f2:2"]
        assert addon._pool_all() == ["http://f1:1", "http://f2:2"]   # free only
        addon._FREE_POOL[0] = []
        assert addon._pool_all() == ["http://env1:1", "http://env2:2"]  # backup
        addon._PROXY_URLS = []
        assert addon._pool_all() == []
    finally:
        addon._PROXY_URLS = saved_urls
        addon._FREE_POOL[0] = saved_fp


def test_reqlog_records_served_requests():
    saved = list(addon._REQLOG)
    addon._REQLOG.clear()
    try:
        c = _http_get("/manifest.json")
        assert c["code"] == 200
        assert _http_get("/health")["code"] == 200
        ent = [e for e in addon._REQLOG if e["path"] == "/manifest.json"]
        assert ent and ent[0]["code"] == 200 and ent[0]["bytes"] > 0
        assert not any(e["path"].startswith("/health") for e in addon._REQLOG)
    finally:
        del addon._REQLOG[:]
        addon._REQLOG.extend(saved)


# --- v1.7.0: trained proxy environment + transient/definitive semantics -----

def test_search_transient_vs_definitive():
    # every strategy hits a transient failure -> None (never a fake [])
    with mock.patch.object(addon, "api_call", return_value=None):
        assert addon.search_subjects("kw", 1) is None
    # platform answers definitively (empty results) -> []
    with mock.patch.object(addon, "api_call",
                           return_value={"results": [{"subjects": []}]}):
        assert addon.search_subjects("kw", 1) == []
    # platform answers with a subject -> filtered list
    sub = {"subjectId": "1", "title": "X", "subjectType": 1}
    with mock.patch.object(addon, "api_call",
                           return_value={"results": [{"subjects": [sub]}]}):
        assert addon.search_subjects("kw", 1) == [sub]

def test_dubs_transient_vs_definitive():
    with mock.patch.object(addon, "api_call", return_value=None):
        assert addon.subject_dubs("7") is None
    with mock.patch.object(addon, "api_call", return_value={"__error__": "x"}):
        assert addon.subject_dubs("7") == []
    with mock.patch.object(addon, "api_call", return_value={"dubs": []}):
        assert addon.subject_dubs("7") == []

# ------------------------------------------------ v1.8.1 pool speed fixes
def test_pool_pick_rides_the_fastest_idle_exit():
    """v1.8.1: no more random among top-3 — the best-scored exit with
    <2 requests in flight is taken; a busy best yields to the next."""
    saved_urls, saved_fp = list(addon._PROXY_URLS), list(addon._FREE_POOL[0])
    try:
        _pool_state_reset()
        addon._PROXY_URLS = []
        addon._FREE_POOL[0] = ["http://fast:1", "http://mid:1", "http://slow:1"]
        addon._POOL_STATS.clear()
        addon._POOL_STATS["http://fast:1"] = {"ok": 10, "fail": 0, "lat": 300}
        addon._POOL_STATS["http://mid:1"] = {"ok": 10, "fail": 0, "lat": 800}
        addon._POOL_STATS["http://slow:1"] = {"ok": 10, "fail": 0, "lat": 2000}
        addon._EXIT_BUSY.clear()
        assert addon._pool_pick()["http"] == "http://fast:1"
        # (pick itself does not mark busy — api_call does; simulate it)
        addon._exit_busy_inc("http://fast:1")
        assert addon._pool_pick()["http"] == "http://fast:1"   # cap is 2
        addon._exit_busy_inc("http://fast:1")                  # now 2 busy
        assert addon._pool_pick()["http"] == "http://mid:1"    # spread!
        # sticky that is busy yields to the ranked list too
        addon._POOL_STICKY[0], addon._POOL_STICKY[1] = "http://fast:1", \
            time.time() + 60
        assert addon._pool_pick()["http"] == "http://mid:1"
        addon._exit_busy_dec("http://fast:1")
        assert addon._pool_pick()["http"] == "http://fast:1"
    finally:
        _pool_state_reset()
        addon._EXIT_BUSY.clear()
        addon._PROXY_URLS = saved_urls
        addon._FREE_POOL[0] = saved_fp


def test_exit_token_bootstrap_is_single_flight():
    """v1.8.1: parallel callers of _exit_token on the SAME tokenless exit
    must share ONE bootstrap round-trip (a dubs+play wave used to fire
    one 6s bootstrap per thread through the same free proxy)."""
    import threading as _th
    calls = {"n": 0}
    calls_lock = _th.Lock()
    saved = addon._EXIT_TOKENS.get("http://x:1")
    addon._EXIT_TOKENS.pop("http://x:1", None)

    def fake_boot(u, timeout=6):
        with calls_lock:
            calls["n"] += 1
        time.sleep(0.4)                 # simulate the 6s round-trip
        addon._EXIT_TOKENS[u] = ("TOK-" + u, time.time())
        return "TOK-" + u

    orig = addon._bootstrap_via_exit
    addon._bootstrap_via_exit = fake_boot
    try:
        results = []

        def worker():
            results.append(addon._exit_token("http://x:1"))

        ts = [_th.Thread(target=worker) for _ in range(5)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert calls["n"] == 1, calls          # ONE bootstrap, not five
        assert all(r == "TOK-http://x:1" for r in results), results
    finally:
        addon._bootstrap_via_exit = orig
        if saved:
            addon._EXIT_TOKENS["http://x:1"] = saved
        else:
            addon._EXIT_TOKENS.pop("http://x:1", None)


def test_pool_refresh_merges_never_shrinks():
    """v1.8.1: a refresh whose fresh sample found few exits must not
    throw away known-healthy members (prod was seen at 8/4-healthy)."""
    import types
    saved_fp, saved_ts0 = list(addon._FREE_POOL[0]), addon._FREE_POOL_TS[0]
    saved_stats = dict(addon._POOL_STATS)
    saved_tok = dict(addon._EXIT_TOKENS)
    try:
        _pool_state_reset()
        # current pool: 20 trained, healthy members
        addon._FREE_POOL[0] = [f"http://old{i}:1" for i in range(20)]
        for u in addon._FREE_POOL[0]:
            addon._POOL_STATS[u] = {"ok": 5, "fail": 0, "lat": 700}
        addon._EXIT_TOKENS["http://old0:1"] = ("tok0", time.time())

        def fake_get(url, timeout=20):
            return types.SimpleNamespace(text="\n".join(
                f"http://new{i}:1" for i in range(10)) + "\n")

        def fake_probe(u, timeout=4):
            return ("good", 300) if u.endswith("new0:1") else ("dead", None)

        orig_get, orig_probe = addon.requests.get, addon._platform_probe
        orig_pool = list(addon._FREE_POOL[0])
        addon.requests.get = fake_get
        addon._platform_probe = fake_probe
        # force the refresh window open
        addon._FREE_POOL_TS[0] = time.time() - 999
        try:
            addon._free_pool_refresh()
        finally:
            addon.requests.get, addon._platform_probe = orig_get, orig_probe
        merged = addon._FREE_POOL[0]
        assert len(merged) == 20, len(merged)           # never shrinks
        assert "http://new0:1" in merged                # fast new find added
        assert "http://old0:1" in merged                # trained kept
        assert "http://new0:1" == merged[0]             # fastest first
        assert addon._EXIT_TOKENS.get("http://old0:1") == ("tok0",
                                                           addon._EXIT_TOKENS["http://old0:1"][1])  # token survived
        # stale entries got pruned
        assert "http://gone:1" not in addon._EXIT_TOKENS
    finally:
        _pool_state_reset()
        addon._FREE_POOL[0] = saved_fp
        addon._FREE_POOL_TS[0] = saved_ts0
        addon._POOL_STATS.clear(); addon._POOL_STATS.update(saved_stats)
        addon._EXIT_TOKENS.clear(); addon._EXIT_TOKENS.update(saved_tok)


def test_pool_pick_prefers_trained_exits():
    # among 5 healthy exits the worst-trained one must never be picked
    # (pick samples only the top-3 by score)
    saved_urls, saved_fp = list(addon._PROXY_URLS), list(addon._FREE_POOL[0])
    saved_stats = dict(addon._POOL_STATS)
    try:
        _pool_state_reset()
        addon._PROXY_URLS = []
        addon._FREE_POOL[0] = ["http://a:1", "http://b:1", "http://c:1",
                               "http://d:1", "http://e:1"]
        addon._POOL_STATS.clear()
        for u in ("http://a:1", "http://b:1", "http://c:1", "http://d:1"):
            addon._POOL_STATS[u] = {"ok": 10, "fail": 0, "lat": 500}
        addon._POOL_STATS["http://e:1"] = {"ok": 0, "fail": 10, "lat": 5000}
        for _ in range(40):
            assert addon._pool_pick()["http"] != "http://e:1"
        # latency feeds the blend: among 4 well-trained exits the slow one
        # (b, 4000ms EWMA) drops out of the top-3 and is never picked
        addon._POOL_STATS["http://a:1"] = {"ok": 10, "fail": 0, "lat": 200}
        addon._POOL_STATS["http://b:1"] = {"ok": 10, "fail": 0, "lat": 4000}
        addon._POOL_STATS["http://e:1"] = {"ok": 10, "fail": 0, "lat": 500}
        picked = set()
        for _ in range(60):
            picked.add(addon._pool_pick()["http"])
        assert "http://a:1" in picked            # fastest is in the top-3
        assert "http://b:1" not in picked        # slowest well-trained is not
    finally:
        _pool_state_reset()
        addon._PROXY_URLS = saved_urls
        addon._FREE_POOL[0] = saved_fp
        addon._POOL_STATS.clear()
        addon._POOL_STATS.update(saved_stats)

def test_pool_note_updates_training_record():
    saved_stats = dict(addon._POOL_STATS)
    try:
        _pool_state_reset()
        addon._POOL_STATS.clear()
        addon._POOL_TLS.url = "http://x:1"
        addon._POOL_TLS.t_req = time.time() - 0.25
        addon._pool_note("good")
        st = addon._POOL_STATS["http://x:1"]
        assert st["ok"] == 1 and 100 <= st["lat"] <= 2000
        assert addon._POOL_STICKY[0] == "http://x:1"
        addon._POOL_TLS.url = "http://x:1"
        addon._pool_note("good", 300)                 # explicit latency
        st = addon._POOL_STATS["http://x:1"]
        assert st["ok"] == 2 and st["lat"] <= 1000    # EWMA blended
        addon._POOL_TLS.url = "http://x:1"
        addon._pool_note("dead")
        st = addon._POOL_STATS["http://x:1"]
        assert st["fail"] == 1
        assert "http://x:1" in addon._POOL_BAD
    finally:
        _pool_state_reset()
        addon._POOL_STATS.clear()
        addon._POOL_STATS.update(saved_stats)

def test_pool_train_once_updates_stats_and_benches():
    saved_urls, saved_fp = list(addon._PROXY_URLS), list(addon._FREE_POOL[0])
    saved_stats = dict(addon._POOL_STATS)
    try:
        _pool_state_reset()
        addon._POOL_STATS.clear()
        addon._PROXY_URLS = []
        addon._FREE_POOL[0] = ["http://g:1", "http://b:1", "http://d:1"]
        results = {"http://g:1": ("good", 250),
                   "http://b:1": ("block", None),
                   "http://d:1": ("dead", None)}
        with mock.patch.object(addon, "_platform_probe",
                               side_effect=lambda u, timeout=4: results[u]):
            addon._pool_train_once()
        assert addon._POOL_STATS["http://g:1"]["ok"] == 1
        assert addon._POOL_STICKY[0] == "http://g:1"
        assert "http://b:1" in addon._POOL_BAD        # blocked: 30-min bench
        assert "http://d:1" in addon._POOL_BAD        # dead: 10-min bench
    finally:
        _pool_state_reset()
        addon._PROXY_URLS = saved_urls
        addon._FREE_POOL[0] = saved_fp
        addon._POOL_STATS.clear()
        addon._POOL_STATS.update(saved_stats)

def test_build_streams_transient_message_not_cached():
    addon._STREAM_CACHE.clear()
    key = ("movie", "tt9990001", None, None)
    try:
        with mock.patch.object(addon, "_meta_any",
                               return_value={"name": "Foo", "year": "2020"}), \
             mock.patch.object(addon, "_cached_search", return_value=None):
            r = addon.build_streams("movie", "tt9990001", None, None, _prewarm_next=False)
        assert r["streams"] == []
        assert "busy" in r.get("message", "")
        assert key not in addon._STREAM_CACHE       # transient: not cached
    finally:
        addon._STREAM_CACHE.clear()


# --- v1.7.2: localised-name rescue (alt titles + fuzzy match) ----------------

def test_fuzzy_match_guards():
    subs = [
        {"subjectId": "1", "title": "See You at Work Tomorrow! [Hindi]",
         "subjectType": 2, "releaseDate": "2026-06-22"},
        {"subjectId": "2", "title": "Demon Slayer: Infinity Castle Review",
         "subjectType": 1, "releaseDate": "2025-09-13"},
        {"subjectId": "3", "title": "Back to the Future",
         "subjectType": 2, "releaseDate": "1985-07-03"},
        {"subjectId": "4", "title": "Work Tomorrow Something Else Entirely Okay",
         "subjectType": 2, "releaseDate": "2019-01-01"},   # 50% overlap but wrong year
    ]
    hits = addon._fuzzy_match(subs, "Going to Work Tomorrow", "2026", 2)
    assert [h[0]["subjectId"] for h in hits] == ["1"]   # junk + weak-overlap + wrong-year rejected
    # no year info on the query side: only tokens guard
    hits2 = addon._fuzzy_match(subs, "Going to Work Tomorrow", "", 2)
    assert {h[0]["subjectId"] for h in hits2} == {"1", "4"}

def test_alt_titles_fetch_and_cache():
    addon._ALT_CACHE.clear()
    try:
        def fake_get(url, **kw):
            assert "alternative_titles" in url
            r = mock.Mock(status_code=200)
            r.json = lambda: {"titles": [
                {"title": "See You at Work Tomorrow!", "type": "alternative"},
                {"title": "李小姐明天也要上班", "type": "other"},      # non-latin: dropped
                {"title": "Going to Work Tomorrow", "type": "working title"},
                {"title": "", "type": "x"},
            ]}
            return r
        with mock.patch.object(addon.requests, "get", side_effect=fake_get):
            alts = addon._alt_titles("series", "289763")
        assert alts == ["See You at Work Tomorrow!", "Going to Work Tomorrow"]
        assert addon._alt_titles("series", "289763") == alts   # served from cache
        assert addon._alt_titles("series", "") == []           # no tmdb id: skip
    finally:
        addon._ALT_CACHE.clear()

def test_build_streams_alt_title_rescue():
    addon._STREAM_CACHE.clear()
    addon._SEARCH_CACHE.clear()
    sub = {"subjectId": "777", "title": "See You at Work Tomorrow! [Hindi]",
           "subjectType": 2, "releaseDate": "2026-06-22", "corner": "Hindi"}
    try:
        with mock.patch.object(addon, "_meta_any",
                               return_value={"name": "Back to Work!", "year": "2026",
                                             "tmdb": "289763"}), \
             mock.patch.object(addon, "_cached_search",
                               side_effect=[[], [sub]]), \
             mock.patch.object(addon, "_alt_titles",
                               return_value=["Going to Work Tomorrow"]), \
             mock.patch.object(addon, "_cached_dubs", return_value=[]), \
             mock.patch.object(addon, "_cached_play", return_value=None), \
             mock.patch.object(addon, "fetch_captions", return_value=[]), \
             mock.patch.object(addon, "_resolve_entry",
                               side_effect=lambda *a, **k: [{"name": "card", "url": "u"}]):
            r = addon.build_streams("series", "tt38960812", 1, 1, _prewarm_next=False)
        assert len(r["streams"]) == 1          # rescued via the alt title
        assert "message" not in r
    finally:
        addon._STREAM_CACHE.clear()
        addon._SEARCH_CACHE.clear()


# --- v1.7.3: metadata race (cinemeta / TMDB / IMDb-suggest) ------------------

def test_meta_any_race_fastest_wins():
    addon._CINEMETA_CACHE.clear()
    calls = {"c": 0, "t": 0, "i": 0}
    def slow_cinemeta(ctype, imdb):
        calls["c"] += 1
        time.sleep(0.5)
        return {"name": "FromCinemeta", "year": "2020", "tmdb": "111"}
    def fast_tmdb(ctype, imdb):
        calls["t"] += 1
        return {"name": "FromTMDB", "year": "2021", "tmdb": "222"}
    def imdb_sug(imdb):
        calls["i"] += 1
        return {"name": "FromIMDb", "year": "2022"}
    # v1.9.3: _meta_any returns as soon as ONE fast future wins — on a
    # single-CPU box the loser worker thread may not have been scheduled
    # yet, so reading `calls` immediately was racy (i stayed 0). Run the
    # race on a DEDICATED executor and shutdown(wait=True) as a barrier:
    # all three submitted tasks are guaranteed to have RUN before comparing.
    from concurrent.futures import ThreadPoolExecutor
    own_ex = ThreadPoolExecutor(max_workers=3)
    try:
        with mock.patch.object(addon, "_META_EX", own_ex), \
             mock.patch.object(addon, "cinemeta", side_effect=slow_cinemeta), \
             mock.patch.object(addon, "_tmdb_find_id", side_effect=fast_tmdb), \
             mock.patch.object(addon, "_imdb_suggest_id", side_effect=imdb_sug):
            t0 = time.time()
            v = addon._meta_any("movie", "tt99990001")
            dt = time.time() - t0
            v2 = addon._meta_any("movie", "tt99990001")   # cached
        own_ex.shutdown(wait=True)      # barrier: stragglers have now run
        assert v["name"] == "FromTMDB"           # fastest valid answer won
        assert dt < 0.45                          # did not wait for cinemeta
        assert v2 == v
        assert calls == {"c": 1, "t": 1, "i": 1}  # second call: cache hit, no calls
    finally:
        addon._CINEMETA_CACHE.clear()
        own_ex.shutdown(wait=True)

def test_meta_any_all_fail_transient():
    addon._CINEMETA_CACHE.clear()
    try:
        with mock.patch.object(addon, "cinemeta", return_value=None), \
             mock.patch.object(addon, "_tmdb_find_id", return_value=None), \
             mock.patch.object(addon, "_imdb_suggest_id", return_value=None):
            assert addon._meta_any("movie", "tt99990002") is None
            assert ("movie", "tt99990002") not in addon._CINEMETA_CACHE  # retry next time
    finally:
        addon._CINEMETA_CACHE.clear()


# --- v1.7.4: article-insensitive matching ------------------------------------

def test_match_leading_article_insensitive():
    subs = [{"subjectId": "1", "title": "The East Palace [Hindi]", "subjectType": 2,
             "releaseDate": "2026-07-17"},
            {"subjectId": "2", "title": "The East Palace", "subjectType": 2,
             "releaseDate": "2026-07-17"},
            {"subjectId": "3", "title": "East of Eden", "subjectType": 2,
             "releaseDate": "2008-08-25"}]
    # TMDB says "East Palace", the platform says "The East Palace"
    m = addon.match_subjects(subs, "East Palace", "2026", 2)
    assert {x[0]["subjectId"] for x in m} == {"1", "2"}
    # and the reverse: query has the article, candidate doesn't
    subs2 = [{"subjectId": "9", "title": "Quiet Place", "subjectType": 1,
              "releaseDate": "2018-04-03"}]
    m2 = addon.match_subjects(subs2, "A Quiet Place", "2018", 1)
    assert [x[0]["subjectId"] for x in m2] == ["9"]
    # unrelated still rejected
    m3 = addon.match_subjects(subs, "East of Eden", "2008", 2)
    assert [x[0]["subjectId"] for x in m3] == ["3"]



# --- v1.7.5: per-exit tokens, direct-auth flag, chain budget, search route --

SD_PATH = "/wefeed-mobile-bff/subject-api/search/v2"

def _v175_reset():
    addon._PLAT_CB_UNTIL = 0.0
    addon._PLAT_FAILS = 0
    addon._DIRECT_AUTH_FLAG[0] = 0.0
    addon._EXIT_TOKENS.clear()
    addon._SD_FALLBACK.clear()
    _pool_state_reset()
    _CHAIN_CLEAN()

def _CHAIN_CLEAN():
    for attr in ("t",):
        try:
            delattr(addon._CHAIN_DDL, attr)
        except Exception:
            pass
    try:
        delattr(addon._EGRESS, "pool")
    except Exception:
        pass

def test_catalog_search_path_route():
    # v1.7.5: Stremio's canonical /catalog/{type}/{id}/search={q}.json form
    _v175_reset()
    got = {}
    def fake_search(ctype, q):
        got["ctype"], got["q"] = ctype, q
        return {"metas": []}
    def fake_cat(ctype, cid, skip):
        got["cat"] = (ctype, cid, skip)
        return {"metas": []}
    with mock.patch.object(addon, "search_catalog", fake_search), \
         mock.patch.object(addon, "get_catalog", fake_cat):
        c = _http_get("/catalog/movie/moviebox-movies/search=dead%20lover.json")
        assert c["code"] == 200
        assert got["ctype"] == "movie" and got["q"] == "dead lover"
        c = _http_get("/catalog/series/netnaija-series/search=naagin.json")
        assert c["code"] == 200
        assert got["ctype"] == "series" and got["q"] == "naagin"
        # plain catalog + query-param search still work
        c = _http_get("/catalog/movie/moviebox-movies.json?skip=10")
        assert c["code"] == 200 and got["cat"] == ("movie", "moviebox-movies", 10)
        c = _http_get("/catalog/movie/moviebox-movies.json?search=moana")
        assert c["code"] == 200 and got["q"] == "moana"

def test_api_call_deadline_exhausted():
    _v175_reset()
    called = {"n": 0}
    def fake_request(*a, **k):
        called["n"] += 1
        raise AssertionError("no network after budget spent")
    try:
        addon._CHAIN_DDL.t = time.time() - 1        # budget gone
        with mock.patch.object(addon, "_bootstrap_token"), \
             mock.patch.object(addon.requests, "request", side_effect=fake_request):
            addon._AUTH_TOKEN = "tok"
            assert addon.api_call("GET", "/x") is None
        assert called["n"] == 0
    finally:
        _CHAIN_CLEAN()

def test_api_call_direct_auth_flag_rides_pool_with_exit_token():
    _v175_reset()
    saved_urls, saved_fp = list(addon._PROXY_URLS), list(addon._FREE_POOL[0])
    try:
        addon._PROXY_URLS = []
        addon._FREE_POOL[0] = ["http://f1:1"]
        addon._EXIT_TOKENS["http://f1:1"] = ("EXT", time.time())
        addon._DIRECT_AUTH_FLAG[0] = time.time() + 60    # direct flagged
        picks, auths = [], []
        def fake_request(method, url, **kw):
            px = kw.get("proxies") or {}
            picks.append(px.get("http"))
            auths.append((kw.get("headers") or {}).get("Authorization"))
            resp = mock.Mock(status_code=200)
            resp.headers = {}
            resp.json = lambda: {"code": 0, "message": "ok", "data": {"x": 1}}
            return resp
        with mock.patch.object(addon, "_bootstrap_token"), \
             mock.patch.object(addon.requests, "request", side_effect=fake_request):
            addon._AUTH_TOKEN = "GLOBAL"
            d = addon.api_call("POST", SD_PATH, "{}")
        assert d == {"x": 1}
        assert picks and picks[0] == "http://f1:1"   # direct skipped entirely
        assert auths[0] == "Bearer EXT"              # exit's own token
        assert getattr(addon._EGRESS, "pool", False) is True
    finally:
        addon._PROXY_URLS, addon._FREE_POOL[0] = saved_urls, saved_fp
        _v175_reset()

def test_api_call_direct_401_marks_flag_and_pool():
    _v175_reset()
    saved_urls, saved_fp = list(addon._PROXY_URLS), list(addon._FREE_POOL[0])
    try:
        addon._PROXY_URLS = []
        addon._FREE_POOL[0] = ["http://f1:1"]
        addon._EXIT_TOKENS["http://f1:1"] = ("EXT", time.time())
        addon._AUTH_REAUTH_TS = time.time()          # throttle reauth
        picks = []
        def fake_request(method, url, **kw):
            px = kw.get("proxies") or {}
            picks.append(px.get("http"))
            if px.get("http") is None:
                resp = mock.Mock(status_code=401)    # the NEW flag signature
                resp.headers = {}
                resp.json = lambda: {"code": 401, "reason": "AUTH_FAIL"}
                return resp
            resp = mock.Mock(status_code=200)
            resp.headers = {}
            resp.json = lambda: {"code": 0, "message": "ok", "data": {"x": 2}}
            return resp
        with mock.patch.object(addon, "_bootstrap_token"), \
             mock.patch.object(addon.requests, "request", side_effect=fake_request):
            addon._AUTH_TOKEN = "tok"
            d = addon.api_call("POST", SD_PATH, "{}")
        assert d == {"x": 2}
        assert picks[0] is None and picks[1] == "http://f1:1"
        assert addon._DIRECT_AUTH_FLAG[0] > time.time()   # direct benched 10 min
        assert addon._sd_forced(SD_PATH)                  # family rides pool now
    finally:
        addon._PROXY_URLS, addon._FREE_POOL[0] = saved_urls, saved_fp
        addon._AUTH_REAUTH_TS = 0.0
        _v175_reset()

def test_api_call_pool_401_benches_exit_and_rotates():
    _v175_reset()
    saved_urls, saved_fp = list(addon._PROXY_URLS), list(addon._FREE_POOL[0])
    try:
        addon._PROXY_URLS = []
        addon._FREE_POOL[0] = ["http://f1:1", "http://f2:2"]
        addon._EXIT_TOKENS["http://f1:1"] = ("T1", time.time())
        addon._EXIT_TOKENS["http://f2:2"] = ("T2", time.time())
        addon._DIRECT_AUTH_FLAG[0] = time.time() + 60    # straight to pool
        seq = []
        def fake_request(method, url, **kw):
            px = kw.get("proxies") or {}
            u = px.get("http")
            seq.append(u)
            if u == "http://f1:1":
                resp = mock.Mock(status_code=401)        # f1's token is dead
            else:
                resp = mock.Mock(status_code=200)
                resp.json = lambda: {"code": 0, "message": "ok", "data": {"x": 3}}
            resp.headers = {}
            if u == "http://f1:1":
                resp.json = lambda: {"code": 401, "reason": "AUTH_FAIL"}
            return resp
        def fake_via_exit(u, timeout=6):
            return None                                  # refresh fails
        # _pool_pick() is random among top-3 — FORCE the dead exit (f1)
        # first so the bench-and-rotate path is always exercised; after
        # f1 is benched the real picker can only choose f2.
        _real_pick = addon._pool_pick
        _forced = {"yes": True}

        def fake_pick():
            if _forced["yes"]:
                _forced["yes"] = False
                # mimic _pool_pick's thread-local bookkeeping exactly —
                # _pool_note() benches whatever _POOL_TLS.url points at
                addon._POOL_TLS.url = "http://f1:1"
                addon._POOL_TLS.t_req = time.time()
                return {"http": "http://f1:1", "https": "http://f1:1"}
            return _real_pick()
        with mock.patch.object(addon, "_bootstrap_token"), \
             mock.patch.object(addon, "_bootstrap_via_exit", fake_via_exit), \
             mock.patch.object(addon, "_pool_pick", side_effect=fake_pick), \
             mock.patch.object(addon.requests, "request", side_effect=fake_request):
            addon._AUTH_TOKEN = "tok"
            d = addon.api_call("POST", SD_PATH, "{}")
        assert d == {"x": 3}
        assert "http://f1:1" in seq and "http://f2:2" in seq
        assert addon._POOL_BAD.get("http://f1:1", 0) > time.time()  # benched
    finally:
        addon._PROXY_URLS, addon._FREE_POOL[0] = saved_urls, saved_fp
        _v175_reset()

def test_platform_probe_captures_exit_token():
    _v175_reset()
    try:
        def hdr_resp(status, xuser=None):
            r = mock.Mock(status_code=status)
            r.headers = {"x-user": xuser} if xuser else {}
            return r
        with mock.patch.object(addon.requests, "get",
                               side_effect=[hdr_resp(200, '{"token": "T1"}'),
                                            hdr_resp(200),
                                            hdr_resp(401)]):
            k1 = addon._platform_probe("http://e1:1")
            k2 = addon._platform_probe("http://e2:2")
            k3 = addon._platform_probe("http://e3:3")
        assert k1[0] == "good" and addon._EXIT_TOKENS["http://e1:1"][0] == "T1"
        assert k2 == (None, None)          # header dropped -> unusable exit
        assert k3 == ("block", None)       # 401 -> exit IP auth-flagged
    finally:
        _v175_reset()

def test_exit_token_cache_and_stale_fallback():
    _v175_reset()
    try:
        now = time.time()
        addon._EXIT_TOKENS["http://e:1"] = ("FRESH", now)
        assert addon._exit_token("http://e:1") == "FRESH"     # cached, no HTTP
        with mock.patch.object(addon, "_bootstrap_via_exit", return_value="NEW"):
            assert addon._exit_token("http://e:1", refresh=True) == "NEW"
        addon._EXIT_TOKENS["http://e:1"] = ("OLD", now - 7 * 3600)
        with mock.patch.object(addon, "_bootstrap_via_exit", return_value=None):
            assert addon._exit_token("http://e:1") == "OLD"   # stale beats none
        assert addon._exit_token(None) is None
    finally:
        _v175_reset()

def test_bootstrap_via_exit_stores_token():
    _v175_reset()
    try:
        r = mock.Mock(status_code=200)
        r.headers = {"x-user": '{"token": "VIAX"}'}
        with mock.patch.object(addon.requests, "get", return_value=r):
            assert addon._bootstrap_via_exit("http://e:9") == "VIAX"
        assert addon._EXIT_TOKENS["http://e:9"][0] == "VIAX"
    finally:
        _v175_reset()

def test_neg_ttl_pool_vs_direct():
    _v175_reset()
    try:
        addon._EGRESS.pool = False
        assert addon._neg_ttl() == 600
        addon._EGRESS.pool = True
        assert addon._neg_ttl() == 60        # pool answers may be proxy lies
    finally:
        _CHAIN_CLEAN()

def test_build_streams_sets_and_clears_budget():
    _v175_reset()
    saved_cache = dict(addon._STREAM_CACHE)
    saved_stale = dict(addon._STREAM_STALE)
    try:
        addon._STREAM_CACHE.clear()
        addon._STREAM_STALE.clear()
        seen = {}
        def fake_inner(*a, **k):
            seen["ddl"] = getattr(addon._CHAIN_DDL, "t", None)
            return {"streams": [], "message": "x"}
        with mock.patch.object(addon, "_build_streams_inner", side_effect=fake_inner):
            r = addon.build_streams("movie", "tt0000001", 1, 1)
        assert r == {"streams": [], "message": "x"}
        assert seen["ddl"] is not None and seen["ddl"] > time.time() + 20
        # v1.7.8: the budget is armed on the BUILD worker thread; the
        # calling thread must not carry a lingering deadline either way.
        assert getattr(addon._CHAIN_DDL, "t", None) is None
    finally:
        addon._STREAM_CACHE.clear()
        addon._STREAM_CACHE.update(saved_cache)
        addon._STREAM_STALE.clear()
        addon._STREAM_STALE.update(saved_stale)
        _v175_reset()

def test_debug_search_endpoint_gated_and_shaped():
    _v175_reset()
    try:
        assert _http_get("/debug/search")["code"] == 404
        assert _http_get("/debug/search?k=wrong")["code"] == 404
        saved_fp = list(addon._FREE_POOL[0])
        addon._FREE_POOL[0] = []                    # no exits -> direct only
        def fake_post(url, **kw):
            r = mock.Mock(status_code=200)
            r.headers = {}
            r.json = lambda: {"code": 0, "message": "ok",
                              "data": {"results": [{"subjects": [
                                  {"subjectId": 1}, {"subjectId": 2}]}]}}
            return r
        with mock.patch.object(addon.requests, "post", side_effect=fake_post):
            c = _http_get("/debug/search?k=mbx-dbg-7f3a&kw=moana")
        assert c["code"] == 200
        d = json.loads(c["body"])
        assert d["kw"] == "moana" and d["direct"]["hits"] == 2
        assert "exits" in d and "exit_tokens" in d
    finally:
        _v175_reset()


# --- v1.7.6: cold-path speed (parallel alt rescue, one-wave dubs+play) ------

def test_v176_alt_rescue_parallel():
    """3 alt-title searches at 0.45s each must run CONCURRENTLY (one wave,
    <=1.3s incl. the 0.45s primary), not serially (>=1.8s); evaluation
    stays in priority order and the 3rd alt's match wins."""
    addon._STREAM_CACHE.clear()
    addon._SEARCH_CACHE.clear()
    sub = {"subjectId": "555", "title": "Alt Three Show [Hindi]",
           "subjectType": 2, "releaseDate": "2026-01-01", "corner": "Hindi"}
    searched = []

    def slow_search(kw, stype):
        searched.append(kw)
        time.sleep(0.45)
        return [sub] if kw == "Alt Three" else []
    try:
        with mock.patch.object(addon, "WEB_MP4_ON", False), \
             mock.patch.object(addon, "_meta_any",
                               return_value={"name": "Primary Name",
                                             "year": "2026", "tmdb": "999"}), \
             mock.patch.object(addon, "_alt_titles",
                               return_value=["Alt One", "Alt Two", "Alt Three"]), \
             mock.patch.object(addon, "_cached_search", side_effect=slow_search), \
             mock.patch.object(addon, "match_subjects", return_value=[]), \
             mock.patch.object(addon, "_fuzzy_match",
                               side_effect=lambda subs, q, y, st:
                                   [(sub, "Hindi")] if q == "Alt Three" else []), \
             mock.patch.object(addon, "_cached_dubs", return_value=[]), \
             mock.patch.object(addon, "_cached_play", return_value=None), \
             mock.patch.object(addon, "_resolve_entry",
                               side_effect=lambda *a, **k: [{"name": "c", "url": "u"}]):
            t0 = time.time()
            r = addon.build_streams("series", "tt76000176", 1, 1,
                                    _prewarm_next=False)
            dt = time.time() - t0
        assert r.get("streams"), r
        assert set(searched) == {"Primary Name", "Alt One", "Alt Two",
                                 "Alt Three"}, searched
        assert dt < 1.3, dt          # one parallel wave, not three serial ones
    finally:
        addon._STREAM_CACHE.clear()
        addon._SEARCH_CACHE.clear()


def test_v176_dubs_and_play_share_one_wave():
    """dubs (0.5s) and play-info (0.5s) for the top matches run in the SAME
    executor wave (~0.5s total) instead of back-to-back waves (~1.0s)."""
    addon._STREAM_CACHE.clear()
    addon._SEARCH_CACHE.clear()
    sub = {"subjectId": "444", "title": "Wave Show", "subjectType": 1,
           "releaseDate": "2026-01-01", "corner": "Original"}

    def slow_dubs(sid):
        time.sleep(0.7)
        return []

    _pmemo = {}

    def slow_play(sid, se=None, ep=None):
        k = (str(sid), se, ep)
        if k not in _pmemo:                    # memoize like the real cache
            time.sleep(0.5)                     # (shorter than dubs: the
                                               #  unjoined prefetch finishes
                                               #  while the dubs wave runs)
            _pmemo[k] = {"streams": [{"id": "s9", "signCookie": FAKE_COOKIE,
                                       "size": 1000, "duration": 3600}]}
        return _pmemo[k]
    try:
        with mock.patch.object(addon, "WEB_MP4_ON", False), \
             mock.patch.object(addon, "_meta_any",
                               return_value={"name": "Wave Show",
                                             "year": "2026", "tmdb": ""}), \
             mock.patch.object(addon, "_cached_search", return_value=[sub]), \
             mock.patch.object(addon, "_cached_dubs", side_effect=slow_dubs), \
             mock.patch.object(addon, "_cached_play", side_effect=slow_play), \
             mock.patch.object(addon, "fetch_captions", return_value=[]), \
             mock.patch.object(addon, "_resolve_entry",
                               side_effect=lambda *a, **k: [{"name": "c", "url": "u"}]):
            t0 = time.time()
            r = addon.build_streams("movie", "tt76000177", 1, 1,
                                    _prewarm_next=False)
            dt = time.time() - t0
        assert r.get("streams"), r
        assert dt < 0.85, dt         # overlapped waves, not sequential
    finally:
        addon._STREAM_CACHE.clear()
        addon._SEARCH_CACHE.clear()


def test_v176_alt_titles_prefetched_with_primary_search():
    """alt titles are kicked off CONCURRENTLY with the primary platform
    search (not lazily at rescue time) — the fetch already ran even though
    the primary title matched directly."""
    addon._STREAM_CACHE.clear()
    addon._SEARCH_CACHE.clear()
    sub = {"subjectId": "333", "title": "Instant Show", "subjectType": 1,
           "releaseDate": "2026-01-01", "corner": "Original"}
    alt_t, search_t = [], []

    def alt_fetch(ctype, tmdb_id):
        alt_t.append(time.time())
        time.sleep(0.3)
        return ["Other Name"]

    def search(kw, stype):
        search_t.append(time.time())
        return [sub]
    try:
        with mock.patch.object(addon, "_meta_any",
                               return_value={"name": "Instant Show",
                                             "year": "2026", "tmdb": "31337"}), \
             mock.patch.object(addon, "_alt_titles", side_effect=alt_fetch), \
             mock.patch.object(addon, "_cached_search", side_effect=search), \
             mock.patch.object(addon, "_cached_dubs", return_value=[]), \
             mock.patch.object(addon, "_cached_play", return_value=None), \
             mock.patch.object(addon, "_resolve_entry",
                               side_effect=lambda *a, **k: [{"name": "c", "url": "u"}]):
            r = addon.build_streams("movie", "tt76000178", 1, 1,
                                    _prewarm_next=False)
        assert r.get("streams"), r
        assert alt_t and search_t, (alt_t, search_t)
        assert abs(alt_t[0] - search_t[0]) < 0.15, (alt_t, search_t)
    finally:
        addon._STREAM_CACHE.clear()
        addon._SEARCH_CACHE.clear()




# --- v1.7.8: stream wall + bounded grinds ----------------------------------

def test_v178_stream_wall_honest_answer():
    """A cold build that exceeds the wall answers honestly and fast, and
    caches nothing (the prod 6.5min+ hang scenario)."""
    key = ("movie", "tt17800001", 1, 1)
    addon._STREAM_CACHE.pop(key, None)

    def slow_inner(*a, **k):
        time.sleep(1.2)
        return {"streams": [{"name": "late", "url": "u"}]}
    orig_wall = addon._STREAM_WALL
    try:
        addon._STREAM_WALL = 0.3
        with mock.patch.object(addon, "_build_streams_inner",
                               side_effect=slow_inner):
            t0 = time.time()
            r = addon.build_streams("movie", "tt17800001", 1, 1)
            el = time.time() - t0
        assert r.get("streams") == [] and \
            "platform slow" in (r.get("message") or ""), r
        assert el < 1.0, el
        assert key not in addon._STREAM_CACHE, "wall miss must not cache"
    finally:
        addon._STREAM_WALL = orig_wall
        addon._STREAM_CACHE.pop(key, None)
        time.sleep(1.3)          # let the orphaned build finish quietly


def test_v178_stream_wall_passthrough_when_fast():
    """A fast build passes through the wall untouched."""
    def fast_inner(*a, **k):
        return {"streams": [{"name": "quick", "url": "u"}]}
    with mock.patch.object(addon, "WEB_MP4_ON", False), \
         mock.patch.object(addon, "_build_streams_inner",
                           side_effect=fast_inner):
        r = addon.build_streams("movie", "tt17800002", 1, 1)
    assert r.get("streams") and r["streams"][0]["name"] == "quick", r


def test_v178_api_call_wall_caps_rotation():
    """api_call stops rotating hosts after _API_CALL_WALL even on a thread
    with no chain deadline (the deadline-less executor-worker case)."""
    calls = []

    def slow_req(*a, **k):
        calls.append(time.time())
        time.sleep(0.15)
        raise addon.requests.RequestException("dead")
    orig_wall = addon._API_CALL_WALL
    orig_tok = addon._AUTH_TOKEN
    try:
        addon._API_CALL_WALL = 0.25
        addon._AUTH_TOKEN = "tok"          # skip the bootstrap branch
        with mock.patch.object(addon.requests, "request",
                               side_effect=slow_req), \
             mock.patch.object(addon, "_note_plat"):
            t0 = time.time()
            r = addon.api_call("GET",
                               "/wefeed-mobile-bff/subject-api/get?subjectId=1")
            el = time.time() - t0
        assert r is None, r
        assert len(calls) <= 3, (len(calls), el)   # wall stopped the grind
        assert el < 1.2, el
    finally:
        addon._API_CALL_WALL = orig_wall
        addon._AUTH_TOKEN = orig_tok
        addon._HOST_BAD.clear()


def test_v178_play_wait_bounded():
    """_cached_play gives up after _PLAY_WAIT instead of waiting forever."""
    def slow_play(s, se, ep):
        time.sleep(1.0)
        return {"streams": []}
    orig = addon._PLAY_WAIT
    try:
        addon._PLAY_WAIT = 0.2
        with mock.patch.object(addon, "play_info", side_effect=slow_play):
            t0 = time.time()
            v = addon._cached_play("178p", 1, 1)
            el = time.time() - t0
        assert v is None and el < 0.7, (v, el)
        assert ("178p", 1, 1) not in addon._PLAY_CACHE
    finally:
        addon._PLAY_WAIT = orig
        addon._PLAY_CACHE.pop(("178p", 1, 1), None)
        addon._PLAY_INFLIGHT.clear()


def test_v178_ddl_inherit():
    """Executor workers see the submitting thread's chain deadline."""
    from concurrent.futures import ThreadPoolExecutor
    ddl = time.time() + 5.0
    addon._CHAIN_DDL.t = ddl
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            bare = ex.submit(
                lambda: getattr(addon._CHAIN_DDL, "t", None)).result()
        assert bare is None, bare          # unwrapped: deadline-less
        wrapped = addon._ddl_inherit(
            lambda: getattr(addon._CHAIN_DDL, "t", None))
        with ThreadPoolExecutor(max_workers=1) as ex:
            seen = ex.submit(wrapped).result()
        assert seen is not None and abs(seen - ddl) < 0.5, seen
        with ThreadPoolExecutor(max_workers=1) as ex:
            after = ex.submit(
                lambda: getattr(addon._CHAIN_DDL, "t", None)).result()
        assert after is None, after        # cleared after the wrapped run
    finally:
        addon._CHAIN_DDL.t = None


def test_v178_pool_pick_prefers_token_exits():
    """A token-carrying exit outranks identical tokenless exits."""
    pool = ["http://e%d:1" % i for i in range(5)]
    addon._FREE_POOL[0] = list(pool)
    addon._POOL_BAD.clear()
    addon._POOL_STICKY[0] = None
    addon._POOL_STATS.clear()
    try:
        # token on the LAST exit: without the bonus a stable sort ranks it
        # 5th and it never enters ranked[:3]; with the bonus it ranks FIRST.
        with addon._EXIT_TOKENS_LOCK:
            addon._EXIT_TOKENS[pool[4]] = ("tok", time.time())
        counts = {}
        for _ in range(200):
            u = addon._pool_pick()["http"]
            counts[u] = counts.get(u, 0) + 1
        assert counts.get(pool[4], 0) > 0, counts      # token exit picked
        assert counts.get(pool[3], 0) == 0, counts     # non-top3 never
        assert counts.get(pool[2], 0) == 0, counts
    finally:
        with addon._EXIT_TOKENS_LOCK:
            addon._EXIT_TOKENS.pop(pool[4], None)
        addon._FREE_POOL[0] = []
        addon._POOL_STATS.clear()



def test_strict_zero_routes_gone():
    """v1.9.4 serving policy: /hls master+variant PLAYLIST TEXT routes exist
    by design (user directive: bring the quality menu back — segments in
    them are absolute self-signed CloudFront URLs, so media bytes still
    never pass through this server). Everything else must stay gone:
    /dash manifests, /sub subtitle relay, /seg, media MIME types."""
    src = open("addon.py").read()
    for gone in ("def dash_manifest", "def _lazy_sub", "def _srt_to_vtt",
                 "_trim_timeline_body", "_VTT_CACHE",
                 '"/dash/', '"/sub/', '"/seg/'):
        assert gone not in src, gone
    # the /hls route ONLY ever matches playlist filenames — no segments
    assert '(master|v\\d+|a\\d+)\\.m3u8' in src   # playlist filenames only
    # hermetic route check: unknown sid -> honest 404 text (no network use
    # here: _cached_play is mocked empty)
    with mock.patch.object(addon, "_cached_play", return_value=None):
        for path in ("/hls/1883954740090864536/0/0/master.m3u8",
                     "/hls/1883954740090864536/0/0/v0.m3u8",
                     "/dash/1883954740090864536/0/0/manifest.mpd",
                     "/sub/1883954740090864536/0/0/en.vtt"):
            r = _http_get(path)
            assert r["code"] == 404, (path, r)

def test_direct_subs_shape():
    """v1.9.6: sub filter REVERTED (user rule — it saved no meaningful
    Render bandwidth) — ALL languages back by default; only malformed
    entries (no lan / no url) are dropped."""
    caps = [{"lan": "en", "url": "https://cacdn.x/subtitle/abc"},
            {"lan": "hi", "url": "https://cacdn.x/subtitle/hin"},
            {"lan": "ara", "url": "https://cacdn.x/subtitle/ara"},
            {"lan": "por", "url": "https://cacdn.x/subtitle/por"},
            {"lan": "", "url": "https://cacdn.x/subtitle/nn"},   # dropped
            {"lan": "fr"}]                                       # dropped
    subs = addon._direct_subs(caps)
    assert [s["url"] for s in subs] == ["https://cacdn.x/subtitle/abc",
                                        "https://cacdn.x/subtitle/hin",
                                        "https://cacdn.x/subtitle/ara",
                                        "https://cacdn.x/subtitle/por"]
    assert subs[0]["lang"] == "eng" and subs[0]["id"] == "mbx-en"
    assert subs[1]["lang"] == "hin" and subs[2]["lang"] == "ara"

def test_direct_subs_filter_env_override():
    """MOVIEBOX_SUBS still available: explicit list filters, default () = all."""
    caps = [{"lan": "en", "url": "u1"}, {"lan": "fr", "url": "u2"}]
    assert len(addon._direct_subs(caps)) == 2          # v1.9.6 default: all
    with mock.patch.object(addon, "_SUB_LANGS", ("fr",)):
        assert [s["url"] for s in addon._direct_subs(caps)] == ["u2"]


if __name__ == "__main__":
    main()


def test_strict_zero_routes_gone():
    """v1.9.4 serving policy: /hls master+variant PLAYLIST TEXT routes exist
    by design (user directive: bring the quality menu back — segments in
    them are absolute self-signed CloudFront URLs, so media bytes still
    never pass through this server). Everything else must stay gone:
    /dash manifests, /sub subtitle relay, /seg, media MIME types."""
    src = open("addon.py").read()
    for gone in ("def dash_manifest", "def _lazy_sub", "def _srt_to_vtt",
                 "_trim_timeline_body", "_VTT_CACHE",
                 '"/dash/', '"/sub/', '"/seg/'):
        assert gone not in src, gone
    # the /hls route ONLY ever matches playlist filenames — no segments
    assert '(master|v\\d+|a\\d+)\\.m3u8' in src   # playlist filenames only
    # hermetic route check: unknown sid -> honest 404 text (no network use
    # here: _cached_play is mocked empty)
    with mock.patch.object(addon, "_cached_play", return_value=None):
        for path in ("/hls/1883954740090864536/0/0/master.m3u8",
                     "/hls/1883954740090864536/0/0/v0.m3u8",
                     "/dash/1883954740090864536/0/0/manifest.mpd",
                     "/sub/1883954740090864536/0/0/en.vtt"):
            r = _http_get(path)
            assert r["code"] == 404, (path, r)



# --------------------------------------------------------------------------
# v1.9.1 — web per-resolution direct MP4 cards
# --------------------------------------------------------------------------
def test_web_lang_map_matching():
    items = [
        {"title": "Our Sticky Love", "subjectId": "111", "detailPath": "osl"},
        {"title": "Our Sticky Love [English]", "subjectId": "222", "detailPath": "osl-en"},
        {"title": "Our Sticky Love [Hindi]", "subjectId": "333", "detailPath": "osl-hi"},
        {"title": "Our Sticky Love: Another Thing", "subjectId": "444", "detailPath": "x"},
        {"title": "Totally Different", "subjectId": "555", "detailPath": "y"},
    ]
    class R:
        status_code = 200
        def json(self):
            return {"data": {"items": items}}
    with mock.patch.object(addon, "_web_jwt", return_value="tok"), \
         mock.patch.object(addon.requests, "post", return_value=R()):
        addon._WEB_LANG_CACHE.clear()
        m = addon._web_lang_map("Our Sticky Love", "series")
    assert m.get("") == ("111", "osl")
    assert m.get("english") == ("222", "osl-en")
    assert m.get("hindi") == ("333", "osl-hi")
    assert len(m) == 3

def test_web_cards_for_resolutions():
    st = [(1080, "https://bcdnx/hi.mp4?sign=1", 877930434, "h264", 4059),
          (480, "https://bcdnx/md.mp4?sign=2", 279436151, "h264", 4059),
          (360, "https://bcdnx/lo.mp4?sign=3", 190165666, "h264", 4059)]
    with mock.patch.object(addon, "WEB_MP4_ON", True), \
         mock.patch.object(addon, "_web_mp4_streams", return_value=st):
        cards = addon._web_cards_for("Our Sticky Love", "Hindi", "series",
                                     1, 5, "mob", {"hindi": ("333", "osl-hi")})
    assert len(cards) == 3
    assert cards[0]["url"].endswith("sign=1")
    assert "1080p" in cards[0]["description"]
    assert cards[0]["behaviorHints"]["notWebReady"] is False
    assert "proxyHeaders" not in cards[0]["behaviorHints"]
    assert cards[0]["bingeGroup"].startswith("mbxw|")
    # original label -> "" key
    with mock.patch.object(addon, "WEB_MP4_ON", True), \
         mock.patch.object(addon, "_web_mp4_streams", return_value=st):
        cards2 = addon._web_cards_for("T", "Original", "movie", 0, 0, "m", {"": ("1", "d")})
    assert len(cards2) == 3
    # unknown dub and empty map -> no cards (NEVER fall back to the
    # original audio — a (Hindi) card must play Hindi audio)
    assert addon._web_cards_for("T", "Tamil", "movie", 0, 0, "m", {"": ("1", "d")}) == []
    assert addon._web_cards_for("T", "Hindi", "movie", 0, 0, "m", {}) == []

def test_web_cards_disabled_by_env_flag():
    with mock.patch.object(addon, "WEB_MP4_ON", False), \
         mock.patch.object(addon, "_web_mp4_streams",
                           return_value=[(480, "u", 1, "h264", 100)]) as ws:
        c = addon._web_cards_for("T", "Original", "movie", 0, 0, "m", {"": ("1", "d")})
    assert c == [] and not ws.called

def test_web_cards_ahead_of_dash_in_resolve():
    addon._MPD_CACHE.clear(); addon._PLAY_CACHE.clear(); addon._WEB_LANG_CACHE.clear(); addon._WEB_MP4_CACHE.clear()
    pi = {"streams": [{"signCookie": FAKE_COOKIE, "url": "https://macdn/x.mp4",
                       "resolutions": "1080,720,480", "size": "1462281731",
                       "duration": 4059, "codecName": "hevc", "id": "1"}]}
    st = [(720, "https://bcdnx/720.mp4?sign=x", 856000000, "h264", 9000)]
    web_langs = {"": ("9048868765454191080", "dune-WLVlz3JUrMa")}
    with mock.patch.object(addon, "WEB_MP4_ON", True), \
         mock.patch.object(addon, "_cached_play", return_value=pi), \
         mock.patch.object(addon, "_web_mp4_streams", return_value=st):
        out = addon._resolve_entry(("123", "Original"), 0, 0, "movie",
                                   "Dune", "2021", caps=[], web_langs=web_langs)
    assert out and len(out) == 2
    assert out[0]["url"].startswith("https://bcdnx/")       # web card first
    assert out[1]["url"].endswith("/index.mpd")             # dash fallback


# --- v1.9.3: cache pruning (unbounded-growth fix) ----------------------------

def test_cache_put_sweeps_expired():
    store = {}
    with mock.patch.object(addon, "_CACHE_SWEEP_AT", 3):
        addon._cache_put(store, "old1", 1, -10)     # already expired
        addon._cache_put(store, "old2", 2, -10)
        addon._cache_put(store, "live", 3, 600)
        assert len(store) == 3
        addon._cache_put(store, "new", 4, 600)      # hits cap -> sweep
    assert "old1" not in store and "old2" not in store
    assert "live" in store and "new" in store       # fresh entries survive
    hit, val = _cache_get_check(store, "live")
    assert hit and val == 3

def _cache_get_check(store, k):
    ent = store.get(k)
    return (True, ent[0]) if ent else (False, None)

def test_stream_stale_sweeps_expired():
    addon._STREAM_STALE.clear()
    now = time.time()
    with mock.patch.object(addon, "_STALE_SWEEP_AT", 2):
        addon._STREAM_STALE["a"] = (now - 5, [{"x": 1}])     # expired
        addon._STREAM_STALE["b"] = (now + 3600, [{"x": 2}])  # fresh
        addon._stale_put("c", [{"x": 3}])                    # write triggers sweep
        assert "a" not in addon._STREAM_STALE
        assert "b" in addon._STREAM_STALE and "c" in addon._STREAM_STALE
        assert addon._STREAM_STALE["b"][1] == [{"x": 2}]     # fresh untouched
    addon._STREAM_STALE.clear()

def test_no_media_routes_remain():
    """Strict zero: none of the pre-v1.9.0 media-serving routes may exist
    (v1.9.4 deliberately restored ONLY the /hls playlist-text routes)."""
    import re as _re
    src = open("addon.py").read()
    for gone in ("def dash_manifest", "def _lazy_sub", "def _srt_to_vtt",
                 "_trim_timeline_body", "_VTT_CACHE"):
        assert gone not in src, gone
    assert "def hls_master" in src and "def hls_media" in src   # v1.9.4: back


# --- v1.9.4: quality-menu HLS layer ------------------------------------------

MPDINFO_FIX = {
    "video": [{"id": "0", "height": 1080, "width": 1920, "bw": 1600000, "codecs": "hev1"},
              {"id": "1", "height": 720, "width": 1280, "bw": 800000, "codecs": "hev1"}],
    "audio": [{"id": "3", "lang": "hin", "bw": 128000, "codecs": "mp4a.40.2"}],
    "dur": 1440.0, "seg_dur": 5.0,
    "tl": {"video": [5.0, 5.0, 5.0, 5.0], "audio": [5.0, 5.0, 5.0, 5.0]},
}
CF_FIX = {"CloudFront-Policy": "POL", "CloudFront-Signature": "SIG",
          "CloudFront-Key-Pair-Id": "KP"}
SESS_FIX = {"dash": "https://sacdn.hakunaymatata.com/dash/999888",
            "cf": CF_FIX, "mpd": MPDINFO_FIX}

def test_v194_hls_master_shape():
    m = addon.hls_master(SESS_FIX)
    assert m.startswith("#EXTM3U")
    assert '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud"' in m and 'URI="a0.m3u8"' in m
    assert "\nv0.m3u8\n" in m and "\nv1.m3u8\n" in m
    assert 'RESOLUTION=1920x1080' in m and 'RESOLUTION=1280x720' in m
    assert "hvc1" in m and "hev1" not in m.split("#EXT-X-STREAM-INF")[1]  # hev1->hvc1
    assert "#EXT-X-STREAM-INF" in m

def test_v194_hls_media_signed_direct_segments():
    with mock.patch.object(addon, "_last_good_seg", side_effect=lambda d, r, n, c: n):
        p = addon.hls_media(SESS_FIX, "0", "v")
    assert p.startswith("#EXTM3U") and "#EXT-X-ENDLIST" in p
    assert '#EXT-X-MAP:URI="https://sacdn.hakunaymatata.com/dash/999888/init-stream0.m4s?' in p
    assert "Policy=POL" in p and "Signature=SIG" in p and "Key-Pair-Id=KP" in p
    segs = [l for l in p.splitlines() if l.startswith("https://")]
    assert len(segs) == 4                        # tl trimmed to 4 segments
    assert "chunk-stream0-00001.m4s?" in segs[0] and "chunk-stream0-00004.m4s?" in segs[-1]
    assert p.count("#EXTINF:5.0,") == 4        # real timeline durations
    assert "bytes" not in p                      # urls are absolute direct — no relay path

def test_v194_hls_media_no_timeline_fallback():
    sess = dict(SESS_FIX, mpd=dict(MPDINFO_FIX, tl={}))
    with mock.patch.object(addon, "_last_good_seg", side_effect=lambda d, r, n, c: n):
        p = addon.hls_media(sess, "3", "a")
    assert "#EXT-X-ENDLIST" in p
    # dur 1440s / seg 5s -> 288 segments
    assert len([l for l in p.splitlines() if l.startswith("https://")]) == 288

def test_v194_lazy_hls_route_serves_playlists():
    addon._MPD_CACHE.clear()
    pi = {"streams": [{"id": "42", "signCookie": FAKE_COOKIE}]}
    dash = addon._dash_base(CF_FIX and addon._cf_parts(FAKE_COOKIE)["CloudFront-Policy"])
    addon._MPD_CACHE[dash] = (MPDINFO_FIX, time.time() + 600)
    try:
        with mock.patch.object(addon, "_cached_play", return_value=pi), \
             mock.patch.object(addon, "_last_good_seg",
                               side_effect=lambda d, r, n, c: n):
            r = _http_get("/hls/11111/1/5/master.m3u8")
            assert r["code"] == 200
            assert r["headers"]["Content-Type"] == "application/vnd.apple.mpegurl"
            body = r["body"].decode()
            assert body.startswith("#EXTM3U") and "\nv0.m3u8\n" in body
            r2 = _http_get("/hls/11111/1/5/v0.m3u8")
            assert r2["code"] == 200 and "chunk-stream0-00001.m4s" in r2["body"].decode()
            r3 = _http_get("/hls/11111/1/5/a0.m3u8")
            assert r3["code"] == 200 and "init-stream3.m4s" in r3["body"].decode()
            # unknown rep -> honest 404
            assert _http_get("/hls/11111/1/5/v9.m3u8")["code"] == 404
    finally:
        addon._MPD_CACHE.clear()

def test_v194_kill_switch_direct_mpd_fallback():
    """MOVIEBOX_HLS=0 (or MPD unparsable) -> the v1.9.0-1.9.3 direct-MPD
    card: DASH manifest + signCookie via proxyHeaders."""
    pi = {"streams": [{"id": "42", "signCookie": FAKE_COOKIE,
                       "resolutions": "1080,720,480", "size": "1", "duration": 1,
                       "codecName": "hevc", "format": "MP4", "idType": ""}]}
    with mock.patch.object(addon, "_cached_play", return_value=pi), \
         mock.patch.object(addon, "fetch_captions", return_value=[]), \
         mock.patch.object(addon, "get_mpd_info", return_value=None):
        cards = addon._resolve_entry(("111", "Original"), 1, 5, "series", "X", "2020")
    assert cards and cards[0]["url"].startswith("https://sacdn.hakunaymatata.com/dash/")
    assert cards[0]["url"].endswith("/index.mpd")
    assert cards[0]["behaviorHints"]["proxyHeaders"]["request"]["Cookie"] == FAKE_COOKIE
    assert cards[0]["behaviorHints"]["notWebReady"] is True
    # and the explicit kill switch:
    with mock.patch.object(addon, "HLS_ON", False), \
         mock.patch.object(addon, "_cached_play", return_value=pi), \
         mock.patch.object(addon, "fetch_captions", return_value=[]):
        cards2 = addon._resolve_entry(("111", "Original"), 1, 5, "series", "X", "2020")
    assert cards2[0]["url"].endswith("/index.mpd")

def test_v194_stream_route_absolutizes_hls_urls():
    """the /stream route must rewrite relative /hls/ card urls against the
    request Host (the player needs an absolute master url)."""
    with mock.patch.object(addon, "build_streams",
                           return_value={"streams": [
                               {"name": "x", "url": "/hls/11111/1/5/master.m3u8",
                                "behaviorHints": {}}]}):
        r = _http_get("/stream/series/tt1:1:5.json")
    assert r["code"] == 200
    body = json.loads(r["body"])
    assert body["streams"][0]["url"] == "https://127.0.0.1:7000/hls/11111/1/5/master.m3u8"


# --- v1.9.5: efficiency pass ---------------------------------------------------

def test_v195_extinf_precision():
    with mock.patch.object(addon, "_last_good_seg", side_effect=lambda d, r, n, c: n):
        p = addon.hls_media(SESS_FIX, "0", "v")
    assert "#EXTINF:5.0," in p and "#EXTINF:5.000," not in p

def test_v195_playlist_cache_header():
    addon._MPD_CACHE.clear()
    pi = {"streams": [{"id": "42", "signCookie": FAKE_COOKIE}]}
    dash = addon._dash_base(addon._cf_parts(FAKE_COOKIE)["CloudFront-Policy"])
    addon._MPD_CACHE[dash] = (MPDINFO_FIX, time.time() + 600)
    try:
        with mock.patch.object(addon, "_cached_play", return_value=pi), \
             mock.patch.object(addon, "_last_good_seg",
                               side_effect=lambda d, r, n, c: n):
            r = _http_get("/hls/11111/1/5/master.m3u8")
        assert r["headers"]["Cache-Control"] == "public, max-age=1800"
    finally:
        addon._MPD_CACHE.clear()
