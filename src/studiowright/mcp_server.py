"""StudioWright as an MCP server (stdio): the same operations as the CLI, for AI agents."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from studiowright import paths
from studiowright.flow.options import (
    COUNTS,
    RESOLUTIONS,
    VIDEO_ASPECTS,
    Aspect,
    ImageModel,
    ImageOptions,
    VideoMode,
    VideoModel,
    VideoOptions,
)
from studiowright.music.options import (
    AudioFormat,
    ChatRequest,
    EditRequest,
    MusicModel,
    MusicRequest,
    VideoTemplate,
)
from studiowright.pacing import Governor, PaceSettings, save_pace
from studiowright.store import Store
from studiowright.studio import ImageRequest, Studio, VideoRequest

INSTRUCTIONS = """\
StudioWright drives Google Flow (images and video) and Google Flow Music (songs and
instrumentals) in the user's own signed-in Chrome, through their web UIs, using the user's
own subscriptions and credits.

- Images (Nano Banana) cost no credits; videos cost credits (the tool reports what Flow
  charges in Flow's own settings pane, not here). Ask before generating many videos.
- Each call takes real time: about 20-30 s for an image, 30-90 s for a clip.
- Calls are queued and paced per profile so Flow is not flooded. If Flow reports unusual
  activity the profile cools down (~30 min); do not retry during a cooldown.
- References and frames accept a local file path (uploaded once, then reused by content)
  or a Flow media id returned by an earlier call.
- Every result lists the saved file paths and the media ids, which later calls can reuse.
  flow_list_media lists what a project holds; flow_download fetches any of it by id.
- Flow Music (music_* tools) has its own credit balance: about 5 credits per new song,
  extension, cover, replacement or variation; about 40 s each. music_library is free and
  shows the balance. Songs are referred to by the "id" values results return, or "last".
- Uploading audio or images to Flow Music needs the user's own confirmation that they
  have the rights to the files (confirm_rights); never set it on your own.
"""

server = MCPServer("studiowright", instructions=INSTRUCTIONS)


def _profile(profile: str | None) -> str:
    return profile or os.environ.get("STUDIOWRIGHT_PROFILE") or paths.DEFAULT_PROFILE


def _out(out_dir: str | None) -> Path | None:
    return Path(out_dir).expanduser() if out_dir else None


@server.tool()
async def flow_options() -> dict[str, Any]:
    """List every model and option Flow offers, with the defaults."""
    return {
        "image": {
            "models": [m.value for m in ImageModel],
            "default_model": ImageModel.NANO_BANANA_2_1.value,
            "aspects": [a.value for a in Aspect],
            "counts": list(COUNTS),
        },
        "video": {
            "models": [m.value for m in VideoModel],
            "default_model": VideoModel.OMNI_FLASH.value,
            "modes": {
                "frames": "optional start/end frames; text-only when none",
                "ingredients": "up to several reference images",
            },
            "aspects": [a.value for a in VIDEO_ASPECTS],
            "resolutions (omni only)": list(RESOLUTIONS),
            "durations_s (omni only)": {
                m.value: list(m.durations) for m in VideoModel if m.durations
            },
            "counts": list(COUNTS),
        },
    }


@server.tool()
async def flow_generate_image(
    prompt: str,
    model: str = ImageModel.NANO_BANANA_2_1.value,
    aspect: str = "16:9",
    count: int = 1,
    refs: list[str] | None = None,
    project: str | None = None,
    out_dir: str | None = None,
    size: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Generate 1-4 images in Google Flow and save them locally.

    refs: reference images, each a local file path or a Flow media id.
    size: download size as Flow labels it ("1K" original, "2K" upscaled); default original.
    Returns file paths, media ids, image sizes and the job id.
    """
    request = ImageRequest(
        prompt,
        ImageOptions.build(model, aspect, count),
        tuple(refs or ()),
        project,
        _out(out_dir),
        size,
    )
    async with Studio(_profile(profile)) as studio:
        return await studio.image(request)


@server.tool()
async def flow_generate_video(
    prompt: str,
    model: str = VideoModel.OMNI_FLASH.value,
    mode: str = VideoMode.FRAMES.value,
    start: str | None = None,
    end: str | None = None,
    refs: list[str] | None = None,
    aspect: str = "16:9",
    resolution: str = "720p",
    duration: int = 8,
    count: int = 1,
    project: str | None = None,
    out_dir: str | None = None,
    size: str | None = None,
    max_credits: int | None = None,
    dry_run: bool = False,
    profile: str | None = None,
) -> dict[str, Any]:
    """Generate a video clip in Google Flow (costs credits) and save it locally.

    mode "frames": optional start and end frame (file path or media id); text-to-video
    when neither is given. mode "ingredients": refs are reference images.
    resolution and duration apply to Omni only: omni-flash takes 4/6/8/10 s, omni-flash-1.2
    any whole number of seconds from 3 to 15. size: download size as Flow labels it
    (e.g. "1080p" upscaled from a 720p clip, "270p" GIF); default original.
    max_credits: refuse before submitting if Flow's stated price is higher.
    dry_run: return Flow's price without submitting (free); frames and refs are not
    uploaded for it, since they do not change the price.
    Returns file paths, media ids and the credits Flow stated.
    """
    if end and not start:
        raise ValueError("an end frame needs a start frame")
    if mode == VideoMode.FRAMES.value and refs:
        raise ValueError("refs need mode 'ingredients'")
    if mode == VideoMode.INGREDIENTS.value and (start or end):
        raise ValueError("start/end frames need mode 'frames'")
    options = VideoOptions.build(model, mode, aspect, resolution, duration, count)
    request = VideoRequest(
        prompt,
        options,
        start,
        end,
        tuple(refs or ()),
        project,
        _out(out_dir),
        size,
        max_credits,
        dry_run,
    )
    async with Studio(_profile(profile)) as studio:
        return await studio.video(request)


@server.tool()
async def music_generate(
    sound: str,
    lyrics: str | None = None,
    bpm: int | None = None,
    length: str | None = None,
    seed: int | None = None,
    model: str = MusicModel.LYRIA_3_5.value,
    title: str | None = None,
    cover_image: str | None = None,
    cover_prompt: str | None = None,
    audio_format: str = "wav",
    out_dir: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Make a song or instrumental in Google Flow Music (flowmusic.app) and save the audio.

    sound: style, instruments, mood (e.g. "calm documentary underscore, soft piano").
    lyrics: song lyrics (any language); leave empty for an instrumental.
    length: "1:00"-"3:00" or seconds. model: "lyria-3.5" (default) or "lyria-3-pro".
    cover_image (a local file) or cover_prompt sets the song's cover art.
    audio_format: "wav" (48 kHz), "m4a" or "mp3". About 5 Flow Music credits per song.
    Returns the saved files and each song's id (for music_edit, music_stems, ...).
    """
    request = MusicRequest.build(
        sound,
        lyrics=lyrics,
        bpm=bpm,
        length=length,
        seed=seed,
        model=model,
        title=title,
        audio_format=audio_format,
        out_dir=_out(out_dir),
        cover_image=cover_image,
        cover_prompt=cover_prompt,
    )
    async with Studio(_profile(profile)) as studio:
        return await studio.music(request)


@server.tool()
async def music_edit(
    song: str,
    mode: str,
    prompt: str | None = None,
    lyrics: str | None = None,
    start: str | None = None,
    end: str | None = None,
    seed: int | None = None,
    title: str | None = None,
    cover_image: str | None = None,
    cover_prompt: str | None = None,
    audio_format: str = "wav",
    out_dir: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Make a new song from an existing one in Google Flow Music.

    song: a song id from an earlier call, a unique id prefix, a flowmusic.app song URL, or
    "last" for the newest song. Uploaded audio (music_upload) works too.
    mode:
    - "extend": continue the song; start = where the extension begins, end = where the
      new song ends (both optional); prompt/lyrics describe the extension.
    - "cover": the whole song in another style; prompt is required (e.g. "slow jazz").
    - "replace": regenerate the part between start and end; prompt and/or lyrics.
    - "variation": new takes with the same prompt and lyrics (other fields ignored).
    - "trim": cut the song to start-end (no generation).
    Times look like "0:45" or "45". Lyrics replace the song's whole lyrics sheet.
    Extend, cover, replace and variation cost about as much as a new song.
    """
    fmt = AudioFormat(audio_format)
    async with Studio(_profile(profile)) as studio:
        if mode == "variation":
            return await studio.music_variation(song, fmt, _out(out_dir))
        if mode == "trim":
            return await studio.music_trim(song, start or "", end or "", fmt, _out(out_dir))
        request = EditRequest.build(
            song,
            mode,
            prompt=prompt,
            lyrics=lyrics,
            start=start,
            end=end,
            seed=seed,
            title=title,
            audio_format=fmt,
            out_dir=_out(out_dir),
            cover_image=cover_image,
            cover_prompt=cover_prompt,
        )
        return await studio.music_edit(request)


@server.tool()
async def music_stems(
    song: str, audio_format: str = "wav", out_dir: str | None = None, profile: str | None = None
) -> dict[str, Any]:
    """Split a Flow Music song into drums, bass, vocals and other, and save each stem."""
    async with Studio(_profile(profile)) as studio:
        return await studio.music_stems(song, AudioFormat(audio_format), _out(out_dir))


@server.tool()
async def music_video(
    song: str, template: str = "simple", out_dir: str | None = None, profile: str | None = None
) -> dict[str, Any]:
    """Save a Flow Music song as a vertical 720x1280 MP4 that plays it over its cover art.

    template: "simple", "modern" or "player". (AI-made music videos are a Producer
    conversation: use music_chat.)
    """
    async with Studio(_profile(profile)) as studio:
        return await studio.music_video(song, VideoTemplate(template), _out(out_dir))


@server.tool()
async def music_download(
    songs: list[str],
    audio_format: str = "wav",
    out_dir: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Save existing Flow Music songs (ids, id prefixes or "last") as wav, m4a or mp3."""
    async with Studio(_profile(profile)) as studio:
        return await studio.music_download(songs, AudioFormat(audio_format), _out(out_dir))


@server.tool()
async def music_library(limit: int = 20, profile: str | None = None) -> dict[str, Any]:
    """The newest songs in the Flow Music account (ids, titles, lengths, kinds) and the
    credit balance and plan. Free."""
    async with Studio(_profile(profile)) as studio:
        return await studio.music_library(limit)


@server.tool()
async def music_chat(
    message: str,
    session: str | None = None,
    audio: str | None = None,
    images: list[str] | None = None,
    confirm_rights: bool = False,
    audio_format: str = "wav",
    out_dir: str | None = None,
    timeout_s: int = 900,
    profile: str | None = None,
) -> dict[str, Any]:
    """Talk to Flow Music's Producer agent, which can do everything the app can: write or
    edit lyrics, plan a song, remix your music, make an AI music video, and more.

    session: continue an earlier conversation (the "session" value a previous call
    returned); omit to start a new one. audio/images: local files to attach; Flow Music
    asks the user to confirm they have the rights to them, so set confirm_rights only
    when the user has said so. Songs the Producer makes during the turn are saved.
    Returns the reply text, any lyrics it wrote, suggested next steps and saved files.
    Chatting is free; songs and videos it makes cost credits (videos can be expensive).
    """
    request = ChatRequest.build(
        message,
        session=session,
        audio=audio,
        images=tuple(images or ()),
        confirm_rights=confirm_rights,
        audio_format=audio_format,
        out_dir=_out(out_dir),
        timeout_s=timeout_s,
    )
    async with Studio(_profile(profile)) as studio:
        return await studio.music_chat(request)


@server.tool()
async def music_upload(
    file: str, confirm_rights: bool = False, profile: str | None = None
) -> dict[str, Any]:
    """Upload your own audio (max 40 MB) to Flow Music as a private song; returns its id
    for music_edit / music_stems. Flow Music asks the user to confirm they have the rights
    to the file: set confirm_rights only when the user has said so."""
    async with Studio(_profile(profile)) as studio:
        return await studio.music_upload(file, confirm_rights=confirm_rights)


@server.tool()
async def flow_upload(
    files: list[str], project: str | None = None, profile: str | None = None
) -> dict[str, Any]:
    """Upload local images to a Flow project once; returns their media ids."""
    async with Studio(_profile(profile)) as studio:
        return await studio.upload(files, project)


@server.tool()
async def flow_download(
    media_ids: list[str],
    project: str | None = None,
    out_dir: str | None = None,
    size: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Download existing images or clips by media id (or unique id prefix), e.g. a clip
    that finished after a timeout, optionally at another size ("2K", "1080p"). Generates
    nothing new."""
    async with Studio(_profile(profile)) as studio:
        return await studio.download(media_ids, project, _out(out_dir), size)


@server.tool()
async def flow_list_media(
    project: str | None = None, limit: int = 50, profile: str | None = None
) -> dict[str, Any]:
    """List a Flow project's media, newest first: media id, kind (image, video or upload),
    title and grid position. flow_download fetches any of them by id. Generates nothing."""
    async with Studio(_profile(profile)) as studio:
        return await studio.media(project, limit)


@server.tool()
async def flow_doctor(
    project: str | None = None, music: bool = False, profile: str | None = None
) -> dict[str, Any]:
    """Check, spending nothing, that Flow's page still has every button, menu and reply
    StudioWright uses (Flow changes its UI without notice). Run it when calls start failing
    in a way that looks like a UI change; "ok" is false when a check failed."""
    async with Studio(_profile(profile)) as studio:
        return await studio.doctor(project, music)


@server.tool()
async def flow_pace(
    gap_s: float | None = None,
    jitter_s: float | None = None,
    cooldown_s: float | None = None,
    reset: bool = False,
    profile: str | None = None,
) -> dict[str, Any]:
    """Show or save a profile's submit pacing; saved values persist for every later call.

    gap_s: seconds between two submits (default 20); jitter_s: random extra seconds;
    cooldown_s: pause after an unusual-activity refusal. A profile that gets unusual-activity
    refusals is safer with gap_s around 150. Environment variables still override.
    """
    name = _profile(profile)
    store = Store()
    if reset:
        store.set_setting(f"pace.{name}", "{}")
    if reset or any(v is not None for v in (gap_s, jitter_s, cooldown_s)):
        save_pace(store, name, {"gap_s": gap_s, "jitter_s": jitter_s, "cooldown_s": cooldown_s})
    settings = PaceSettings.load(store, name)
    return {
        "profile": name,
        "gap_s": settings.gap_s,
        "jitter_s": settings.jitter_s,
        "cooldown_s": settings.cooldown_s,
        "state": Governor(store, name, settings).status(),
    }


@server.tool()
async def flow_create_project(
    title: str | None = None, make_default: bool = True, profile: str | None = None
) -> dict[str, Any]:
    """Create a Flow project; by default later calls use it."""
    async with Studio(_profile(profile)) as studio:
        project_id = await studio.create_project(title)
        if make_default:
            studio.use_project(project_id)
    return {"project_id": project_id, "default": make_default}


@server.tool()
async def flow_use_project(project_id: str, profile: str | None = None) -> dict[str, Any]:
    """Make an existing Flow project (id from its URL) the default."""
    Studio(_profile(profile)).use_project(project_id)
    return {"default_project": project_id}


@server.tool()
async def flow_status(profile: str | None = None, limit: int = 10) -> dict[str, Any]:
    """Default project, known projects, recent jobs and pacing/cooldown state."""
    name = _profile(profile)
    store = Store()
    jobs = [
        {
            "id": row["id"],
            "kind": row["kind"],
            "status": row["status"],
            "error": row["error"],
            "result": row["result"],
        }
        for row in store.recent_jobs(name, limit)
    ]
    return {
        "profile": name,
        "default_project": store.get_setting(f"project.default.{name}"),
        "projects": [{"id": r["id"], "title": r["title"]} for r in store.projects(name)],
        "jobs": jobs,
        "pacing": Governor(store, name).status(),
        # Flow reports its balance only when a video is submitted; this is the latest.
        "flow_credits_last_seen": json.loads(store.get_setting(f"flow.credits.{name}") or "null"),
    }


def run() -> None:
    server.run("stdio")
