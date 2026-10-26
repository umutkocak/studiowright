<div align="center">

# StudioWright

**Google Flow and Google Flow Music, from your terminal and your AI agent.**

Images, video clips and songs made in your own signed-in Chrome, on your own subscription.<br>
No API keys. No cookie exports. No captcha solvers. No browser extension.

![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![MCP server](https://img.shields.io/badge/MCP-server-6E56CF)
![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-D97757)
![Tested on Windows 11](https://img.shields.io/badge/tested%20on-Windows%2011-0078D4)

</div>

> [!IMPORTANT]
> StudioWright is an independent, unofficial project. It is not affiliated with, endorsed
> or sponsored by Google. It drives Flow's and Flow Music's web pages in **your** browser
> and spends **your** credits. Read the [disclaimer](#disclaimer) before you use it.

```bash
sw image "a lighthouse keeper with a teal coat, flat textured 2D" --aspect 16:9
sw video "he turns towards the sea" --start keeper.jpg --duration 4 --resolution 360p
sw music "moody indie folk, warm male vocal" --lyrics-file chorus.txt --length 1:00
```

## Contents

- [Showcase](#showcase)
- [Why StudioWright](#why-studiowright)
- [Features](#features)
- [Install](#install)
- [Quick start](#quick-start)
- [Use it from AI agents](#use-it-from-ai-agents)
- [Command reference](#command-reference)
- [Batch files](#batch-files)
- [Costs and safety](#costs-and-safety)
- [How it works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [Configuration](#configuration)
- [Development](#development)
- [Disclaimer](#disclaimer)

## Showcase

Everything here was made with StudioWright, from one small batch file and two chat
messages.

```json
{"id": "keeper", "kind": "image", "prompt": "a lighthouse keeper with a teal coat, standing, flat textured 2D illustration", "refs": ["style.png"]}
{"id": "keeper-close", "kind": "image", "prompt": "close-up portrait of the same lighthouse keeper, flat textured 2D", "refs": ["keeper"]}
{"id": "keeper-clip", "kind": "video", "prompt": "the lighthouse keeper turns his head towards the sea, gentle wind", "start": "keeper", "resolution": "360p", "duration": 4}
```

<table>
<tr>
<td width="50%"><img src="docs/media/keeper.jpg" alt="A lighthouse keeper in a teal coat on a cliff at night"><br><sub><b>keeper</b> · Nano Banana 2.1 · style from a reference image · 22 s</sub></td>
<td width="50%"><img src="docs/media/keeper-closeup.jpg" alt="Close-up of the same lighthouse keeper"><br><sub><b>keeper-close</b> · the same character, by referring to the first job · 21 s</sub></td>
</tr>
<tr>
<td width="50%"><img src="docs/media/keeper-clip.gif" alt="The keeper turns towards the sea"><br><sub><b>keeper-clip</b> · Omni Flash, 4 s, start frame from the first job · 32 s · 4 credits</sub></td>
<td width="50%"><a href="docs/media/beacon-in-the-dark.mp4"><img src="docs/media/beacon-cover.jpg" alt="Cover art of the song Beacon in the Dark: a lighthouse in a storm"></a><br><sub><b>Beacon in the Dark</b> · written and sung by the Flow Music Producer · 37 s · 5 credits<br><a href="docs/media/beacon-in-the-dark.mp4">▶ listen (MP4)</a> · <a href="docs/media/beacon-in-the-dark.mp3">MP3</a></sub></td>
</tr>
</table>

The song took two chat turns:

```bash
sw music chat "Write a four-line chorus about a lighthouse keeper who waits for the ships."
sw music chat "Moody indie-folk please. Make the song now, about one minute long." --session <id from the first reply>
```

## Why StudioWright

Most ways to automate Google's media models need one of these: a paid API key, cookies
copied out of your browser, a captcha-solving service, an unpacked browser extension, or a
third-party relay that holds your session. StudioWright needs none of them.

| | Paid model APIs | Cookie-based wrappers | Hosted relays | **StudioWright** |
| --- | :---: | :---: | :---: | :---: |
| Uses the credits of your Flow / Flow Music plan | ✗ | ✓ | ✓ | **✓** |
| No API key or per-call billing | ✗ | ✓ | ✗ | **✓** |
| Your session never leaves your machine | ✓ | ✗ | ✗ | **✓** |
| No captcha solving or token forging | ✓ | ✗ | ? | **✓** |
| Every option of the web app | partial | partial | partial | **✓** |
| Images, video **and** music in one tool | ✗ | ✗ | partial | **✓** |

What makes it different:

- **It acts like you.** StudioWright types, clicks and chooses files in Flow's real pages,
  in a Chrome you signed in to yourself. It only *reads* the replies those pages receive,
  to learn ids and download links. It never forges, replays or alters a request.
- **One package, three front doors.** The same engine is a CLI (`sw`), an MCP server
  (`sw mcp`) and a Claude Code plugin.
- **Built for real production.** Price checks before you spend (`--dry-run`,
  `--max-credits`), resumable batch files that chain results, sized downloads (2K images,
  1080p/4K clips), pacing across processes, and a cooldown when Google asks for a break.
- **Fast on a warm tab.** About 20 s for an image, 30-60 s for a clip, 40 s for a song.

## Features

**Images (Google Flow)**
- Nano Banana 2.1 (default), Nano Banana Pro and Nano Banana 2 Lite.
- Aspect ratios 16:9, 4:3, 1:1, 3:4, 9:16; one to four images per call.
- Reference images from files or earlier results; each file is uploaded once per project
  and then reused by content.
- Downloads at Flow's own sizes: 1K (original), 2K (upscaled by Flow), 4K where the plan
  allows.

**Video (Google Flow)**
- Omni Flash (default), Veo 3.1 Lite, Fast and Quality.
- Text only, start frame, start and end frames, or ingredients (several reference images).
- Omni: 360p / 720p and 4, 6, 8 or 10 s. Aspect 16:9 or 9:16.
- Downloads at 720p, 1080p or 4K (upscaled by Flow), or as a GIF.
- `--dry-run` shows Flow's price without submitting; `--max-credits` refuses anything
  more expensive.

**Music (Google Flow Music)**
- Songs with your lyrics (any language) or instrumentals; Lyria 3.5 or Lyria 3 Pro;
  BPM, length (1:00-3:00), seed, title, and cover art from a file or a prompt.
- **Edit existing songs:** extend, cover in another style, replace a section, variation,
  trim.
- **Stems:** drums, bass, vocals and other, each as its own file.
- **Downloads:** WAV (48 kHz), M4A or MP3; vertical MP4 videos from Flow Music's
  templates.
- **The Producer agent:** chat with it to write lyrics, plan songs, remix your own audio
  or make AI music videos; songs it makes are saved automatically.
- **Your own audio:** upload a file and extend, cover or split it like any other song.

**Automation**
- CLI with `--json` output, an MCP server with 16 tools, and a Claude Code plugin with a
  skill.
- JSON Lines batch files that resume where they stopped and pass results between jobs.
- A job history (`sw jobs`) and a screenshot of the page whenever a job fails.

## Install

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Google Chrome, and a Google
account with a Flow plan (images and video) and/or a Flow Music plan (music).

```bash
uv tool install git+https://github.com/umutkocak/studiowright
sw --version
```

This puts two identical commands on your PATH: `studiowright` and its short form `sw`.

<details>
<summary>From a local checkout (for development)</summary>

```bash
git clone https://github.com/umutkocak/studiowright.git
cd studiowright
uv sync
uv tool install --python 3.13 -e .
```

</details>

### Updating

- **Plugin** (MCP server and skill): `claude plugin update studiowright@studiowright`, or in
  Claude Code `/plugin` → Installed → studiowright → Update now; then `/reload-plugins`.
  Each plugin version runs the StudioWright release of the same version. To update
  automatically, turn it on once: `/plugin` → Marketplaces → studiowright → Enable
  auto-update.
- **Command line**: `uv tool install --force git+https://github.com/umutkocak/studiowright@vX.Y.Z`
  with the release you want (see [Releases](https://github.com/umutkocak/studiowright/releases)).

## Quick start

**1. Sign in once.** Chrome opens on a profile that belongs to StudioWright only. Sign in
to Google, open Flow once, then close the window.

```bash
sw auth login
sw auth login --url https://www.flowmusic.app/    # only if you will make music
```

Run `sw stop` first if StudioWright's Chrome is already running.

**2. Make something.**

```bash
sw image "a red fox in the snow, flat 2D" --aspect 1:1
sw video "slow push-in on a quiet harbour at dawn" --model veo-lite --dry-run   # price only
sw music "calm documentary underscore, soft piano, warm strings" --bpm 80 --length 1:30
```

Files land in `Downloads/studiowright` (change it with `--out`), and each command prints
their paths. Add `--json` for ids, sizes, timings and credits.

**3. Keep Chrome in the background.** `sw window hide` moves the window off-screen. Never
minimize it: a minimized Chrome stops rendering and Flow stalls.

## Use it from AI agents

Every client below runs the same server: `sw mcp` (stdio). If a client cannot find `sw`,
give it the full path (`where sw` on Windows, `which sw` elsewhere).

Generating takes real time (an image ~20 s, a clip up to a few minutes), so give the
client a generous tool timeout where it has one.

### Claude Code plugin (recommended)

The plugin adds the MCP server and a skill that teaches Claude the options, prices and
safe habits.

```bash
claude plugin install studiowright --marketplace umutkocak/studiowright
```

or inside Claude Code:

```text
/plugin marketplace add umutkocak/studiowright
/plugin install studiowright@studiowright
```

The plugin starts its own matching StudioWright release with `uvx`, so it needs only `uv`
and Chrome; installing `sw` separately is for the command line.

Then just ask:

> Make a 16:9 image of a lighthouse keeper, then a 4-second clip where he turns to the
> sea. Check the price first.
>
> Write a one-minute instrumental bed for a documentary intro and give me the stems.

### Claude Code (MCP server only)

```bash
claude mcp add --scope user studiowright -- sw mcp
```

For long video jobs, raise the tool timeout, e.g. `MCP_TOOL_TIMEOUT=900000` in your
environment.

<details>
<summary><b>Claude Desktop</b></summary>

Settings → Developer → Edit Config, then add the server to `claude_desktop_config.json`
(`%APPDATA%\Claude\` on Windows, `~/Library/Application Support/Claude/` on macOS):

```json
{
  "mcpServers": {
    "studiowright": { "command": "sw", "args": ["mcp"] }
  }
}
```

Quit Claude Desktop completely and start it again.
</details>

<details>
<summary><b>Cursor</b></summary>

`~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json` (one project):

```json
{
  "mcpServers": {
    "studiowright": { "command": "sw", "args": ["mcp"] }
  }
}
```
</details>

<details>
<summary><b>VS Code (GitHub Copilot)</b></summary>

`.vscode/mcp.json` (note the `servers` key):

```json
{
  "servers": {
    "studiowright": { "type": "stdio", "command": "sw", "args": ["mcp"] }
  }
}
```

For all workspaces, run **MCP: Open User Configuration** and add the same entry.
</details>

<details>
<summary><b>Codex CLI</b></summary>

```bash
codex mcp add studiowright -- sw mcp
```

or in `~/.codex/config.toml`:

```toml
[mcp_servers.studiowright]
command = "sw"
args = ["mcp"]
tool_timeout_sec = 900
```
</details>

<details>
<summary><b>Gemini CLI</b></summary>

```bash
gemini mcp add -s user studiowright sw mcp
```

or in `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "studiowright": { "command": "sw", "args": ["mcp"], "timeout": 900000 }
  }
}
```
</details>

### MCP tools

| Tool | What it does |
| --- | --- |
| `flow_generate_image` | Images with any model, aspect, count, references and download size |
| `flow_generate_video` | Clips with any model and mode, frames, references, size; `dry_run`, `max_credits` |
| `flow_upload` / `flow_download` | Upload references once; fetch existing media again at any size |
| `flow_list_media` | A project's media, newest first: id, kind, title, grid position |
| `flow_create_project` / `flow_use_project` | Choose where Flow keeps the work |
| `flow_options` / `flow_status` | Every model and option; recent jobs, pacing and cooldown |
| `flow_pace` | Show or save a profile's pacing (seconds between submits, cooldown) |
| `flow_doctor` | Check that Flow's page still has every part StudioWright uses (free) |
| `music_generate` | A song or instrumental, with lyrics, BPM, length, seed, model, title, cover art |
| `music_edit` | Extend, cover, replace, variation or trim an existing song |
| `music_stems` | Split a song into drums, bass, vocals and other |
| `music_video` | A vertical MP4 of a song from Flow Music's templates |
| `music_download` | Existing songs as WAV, M4A or MP3 |
| `music_library` | The newest songs, the credit balance and the plan (free) |
| `music_chat` | Talk to the Producer agent; saves any songs it makes |
| `music_upload` | Upload your own audio as a private song |

## Command reference

`sw --help` and `sw <command> --help` show every option. The most used ones:

### Images and video

```bash
sw image "PROMPT" [--model nano-banana-2.1|nano-banana-pro|nano-banana-2-lite]
                  [--aspect 16:9|4:3|1:1|3:4|9:16] [--count 1-4] [--ref FILE_OR_ID ...]
                  [--size 1K|2K|4K] [--project ID] [--out DIR] [--json]

sw video "PROMPT" [--model omni-flash|veo-lite|veo-fast|veo-quality] [--mode frames|ingredients]
                  [--start FILE_OR_ID] [--end FILE_OR_ID] [--ref FILE_OR_ID ...]
                  [--aspect 16:9|9:16] [--resolution 360p|720p] [--duration 4|6|8|10]
                  [--size 720p|1080p|4K|270p] [--max-credits N] [--dry-run] [--json]

sw upload FILE ...                 # upload references once, print their media ids
sw download MEDIA_ID ... [--size 2K|1080p]
sw media list [--project ID] [--limit N] [--json]   # what a project holds, newest first
sw project create "TITLE" | use ID | list
sw run BATCH.jsonl [--keep-going]
sw credits                         # Flow (as of the last clip) and Flow Music balances
sw pace [set --gap S --jitter S --cooldown S | --reset]   # show or save pacing
sw doctor [--project ID] [--music]                  # check Flow's UI hooks; spends nothing
sw jobs | sw window show|hide | sw stop | sw mcp
```

### Music

`SONG` is a song id, a unique id prefix, a flowmusic.app song URL, or `last`.

```bash
sw music "SOUND" [--lyrics TEXT | --lyrics-file FILE] [--bpm N] [--length 1:00-3:00] [--seed N]
                 [--model lyria-3.5|lyria-3-pro] [--title T] [--cover IMAGE | --cover-prompt TEXT]
                 [--format wav|m4a|mp3] [--out DIR] [--json]

sw music extend SONG [--from 0:45] [--until 2:30] [--prompt TEXT] [--lyrics-file FILE]
sw music cover SONG --prompt "slow jazz trio, brushed drums"
sw music replace SONG --start 0:20 --end 0:35 [--prompt TEXT] [--lyrics-file FILE]
sw music variation SONG
sw music trim SONG --start 0:05 --end 0:35
sw music stems SONG
sw music video SONG [--template simple|modern|player]
sw music download SONG ... [--format wav|m4a|mp3]
sw music chat "MESSAGE" [--session ID] [--audio FILE] [--image FILE] [--confirm-rights]
sw music upload FILE --confirm-rights
sw music list | sw music credits
```

`sw music "SOUND"` is short for `sw music create "SOUND"`. Flow Music asks you to confirm
that you have the rights to files you upload; `--confirm-rights` is that confirmation, and
StudioWright never gives it on its own.

## Batch files

A batch is JSON Lines (one job per line) or a JSON list. Jobs run in order, and a job can
use an earlier job's result by its `id`.

```json
{"id": "keeper", "kind": "image", "prompt": "a lighthouse keeper, flat 2D", "refs": ["style.png"]}
{"id": "clip", "kind": "video", "prompt": "he turns to the sea", "start": "keeper", "duration": 6, "max_credits": 12}
{"id": "bed", "kind": "music", "sound": "calm piano underscore", "length": "1:30"}
{"id": "bed-long", "kind": "music", "action": "extend", "song": "bed", "end": "2:30"}
{"id": "bed-stems", "kind": "music", "action": "stems", "song": "bed-long"}
```

```bash
sw run shots.jsonl
```

- `refs`, `start` and `end` take file paths (relative to the batch file), media ids or job
  ids. A music job's `song` takes a song id or a job id.
- Music `action`s: `create` (default), `extend`, `cover`, `replace`, `variation`, `trim`,
  `stems`, `video`. Extend, replace and trim take their times as `start` / `end`.
- `name` gives a job's media that display name in Flow's project grid (`V03`; several results
  become `V03-1`, `V03-2`); a video job's `start_name` names its start frame too (`I03`).
  `sw media rename MEDIA_ID TITLE ...` does it by hand.
- Results are appended to `shots.jsonl.results.jsonl`. Running the batch again skips jobs
  that already finished; `--keep-going` continues past a failure.

## Costs and safety

Prices as Flow and Flow Music showed them on 25 October 2026. They differ between accounts
(Veo Fast was 10 credits on one and 20 on another that day) and Flow changes them: a dry
run (`--dry-run`, `dry_run=true`) shows your current price for free.

| Google Flow | Credits |
| --- | --- |
| Image, any Nano Banana model | 0 (daily quota) |
| Omni Flash clip, 360p, 4 s | 4 |
| Omni Flash clip, 720p, 4 / 6 / 8 / 10 s | 7 / 10 / 12 / 15 |
| Veo 3.1 Lite / Fast / Quality, 8 s | 10 / 10 or 20 / 100 |

| Google Flow Music | Credits | Time |
| --- | --- | --- |
| New song, extend, cover, replace, variation | 5 | 30-50 s |
| Trim, stems, video template, chat, upload, downloads | 0 | 10-60 s |
| Song made by the Producer in chat | 5 | ~40 s |
| AI music video (through chat) | expensive; check in the app | minutes |

Built-in guards:

- `--dry-run` / `dry_run` sets up a clip and reports Flow's price without submitting.
  It uploads nothing: frames and references do not change the price.
- `--max-credits` / `max_credits` refuses a clip that costs more.
- Submits are spaced (20 s plus jitter) per profile, across processes. `sw pace set
  --gap 150` (or `flow_pace`) keeps a slower pace for a profile that Google has asked
  to slow down.
- When Google reports unusual activity, the profile cools down for 30 minutes and every
  call says so instead of retrying.
- One job at a time per profile (and one Flow Music job at a time), queued by a lock.
  Downloads run in a tab of their own beside generations, since they submit nothing.
- Upscaled downloads (2K/4K, 1080p) are spaced a minute apart. Flow refuses bulk upscaling
  with "unusual activity"; StudioWright then pauses upscales for an hour (generations go
  on) and saves new media at their original size meanwhile.

## How it works

```mermaid
flowchart LR
    A["You, Claude or any MCP client"] -->|"sw … / MCP tools"| B["StudioWright"]
    B -->|"CDP on 127.0.0.1"| C["Your Chrome<br/>dedicated profile, signed in by you"]
    C -->|"types, clicks, chooses files"| D["Google Flow"]
    C -->|"types, clicks, chooses files"| E["Google Flow Music"]
    D -. "replies are read, never changed" .-> B
    E -. "replies are read, never changed" .-> B
    B --> F[("Downloads/studiowright")]
```

- StudioWright starts your installed Chrome on its own profile and attaches over the
  DevTools protocol on loopback. Chrome keeps running between commands, so every call finds
  a warm, signed-in tab.
- Inputs are a person's actions: typing, clicking, menus and the file chooser.
- The pages' own replies are read to learn ids, progress and download links. Requests are
  never intercepted, replayed or rewritten, and protective tokens are never touched.
- Nothing is sent anywhere except to Google, by Google's own pages. Your password is typed
  by you, once, in Chrome.

Technical notes: [Flow](docs/notes/flow.md) · [Flow Music](docs/notes/flow-music.md).

## Troubleshooting

<details>
<summary><b>"not signed in", or a sign-in page appears</b></summary>

Run `sw stop`, then `sw auth login` (Flow) or `sw auth login --url https://www.flowmusic.app/`
(Flow Music), sign in, and close the window.
</details>

<details>
<summary><b>Calls start failing after working for weeks</b></summary>

Google changes Flow's page without notice. `sw doctor` opens the prompt box, the option
panes, the model menus, a tile's menu, the upload menu, the media list and an edit view,
reads them and closes them again, and says which part moved. It submits, uploads and
renames nothing, and keeps a screenshot of each failed check in the `incidents` folder.
</details>

<details>
<summary><b>A job is stuck or very slow</b></summary>

Make sure StudioWright's Chrome window is not minimized (`sw window hide` is the safe way
to hide it). Run with `sw -v …` to see each step. A failed job leaves a screenshot in the
`incidents` folder of the data directory, and `sw jobs` shows the error.
</details>

<details>
<summary><b>A clip finished in Flow but the command timed out</b></summary>

`sw download <media id>` fetches it without generating again. The timeout message prints
the ids, and the job's record keeps them as soon as Flow accepts the clip (`flow_status`
shows them), even if the job was interrupted.
</details>

<details>
<summary><b>An image is in Flow but the job said it failed</b></summary>

Flow's page cancels some replies once it has read them, and Chrome then keeps no copy, so
StudioWright reads every reply as it streams in. If an image reply is lost all the same,
the job looks the new image up in the project's media list, downloads it and says
"recovered from the project grid". `sw media list` shows what a project holds;
`sw download <media id>` fetches any of it.
</details>

<details>
<summary><b>"unusual activity" / cooldown</b></summary>

Google asked for a break. Wait for the cooldown (`sw pace` shows it), and spread large
batches out. Do not run several profiles of the same account in parallel.
</details>

<details>
<summary><b>A large project (hundreds of media)</b></summary>

Flow's grid only renders the tiles near the viewport. StudioWright finds older references
by searching the prompt box's picker (uploads keep their `sw-<hash>` name) or by scrolling
the grid from where the medium should be, and downloads open each medium's own edit view,
so position in the grid does not matter. Uploaded images download only at their own size;
Flow upscales generated media only.
</details>

<details>
<summary><b>2K / 1080p downloads are refused or paused</b></summary>

Flow limits how many upscales an account may request in a row and answers further ones
with "unusual activity". StudioWright stops at the first refusal, pauses upscales for an
hour (`sw pace` shows it) and keeps generating. Download the originals meanwhile, upscale
fewer media, or upscale locally.
</details>

<details>
<summary><b>A music video template fails</b></summary>

Flow Music sometimes cannot render a template for a song. Try again or pick another
template (`--template modern|simple|player`).
</details>

<details>
<summary><b>The MCP client cannot start the server</b></summary>

Give the client the full path of `sw`. The Claude Code plugin needs `uvx` (part of uv) on
the PATH. `sw mcp` run in a terminal should wait silently for input.
</details>

## Configuration

| Variable | Meaning | Default |
| --- | --- | --- |
| `STUDIOWRIGHT_PROFILE` | Chrome profile to use | `default` |
| `STUDIOWRIGHT_HOME` | data directory: profiles, job database, incidents | `%LOCALAPPDATA%\studiowright` on Windows |
| `STUDIOWRIGHT_OUTPUT` | where files are saved | `Downloads/studiowright` |
| `STUDIOWRIGHT_GAP_S`, `STUDIOWRIGHT_JITTER_S` | pacing between submits; overrides `sw pace set` | 20 s + 0-6 s |
| `STUDIOWRIGHT_COOLDOWN_S` | pause after an unusual-activity refusal | 1800 s |
| `STUDIOWRIGHT_UPSCALE_GAP_S`, `STUDIOWRIGHT_UPSCALE_JITTER_S` | spacing of upscaled downloads | 60 s + 0-20 s |
| `STUDIOWRIGHT_UPSCALE_COOLDOWN_S` | pause of upscales after a refusal | 3600 s |
| `STUDIOWRIGHT_IMAGE_TIMEOUT_S` | how long to wait for an image reply before looking in the project | 240 s |
| `STUDIOWRIGHT_SOURCE` | what the Claude Code plugin runs with `uvx --from` (e.g. a local checkout) | the plugin's own release |

### Several Google accounts

Each profile is one signed-in Google account with its own Chrome, queue and credits.
Profiles run in parallel; jobs on the same profile take turns.

```bash
sw --profile account-1 auth login     # name it as you like; sign in, close the window
sw --profile account-1 image "…"      # or set STUDIOWRIGHT_PROFILE=account-1
sw auth list                          # each profile and the account it is signed in to
sw auth rename account-1 studio       # keeps the sign-in, projects and settings
sw auth logout studio                 # removes the profile from this computer
```

`logout` deletes only the profile's Chrome data and StudioWright's records of it.
Downloaded files stay where they are, and the projects stay in your Google account.
Both `rename` and `logout` wait until that profile's Chrome is closed (`sw stop`).

## Development

```bash
uv sync
uv run pytest                        # unit tests, no browser needed
uv run ruff check src tests spikes
```

- `src/studiowright/flow/` drives Flow, `src/studiowright/music/` drives Flow Music, and
  `studio.py` adds locking, pacing, job records and downloads on top.
- `spikes/` holds the small scripts used to map each page. Most only open menus and read
  replies, at no cost; their docstrings say when one spends credits.
- Tested on Windows 11. The code also finds Chrome on macOS and Linux, but those platforms
  have not been tested yet.

## Disclaimer

- StudioWright is not affiliated with, endorsed or sponsored by Google LLC. Google, Flow,
  Flow Music, Veo, Nano Banana and Lyria are trademarks of Google LLC.
- The MIT license covers this code only. It grants no rights to Google's services or to
  what they generate. Your use of Flow and Flow Music, and of their output, is governed by
  Google's terms and your plan.
- Automated use of a web app may conflict with its terms. Use your own account, at your
  own risk and at a human pace. StudioWright stops when Google asks it to.
- The web apps can change at any time and break StudioWright until it is updated.

## License

[MIT](LICENSE) © 2026 Umut Koçak
