#!/usr/bin/env python3
"""Telegram bot that forwards shared links, raw text, and screenshots to Claude Code for capture into an Obsidian vault."""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state.json"
PROMPT_PATH = ROOT / "prompt.md"
LOG_PATH = ROOT / "bot.log"


def load_env() -> dict:
    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"offset": 0}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")


URL_RE = re.compile(r"https?://\S+")


def extract_url_and_note(text: str) -> tuple[str | None, str]:
    m = URL_RE.search(text or "")
    if not m:
        return None, ""
    url = m.group(0).rstrip(").,;!?")
    note = (text[: m.start()] + " " + text[m.end():]).strip()
    return url, note


def download_photo(token: str, photo_sizes: list) -> str:
    """Download the highest-res photo and return the local temp file path."""
    best = max(photo_sizes, key=lambda p: p.get("file_size", 0))
    file_id = best["file_id"]
    info = requests.get(
        f"https://api.telegram.org/bot{token}/getFile",
        params={"file_id": file_id},
        timeout=30,
    ).json()
    file_path = info["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    ext = Path(file_path).suffix or ".jpg"
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False, dir=ROOT / "tmp")
    tmp.write(requests.get(url, timeout=60).content)
    tmp.close()
    return tmp.name


def run_claude(prompt: str, env: dict) -> tuple[str, str]:
    sub_env = os.environ.copy()
    sub_env["GEMINI_API_KEY"] = env["GEMINI_API_KEY"]
    proc = subprocess.run(
        [env["CLAUDE_BIN"], "-p", prompt, "--permission-mode", "bypassPermissions"],
        capture_output=True,
        text=True,
        env=sub_env,
        timeout=600,
    )
    return proc.stdout.strip(), proc.stderr.strip()


def build_prompt(mode: str, content: str, note: str, env: dict) -> str:
    template = PROMPT_PATH.read_text()
    return (
        template
        .replace("{MODE}", mode)
        .replace("{CONTENT}", content)
        .replace("{USER_NOTE}", note or "(none)")
        .replace("{VAULT_PATH}", env["VAULT_PATH"])
    )


def parse_result(stdout: str) -> str:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith(("SAVED:", "SKIP:", "FAIL:")):
            return line
    return "FAIL: no status line from claude"


def tg(token: str, method: str, **params) -> dict:
    r = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        json=params,
        timeout=(10, 90),
    )
    r.raise_for_status()
    return r.json()


def handle_message(env: dict, msg: dict) -> None:
    token = env["TELEGRAM_BOT_TOKEN"]
    chat_id = msg["chat"]["id"]
    text = msg.get("text") or msg.get("caption") or ""

    # --- Determine input mode ---

    # 1. Screenshot / photo
    if msg.get("photo"):
        (ROOT / "tmp").mkdir(exist_ok=True)
        img_path = download_photo(token, msg["photo"])
        note = text.strip()  # caption becomes the user note
        tg(token, "sendMessage", chat_id=chat_id, text="⏳ Reading screenshot...")
        log(f"capture start mode=image path={img_path} note={note!r}")
        prompt = build_prompt("IMAGE", img_path, note, env)

    # 2. URL (link)
    else:
        url, note = extract_url_and_note(text)
        if url:
            tg(token, "sendMessage", chat_id=chat_id, text=f"⏳ Capturing {url} ...")
            log(f"capture start mode=url url={url} note={note!r}")
            prompt = build_prompt("URL", url, note, env)

        # 3. Raw text (no URL) — e.g. pasted Claude conversation
        elif text.strip():
            tg(token, "sendMessage", chat_id=chat_id, text="⏳ Saving text clipping...")
            log(f"capture start mode=text chars={len(text)}")
            prompt = build_prompt("TEXT", text.strip(), note, env)

        else:
            tg(token, "sendMessage", chat_id=chat_id,
               text="Send me a link, paste some text, or share a screenshot.")
            return

    try:
        stdout, stderr = run_claude(prompt, env)
    except subprocess.TimeoutExpired:
        log("claude timeout")
        tg(token, "sendMessage", chat_id=chat_id, text="❌ Timed out after 10 minutes.")
        return
    except Exception as e:
        log(f"claude error: {e}")
        tg(token, "sendMessage", chat_id=chat_id, text=f"❌ Error: {e}")
        return
    finally:
        # clean up temp image if any
        if msg.get("photo"):
            try:
                Path(img_path).unlink(missing_ok=True)
            except Exception:
                pass

    status = parse_result(stdout)
    log(f"capture done: {status}")
    if stderr:
        log(f"stderr: {stderr[:500]}")

    if status.startswith("SAVED:"):
        reply = f"✅ {status[len('SAVED:'):].strip()}"
    elif status.startswith("SKIP:"):
        reply = f"↪️ Already saved: {status[len('SKIP:'):].strip()}"
    else:
        reply = f"❌ {status}"
    tg(token, "sendMessage", chat_id=chat_id, text=reply)


def main() -> int:
    env = load_env()
    token = env["TELEGRAM_BOT_TOKEN"]
    state = load_state()
    log(f"bot starting, offset={state['offset']}")

    while True:
        try:
            resp = tg(
                token, "getUpdates",
                offset=state["offset"],
                timeout=50,
                allowed_updates=["message", "channel_post"],
            )
        except requests.exceptions.ReadTimeout:
            continue
        except Exception as e:
            log(f"getUpdates error: {e}; sleeping 10s")
            time.sleep(10)
            continue

        for upd in resp.get("result", []):
            state["offset"] = upd["update_id"] + 1
            save_state(state)
            msg = upd.get("message") or upd.get("channel_post")
            if not msg:
                continue
            try:
                handle_message(env, msg)
            except Exception as e:
                log(f"handle_message error: {e}")


if __name__ == "__main__":
    sys.exit(main() or 0)
