#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Очередь проверки раскадровок критиком.

Общая для профилей (canvas / forge — авторы, director — критик). Живёт в
конвейере рядом с раскадровками, потому что состояние — часть проекта.

Состояние одной раскадровки — файл jobs/<проект>.json:

    project      имя проекта (папка в out/)
    producer     кто автор: canvas | forge
    channel      channel1 | channel2
    storyboard   путь к storyboard.json
    round        текущий круг правок (1 = первая проверка)
    state        pending_review | changes_requested | approved | escalated
    issues       замечания критика (список строк)
    report       путь к отчёту критика
    history      журнал переходов

Переходы:
    pending_review --(критик: changes)-->  changes_requested
    changes_requested --(автор починил)--> pending_review   (round+1)
    pending_review --(критик: approved)--> approved
    round > MAX_ROUNDS при resubmit --> escalated  (человек нужен)

Команды:
    enqueue --project P --storyboard F [--producer X] [--channel C]
    enqueue-scan [--root DIR]
    pick --role critic|fix|report [--profile NAME]
    verdict --project P --result changes|approved --report F [--issue "..."]
    resubmit --project P
    status
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

BASE = Path(os.environ.get("CRITIC_BASE", Path(__file__).resolve().parent))
JOBS = BASE / "jobs"
REPORTS = BASE / "reports"
STATE = BASE / "state"
PIPELINE_OUT = Path(os.environ.get("PIPELINE_OUT", "out"))

CHANNEL_BY_PRODUCER = {"canvas": "channel1", "forge": "channel2"}
PRODUCER_BY_CHANNEL = {"channel1": "canvas", "channel2": "forge"}
INBOX_BY_PRODUCER = {"canvas": "to-canvas.md", "forge": "to-forge.md"}

MAX_ROUNDS = 4
RETRY_AFTER = 30 * 60  # секунд до повторной выдачи необработанного пункта


# ---------------------------------------------------------------- утилиты

def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _ensure_dirs() -> None:
    for d in (JOBS, REPORTS, STATE):
        d.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _job_path(project: str) -> Path:
    return JOBS / f"{project}.json"


def _load_job(project: str):
    return _read_json(_job_path(project))


def _save_job(job: dict) -> None:
    job["updated"] = _now()
    _write_json(_job_path(job["project"]), job)


def detect_channel(storyboard: Path) -> str:
    """Канал по данным раскадровки (то же правило, что у check_critic.py)."""
    data = _read_json(storyboard)
    scenes = data.get("scenes") if isinstance(data, dict) else data
    if isinstance(scenes, list) and scenes:
        keys = set(scenes[0].keys())
        if {"char", "role", "scene"} & keys:
            return "channel1"
        if "who" in keys:
            return "channel2"
    return ""


def _producer_for(project: str, channel: str, explicit: str = "") -> str:
    if explicit:
        return explicit
    if channel in PRODUCER_BY_CHANNEL:
        return PRODUCER_BY_CHANNEL[channel]
    # эвристика по имени проекта: channel2-* -> forge
    if project.lower().startswith("channel2"):
        return "forge"
    return "canvas"


# ------------------------------------------------------------- enqueue

def cmd_enqueue(args) -> int:
    _ensure_dirs()
    sb = Path(args.storyboard)
    if not sb.exists():
        print(f"НЕТ ФАЙЛА: {sb}")
        return 1
    project = args.project or sb.parent.name
    channel = args.channel or detect_channel(sb)
    producer = _producer_for(project, channel, args.producer)
    if not channel:
        print(f"НЕ ОПРЕДЕЛЁН КАНАЛ для {sb} — укажи --channel вручную")
        return 1

    job = _load_job(project)
    if job and job.get("state") in ("pending_review", "changes_requested"):
        print(f"УЖЕ В ОБОРОТЕ: {project} ({job['state']}, круг {job.get('round')})")
        return 0

    if job:
        rnd = int(job.get("round", 1)) + 1
    else:
        rnd = 1
    if rnd > MAX_ROUNDS:
        job = job or {}
        job.update({"project": project, "producer": producer, "channel": channel,
                    "storyboard": str(sb), "round": rnd, "state": "escalated",
                    "issues": job.get("issues", []),
                    "history": job.get("history", []) + [
                        {"at": _now(), "event": "escalated",
                         "why": f"круг {rnd} > предел {MAX_ROUNDS}"}]})
        _save_job(job)
        print(f"ЭСКАЛАЦИЯ: {project} — круг {rnd} превысил предел {MAX_ROUNDS}")
        return 0

    job = {
        "project": project, "producer": producer, "channel": channel,
        "storyboard": str(sb), "round": rnd, "state": "pending_review",
        "issues": [], "report": "",
        "created": (job or {}).get("created", _now()),
        "history": (job or {}).get("history", []) + [
            {"at": _now(), "event": "submitted", "round": rnd}],
    }
    _save_job(job)
    print(f"В ОЧЕРЕДЬ: {project} канал={channel} автор={producer} круг={rnd}")
    return 0


def cmd_enqueue_scan(args) -> int:
    """Поставить в очередь раскадровки, изменённые за последние N минут.

    Окно времени нужно, чтобы не тащить в работу весь архив out/ (там десятки
    старых и тестовых проектов). Новые проекты и переделанные раскадровки
    попадают в очередь автоматически.
    """
    _ensure_dirs()
    roots = [Path(args.root)] if args.root else [PIPELINE_OUT]
    window = float(args.minutes) * 60.0
    cutoff = time.time() - window
    added = 0
    for root in roots:
        if not root.exists():
            continue
        for sb in sorted(root.glob("*/storyboard.json")):
            try:
                mt = sb.stat().st_mtime
            except OSError:
                continue
            if mt < cutoff:
                continue
            project = sb.parent.name
            job = _load_job(project)
            if job and job.get("state") in ("pending_review", "changes_requested"):
                continue
            rc = argparse.Namespace(project=project, storyboard=str(sb),
                                    producer=(job or {}).get("producer", ""),
                                    channel=(job or {}).get("channel", ""))
            if cmd_enqueue(rc) == 0:
                added += 1
    print(f"скан: новых в очереди {added}")
    return 0


# ---------------------------------------------------------------- pick

def _seen_path(role: str, profile: str) -> Path:
    return STATE / f"seen_{role}_{profile}.json"


def _load_seen(role: str, profile: str) -> dict:
    return _read_json(_seen_path(role, profile)) or {}


def _save_seen(role: str, profile: str, data: dict) -> None:
    _write_json(_seen_path(role, profile), data)


def _select(role: str, profile: str) -> list:
    """Пункты работы по роли — БЕЗ захвата (только чтение)."""
    _ensure_dirs()
    out = []
    for path in sorted(JOBS.glob("*.json")):
        job = _read_json(path)
        if not job:
            continue
        state = job.get("state")
        if role == "critic":
            if state != "pending_review":
                continue
        elif role == "fix":
            if state != "changes_requested":
                continue
            if job.get("producer") != profile:
                continue
        elif role == "approved":
            if state != "approved":
                continue
            if job.get("producer") != profile:
                continue
        elif role == "report":
            if state not in ("approved", "escalated"):
                continue
        else:
            raise SystemExit(f"неизвестная роль: {role}")
        out.append(job)
    return out


def _pick(role: str, profile: str) -> list:
    """Выдать пункты работы, которые этот профиль ещё не обрабатывал (с захватом)."""
    seen = _load_seen(role, profile)
    now = time.time()
    out = []
    for job in _select(role, profile):
        key = f"{job.get('project')}#{job.get('round')}"
        ts = seen.get(key)
        if ts is not None and now - float(ts) < RETRY_AFTER:
            continue
        seen[key] = now
        out.append(job)
    if out:
        _save_seen(role, profile, seen)
    return out


def cmd_list(args) -> int:
    profile = args.profile or "default"
    jobs = _select(args.role, profile)
    if not jobs:
        print("IDLE")
        return 0
    for job in jobs:
        _print_job(args.role, job)
    return 0


def cmd_pick(args) -> int:
    profile = args.profile or "default"
    jobs = _pick(args.role, profile)
    if not jobs:
        print("IDLE")
        return 0
    for job in jobs:
        _print_job(args.role, job)
    return 0


def _print_job(role: str, job: dict) -> None:
    if role == "critic":
        print(f"PENDING {job['project']} round={job.get('round')} "
              f"channel={job.get('channel')} storyboard={job.get('storyboard')}")
    elif role == "fix":
        issues = "; ".join(job.get("issues", []))
        print(f"FIX {job['project']} round={job.get('round')} "
              f"storyboard={job.get('storyboard')} report={job.get('report')} "
              f"issues={issues}")
    else:
        print(f"{job.get('state','').upper()} {job['project']} round={job.get('round')} "
              f"channel={job.get('channel')} producer={job.get('producer')} "
              f"report={job.get('report','')}")

# ------------------------------------------------------------- verdict

def cmd_verdict(args) -> int:
    job = _load_job(args.project)
    if not job:
        print(f"НЕТ ЗАДАЧИ: {args.project}")
        return 1
    if job.get("state") != "pending_review":
        print(f"НЕ ЖДЁТ ПРОВЕРКИ: {args.project} ({job.get('state')})")
        return 1
    issues = list(args.issue or [])
    if args.issues_file:
        issues.extend(_read_issues(args.issues_file))
    report = args.report
    if not report:
        report = str(REPORTS / f"{args.project}-r{job.get('round', 1)}.md")
    Path(report).parent.mkdir(parents=True, exist_ok=True)
    state = "approved" if args.result == "approved" else "changes_requested"
    job["state"] = state
    job["issues"] = issues
    job["report"] = report
    job["history"] = job.get("history", []) + [
        {"at": _now(), "event": state, "round": job.get("round"),
         "issues": len(issues)}]
    _save_job(job)
    print(f"ВЕРДИКТ {args.project}: {state} замечаний={len(issues)} отчёт={report}")
    return 0


def _read_issues(path: str) -> list:
    """Замечания из файла: JSON-массив или обычный текст (по строке на замечание)."""
    p = Path(path)
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except Exception:
            pass
    out = []
    for line in text.splitlines():
        s = line.strip()
        s = re.sub(r"^[-*•]\s*", "", s)
        s = re.sub(r"^\d+[.)]\s*", "", s)
        if s:
            out.append(s)
    return out


def cmd_resubmit(args) -> int:
    job = _load_job(args.project)
    if not job:
        print(f"НЕТ ЗАДАЧИ: {args.project}")
        return 1
    if job.get("state") not in ("changes_requested",):
        print(f"НЕЖДАТ ПРАВОК: {args.project} ({job.get('state')})")
        return 1
    rnd = int(job.get("round", 1)) + 1
    job["round"] = rnd
    job["issues"] = []
    if rnd > MAX_ROUNDS:
        job["state"] = "escalated"
        job["history"] = job.get("history", []) + [
            {"at": _now(), "event": "escalated",
             "why": f"круг {rnd} > предел {MAX_ROUNDS}"}]
        _save_job(job)
        print(f"ЭСКАЛАЦИЯ: {args.project} — круг {rnd} превысил предел {MAX_ROUNDS}")
        return 0
    job["state"] = "pending_review"
    job["history"] = job.get("history", []) + [
        {"at": _now(), "event": "resubmitted", "round": rnd}]
    _save_job(job)
    print(f"ПЕРЕПОДАНО НА ПРОВЕРКУ: {args.project} круг={rnd}")
    return 0


def _age_bucket(job: dict) -> int:
    """Грубая «возрастная» метка задачи: меняется раз в 30 минут.

    Нужна, чтобы потерянная задача (взята, но не доделана) снова вызвала
    агента: идентификатор меняется -> меняется хеш монитора -> LLM будится.
    """
    stamp = job.get("updated") or job.get("created") or ""
    try:
        t = time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))
    except (ValueError, TypeError):
        return 0
    return max(0, int((time.time() - t) // (30 * 60)))


def _digest_state_path(role: str, profile: str) -> Path:
    return STATE / f"digest_{role}_{profile}.json"


def cmd_digest(args) -> int:
    """Дешёвый сигнал «появилась работа» для monitor-режима cron.

    Вывод МОНОТОННЫЙ: накопленные идентификаторы задач не исчезают, поэтому
    исчезновение задачи (она сделана) НЕ будит агента, а появление новой — будит.

    Идентификатор: <проект>#<круг>[#<корзина>]. Корзина — число получасов с момента,
    когда задачу забрали: пока задача свежая, корзина 0 (стабильный вывод), а через
    полчаса меняется -> хеш монитора меняется -> агент будится и поднимает забытую
    задачу снова.
    """
    role = args.role
    profile = args.profile or "default"
    _ensure_dirs()
    seen = _load_seen(role, profile)
    now = time.time()
    current = []
    for job in _select(role, profile):
        key = f"{job.get('project')}#{job.get('round')}"
        ident = key
        if role in ("critic", "fix", "approved"):
            ts = seen.get(key)
            bucket = 0 if ts is None else int((now - float(ts)) // (30 * 60))
            ident = f"{key}#{bucket}"
        current.append(ident)
    path = _digest_state_path(role, profile)
    data = _read_json(path) or {}
    prev = [str(x) for x in (data.get("ids") or [])]
    merged = sorted(set(prev) | set(current))
    _write_json(path, {"ids": merged, "updated": _now()})
    if not merged:
        print("IDLE")
        return 0
    for ident in merged:
        print(ident)
    return 0


def cmd_done(args) -> int:
    """Пометить задачу как отработанную (раскадровка сгенерирована)."""
    job = _load_job(args.project)
    if not job:
        print(f"НЕТ ЗАДАЧИ: {args.project}")
        return 1
    job["state"] = "produced"
    job["history"] = job.get("history", []) + [
        {"at": _now(), "event": "produced", "round": job.get("round")}]
    _save_job(job)
    print(f"ОТРАБОТАНО: {args.project} (круг {job.get('round')})")
    return 0


def cmd_status(args) -> int:
    _ensure_dirs()
    rows = []
    for path in sorted(JOBS.glob("*.json")):
        job = _read_json(path)
        if job:
            rows.append(job)
    if not rows:
        print("очередь пуста")
        return 0
    for job in rows:
        print(f"{job.get('state',''):18} r{job.get('round')} "
              f"{job.get('project','')} channel={job.get('channel')} "
              f"author={job.get('producer')} issues={len(job.get('issues',[]))}")
    return 0


def cmd_report_path(args) -> int:
    """Путь отчёта критика для проекта (используется в промптах)."""
    job = _load_job(args.project)
    if not job:
        return 1
    print(REPORTS / f"{args.project}-r{job.get('round', 1)}.md")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="очередь проверки раскадровок")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("enqueue")
    p.add_argument("--project", default="")
    p.add_argument("--storyboard", required=True)
    p.add_argument("--producer", default="")
    p.add_argument("--channel", default="")
    p.set_defaults(func=cmd_enqueue)

    p = sub.add_parser("enqueue-scan")
    p.add_argument("--root", default="")
    p.add_argument("--minutes", type=float, default=30.0)
    p.set_defaults(func=cmd_enqueue_scan)

    p = sub.add_parser("list")
    p.add_argument("--role", required=True, choices=["critic", "fix", "approved", "report"])
    p.add_argument("--profile", default="")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("digest")
    p.add_argument("--role", required=True, choices=["critic", "fix", "approved", "report"])
    p.add_argument("--profile", default="")
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("done")
    p.add_argument("--project", required=True)
    p.set_defaults(func=cmd_done)

    p = sub.add_parser("pick")
    p.add_argument("--role", required=True, choices=["critic", "fix", "approved", "report"])
    p.add_argument("--profile", default="")
    p.set_defaults(func=cmd_pick)

    p = sub.add_parser("verdict")
    p.add_argument("--project", required=True)
    p.add_argument("--result", required=True, choices=["changes", "approved"])
    p.add_argument("--report", default="")
    p.add_argument("--issues-file", default="")
    p.add_argument("--issue", action="append", default=[])
    p.set_defaults(func=cmd_verdict)

    p = sub.add_parser("resubmit")
    p.add_argument("--project", required=True)
    p.set_defaults(func=cmd_resubmit)

    p = sub.add_parser("status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("report-path")
    p.add_argument("--project", required=True)
    p.set_defaults(func=cmd_report_path)

    args = ap.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
