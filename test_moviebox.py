#!/usr/bin/env python3
"""Unit tests for MOVIE BOX addon. Run: python3 test_moviebox.py"""
import base64
import json
import os
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
    subs = [SUBJ_INCEPTION]
    assert addon.match_subjects(subs, "Inception", "1999", 1) == []

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
    assert 'CODECS="hev1,mp4a.40.2"' in m
    assert 'RESOLUTION=1920x1080' in m and 'RESOLUTION=1280x720' in m
    assert "v0.m3u8" in m and "v2.m3u8" in m and "a0.m3u8" in m

def test_hls_media_counts():
    mpd = addon._parse_mpd(MPD_FIX)   # dur 3643.6s / 5s = 729 chunks
    sess = {"dash": "https://sacdn.hakunaymatata.com/dash/999888_1_1_1080_h265_299",
            "cf": addon._cf_parts(FAKE_COOKIE), "mpd": mpd}
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
    body = addon.hls_media(sess, "3", "a")
    assert "init-stream3.m4s" in body and "chunk-stream3-00001.m4s" in body

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
    addon._STREAM_CACHE.clear()
    dubs = [{"subjectId": "973041525783496480", "lanName": "Hindi dub"},
            {"subjectId": "3089349649006742360", "lanName": "Original"}]
    with mock.patch.object(addon, "cinemeta",
                           return_value={"name": "Squid Game", "year": "2021"}), \
         mock.patch.object(addon, "search_subjects",
                           return_value=[SUBJ_SQUID_ORIG, SUBJ_SQUID_HI]), \
         mock.patch.object(addon, "subject_dubs", return_value=dubs), \
         mock.patch.object(addon, "play_info", return_value=PLAY_INFO_FIX), \
         mock.patch.object(addon.requests, "get") as g:
        r = mock.Mock(status_code=200, content=b"<MPD" + b"x" * 50)
        r.text = MPD_FIX
        g.return_value = r
        res = addon.build_streams("series", "tt10919420", 1, 1)
    assert len(res["streams"]) >= 2
    s0 = res["streams"][0]
    assert s0["name"] == "𖤍 MULTI 𖤍"
    assert "Squid Game (2021)" in s0["description"]
    assert "▣ S01E01" in s0["description"] and "▣ MOVIE BOX" in s0["description"]
    assert s0["url"].startswith("/hls/") and s0["url"].endswith("/master.m3u8")
    assert s0["bingeGroup"].startswith("mbx|Squid Game")

def test_build_streams_movie_no_dubs():
    addon._MPD_CACHE.clear()
    addon._STREAM_CACHE.clear()
    with mock.patch.object(addon, "cinemeta",
                           return_value={"name": "Inception", "year": "2010"}), \
         mock.patch.object(addon, "search_subjects",
                           return_value=[SUBJ_INCEPTION]), \
         mock.patch.object(addon, "subject_dubs", return_value=[]), \
         mock.patch.object(addon, "play_info", return_value=PLAY_INFO_FIX), \
         mock.patch.object(addon.requests, "get") as g:
        r = mock.Mock(status_code=200, content=b"<MPD" + b"x" * 50)
        r.text = MPD_FIX
        g.return_value = r
        res = addon.build_streams("movie", "tt1375666", 1, 1)
    assert len(res["streams"]) == 1
    assert "(Original)" in res["streams"][0]["description"]
    assert "S01E01" not in res["streams"][0]["description"]

def test_build_streams_result_cached():
    addon._MPD_CACHE.clear()
    addon._STREAM_CACHE.clear()
    calls = {"search": 0}
    def counting_search(kw, st):
        calls["search"] += 1
        return [SUBJ_INCEPTION]
    with mock.patch.object(addon, "cinemeta",
                           return_value={"name": "Inception", "year": "2010"}), \
         mock.patch.object(addon, "search_subjects", side_effect=counting_search), \
         mock.patch.object(addon, "subject_dubs", return_value=[]), \
         mock.patch.object(addon, "play_info", return_value=PLAY_INFO_FIX), \
         mock.patch.object(addon.requests, "get") as g:
        r = mock.Mock(status_code=200, content=b"<MPD" + b"x" * 50)
        r.text = MPD_FIX
        g.return_value = r
        r1 = addon.build_streams("movie", "tt1375666", 1, 1)
        r2 = addon.build_streams("movie", "tt1375666", 1, 1)
    assert r1 == r2 and len(r2["streams"]) == 1
    assert calls["search"] == 1  # second call served from cache

def test_build_streams_no_match():
    addon._STREAM_CACHE.clear()
    with mock.patch.object(addon, "cinemeta",
                           return_value={"name": "Zzz Nothing", "year": "1990"}), \
         mock.patch.object(addon, "search_subjects", return_value=[SUBJ_INCEPTION]):
        res = addon.build_streams("movie", "tt0000001", 1, 1)
    assert res["streams"] == []

def test_build_streams_play_info_transparent_on_none():
    addon._STREAM_CACHE.clear()
    addon._MPD_CACHE.clear()
    with mock.patch.object(addon, "cinemeta",
                           return_value={"name": "Inception", "year": "2010"}), \
         mock.patch.object(addon, "search_subjects", return_value=[SUBJ_INCEPTION]), \
         mock.patch.object(addon, "subject_dubs", return_value=[]), \
         mock.patch.object(addon, "play_info", return_value=None):
        res = addon.build_streams("movie", "tt1375666", 1, 1)
    assert res["streams"] == []

def test_hls_session_roundtrip():
    mpd = addon._parse_mpd(MPD_FIX)
    tok = addon.new_hls_session("https://x", addon._cf_parts(FAKE_COOKIE), mpd)
    with addon._SESS_LOCK:
        sess = addon._HLS_SESSIONS[tok]
    assert sess["mpd"]["dur"] > 3600
    del addon._HLS_SESSIONS[tok]

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
    assert d["ok"] is True and d["brand"] == "MOVIE BOX"

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

def main():
    global PASS, FAIL
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        run(t)
    print("\n%d/%d passed" % (PASS, len(tests)))
    sys.exit(0 if FAIL == 0 else 1)

if __name__ == "__main__":
    main()
