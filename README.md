# MOVIE BOX — Stremio Addon

**netnaija.film + movieboxonline.net** — both sites run on the same
"oneroom / wefeed" platform, and this addon brings their full catalogs
(movies, TV series, animation) into Stremio with direct CDN streams.

## Features

- 🎬 **8 catalogs** — Netnaija & MovieBox: Movies / Series / Animation,
  resolved to real IMDb ids (posters & metadata via Cinemeta)
- 🔍 **Search** — searches the platform's own subject index
- 🌐 **Multi-language dubs** — Original, English, Hindi, Tamil, Telugu,
  Spanish, Portuguese … every available dub appears as its own stream card
- 📺 **Up to 1080p** (480/720/1080 ladder) per stream
- ⚡ **CDN-direct playback** — the addon signs the platform's CloudFront
  DASH manifests and republishes them as local HLS playlists whose segment
  URLs are CloudFront *query-signed* and point straight at the CDN
  (no video proxying, `Access-Control-Allow-Origin: *`, Range supported)

## Stream hang fixed (v1.7.8)

Cold `/stream` requests on prod hung **6.5+ minutes with no answer at all**
(only cached titles responded — the reqlog showed zero `/stream` entries
because hung requests never complete). Root cause chain:

- Render's IP is platform-flagged: all 7 API hosts answer `401 AUTH_FAIL`
  direct, so every platform call rides the free proxy pool;
- the 25s chain budget lived in a **thread-local** the fan-out waves
  (alt searches, dubs, play-info single-flight, resolve) never saw;
- `_cached_play` waited on its future **unbounded**;
- one `api_call` could rotate 2 attempts x 7 hosts x (6s request + 6s
  token bootstrap) ≈ 250s per call on a deadline-less thread.

Fixes: a **hard 24s player-facing wall** (the build keeps running in the
background and lands in the cache — a retry a minute later hits it), a
**14s per-`api_call` wall cap** on every thread, **deadline inheritance**
for all fan-out waves, bounded play-info waits, and pool picks now
**prefer exits that already carry a platform token** (the 240s pool
refresh kept re-paying the ~6s token bootstrap). New `/debug/phases`
endpoint shows recent build phase records + pool state.

## Cold-path speed (v1.7.7)

A cold `/stream` build (nothing cached) used to take 5–30s on prod. Phase
telemetry (`/debug/reqlog?k=mbx-dbg-7f3a` now carries a `phases` breakdown
per request: `meta | search | match | rescue | dubs+play | resolve`) found
the fat; the fixes:

- **parallel alt-title rescue** — localized-name titles ran one serial
  platform search per TMDB alt title (up to ~10s); now 3 concurrent
  searches, evaluated in EN-first priority order, and the alt-title list
  is prefetched during the primary search
- **dubs + play-info overlap** — play-info for the top matches is
  prefetched fire-and-forget (single-flight, deduped with the resolve
  wave), so a slow prefetch can't hold the dubs wave
- **direct-host health** — `api_call` used to walk all 7 API hosts per
  attempt (a sick host = its full timeout; measured 8.8–13.8s waves).
  Now the last healthy host is sticky-first and transport-failed hosts
  are benched 3 min
- **hot-path timeouts** — search/dubs/play/captions calls capped at 4s
  (was 10s), slow first attempts skip the retry, sick caption families
  skip the web fallback
- **single-flight `play-info`** — concurrent callers share one in-flight
  future (no duplicate calls, no double waits)

Measured locally (sandbox, unthrottled): **median cold ≈ 3.7s, p90 ≈ 5.3s**
(was median 5.9–6.1s with 10–30s outliers). SWR replay stays ~0.3s.

## How it works

1. Stremio asks for `stream/{movie|series}/{imdb}` (+ season/episode)
2. Cinemeta → title & year → platform `subject-api/search/v2`
   (signed mobile API: `X-Client-Token`, `x-tr-signature` HMAC-MD5)
3. Dub expansion via `subject-api/get` → `play-info/v2` per dub
4. `play-info` returns a CloudFront **signed cookie** whose policy points
   at `sacdn.hakunaymatata.com/dash/{subject}_{se}_{ep}_{res}_h265_x/`
5. The addon fetches `index.mpd`, parses the representation ladder and
   emits an HLS master (video variants + audio group) plus media playlists
   with `#EXT-X-MAP`; segment URLs carry
   `Policy=…&Signature=…&Key-Pair-Id=…` as query params (CloudFront
   treats cookie values as a signed URL), so players fetch segments
   straight from the CDN without cookies or headers.

> **Codec note:** streams are HEVC/H.265 — plays great on Stremio
> desktop (mpv), Android & TV. In browsers it needs HEVC support
> (Chrome 107+ with hardware decode, Safari ✓; Firefox ✗).

## Self-hosting (Render)

1. Push this repo to GitHub and create a **Web Service** on Render
   (runtime: **Docker**).
2. Add environment variable
   `MB_PUBLIC_URL = https://<your-service>.onrender.com`
   (keeps the free instance awake via a self-ping every 5 minutes).
3. Install in Stremio: `https://<your-service>.onrender.com/manifest.json`

### Environment variables

| Var | Default | Purpose |
| --- | --- | --- |
| `PORT` | `7000` | server port |
| `MB_PUBLIC_URL` | — | enables anti-sleep keep-alive |
| `TMDB_API_KEY` | shared key | IMDb resolution fallback (search + external_ids) |

## Tests

```
python3 test_moviebox.py   # 155 offline tests
```

---
*This addon scrapes third-party websites for personal use.*
