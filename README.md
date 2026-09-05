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
python3 test_moviebox.py   # 48 unit tests
```

---
*This addon scrapes third-party websites for personal use.*
