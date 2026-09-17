# bookmarks-bot

A Telegram bot that turns anything you share with it into a clean Markdown clipping in an
Obsidian vault. Send it a link, paste some text, or share a screenshot; it replies with the
path it saved.

It does no parsing itself. It shells out to [Claude Code](https://claude.com/claude-code)
in headless mode (`claude -p`) with `prompt.md` as the instruction set, and Claude does the
fetching, extraction, dedupe, and file writing.

## What it accepts

| You send | Mode | What happens |
|---|---|---|
| A URL (with optional note around it) | `URL` | Fetched and saved as an article clipping |
| Plain text, no URL | `TEXT` | Saved as a text clipping — handy for pasted conversations |
| A photo or screenshot | `IMAGE` | Read via Gemini, text extracted, saved |

Any text alongside a link or photo caption becomes the clipping's user note (tags, comments).

Per-source handling lives in `prompt.md`, not in the bot: X/Twitter is rewritten to
`fxtwitter.com` and threads are walked up to 20 replies; WeChat articles get title, 公众号,
and body; RedNote `xhslink.com` links are redirect-resolved and parsed from
`window.__INITIAL_STATE__`; everything else falls back to readable-article extraction.

**Images are not saved by default.** Text and OCR are always captured, but image files and
`![](...)` embeds are skipped unless your note opts in ("save the images", "with media",
"图片", "带图").

Before fetching a URL it greps the Clippings folder for an existing note with the same
`url:` frontmatter and skips duplicates.

## Replies

Claude ends with one status line, which the bot turns into a reply:

- `SAVED: <path> :: <title>` → ✅ with the path
- `SKIP: <path>` → ↪️ already saved
- `FAIL: <reason>` → ❌ with the reason

Captures time out after 10 minutes.

## Setup

```bash
pip install requests
cp .env.example .env    # then fill it in
python3 bot.py
```

`.env`:

| Key | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | From [@BotFather](https://t.me/BotFather) |
| `GEMINI_API_KEY` | Google AI Studio key, used to read screenshots |
| `VAULT_PATH` | Absolute path to the Obsidian vault; clippings land in `<vault>/Clippings/` |
| `CLAUDE_BIN` | Absolute path to the `claude` binary |

The bot long-polls `getUpdates` and stores its update offset in `state.json`, so it resumes
where it left off across restarts. On macOS it runs as a launchd agent
(`~/Library/LaunchAgents/com.zhezheng.bookmarks-bot.plist`).

## A note on the logs

`bot.log` records the Telegram API calls it makes, and those URLs embed the bot token. The
logs are gitignored for that reason — don't commit them, and rotate the token via
`@BotFather` if one ever leaves your machine.

Claude is invoked with `--permission-mode bypassPermissions` so it can write to the vault
without prompting. It will act on whatever you send the bot, so keep the bot private to you
(and see `habit-bot`'s `ALLOWED_CHAT_ID` for the pattern if you want to pin it to one chat).

## Related

The same capture engine backs the `save-clipping` Claude Code skill; this bot is the
share-sheet-friendly front end to it.
