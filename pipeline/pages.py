"""Раскладка комиксных страниц: сцены -> страницы, слоты, формы.

Читает storyboard.json, добавляет каждой сцене поля page / slot / shape / page_template
и пишет pages.json (по страницам: шаблон, слоты, когда переворот).

Стандарт — `COMIC-PAGES.md` (шаблоны A/B/C/D, формы square/vertical/wide, правила переворота).

Пример:
  python pages.py --project out/ep1 [--seed 1] [--min 3] [--max 4]
"""
import argparse
import json
import os
import random
import sys

TEMPLATES = {
    "A": [["wide"], ["square", "square"], ["wide"]],                     # 4 панели, 3 переезда
    "B": [["square", "vertical"], ["square", "square"]],                 # 4 панели, 3 переезда
    "C": [["square", "square"], ["square", "square"]],                   # 4 панели, 3 переезда
    "D": [["square", "square"], ["wide"], ["square", "square"]],         # 5 панелей, 4 переезда
}
SHAPES = {"square": (1040, 1040), "vertical": (1040, 1849), "wide": (2160, 1215)}
# Вес выбора: где много «пустых» кадров без персонажей — чаще широкие раскладки.
WEIGHTS = {"A": 3, "B": 3, "C": 2, "D": 2}


def shapes_of(tpl):
    return [s for row in TEMPLATES[tpl] for s in row]


def ends_sentence(text):
    return text.strip().endswith((".", "!", "?", "…", ":"))


def _is_empty_frame(s):
    """Кадр без персонажа (место/космос/небо) — под широкие раскладки B/D.

    `who` может быть списком ([\"hero\", \"place\"]) или строкой — учитываем оба;
    в канал-1иной схеме признак пустого кадра — отсутствие `char`.
    """
    who = s.get("who")
    if "char" in s:
        # канал-1ина схема: пустой кадр — тот, где нет персонажа
        return s.get("char") in (None, "", "place", "space")
    if isinstance(who, (list, tuple, set)):
        if any(w in ("place", "space", "sky") for w in who):
            return True
    elif isinstance(who, str) and who in ("place", "space", "sky"):
        return True
    return False


def pick_template(prev, scenes):
    """Случайный шаблон под число панелей; один и тот же не идёт два раза подряд."""
    want = len(scenes)
    names = [n for n in TEMPLATES if len(shapes_of(n)) == want] or list(TEMPLATES)
    names = [n for n in names if n != prev] or names
    return random.choice(names)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="папка проекта (где storyboard.json)")
    ap.add_argument("--min", type=int, default=4, help="минимум панелей на страницу")
    ap.add_argument("--max", type=int, default=5, help="максимум панелей на страницу")
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args()

    sb_path = os.path.join(a.project, "storyboard.json")
    if not os.path.exists(sb_path):
        sys.exit(f"нет файла {sb_path}")
    sb = json.load(open(sb_path, encoding="utf-8"))
    scenes = sb["scenes"]
    if a.seed is not None:
        random.seed(a.seed)

    # 1. Группируем сцены в страницы: 3-4 панели, но не рвём фразу — если сцена
    #    заканчивает предложение и набралось хотя бы min, страницу можно закрывать.
    pages, cur = [], []
    for s in scenes:
        cur.append(s)
        enough = len(cur) >= a.min
        if len(cur) >= a.max or (enough and ends_sentence(s.get("script", ""))):
            pages.append(cur)
            cur = []
    if cur:
        # Хвост не приклеиваем к прошлой странице (это давало страницу из 5 панелей,
        # §8 запрещает >4). §5: хвост закрываем страницей на 2 панели — одну берём
        # из предыдущей, если та не уйдёт ниже минимума.
        if len(cur) < 2 and pages and len(pages[-1]) > a.min:
            cur.insert(0, pages[-1].pop())
        pages.append(cur)

    # 2. Каждой странице — шаблон (не тот же, что у предыдущей), сценам — слоты и формы.
    prev = None
    for i, page in enumerate(pages, 1):
        tpl = pick_template(prev, page)
        prev = tpl
        seq = shapes_of(tpl)
        if len(page) > len(seq):            # больше сцен, чем слотов — добьём квадратами
            seq = seq + ["square"] * (len(page) - len(seq))
        for idx, (s, shape) in enumerate(zip(page, seq), 1):
            s["page"] = i
            s["slot"] = idx
            s["shape"] = shape
            s["page_template"] = tpl

    json.dump(sb, open(sb_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    pages_out = {
        "grid": {"col_w": 1040, "row_h": 1215, "gutter": 80, "page_w": 2160},
        "pages": [],
    }
    t = 0.0
    for i, p in enumerate(pages, 1):
        for s in p:
            t += float(s.get("seconds", 0) or 0)
        pages_out["pages"].append({
            "page": i, "template": p[0]["page_template"],
            "flip_at": round(t, 2),      # §6: время переворота = сумма seconds по страницу
            "panels": [{"n": s["n"], "slot": s["slot"], "shape": s["shape"]} for s in p],
        })
    json.dump(pages_out, open(os.path.join(a.project, "pages.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    from collections import Counter
    c = Counter(s["page_template"] for s in scenes)
    print(f"страниц: {len(pages)} · панелей: {len(scenes)} · шаблоны: {dict(c)}")
    for i, p in enumerate(pages, 1):
        print(f"  стр.{i:>2} {p[0]['page_template']}: " +
              ", ".join(f"{s['n']}:{s['shape']}" for s in p))


if __name__ == "__main__":
    main()
