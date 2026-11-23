# Capri desktop companion

A small, private AI companion that lives on your Windows desktop. Capri chats
with you through a speech bubble, speaks her replies out loud, shows a matching
expression, blinks, glances around and moves her mouth with her voice. By
default the language model and the voice run entirely on this PC, and nothing
leaves your computer unless you ask her to look something up online. If you
like, she can think with Claude instead, online, using your own API key.

This is version 0.1: text chat, a spoken voice (no voice control), lookups you
ask for, and an optional online brain (Claude).

Capri is a fan character from *Cyberpunk: Edgerunners 2*. This is an unofficial
fan project, not affiliated with or endorsed by CD PROJEKT or Studio Trigger. The
character art isn't included (see [Character art](#character-art)).

## Installing

You need Windows 10 or 11 (64-bit) and [Python 3.13](https://www.python.org/downloads/)
(exactly 3.13: the pinned packages are built for it). Then double-click
**Install.cmd**. It sets everything up inside this folder:

| Step | What | Size |
|---|---|---|
| 1 | A private Python environment (`.venv`) with the pinned packages | about 385 MB |
| 2 | [llama.cpp](https://github.com/ggml-org/llama.cpp) b12317, which runs the language model (Vulkan, also runs on the CPU) | 38 MB download |
| 3 | The language model, Qwen3.5 4B | 2.7 GB |
| 4 | The voice, Kokoro-82M | 350 MB |

Every download is checked against a fixed SHA-256 checksum, and Python packages
are installed with `--require-hashes` and `--only-binary=:all:`, so no package
build script ever runs. An interrupted download resumes when you run Install.cmd
again. `Install.cmd --small` installs the faster Qwen3.5 2B (1.3 GB) instead.

## Starting

Double-click **Companion.cmd**. Capri appears at the bottom-right of your screen.
For a desktop shortcut, point it at `.venv\Scripts\pythonw.exe` with the argument
`-m companion`, starting in this folder.
She needs about 25–30 seconds to wake up after each start, while she loads the
model and warms up the graphics chip. Her voice loads a few seconds after that.

| Do this | To |
|---|---|
| Type and press **Enter** | Talk to her |
| Click 🌐, or press **Ctrl+Enter** | Let her look your message up online (that message only) |
| Press **Esc** | Stop her reply and her voice, and show the whole reply |
| Drag her (or her window's title bar) | Move her (she remembers where) |
| Click her | Get her attention |
| Right-click her | New conversation, **Settings…**, personality, **Brain**, **Voice**, **Internet**, always on top, show in a window, hide, restart, quit |
| Use the tray icon | Show or hide her, open the settings, or quit |

Quitting stops the AI engine and frees its memory. She doesn't start with Windows.

## Her brain: local or Claude

Right-click her and open **Brain** to choose what she thinks with.

| Brain | Where it runs | What leaves this PC | Cost |
|---|---|---|---|
| **Local** (default) | Qwen3.5 4B, on this PC | Nothing, unless you use 🌐 | Free |
| **Claude** | Anthropic's Claude API, online | Every message, with her personality and your recent conversation | Paid from your own API credit |

With Claude she's much smarter, especially with search results, and she's ready
in about 6 seconds instead of 25–30. The local model isn't loaded then, which
leaves about 4 GB of memory free. Her voice, robot effect, subtitles and
expressions stay on this PC either way.

**Setting up Claude.** A Claude Pro or Max subscription doesn't include the API;
it's billed separately.

1. Sign in to the Claude Console (platform.claude.com) and buy a few dollars of
   credit. Leave auto-reload off: then it can never cost more than what you
   bought.
2. Create an API key there.
3. Right-click her → **Brain** → **Claude (online)**. She tells you what will be
   sent, asks for the key and connects. The key is stored encrypted for your
   Windows account (`data/claude-key.bin`) and is only ever sent to Anthropic.

**Models** (Brain → Claude model). Prices per million tokens, checked in November
2026; see Anthropic's pricing page for current ones.

| Model | Input / output | Roughly per message |
|---|---|---|
| Haiku 5.5 (default) | $0.10 / $0.50 | $0.0002–0.0005 |
| Sonnet 5.5 | $2 / $10 | $0.003–0.009 |
| Opus 5.5 | $4 / $20 | $0.01–0.02 |

A web search adds 1 cent, plus the result pages Claude reads (a fraction of a
cent with Haiku, a few cents with Sonnet or Opus). Anthropic keeps her
personality and your conversation in a short-lived cache, which makes follow-up
messages cheaper. The line under each reply shows the model, what that reply
cost and the total for this session. These are estimates from Anthropic's usage
numbers; the Console shows the exact bill.

**Weather with Claude** needs no 🌐: whenever you ask about the weather (or
whether you need a jacket), Claude asks the app for an Open-Meteo forecast, which
is free and exact. Only the place name goes to Open-Meteo. Set your town under
right-click → **Internet** → **Weather location…** so questions like "will it rain
tomorrow?" work without naming a place; otherwise she asks where you mean.

**Searching with Claude.** By default Claude only searches the web when you ask
(🌐 or Ctrl+Enter), at most 3 searches per message, and tells you where the answer
came from. Choose Brain → **Claude searches the web** → **Whenever it helps** to let
Claude decide by itself. Messages that look like they contain a password, key or
card number aren't searched.

**If Claude can't answer** (no internet, credit used up, key rejected, Claude
overloaded), her local brain answers instead and says why under the reply. If it
isn't loaded, it wakes up first (about half a minute). The next message tries
Claude again.

## Looking things up online

**Weather** works by itself: ask "what's the weather in Ghent?", "will it rain
tomorrow?" or a follow-up like "and tomorrow?", and she fetches a forecast from
Open-Meteo. Only the place name is sent.

For anything else, she only goes online when you ask: click the 🌐 button next to
the text box (or send with **Ctrl+Enter**), and she looks up that one message. The
button switches itself off afterwards. Under her reply you'll see where the answer
came from. This section describes the local brain; with Claude, Claude does the
searching (see above).

| Question | Looked up with | Account needed |
|---|---|---|
| Weather ("weather in Paris tomorrow?") | [Open-Meteo](https://open-meteo.com) | No |
| Anything else, with a Brave key | [Brave Search API](https://brave.com/search/api/) | Yes, your own |
| Anything else, without a key (or if Brave fails) | [Wikipedia](https://en.wikipedia.org) | No |

**Brave web search:** create a key at Brave's API dashboard, then right-click her →
**Internet** → **Set web search key (Brave)…**. The key is stored encrypted for your
Windows account (`data/brave-key.bin`). Brave's Search plan costs $5 per 1,000
searches and includes $5 of credit each month, roughly 1,000 free searches. Brave
may ask for a card, so consider setting a spending limit in its dashboard. Prices
were checked in November 2026; see Brave's site for current ones.

**Weather:** if you don't name a place, she uses your weather location (right-click
→ **Internet** → **Weather location…**) or asks you.

Looked-up answers take a little longer: about 3–4 s to her first word, instead
of about 1.5–2.5 s. She's a small model, so check the sources for anything
important.

## Her voice

Right-click her and open **Voice** to turn speech on or off, pick a voice, add a
robot effect or change the pitch. Every choice plays a short sample right away.
The voices are Kokoro's own synthetic voices (Heart is the default), plus a mix
of Heart and Bella. They only speak English, so she always answers in English,
even when you write to her in another language.

| Robot effect | What you hear |
|---|---|
| No effect | Her plain voice |
| Light robotic | Her natural intonation with a metallic sheen and a faint doubled voice |
| Robotic (default) | Partly flattened onto a machine note, with a metallic buzz and slight digital grain |
| Full robot | A monotone machine voice: every word on one note, with strong buzz and grit |

**Pitch** (Lower, Normal, Higher, Much higher) changes how high she sounds
without changing how fast she talks. The effects take a few milliseconds per
sentence and use no extra memory.

**Show text as she speaks** (on by default) makes her words appear in the bubble
as she says them, like subtitles, instead of all at once before she speaks.
While she's about to speak, the bubble shows dots. Press **Esc** to stop her and
see the whole reply immediately. Turn the option off in the Voice menu if you
prefer to read ahead. With speech off, text always appears right away.

The voice is [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) (Apache-2.0),
running on the CPU while the chat model uses the graphics chip. She starts
speaking after the first sentence or phrase is written, and keeps going sentence
by sentence. Text in `*asterisks*`, emoji and links are shown but not read out.

The voice isn't the one from the original CapriCore project. That voice was made
from a voice actress's dialogue in an anime, without her permission (CapriCore's
own voice pack notice says so), so this project doesn't copy it.

## Speed on a typical laptop

Measured on a laptop with an Intel i5-1235U, Iris Xe graphics and 16 GB of RAM,
using Qwen3.5 4B on the graphics chip and the voice on the CPU:

| | Measured |
|---|---|
| Waking up (model load + warm-up) | about 22–30 s |
| First words of a reply on screen | about 1.5–2.6 s |
| Her voice starts | about 5 s after you press Enter |
| Writing speed | about 9 tokens/s (about 7 words/s) |
| Voice speed while she's still writing | 1.2–1.4× faster than real time |
| Memory while running | about 4.8 GB (4.0 GB model, the rest app and voice) |

For faster replies with a less capable model, run `Install.cmd --small`, which
downloads Qwen3.5 2B (1.3 GB) and switches to it. To go back, set `"model"` in
settings.json to `models/Qwen3.5-4B-Q4_K_M.gguf`.

## Personality

Edit **personality.txt** (right-click her, then **Edit personality…**). Changes
apply to her next reply. Keep it short; every word is read before each reply.

On the first start, personality.txt is created with Capri's built-in personality
(`DEFAULT_PERSONALITY` in `companion/config.py`). Delete the file to get that back.
Like settings.json, it's your own file, so Git ignores it.

## Settings

Right-click her (or the tray icon) and choose **Settings…**. The settings window opens
in the middle of the screen. It's an ordinary window: it doesn't stay on top, and you
can minimize it. Change as many settings as you like across
its pages (Brain, Local brain, Voice, Internet, Display), then press **Save**. The bottom
line counts your unsaved changes and says what saving will reload.

Saving applies everything right away, and reloads only what has to load again:

- **Local brain** settings (graphics chip or CPU, threads, working memory) reload the
  local model, which takes about half a minute. If Claude is her brain, they apply the
  next time the local model starts.
- Changing the **brain**, the **Claude model** or the **Claude API key** reconnects her.
- **Voice threads** reload her voice.
- Everything else (voice, pitch, size, bubble time, lookups…) applies without reloading.

If she's in the middle of a reply, saving stops it first. The window stays open after
saving; **Close**, **✕** or **Esc** closes it (asking first if something is unsaved); **Defaults** puts every setting back (your keys stay).

The window saves to `settings.json`, which you can also edit by hand (**Display** →
**Open file**, or the file itself). It also holds the model and engine paths, which the
window doesn't show. Restart her after editing the file.

| Setting | Meaning |
|---|---|
| `device` | `"gpu"` uses the graphics chip through Vulkan (faster to start replying); `"cpu"` doesn't use it. If the graphics chip fails, she switches to the CPU on her own. |
| `threads`, `threads_batch` | CPU threads for writing and for reading the prompt (CPU mode only) |
| `context_tokens` | The model's working memory. Bigger uses more RAM. |
| `history_tokens` | How much of your past conversation goes into each reply |
| `max_reply_tokens` | Longest possible reply |
| `temperature` | Higher is more creative, lower is more predictable |
| `voice_enabled`, `voice` | Speech on or off, and which voice (the Voice menu changes both) |
| `text_with_voice` | Show the reply's words as she says them (`true`), or all at once (`false`) |
| `voice_speed`, `voice_volume` | Speaking speed (0.5–2.0) and loudness (0–1) |
| `voice_effect`, `voice_pitch` | Robot effect (`off`, `light`, `robotic`, `full`) and pitch (0.8–1.25); the Voice menu changes both |
| `voice_threads` | CPU threads for the voice. 4 worked best on the laptop above; more competes with the chat model. |
| `character_height` | Her size on screen, in pixels |
| `window_mode` | `"overlay"`: she stands right on your desktop; `"window"`: she's in a small framed window you can minimize (its ✕ hides her in the tray) |
| `always_on_top`, `show_stats`, `bubble_seconds` | Whether she stays above other windows, the speed line under replies, and how long the bubble stays |
| `web_location` | Your city, for weather questions that don't name a place (the Internet menu sets it) |
| `web_results`, `web_safesearch` | How many search results she reads (1–10), and Brave's safe search (`off`, `moderate`, `strict`) |
| `brain` | `"local"` or `"claude"` (the Brain menu changes it) |
| `claude_model` | `claude-haiku-5-5`, `claude-sonnet-5-5` or `claude-opus-5-5` |
| `claude_search`, `claude_max_searches` | When Claude may search the web (`globe`: only when you ask; `auto`: whenever it helps), and the most searches per message (1–5) |

## Privacy and security

- **You choose the brain.** With the local brain (the default), your conversation
  never leaves this PC. With Claude, each message goes to Anthropic over HTTPS
  together with her personality and your recent conversation. Under Anthropic's
  commercial terms, API data isn't used to train models by default, but it is
  processed and stored on their servers for a while. Switching to Claude always
  asks first, and you can switch back at any time.
- **Lookups only when you ask** (local brain). Apart from weather questions, which
  send only a place name to Open-Meteo, nothing goes online unless you use 🌐 or
  Ctrl+Enter for a message. Then only the question part of that message is sent,
  never your history or personality. Messages that look like they contain a
  password, key, email address or card number aren't sent at all.
- **Fixed addresses.** Lookups can only reach Brave's search API, Wikipedia and
  Open-Meteo, over HTTPS. Redirects elsewhere are refused, and she never opens
  result links, so a web page can't make the app connect anywhere else. The Claude
  brain only talks to api.anthropic.com; that address is fixed in the code, so an
  environment variable can't send your key somewhere else. Claude's web search runs
  on Anthropic's servers, and no location is sent with it.
- **Web text is untrusted.** Results are size-limited, stripped of markup and
  marked as untrusted for the model. They're used for that one reply and aren't
  saved or logged. Since she can't act on anything, the worst a misleading page can
  do is make her say something wrong.
- **No other network use.** The AI engine listens only on 127.0.0.1, on a random
  port, and needs a random key that is created at each start. No telemetry (the
  voice library's is switched off), no update checks. Anthropic's library includes
  the OpenTelemetry interface but nothing that sends data; like any API client, it
  tells Anthropic its own version and your Windows and Python versions.
- **Chat only.** Neither brain can run programs, open apps, read or change files
  or see your screen. Claude's only tools are Anthropic's web search and a weather
  tool that sends a place name to Open-Meteo. Replies are shown as plain text and
  never acted on.
- **Voice stays in memory.** Speech is generated on the CPU and played through
  Windows directly. No audio files are written.
- **Encrypted history.** Your conversation is saved in `data/history.bin`, encrypted
  for your Windows account (DPAPI). Right-click, then **New conversation**, to delete it.
  `data/engine.log` holds only technical timings, not your messages.
- **No leftover processes.** The engine is tied to the app with a Windows job object,
  so it ends even if the app crashes. It runs at below-normal priority.
- **Pinned downloads.** Every download (Python packages including Anthropic's
  `anthropic` 2.12.0, llama.cpp b12317, the language model and the voice model) is
  checked against a fixed SHA-256 checksum. Updating is manual.
- **Encrypted keys.** The Brave and Claude API keys are encrypted for your Windows
  account, each separately, and never shown or logged.

## Files

| Path | What it is |
|---|---|
| `companion/` | The app's source code (Python) |
| `tests/` | Unit tests |
| `LICENSE` | MIT License for the code |
| `personality.txt`, `settings.json` | Yours to edit; created on the first start and not tracked by Git |
| `data/` | Your encrypted conversation and keys, window position and the engine log (not tracked) |
| `engine/`, `models/`, `.venv/` | Downloaded by Install.cmd (`models/voice/` is the voice) |
| `assets/character/`, `assets/icon/` | Character images and icon: not in the repository, see below |
| `assets/fonts/exo2/` | Exo 2 font (SIL Open Font License) |
| `assets/ui/` | Small arrow images for the settings window |

To uninstall, quit Capri and delete this folder. Nothing is installed anywhere else.

## Character art

The character images and the icon aren't in this repository, because the
character isn't ours to share. Without them, she appears as a simple placeholder.

To use your own character, put PNG images with a transparent background in
`assets/character/`, all the same square size:

- `capri-neutral.png`, her resting look.
- `capri-idle-<mood>.png` for each expression: `happy`, `excited`, `laughing`,
  `smirk`, `flirty`, `surprised`, `thinking`, `focused`, `attentive`, `concerned`,
  `sad`, `embarrassed`, `annoyed`, `skeptical`, `sleepy`.
- `capri-idle-<frame>.png` for the animation frames: `blink_half`, `blink_closed`,
  `wink_half`, `wink_closed`, `look_left`, `look_right`, `look_up`, `look_down`,
  `mouth_small`, `mouth_round`, `mouth_wide`, `surprised_closed`.

Any image that's missing falls back to the neutral one. An icon at
`assets/icon/capri.ico` (or `app.ico`) is used for the tray and her windows.

## Development

Run the tests with:

```
.venv\Scripts\python.exe -m unittest discover -s tests
```

Run with a console for error messages: `.venv\Scripts\python.exe -m companion`.

## License

The code is under the [MIT License](LICENSE). That covers the code only: the
Capri character and her name belong to their owners, the Exo 2 font keeps its own
license (see `assets/fonts/exo2/`), and the models and engine that Install.cmd
downloads come under their own licenses.
