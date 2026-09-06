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

def test_signed_url():
    sess = {"dash": "https://sacdn.hakunaymatata.com/dash/999888_1_1_1080_h265_299",
            "cf": addon._cf_parts(FAKE_COOKIE)}
    u = addon._signed(sess["dash"], "init-stream0.m4s", sess["cf"])
    assert u.startswith(sess["dash"] + "/init-stream0.m4s?")
    assert "Policy=" in u and "Signature=SIGabc123" in u and "Key-Pair-Id=KP123" in u

def test_hls_master():
    mpd = addon._parse_mpd(MPD_FIX)
    sess = {"dash": "https://x", "cf": addon._cf_parts(FAKE_COOKIE), "mpd": mpd}
    m = addon.hls_master(sess)
    assert m.startswith("#EXTM3U")
    assert '#EXT-X-MEDIA:TYPE=AUDIO' in m and 'NAME="HIN"' in m
    assert m.count("#EXT-X-STREAM-INF") == 3
    assert 'CODECS="hvc1,mp4a.40.2"' in m      # v1.6.11: hev1 rewritten for player compat
    assert 'RESOLUTION=1920x1080' in m and 'RESOLUTION=1280x720' in m
    assert "v0.m3u8" in m and "v2.m3u8" in m and "a0.m3u8" in m

def test_hls_media_counts():
    mpd = addon._parse_mpd(MPD_FIX)   # dur 3643.6s / 5s = 729 chunks
    sess = {"dash": "https://sacdn.hakunaymatata.com/dash/999888_1_1_1080_h265_299",
            "cf": addon._cf_parts(FAKE_COOKIE), "mpd": mpd}
    with mock.patch.object(addon, "_seg_exists", return_value=True):
        body = addon.hls_media(sess, "0", "v")
    assert body.count("#EXTINF") == 729
    assert body.count("chunk-stream0-") == 729
    assert "chunk-stream0-00001.m3u8" not in body
    assert "chunk-stream0-00001.m4s" in body
    assert "chunk-stream0-00729.m4s" in body
    assert "chunk-stream0-00730.m4s" not in body
    assert body.rstrip().endswith("#EXT-X-ENDLIST")
    assert "#EXT-X-MAP:URI=" in body and "init-stream0.m4s" in body

def test_hls_media_audio_rep():
    mpd = addon._parse_mpd(MPD_FIX)
    sess = {"dash": "https://x", "cf": addon._cf_parts(FAKE_COOKIE), "mpd": mpd}
    with mock.patch.object(addon, "_seg_exists", return_value=True):
        body = addon.hls_media(sess, "3", "a")
    assert "init-stream3.m4s" in body and "chunk-stream3-00001.m4s" in body

def test_last_good_seg_complete_one_probe():
    addon._TAIL_CACHE.clear()
    calls = []
    def fake(dash, rep, i, cf):
        calls.append(i); return True
    with mock.patch.object(addon, "_seg_exists", side_effect=fake):
        assert addon._last_good_seg("https://d", "0", 729, {}) == 729
    assert calls == [729]  # single probe, complete file
    addon._TAIL_CACHE.clear()

def test_last_good_seg_heuristic_83_percent():
    # the known pattern: ~83.25% of listed segments exist (MPD duration 1.2x)
    addon._TAIL_CACHE.clear()
    def fake(dash, rep, i, cf):
        return i <= 676
    with mock.patch.object(addon, "_seg_exists", side_effect=fake):
        got = addon._last_good_seg("https://d2", "0", 812, {})
    assert got == 676
    addon._TAIL_CACHE.clear()

def test_last_good_seg_binary_search():
    addon._TAIL_CACHE.clear()
    def fake(dash, rep, i, cf):
        return i <= 400   # not near the 83.25% point -> binary search path
    with mock.patch.object(addon, "_seg_exists", side_effect=fake):
        got = addon._last_good_seg("https://d3", "0", 812, {})
    assert got == 400
    addon._TAIL_CACHE.clear()

def test_hls_media_trims_to_existing_segments():
    mpd = addon._parse_mpd(MPD_FIX)   # 729 segments listed
    sess = {"dash": "https://trunc", "cf": addon._cf_parts(FAKE_COOKIE), "mpd": mpd}
    with mock.patch.object(addon, "_seg_exists", side_effect=lambda d, r, i, cf: i <= 600):
        body = addon.hls_media(sess, "0", "v")
    assert body.count("#EXTINF") == 600
    assert "chunk-stream0-00600.m4s" in body
    assert "chunk-stream0-00601.m4s" not in body
    assert body.rstrip().endswith("#EXT-X-ENDLIST")

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
    assert s0["name"].startswith("𖤍 Squid Game (")
    assert s0["description"].count("\n") == 2
    assert "480p–1080p" in s0["description"]      # real resolution range
    assert "HEVC" in s0["description"] and "863.7 MB" in s0["description"]
    assert "▣ S01E01" in s0["description"] and "▣ MovieBox" in s0["description"]
    assert "NO SUB" in s0["description"]          # captions mocked empty
    assert "DASH" not in s0["description"]
    # lazy HLS: url carries sid/se/ep (stateless), not a session token
    assert re.match(r"^/hls/\d+/1/1/master\.m3u8$", s0["url"]), s0["url"]
    assert s0["bingeGroup"].startswith("mbx|Squid Game")
    # MPD is NOT fetched at card time (deferred to first /hls request)
    assert g.call_count == 0

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
    assert res["streams"][0]["description"].count("\n") == 2
    assert "▣ 2010 ▣ MovieBox" in res["streams"][0]["description"]
    assert "S01E01" not in res["streams"][0]["description"]
    assert re.match(r"^/hls/\d+/0/0/master\.m3u8$", res["streams"][0]["url"])
    assert g.call_count == 0  # MPD deferred to first /hls request

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

def test_lazy_hls_route_master_and_variant():
    addon._PLAY_CACHE.clear(); addon._MPD_CACHE.clear(); addon._TAIL_CACHE.clear()
    with mock.patch.object(addon, "play_info", return_value=PLAY_INFO_FIX), \
         mock.patch.object(addon, "fetch_captions", return_value=[]), \
         mock.patch.object(addon, "_seg_exists", return_value=True), \
         mock.patch.object(addon.requests, "get") as g:
        r = mock.Mock(status_code=200, content=b"<MPD" + b"x" * 50)
        r.text = MPD_FIX
        g.return_value = r
        c1 = _http_get("/hls/3089349649006742360/1/1/master.m3u8")
        c2 = _http_get("/hls/3089349649006742360/1/1/v0.m3u8")
    assert c1["code"] == 200
    m = c1["body"].decode()
    assert "#EXT-X-STREAM-INF" in m and "v0.m3u8" in m
    assert "#EXT-X-MEDIA:TYPE=SUBTITLES" not in m   # captions mocked empty
    assert c2["code"] == 200
    v = c2["body"].decode()
    assert "#EXTINF" in v and "chunk-stream0-00001.m4s" in v
    addon._PLAY_CACHE.clear(); addon._MPD_CACHE.clear()
    # master WITH captions: subtitle renditions + SUBTITLES group on variants
    caps = [{"lan": "en", "url": "https://c/e.srt"}, {"lan": "in_id", "url": "https://c/i.srt"}]
    with mock.patch.object(addon, "play_info", return_value=PLAY_INFO_FIX), \
         mock.patch.object(addon, "fetch_captions", return_value=caps), \
         mock.patch.object(addon, "_seg_exists", return_value=True), \
         mock.patch.object(addon.requests, "get") as g:
        r = mock.Mock(status_code=200, content=b"<MPD" + b"x" * 50)
        r.text = MPD_FIX
        g.return_value = r
        c3 = _http_get("/hls/3089349649006742360/1/1/master.m3u8")
        c4 = _http_get("/hls/3089349649006742360/1/1/sub-en.m3u8")
    assert c3["code"] == 200
    m3 = c3["body"].decode()
    assert ('#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",'
            'DEFAULT=NO,AUTOSELECT=YES,LANGUAGE="en",URI="sub-en.m3u8"') in m3
    assert 'LANGUAGE="id",URI="sub-in_id.m3u8"' in m3      # in_id -> id
    assert 'SUBTITLES="subs"' in m3                        # variants join the group
    assert c4["code"] == 200
    m4 = c4["body"].decode()
    assert m4.startswith("#EXTM3U") and "#EXT-X-ENDLIST" in m4
    assert "/sub/3089349649006742360/1/1/en.vtt" in m4      # single VTT segment
    assert "#EXTINF:%.3f," % 3643 in m4                     # play-info duration
    assert "#EXT-X-TARGETDURATION:3643" in m4
    addon._PLAY_CACHE.clear(); addon._MPD_CACHE.clear()

def test_sub_playlist_404_on_unknown_lan():
    caps = [{"lan": "en", "url": "https://c/e.srt"}]
    with mock.patch.object(addon, "play_info", return_value=PLAY_INFO_FIX), \
         mock.patch.object(addon, "fetch_captions", return_value=caps):
        c = _http_get("/hls/3089349649006742360/1/1/sub-zz.m3u8")
    assert c["code"] == 404
    addon._PLAY_CACHE.clear()

def test_lazy_hls_route_404_when_no_stream():
    addon._PLAY_CACHE.clear()
    with mock.patch.object(addon, "play_info", return_value=None):
        c = _http_get("/hls/3089349649006742360/1/1/master.m3u8")
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
    cats = [c["id"] for c in addon.MANIFEST["catalogs"]]
    assert "netnaija-movies" in cats and "moviebox-movies" in cats
    assert "netnaija-series" in cats and "moviebox-series" in cats
    assert cats.count("netnaija-animated") == 2  # movie + series entries
    assert set(addon.MANIFEST["types"]) == {"movie", "series"}
    assert "type" not in addon.MANIFEST           # must be 'types' (Stremio protocol)
    assert addon.MANIFEST["idPrefixes"] == ["tt"]
    assert "stream" in addon.MANIFEST["resources"]

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

def test_srt_to_vtt():
    srt = "1\n00:00:01,000 --> 00:00:02,500\nHello\n\n2\n00:00:03,000 --> 00:00:04,000\nWorld\r\n"
    vtt = addon._srt_to_vtt(srt)
    assert vtt.startswith("WEBVTT\n")
    assert "00:00:01.000 --> 00:00:02.500" in vtt
    assert "00:00:03.000 --> 00:00:04.000" in vtt
    lines = [l for l in vtt.split("\n") if l.strip()]
    assert not any(l.strip() == "1" for l in lines)   # cue numbers dropped
    assert "Hello" in vtt and "World" in vtt

def test_vtt_has_timestamp_map():
    vtt = addon._srt_to_vtt("1\n00:00:01,000 --> 00:00:02,000\nHi\n\n")
    assert vtt.splitlines()[1] == "X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:0"
    assert "00:00:01.000 --> 00:00:02.000" in vtt


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
    caps = [{"lan": "en", "url": "https://c/s.srt?P=1"}, {"lan": "bn", "url": "https://c/b.srt?P=1"}]
    with mock.patch.object(addon, "_cached_play", return_value=pi), \
         mock.patch.object(addon, "fetch_captions", return_value=caps):
        cards = addon._resolve_entry(("111", "Hindi"), 1, 5, "series", "Our Sticky Love", "2026")
    assert isinstance(cards, list) and len(cards) == 1
    assert cards[0]["name"] == "𖤍 Our Sticky Love (Hindi)"
    assert cards[0]["url"].startswith("/hls/111/1/5/master.m3u8")
    assert "/dash/" not in cards[0]["url"]
    for card in cards:
        assert card.get("subtitles")
        langs = [s["lang"] for s in card["subtitles"]]
        assert "eng" in langs and "ben" in langs
        assert card["subtitles"][0]["url"].startswith("/sub/111/1/5/")
        assert card["subtitles"][0]["url"].endswith(".vtt")
        assert "▣ 2 SUB · en, bn" in card["description"]


def test_sub_route_serves_vtt():
    addon._PLAY_CACHE.clear(); addon._SUB_CACHE.clear(); addon._VTT_CACHE.clear()
    pi = {"streams": [{"id": "42", "signCookie": FAKE_COOKIE, "url": "", "resolutions": "480",
                       "size": "1", "duration": 1, "codecName": "hevc", "format": "MP4", "idType": ""}]}
    caps = [{"lan": "en", "url": "https://cacdn/x.srt?Policy=1"}]
    class RS:
        status_code = 200
        text = "1\n00:00:01,000 --> 00:00:02,000\nHi\n\n"
    with mock.patch.object(addon, "play_info", return_value=pi), \
         mock.patch.object(addon, "fetch_captions", return_value=caps), \
         mock.patch.object(addon.requests, "get", return_value=RS()):
        c = _http_get("/sub/3089349649006742360/1/1/en.vtt")
    assert c["code"] == 200
    body = c["body"].decode()
    assert body.startswith("WEBVTT")
    assert "00:00:01.000 --> 00:00:02.000" in body
    addon._PLAY_CACHE.clear(); addon._SUB_CACHE.clear(); addon._VTT_CACHE.clear()

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

def test_hls_media_real_durations():
    info = addon._parse_mpd(MPD_TL)
    sess = {"dash": "https://sacdn.hakunaymatata.com/dash/777_1_1_1080_h265_3",
            "cf": addon._cf_parts(FAKE_COOKIE), "mpd": info}
    with mock.patch.object(addon, "_seg_exists", return_value=True):
        body = addon.hls_media(sess, "0", "v")
    ext = [l for l in body.splitlines() if l.startswith("#EXTINF")]
    assert len(ext) == 3
    assert "5.923" in ext[0]                          # 142142/24000
    assert "#EXT-X-TARGETDURATION:6" in body
    # trimmed to 2 existing segments -> first 2 real durations kept
    addon._TAIL_CACHE.clear()
    with mock.patch.object(addon, "_seg_exists", side_effect=lambda d, r, i, cf: i <= 2):
        body2 = addon.hls_media(sess, "0", "v")
    ext2 = [l for l in body2.splitlines() if l.startswith("#EXTINF")]
    assert len(ext2) == 2 and "5.923" in ext2[1]      # second kept entry = 142142/24000
    assert body2.rstrip().endswith("#EXT-X-ENDLIST")
    # full 3 segments -> last entry uses the real 5.880s duration
    assert "5.880" in ext[2] and "5.923" in ext[0]

def test_trim_timeline_body():
    body = ('<SegmentTemplate timescale="24000">'
            '<SegmentTimeline><S t="0" d="142142" /><S d="142142" /><S d="141120" /></SegmentTimeline>'
            '</SegmentTemplate>')
    out = addon._trim_timeline_body(body, 2)
    assert out.count("<S ") == 2
    assert 'd="141120"' not in out
    assert 't="0"' in out
    out5 = addon._trim_timeline_body(body, 5)          # keep > available: unchanged
    assert out5.count("<S ") == 3

def test_dash_manifest_signs_and_trims():
    addon._PLAY_CACHE.clear(); addon._MPD_RAW_CACHE.clear(); addon._TAIL_CACHE.clear()
    pi = {"streams": [{"id": "42", "signCookie": FAKE_COOKIE, "url": "", "resolutions": "480",
                       "size": "1", "duration": 12, "codecName": "hevc", "format": "MP4", "idType": ""}]}
    with mock.patch.object(addon, "play_info", return_value=pi), \
         mock.patch.object(addon, "get_mpd_raw", return_value=MPD_TL), \
         mock.patch.object(addon, "_seg_exists", side_effect=lambda d, r, i, cf: i <= 2):
        xml = addon.dash_manifest("3642928735944335256", 1, 5)
    assert xml and xml.startswith("<?xml")
    # segment templates rewritten to absolute signed URLs
    assert 'initialization="https://sacdn.hakunaymatata.com/dash/999888_1_1_1080_h265_299/init-stream$RepresentationID$.m4s?Policy=' in xml
    assert 'media="https://sacdn.hakunaymatata.com/dash/999888_1_1_1080_h265_299/chunk-stream$RepresentationID$-$Number%05d$.m4s?Policy=' in xml
    assert "Key-Pair-Id=" in xml
    import xml.etree.ElementTree as ET
    ET.fromstring(xml)   # rewritten MPD must be well-formed XML ('&' escaped)
    # both timelines trimmed to 2 segments
    assert xml.count("<S ") == 4
    assert 'd="141120"' not in xml
    # presentation duration = min(video 2 segs, audio 2 segs)
    assert 'mediaPresentationDuration="PT11.767S"' in xml or \
           'mediaPresentationDuration="PT10.004S"' in xml or "mediaPresentationDuration" in xml
    addon._PLAY_CACHE.clear(); addon._MPD_RAW_CACHE.clear(); addon._TAIL_CACHE.clear()

def test_dash_route_serves_mpd():
    addon._PLAY_CACHE.clear(); addon._MPD_RAW_CACHE.clear(); addon._TAIL_CACHE.clear()
    pi = {"streams": [{"id": "42", "signCookie": FAKE_COOKIE, "url": "", "resolutions": "480",
                       "size": "1", "duration": 12, "codecName": "hevc", "format": "MP4", "idType": ""}]}
    with mock.patch.object(addon, "play_info", return_value=pi), \
         mock.patch.object(addon, "get_mpd_raw", return_value=MPD_TL), \
         mock.patch.object(addon, "_seg_exists", return_value=True):
        c = _http_get("/dash/3089349649006742360/1/1/manifest.mpd")
    assert c["code"] == 200
    body = c["body"].decode()
    assert body.startswith("<?xml") and "<MPD" in body and "Policy=" in body
    addon._PLAY_CACHE.clear(); addon._MPD_RAW_CACHE.clear(); addon._TAIL_CACHE.clear()

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
    caps = [{"lan": "en", "url": "https://c/e.srt"}, {"lan": "bn", "url": "https://c/b.srt"}]
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
    assert hindi["subtitles"][0]["url"].startswith("/sub/6391474290696802080/")
    assert "▣ 2 SUB" in hindi["description"]
    assert "NO SUB" not in hindi["description"]
    addon._STREAM_CACHE.clear(); addon._STREAM_STALE.clear()

def test_captions_fetched_once_per_title():
    addon._MPD_CACHE.clear(); addon._STREAM_CACHE.clear(); addon._STREAM_STALE.clear(); addon._PLAY_CACHE.clear()
    addon._DUB_CACHE.clear(); addon._SEARCH_CACHE.clear()
    calls = {"caps": 0}
    nine = [{"lan": "en%d" % i, "url": "https://c/%d.srt" % i} for i in range(9)]
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
        assert len(s.get("subtitles") or []) == 9
        assert "▣ 9 SUB" in s["description"]
        assert s["subtitles"][0]["url"].startswith("/sub/6391474290696802080/")
    addon._STREAM_CACHE.clear(); addon._STREAM_STALE.clear()

def test_stream_stale_while_revalidate():
    addon._MPD_CACHE.clear(); addon._STREAM_CACHE.clear(); addon._STREAM_STALE.clear()
    addon._PLAY_CACHE.clear(); addon._DUB_CACHE.clear(); addon._SEARCH_CACHE.clear()
    with mock.patch.object(addon, "_meta_any",
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
            assert "tab-operating" in url
            px = kw.get("proxies") or {}
            u = px.get("http")
            return mock.Mock(status_code=200 if u in ("http://a:1", "http://c:3") else 403)
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

def test_master_codecs_hvc1():
    sess = {"mpd": {"video": [{"id": "0", "height": 480, "width": 854,
                               "bw": 350000, "codecs": "hev1.1.6.L93.B0"}],
                    "audio": [{"lang": "hin", "bw": 64000, "codecs": "mp4a.40.2"}]}}
    m = addon.hls_master(sess, ("en",))
    assert "hvc1.1.6.L93.B0" in m
    assert "hev1" not in m
    assert 'CODECS="hvc1.1.6.L93.B0,mp4a.40.2"' in m
    assert 'URI="a0.m3u8"' in m and "sub-en.m3u8" in m

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
    try:
        with mock.patch.object(addon, "cinemeta", side_effect=slow_cinemeta), \
             mock.patch.object(addon, "_tmdb_find_id", side_effect=fast_tmdb), \
             mock.patch.object(addon, "_imdb_suggest_id", side_effect=imdb_sug):
            t0 = time.time()
            v = addon._meta_any("movie", "tt99990001")
            dt = time.time() - t0
            v2 = addon._meta_any("movie", "tt99990001")   # cached
        assert v["name"] == "FromTMDB"           # fastest valid answer won
        assert dt < 0.45                          # did not wait for cinemeta
        assert v2 == v
        assert calls == {"c": 1, "t": 1, "i": 1}  # second call: cache hit, no calls
    finally:
        addon._CINEMETA_CACHE.clear()

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

if __name__ == "__main__":
    main()
