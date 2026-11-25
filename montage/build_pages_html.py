# -*- coding: utf-8 -*-
"""Сборка index.html в режиме КОМИКСНОЙ СТРАНИЦЫ (камера по листу + переворот).

В отличие от build_html.py (один кадр = один экран, лист наезжает целиком), здесь
на экране лежит целый ЛИСТ, а по нему ездит КАМЕРА: панель появляется, пока идёт её
фраза, затем камера переезжает к следующей панели, а после последней — лист
переворачивается. Раскладка берётся из pages.json (шаблоны A/B/C/D).

    python engine/build_pages_html.py --pages out/ep1/pages.json \
        --images out/ep1/images --workdir montage/page-test \
        --style comic-style --limit 2 --panel-seconds 3.2

Читает: pages.json (сетка + панели), storyboard.json (текст подписей), style.json.
Пишет: <workdir>/index.html, <workdir>/images/*.png (копии нужных панелей).
Стандарт раскладки — `pipeline/COMIC-PAGES.md`
"""
import argparse
import base64
import json
import math
import os
import shutil
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TEMPLATES = {
    "A": [["wide"], ["square", "square"], ["wide"]],              # 4 панели, 3 переезда
    "B": [["square", "vertical"], ["square", "square"]],          # 4 панели, 3 переезда
    "C": [["square", "square"], ["square", "square"]],            # 4 панели, 3 переезда
    "D": [["square", "square"], ["wide"], ["square", "square"]],  # 5 панелей, 4 переезда
}
# размеры слота = ровно форма картинки, чтобы монтаж НЕ резал генерацию
SHAPES = {"square": (1040, 1040), "vertical": (1040, 1849), "wide": (2160, 1215)}
GUT, PAGE_W = 80, 2160
# диагональные направления подхода камеры: цель смещается от центра панели, поэтому
# переезд идёт по диагонали, а не только вниз-вверх
DIRS = [(1, 1), (-1, -1), (1, -1), (-1, 1)]


def short_cap(text, n=8):
    """Короткая надпись для кадра: первая фраза, не длиннее n слов.

    В озвучку идёт полный текст, в кадр — только эта строка, иначе плашка закрывает
    рисунок и персонажа.
    """
    t = (text or "").strip()
    if not t:
        return ""
    for sep in [".", "!", "?", ";", "—", ","]:
        head = t.split(sep)[0].strip()
        if len(head.split()) >= 3:
            t = head
            break
    w = t.split()
    return " ".join(w[:n]) + ("…" if len(w) > n else "")


def layout(tpl):
    """Раскладка листа по регулярным шаблонам A/B/C/D: [(x, y, w, h)] и высота.

    Используется только когда в pages.json нет форм панелей. Когда формы есть,
    геометрию считает `layout_shapes` — из фактических форм pages.json, а не из
    буквы шаблона (раскладку берём из файла, а не выдумываем).
    """
    geoms, y = [], 0
    for row in TEMPLATES[tpl]:
        x, hmax = 0, 0
        for sh in row:
            w, h = SHAPES[sh]
            geoms.append((x, y, w, h))
            x += w + GUT
            hmax = max(hmax, h)
        y += hmax + GUT
    return geoms, y


def layout_shapes(shapes):
    """Раскладка листа по фактическим формам панелей из pages.json.

    Ряд = одна `wide` ИЛИ две узкие (square/vertical) по правилам COMIC-PAGES
    (желоб 80, ширина листа 2160). Формы приходят в порядке слотов, поэтому
    геометрия всегда совпадает с тем, что нарисовал C.A.N.V.A.S., без буквы
    шаблона между данными и картинкой.
    """
    geoms, y = [], 0
    row = []

    def flush():
        nonlocal y
        if not row:
            return
        x, hmax = 0, 0
        for sh in row:
            w, h = SHAPES[sh]
            geoms.append((x, y, w, h))
            x += w + GUT
            hmax = max(hmax, h)
        y += hmax + GUT
        row.clear()

    for sh in shapes:
        if sh == "wide":
            flush()
            row = [sh]
            flush()
        elif len(row) == 2:
            flush()
            row.append(sh)
        else:
            row.append(sh)
    flush()
    return geoms, y


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def data_uri(path, mime):
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode("ascii")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", required=True, help="pages.json (сетка + панели)")
    ap.add_argument("--images", required=True, help="папка с 001.png… (панели)")
    ap.add_argument("--storyboard", default=None, help="storyboard.json — тексты подписей")
    ap.add_argument("--workdir", required=True, help="куда писать index.html")
    ap.add_argument("--style", default="comic-style")
    ap.add_argument("--limit", type=int, default=2, help="сколько страниц собрать")
    ap.add_argument("--panel-seconds", type=float, default=3.2, help="сколько живёт панель")
    ap.add_argument("--cam", type=float, default=0.65, help="длительность переезда камеры, с")
    ap.add_argument("--diag", type=int, default=190, help="смещение цели камеры по диагонали, px")
    ap.add_argument("--caption-words", type=int, default=6, help="слов в надписи кадра")
    ap.add_argument("--caption-pos", choices=["tl", "tr", "bl", "br"], default="tl",
                    help="угол плашки: центр кадра обычно занят персонажем, угол его не закрывает")
    ap.add_argument("--title", default="channel1 page test")
    ap.add_argument("--timings", default=None,
                    help="audio/timings2.json от retime.py: камера шагает по panel_start реплик, "
                         "переворот после конца речи последней панели листа + пауза; вставляет "
                         "audio/narration2.wav той же длительности")
    a = ap.parse_args()

    style = load(os.path.join(ROOT, "styles", a.style, "style.json"))
    pal, fonts = style["palette"], style["fonts"]
    assets = os.path.join(ROOT, "styles", a.style, "assets")
    cw, ch = style["canvas"]["w"], style["canvas"]["h"]
    fps = style["canvas"].get("fps", 24)
    page_in = float(style.get("page_in", 0.46))

    raw = load(a.pages)
    misfits = []
    pages = raw["pages"][: a.limit]

    # речевые тайминги от retime.py: panel_start/dur каждой сцены по её id (NNN).
    # В реальном эпизоде камера переезжает в момент panel_start панели, переворот — после
    # конца речи последней панели листа + паузы (правило COMIC-PAGES §5).
    tim = None
    if a.timings and os.path.exists(a.timings):
        tim = load(a.timings)
    elif a.timings:
        sys.exit(f"нет таймингов: {a.timings}")
    def shot_panel_start(n):
        if not tim:
            return None
        s = tim.get("shots", {}).get(f"{n:03d}")
        return s["panel_start"] if s else None

    caps = {}
    if a.storyboard and os.path.exists(a.storyboard):
        for s in load(a.storyboard).get("scenes", []):
            caps[s["n"]] = (s.get("script") or "").strip()

    wd = os.path.abspath(a.workdir)
    imgdir = os.path.join(wd, "images")
    os.makedirs(imgdir, exist_ok=True)

    def font_face(family, filename, weight):
        p = os.path.join(assets, "fonts", filename)
        if not os.path.exists(p):
            sys.exit(f"нет шрифта: {p}")
        return f'@font-face{{font-family:"{family}";src:url("{data_uri(p, "font/woff2")}") format("woff2");font-weight:{weight};font-display:block;}}'

    gsap = open(os.path.join(assets, "gsap.min.js"), encoding="utf-8").read()

    # ---- геометрия листов ----
    sheets, slots_html, cam_lines = [], [], []
    page_len = 0.0
    for p, page in enumerate(pages):
        tpl = page["template"]
        panels = page["panels"]
        # формы из pages.json — источник истины (не режем генерацию): если у панели
        # есть shape, раскладываем по нему, а не по букве шаблона
        if all("shape" in pn for pn in panels):
            geoms_xy, sheet_h = layout_shapes([pn["shape"] for pn in panels])
        else:
            geoms_xy, sheet_h = layout(tpl)
        geoms = [(pn["n"], x, y, w, h) for (x, y, w, h), pn in zip(geoms_xy, panels)]
        geoms = [(n, x, y, w, h) for (n, x, y, w, h) in geoms]
        sheets.append({"page": p, "tpl": tpl, "h": sheet_h, "geoms": geoms})
        page_len = max(page_len, len(geoms) * a.panel_seconds + page_in)

    # реальные границы листов из речи. retime считает panel_start ПОДРЯД по dur —
    # это шкала, на которой построена и сама аудио-дорожка narration2.wav, поэтому
    # панели НЕ сдвигаем (синхрон речи = синхрон картинки). Переворот листа делаем
    # на момент начала первой фразы листа: лист вводится ровно к фразе, рез фраз нет.
    if tim is not None:
        for p, sh in enumerate(sheets):
            first = sh["geoms"][0][0]
            ps_first = shot_panel_start(first)
            sh["flip"] = ps_first  # момент въезда листа (лист 0 = 0, прочие = начало фразы)
            sh["t0"] = ps_first if ps_first is not None else (p * page_len)

    k0 = cw / PAGE_W
    html_frames, tweens = [], []
    for p, sh in enumerate(sheets):
        t0 = sh.get("t0", p * page_len)
        prev_tx = prev_ty = 0.0
        frames = []
        for i, (n, x, y, w, h) in enumerate(sh["geoms"]):
            ps = shot_panel_start(n)
            src = os.path.join(a.images, f"{n:03d}.png")
            if not os.path.exists(src):
                sys.exit(f"нет панели: {src}")
            shutil.copyfile(src, os.path.join(imgdir, f"{n:03d}.png"))
            # форма картинки должна совпадать с формой слота: иначе не режем, а вписываем
            with Image.open(src) as im0:
                ar_img = im0.width / im0.height
            ar_slot = w / h
            fit, note = "cover", ""
            if abs(math.log(ar_img / ar_slot)) > 0.12:
                fit = "contain"
                note = f"  ⚠ панель {n}: картинка {ar_img:.2f} не под слот {ar_slot:.2f} — вписана без обрезки"
                misfits.append(note)
            cap = short_cap(caps.get(n, ""), a.caption_words)
            if cap:
                fs = 42 if len(cap) <= 40 else 34
                mw = int(min(w * 0.46, 1000))
                cap_html = (f'<div class="cap {a.caption_pos}" id="cp{p}_{i}" '
                            f'style="max-width:{mw}px; font-size:{fs}px">{cap}</div>')
            else:
                cap_html = ""
            frames.append(
                f'<div class="frame" id="pn{p}_{i}" style="left:{x}px; top:{y}px; width:{w}px; height:{h}px">'
                f'<img class="{fit}" src="images/{n:03d}.png" alt="">'
                f'<div class="dots"></div><div class="vig"></div>'
                + cap_html
                + "</div>")
            # камера: приземление РОВНО по центру панели (панель видна целиком), но подъезд
            # идёт с диагонали: точка-подход смещена вбок+вниз/вверх от цели, затем доводка
            cx, cy = x + w / 2, y + h / 2
            tx = cw / 2 - k0 * cx
            ty = ch / 2 - k0 * cy
            tx = min(0.0, max(cw - k0 * PAGE_W, tx))
            ty = min(0.0, max(ch - k0 * sh["h"], ty))
            ts = ps if ps is not None else t0 + i * a.panel_seconds
            if i == 0:
                tweens.append(f'\n      tl.set("#cam{p}", {{x:{tx:.1f}, y:{ty:.1f}, scale:{k0:.4f}}}, {ts:.3f});')
            else:
                # подход по перпендикуляру к направлению переезда — диагональный наезд
                ddx, ddy = tx - prev_tx, ty - prev_ty
                L = math.hypot(ddx, ddy)
                if L > 1.0:
                    ax = tx - ddy / L * a.diag
                    ay = ty + ddx / L * a.diag
                    ax = min(0.0, max(cw - k0 * PAGE_W, ax))
                    ay = min(0.0, max(ch - k0 * sh["h"], ay))
                    tweens.append(f'\n      tl.to("#cam{p}", {{x:{ax:.1f}, y:{ay:.1f}, scale:{k0:.4f}, '
                                  f'duration:{a.cam * 0.40:.2f}, ease:"power1.in"}}, {ts:.3f});')
                    tweens.append(f'\n      tl.to("#cam{p}", {{x:{tx:.1f}, y:{ty:.1f}, scale:{k0:.4f}, '
                                  f'duration:{a.cam * 0.60:.2f}, ease:"power2.out"}}, {ts + a.cam * 0.40:.3f});')
                else:
                    tweens.append(f'\n      tl.to("#cam{p}", {{x:{tx:.1f}, y:{ty:.1f}, scale:{k0:.4f}, '
                                  f'duration:{a.cam:.2f}, ease:"power2.inOut"}}, {ts:.3f});')
            prev_tx, prev_ty = tx, ty
            tweens.append(f'\n      tl.from("#pn{p}_{i}", {{scale:0.96, opacity:0, duration:0.42, '
                          f'ease:"power3.out"}}, {ts + 0.02:.3f});')
            if cap:
                tweens.append(f'\n      tl.from("#cp{p}_{i}", {{y:28, opacity:0, duration:0.42, '
                              f'ease:"power2.out"}}, {ts + 0.34:.3f});')
        # въезд листа: лист 0 стоит с 0-й секунды, следующие — на момент начала первой
        # фразы листа (рез фраз нет). В тестовом режиме — граница такта.
        flip_at = sh.get("flip", t0)
        tweens.append(f'\n      tl.from("#pg{p}", {{x:{cw}, duration:{page_in:.2f}, ease:"power3.inOut"}}, {flip_at:.3f});')
        if p > 0:
            tweens.append(f'\n      tl.to("#pg{p - 1}", {{x:-620, duration:{page_in:.2f}, '
                          f'ease:"power3.inOut"}}, {flip_at:.3f});')
        slots_html.append(
            f'<div class="slot" id="pg{p}"><div class="cam" id="cam{p}">'
            f'<div class="sheet" style="width:{PAGE_W}px; height:{sh["h"]}px">'
            + "".join(frames) +
            f'</div></div>'
            f'<div class="pageno">PAGE {sh["page"] + 1} / {len(raw["pages"])}</div>'
            f'<div class="tag">COMIC</div>'
            f'<div class="bodyframe"></div></div>')

    total = page_len * len(sheets)
    audio_tag = ""
    if tim is not None:
        total = tim.get("total", total)
        # narration2.wav лежит рядом с timings2.json (workdir/audio/narration2.wav)
        tim_dir = os.path.dirname(os.path.abspath(a.timings))
        nar = os.path.join(tim_dir, "narration2.wav")
        if os.path.exists(nar):
            audio_tag = (f'\n    <audio id="vo" data-start="0" data-duration="{total:.2f}" '
                         f'data-track-index="20" src="audio/narration2.wav"></audio>')
        # SFX-слой (звуки монтажа: переворот листа и т.п.) — отдельный трек поверх речи,
        # генерится engine/sfx.py в <workdir>/audio/sfx.wav; как в build_html.py (track 21)
        sfx = os.path.join(tim_dir, "sfx.wav")
        if os.path.exists(sfx):
            audio_tag += (f'\n    <audio id="sfx" data-start="0" data-duration="{total:.2f}" '
                          f'data-track-index="21" src="audio/sfx.wav"></audio>')
    CSS = f"""{font_face(fonts['display'], fonts.get('files', {}).get('display', 'Bangers-latin.woff2'), 400)}
{font_face(fonts['text'], fonts.get('files', {}).get('text', 'Outfit-var.woff2'), '100 900')}
:root{{--paper:{pal['paper']}; --paper2:{pal['paper2']}; --ink:{pal['ink']}; --red:{pal['red']};
  --gold:{pal['gold']}; --blue:{pal['blue']};}}
*{{box-sizing:border-box;margin:0;padding:0;}}
html,body{{width:{cw}px;height:{ch}px;overflow:hidden;background:#111;font-family:"{fonts['text']}",sans-serif;}}
.slot{{position:absolute;inset:0;overflow:hidden;}}
.cam{{position:absolute;top:0;left:0;transform-origin:0 0;}}
.sheet{{position:absolute;top:0;left:0;background:var(--paper);
  background-image:radial-gradient(circle at 1px 1px, rgba(23,21,15,.10) 1px, transparent 1.6px),
                   radial-gradient(circle at 50% 50%, rgba(255,255,255,.55) 0%, rgba(0,0,0,.06) 100%);
  background-size:5px 5px, 100% 100%;}}
.frame{{position:absolute;border:16px solid var(--ink);background:#0d0c0a;overflow:hidden;
  box-shadow:36px 36px 0 0 var(--ink);}}
.frame img{{width:100%;height:100%;object-fit:cover;display:block;}}
.frame img.contain{{object-fit:contain;background:#0b0a08;}}
.frame .dots{{position:absolute;inset:0;pointer-events:none;mix-blend-mode:multiply;opacity:.45;
  background-image:radial-gradient(circle at 1px 1px, rgba(23,21,15,.55) 1px, transparent 1.7px);
  background-size:6px 6px;}}
.frame .vig{{position:absolute;inset:0;pointer-events:none;
  background:radial-gradient(ellipse at 50% 45%, transparent 45%, rgba(23,21,15,.35) 100%);}}
.cap{{position:absolute;left:44px;bottom:44px;background:var(--paper2);border:8px solid var(--ink);
  box-shadow:14px 14px 0 0 var(--ink);padding:22px 28px;color:var(--ink);font-weight:900;
  text-transform:uppercase;font-size:42px;line-height:1.14;max-width:46%;letter-spacing:.005em;}}
.cap.tl{{left:40px; top:40px; bottom:auto;}}
.cap.tr{{right:40px; left:auto; top:40px; bottom:auto;}}
.cap.bl{{left:40px; bottom:40px; top:auto;}}
.cap.br{{right:40px; left:auto; bottom:40px; top:auto;}}
.pageno{{position:absolute;right:40px;bottom:30px;z-index:80;font-weight:800;font-size:24px;
  letter-spacing:.22em;color:var(--ink);opacity:.75;}}
.tag{{position:absolute;left:40px;bottom:30px;z-index:80;font-weight:800;font-size:22px;
  letter-spacing:.30em;color:var(--ink);opacity:.7;}}
.bodyframe{{position:absolute;inset:0;z-index:2000;pointer-events:none;
  box-shadow:inset 0 0 0 12px var(--ink), inset 0 0 0 20px var(--paper);}}
"""

    HTML = f'''<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><title>{a.title}</title>
<style>{CSS}</style></head>
<body>
  <div id="stage" data-composition-id="root" data-start="0" data-duration="{total:.2f}"
       data-width="{cw}" data-height="{ch}" data-fps="{fps}">
{"".join(slots_html)}{audio_tag}
  </div>
  <script>{gsap}</script>
  <script>
    window.__timelines = window.__timelines || {{}};
    const tl = gsap.timeline({{ paused: true }});
    {''.join(tweens)}
    window.__timelines["root"] = tl;
  </script>
</body>
</html>
'''
    out = os.path.join(wd, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(HTML)
    print(f"{out}: листов {len(sheets)}, всего {round(total, 2)} с, "
          f"шаблоны {[s['tpl'] for s in sheets]}, панели {sum(len(s['geoms']) for s in sheets)}")
    for m in misfits:
        print(m)


if __name__ == "__main__":
    main()
