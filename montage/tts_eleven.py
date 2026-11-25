"""Озвучка реплик выпуска через ElevenLabs API (в обход гео-блока BY).

Ключ и прокси берутся из окружения: ELEVENLABS_API_KEY, ELEVENLABS_PROXY
(если переменных нет — читаются из .env).

Примеры:
    python engine/tts_eleven.py --project projects/channel2-ep1 --voice 21m00Tcm4TlvDq8ikWAM --demo 2
    python engine/tts_eleven.py --project projects/channel2-ep1 --voice <id> --all
    python engine/tts_eleven.py --text "Hello world" --voice <id> --out out.mp3

Результат: assets/vo_el/<shot_id>.mp3 + .wav (24 кГц моно) и durations.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.elevenlabs.io/v1"
ROOT = Path(__file__).resolve().parents[1]
_prof = os.environ.get("HERMES_PROFILE") or "default"
ENV_FILES = [Path(os.environ.get("HERMES_ENV_FILE", ".env"))]


def load_env() -> None:
    for env_file in ENV_FILES:
        if not env_file.exists():
            continue
        text = env_file.read_text(encoding="utf-8", errors="ignore")
        for name, value in re.findall(r"(?m)^([A-Z0-9_]+)=(.*)$", text):
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(name, value)


def make_opener(proxy: str | None):
    if not proxy:
        return urllib.request.build_opener()
    # urllib не умеет socks5h — гоняем через curl
    return None


def curl_tts(text: str, voice: str, model: str, out: Path, key: str, proxy: str | None,
             speed: float = 1.0, stability: float | None = None) -> int:
    body: dict = {"text": text, "model_id": model}
    vs = {k: v for k, v in {"speed": speed, "stability": stability}.items() if v is not None}
    vs = {k: (v if not abs(v - 1.0) < 1e-6 else 1.0) for k, v in vs.items()}
    if vs:
        body["voice_settings"] = vs
    payload = json.dumps(body)
    url = f"{API}/text-to-speech/{voice}?output_format=mp3_44100_128"
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        fh.write(payload)
        body_path = fh.name
    cmd = ["curl", "-s", "-X", "POST", url,
           "-H", f"xi-api-key: {key}", "-H", "Content-Type: application/json",
           "--data-binary", f"@{body_path}", "-o", str(out), "-w", "%{http_code}"]
    if proxy:
        cmd[1:1] = ["--proxy", proxy]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    finally:
        os.unlink(body_path)
    code = (res.stdout or "").strip()[-3:]
    if code != "200":
        detail = out.read_text(encoding="utf-8", errors="ignore")[:300] if out.exists() else res.stderr[:300]
        raise RuntimeError(f"HTTP {code}: {detail}")


def to_wav(mp3: Path, wav: Path, sr: int = 24000) -> float:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3),
                    "-ac", "1", "-ar", str(sr), str(wav)], check=True)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(wav)], capture_output=True, text=True, check=True)
    return round(float(probe.stdout.strip()), 3)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project")
    ap.add_argument("--voice", required=True)
    ap.add_argument("--model", default="eleven_multilingual_v2")
    ap.add_argument("--text")
    ap.add_argument("--out", default="out.mp3")
    ap.add_argument("--demo", type=int, default=0, help="озвучить первые N реплик")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--stability", type=float, default=None,
                    help="стабильность голоса 0-100 (ElevenLabs voice_settings.stability)")
    args = ap.parse_args()

    load_env()
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    proxy = os.environ.get("ELEVENLABS_PROXY", "").strip() or None
    if not key:
        print("ELEVENLABS_API_KEY не задан")
        return 1

    if args.text:
        out = Path(args.out)
        curl_tts(args.text, args.voice, args.model, out, key, proxy, args.speed, args.stability)
        print(f"ok: {out} ({out.stat().st_size} байт)")
        return 0

    if not args.project:
        print("нужен --project или --text")
        return 1
    proj = Path(args.project)
    if not proj.is_absolute():
        proj = ROOT / proj
    data = json.loads((proj / "project.json").read_text(encoding="utf-8"))
    shots = data["shots"]
    if args.demo:
        shots = shots[: args.demo]
    if not (args.all or args.demo):
        print("укажи --all или --demo N")
        return 1

    vo = proj / "assets" / "vo_el"
    vo.mkdir(parents=True, exist_ok=True)
    durations: dict[str, float] = {}
    chars = 0
    for shot in shots:
        sid = shot["id"]
        text = shot.get("line", "").strip()
        if not text:
            continue
        mp3, wav = vo / f"{sid}.mp3", vo / f"{sid}.wav"
        try:
            curl_tts(text, args.voice, args.model, mp3, key, proxy, args.speed, args.stability)
        except Exception as exc:  # noqa: BLE001
            print(f"{sid}: ошибка — {exc}")
            return 2
        durations[sid] = to_wav(mp3, wav)
        chars += len(text)
        print(f"{sid}: {durations[sid]:.2f} c  ({len(text)} симв.)")
    (vo / "durations.json").write_text(json.dumps(durations, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nвсего: {len(durations)} реплик, {chars} символов, {sum(durations.values()):.2f} c речи")
    return 0


if __name__ == "__main__":
    sys.exit(main())
