# montage — video assembly engine

Shared by both channels' montage agents (P.A.N.E.L. for channel1, F.R.A.M.E. for Channel Two).

- `build_pages_html.py` — **comic-page render mode**: builds a sheet larger than the screen, a camera
  drives between panels (diagonal approach, lands exactly centered), 3–4 moves per page, page turn,
  halftone dots, short captions (3–6 words, corner placement). Rendered via HyperFrames/GSAP at
  1920×1080 24fps.
- `tts_eleven.py` — ElevenLabs voice-over: voice, speed, model; `.wav` → retime → render.
- `comic-style-style.json` / `.md` — the first channel look: palette, fonts, timing, grid.

One engine, one style per channel, separate project folders so two videos render in parallel.
