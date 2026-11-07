---
name: studiowright
description: Use when the user wants images or video clips made in Google Flow (Nano Banana images; Omni or Veo video), or songs, instrumentals, song edits (extend, cover, replace, variation, trim), stems, lyrics or music videos made in Google Flow Music, wants to reuse reference images or start/end frames, or wants existing Flow or Flow Music media downloaded. Explains the StudioWright MCP tools, their options, costs and pacing.
---

# StudioWright: Google Flow and Flow Music through the user's own Chrome

StudioWright drives Google Flow's and Google Flow Music's web UIs in the user's own signed-in Chrome. It spends the user's own subscriptions and credits. It is unofficial and not affiliated with Google.

## Before the first call

- Run `flow_status` once to see the default project, recent jobs and any cooldown. For music, `music_library` shows the credit balance and the newest songs (free).
- If there is no default Flow project, call `flow_create_project` with a short title, or ask the user for a project id from their Flow URL and call `flow_use_project`.
- If a tool says the profile is not signed in, tell the user to run `sw stop`, then `sw auth login` (Flow) or `sw auth login --url https://www.flowmusic.app/` (Flow Music) in a terminal and sign in by hand. Never ask for passwords.

## Images and video (Google Flow)

- Images: `flow_generate_image(prompt, model, aspect, count, refs)`.
  - The default is Nano Banana 2.1 at 16:9 and x1. Images cost no credits.
  - `refs` takes local file paths or media ids. Local files are uploaded once and reused by content afterwards.
- Video: `flow_generate_video(prompt, model, mode, start, end, refs, aspect, resolution, duration, count, size, max_credits, dry_run)`.
  - Video costs credits. Use `dry_run=true` first to learn Flow's price for free, and pass `max_credits` so a call never spends more than agreed.
  - Prices seen on 25 Oct 2026: Omni 360p 4 s = 4, Veo Lite = 10, Veo Fast = 10 or 20 (it
    differed between accounts), Veo Quality = 100 credits. Prices change; `dry_run=true` shows
    the account's current one for free and uploads nothing (frames and refs do not change it).
  - Confirm with the user before making several clips or using Veo Quality.
  - `mode="frames"`: optional `start` and `end` frames (file or media id). With neither, it is text-to-video.
  - `mode="ingredients"`: `refs` are reference images.
  - Models: `omni-flash` (Omni 1.1, the default), `omni-flash-1.2`, `veo-lite`, `veo-fast`, `veo-quality`. Flow retires Omni 1.1, Veo Fast and Veo Quality on 5 Dec 2026.
  - `resolution` (360p/720p) and `duration` apply to Omni only: Omni 1.1 takes 4/6/8/10 s, Omni 1.2 any whole second from 3 to 15.
  - Omni 1.2 at 720p costs 3 credits a second up to 10 s and 6 a second beyond (8 s = 24, twice Omni 1.1); at 360p 1 a second (2 beyond 10 s).
- `size` downloads Flow's own other sizes: images `2K` (upscaled); clips `1080p` from a 720p clip, `720p` from a 360p clip, `270p` (GIF).
- `flow_options` lists every model and option.

## Music (Google Flow Music)

Flow Music has its own credit balance. Songs are referred to by the `id` values results return, a unique id prefix, a flowmusic.app song URL, or `"last"` for the newest song.

- New song: `music_generate(sound, lyrics, bpm, length, seed, model, title, cover_image, cover_prompt, audio_format)`.
  - `sound` describes style, instruments and mood. Leave `lyrics` empty for an instrumental; any language works.
  - `length` is "1:00"-"3:00". `audio_format` is "wav" (48 kHz, default), "m4a" or "mp3".
- Change a song: `music_edit(song, mode, prompt, lyrics, start, end, ...)`.
  - `extend`: continue the song; `start` = where the extension begins, `end` = where the new song ends.
  - `cover`: the whole song in another style; `prompt` is required.
  - `replace`: regenerate `start`-`end`, described by `prompt` and/or new `lyrics` (the whole sheet).
  - `variation`: new takes with the same prompt and lyrics.
  - `trim`: cut to `start`-`end` (no generation, free).
- `music_stems(song)`: drums, bass, vocals and other as separate files (free).
- `music_video(song, template)`: a vertical MP4 over the cover art (`simple`, `modern`, `player`; free). If one template fails, try another.
- `music_download(songs, audio_format)`: existing songs again, any format.
- `music_chat(message, session, audio, images, confirm_rights)`: the Producer agent can write or edit lyrics, plan a song, remix uploaded audio, or make an AI music video. Continue a conversation with the `session` a previous reply returned. Chatting is free; songs it makes cost credits, and AI music videos can be expensive, so ask the user first.
- `music_upload(file, confirm_rights)`: the user's own audio becomes a private song that `music_edit` and `music_stems` accept.
- **Rights:** Flow Music asks the user to confirm they have the rights to uploaded audio and images. Set `confirm_rights=true` only after the user has said so. Never decide it yourself.
- **Costs:** about 5 credits per new song, extension, cover, replacement or variation, and about 30-50 s each. Ask before making many.

## Results

Each result returns `files` (saved local paths) and `job_id`; Flow results add `media_ids`, music results add `songs` (with ids).

- Reuse a `media_id` as a later `ref`, `start` or `end` instead of re-uploading the file, and a song `id` for later music calls.
- `flow_list_media(project, limit)` lists what a project holds, newest first (media id, kind, title); `flow_download(media_ids=[...])` fetches any of it, e.g. an image that finished after a failed call.
- A result note "recovered from the project grid" means Flow's reply was lost and the new image was found in the project instead; the image is fine.
- To show an image to the user, read the saved file.

## Timing, pacing, failures

- **Timing:** an image takes about 20-30 s, a clip about 30-90 s, a song about 40 s. Calls are queued and paced per profile. Don't fire many calls in parallel; run them one after another.
- **Unusual activity:** if Flow reports it, the profile cools down for about 30 min. Tell the user and stop; do not retry during the cooldown.
- **UI changes:** if calls that used to work start failing on a missing button or menu, run `flow_doctor()` (free): it checks every part of Flow's page StudioWright uses and names the one that moved.
- **Slower pace:** `flow_pace(gap_s=150)` saves a wider gap between submits for a profile (it lasts across calls and restarts); a profile that has had unusual-activity refusals is safer that way. `flow_pace()` alone shows the current pace.
- **Clip timeout:** if a clip times out, the error names its media id. Later, `flow_download(media_ids=[...])` fetches it without generating again.
- **Refusals:** a content refusal is final for that prompt. Rephrase it rather than resubmitting it unchanged.
- **Failures** leave a screenshot path in the error; mention it to the user.
