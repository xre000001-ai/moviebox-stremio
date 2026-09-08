#!/usr/bin/env python3
"""MovieBox prod verifier — run after each deploy.
Usage: python3 verify_prod.py [base_url]
IDs verified correct via IMDb-suggest on 2026-09-07 (never guess ids —
tt13640284 turned out to be "Episode #1.1" and cost a false regression alarm).
"""
import json, sys, time, urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://moviebox-f3hf.onrender.com"

def get(u, t=90):
    with urllib.request.urlopen(u, timeout=t) as r:
        return json.loads(r.read())

h = get(f"{BASE}/health")
print("health:", h.get("version"))

CASES = [
    # (type, video_id, label, expected_min)
    ("series", "tt35051401:1:1", "East Palace (v1.7.4 fix)", 1),
    ("series", "tt38960812:1:1", "See You at Work", 1),
    ("movie",  "tt11032374",     "Mugen Train", 1),
    ("movie",  "tt16492636",     "Asakusa Arc", 1),
    ("series", "tt32550889:1:1", "Witch Hat Atelier", 1),
    ("movie",  "tt1375666",      "Inception", 1),
    ("movie",  "tt9243807",      "Infinity Castle (known absent)", 0),
]
fails = 0
for ctype, vid, label, exp in CASES:
    try:
        t0 = time.time()
        st = get(f"{BASE}/stream/{ctype}/{vid}.json")
        n = len(st.get("streams", []))
        ok = (n >= exp) if exp else True
        if not ok:
            fails += 1
        print(f"{'OK ' if ok else 'FAIL'} {label:32s} {n:2d} streams  {time.time()-t0:5.1f}s")
    except Exception as e:
        fails += 1
        print(f"ERR  {label:32s} {e}")
sys.exit(1 if fails else 0)
