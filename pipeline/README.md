# pipeline — from script to panels

Real scripts used by the colony for the first channel.

- `pages.py` — comic-page layout: splits scenes into pages (4–5 panels), picks a template **at random**
  (never the same twice in a row), assigns each scene its panel shape, writes `pages.json`.
- `COMIC-PAGES.md` — the page standard every agent follows: sheet larger than the screen, camera rules,
  templates A–D, captions, page-turn timing.
- `critic_queue.py` — the review queue state machine: pending → changes → approved / escalated,
  with per-role digests (used by C.H.E.K.E.R. and the storyboarders).

Flow: script → `pages.py` layout → panels generated in the exact slot format (ComfyUI) →
`montage/build_pages_html.py` assembly → render.
