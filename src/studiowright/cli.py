"""The `studiowright` / `sw` command line."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import click
from playwright.async_api import Error as PlaywrightError

from studiowright import __version__, chrome, paths
from studiowright.flow.options import (
    COUNTS,
    RESOLUTIONS,
    Aspect,
    ImageModel,
    ImageOptions,
    OptionError,
    VideoMode,
    VideoModel,
    VideoOptions,
)
from studiowright.flow.page import FlowError
from studiowright.music.options import (
    AudioFormat,
    ChatRequest,
    EditRequest,
    MusicModel,
    MusicRequest,
    VideoTemplate,
)
from studiowright.pacing import CoolingDown
from studiowright.store import Store
from studiowright.studio import ImageRequest, Studio, VideoRequest

IMAGE_MODELS = [m.value for m in ImageModel]
VIDEO_MODELS = [m.value for m in VideoModel]
ASPECTS = [a.value for a in Aspect]


def _run(make: Callable[[Studio], Awaitable[Any]], profile: str) -> Any:
    async def main() -> Any:
        async with Studio(profile) as studio:
            return await make(studio)

    try:
        return asyncio.run(main())
    except (FlowError, CoolingDown, OptionError, FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    except chrome.ChromeError as exc:
        raise click.ClickException(str(exc)) from exc
    except PlaywrightError as exc:
        first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        raise click.ClickException(f"browser step failed: {first_line}") from exc


def _emit(result: dict[str, Any], as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=1))
        return
    for path in result.get("files", []):
        click.echo(path)
    for note in result.get("notes", []):
        click.echo(f"note: {note}", err=True)
    seconds = result.get("seconds")
    if seconds is not None:
        click.echo(f"done in {seconds} s (job {result.get('job_id')})", err=True)


@click.group()
@click.version_option(__version__, "-V", "--version", prog_name="StudioWright")
@click.option(
    "--profile",
    envvar="STUDIOWRIGHT_PROFILE",
    default=paths.DEFAULT_PROFILE,
    show_default=True,
    help="Signed-in Chrome profile to use.",
)
@click.option("-v", "--verbose", is_flag=True, help="Log each step to stderr.")
@click.pass_context
def main(ctx: click.Context, profile: str, verbose: bool) -> None:
    """StudioWright: Google Flow from the terminal. Unofficial; not affiliated with Google."""
    # Paths are printed as UTF-8 even when Windows redirects the output to a file.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    if verbose:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S"
        )
    ctx.obj = {"profile": profile}


# --- auth ---------------------------------------------------------------------------------
@main.group()
def auth() -> None:
    """Sign in and manage profiles."""


@auth.command("login")
@click.option("--url", default=chrome.FLOW_URL, show_default=True, help="Page to open.")
@click.pass_obj
def auth_login(obj: dict[str, str], url: str) -> None:
    """Open Chrome on the profile; sign in by hand, then close the window."""
    profile = obj["profile"]
    if chrome.endpoint_version(chrome.debug_port(profile)):
        raise click.ClickException(
            "StudioWright's Chrome for this profile is running; close its window first."
        )
    exe = chrome.find_chrome()
    if exe is None:
        raise click.ClickException("Google Chrome was not found; install it first.")
    target = paths.profile_dir(profile)
    click.echo(f"Opening Chrome on profile '{profile}' ({target}).")
    click.echo("Sign in to your Google account, open Flow once, then close the window.")
    chrome.run_sign_in(exe, target, url)
    click.echo("Chrome closed. The profile is saved.")


@auth.command("list")
def auth_list() -> None:
    """List profiles."""
    root = paths.home() / "profiles"
    names = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    if not names:
        click.echo("No profiles yet. Run `sw auth login`.")
    width = max((len(name) for name in names), default=0)
    for name in names:
        click.echo(f"{name:<{width}}  {_signed_in_as(name) or '(not signed in)'}")


def _signed_in_as(profile: str) -> str | None:
    """The Google account a profile is signed in to, from Chrome's own profile file."""
    prefs = paths.profile_dir(profile) / "Default" / "Preferences"
    try:
        accounts = json.loads(prefs.read_text(encoding="utf-8")).get("account_info") or []
    except (OSError, ValueError):
        return None
    emails = [a["email"] for a in accounts if a.get("email")]
    return ", ".join(emails) or None


def _profile_dir(name: str) -> Path:
    try:
        return paths.profile_dir(name)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None


def _closed_profile(name: str) -> Path:
    """An existing profile's folder, refused while its Chrome is running."""
    folder = _profile_dir(name)
    if not folder.is_dir():
        raise click.ClickException(f"no profile named {name!r}; see `sw auth list`")
    if chrome.endpoint_version(chrome.debug_port(name)):
        raise click.ClickException(f"Chrome for profile {name!r} is running; close it first.")
    return folder


@auth.command("logout")
@click.argument("name")
@click.confirmation_option(prompt="Sign out and delete this profile from this computer?")
def auth_logout(name: str) -> None:
    """Delete profile NAME: its Chrome sign-in and StudioWright's records of it.

    Downloaded files and the media in Flow are not touched; signing in again with
    `sw --profile NAME auth login` brings the account back.
    """
    folder = _closed_profile(name)
    shutil.rmtree(folder)
    Store().forget_profile(name)
    click.echo(f"Profile {name!r} removed.")


@auth.command("rename")
@click.argument("old")
@click.argument("new")
def auth_rename(old: str, new: str) -> None:
    """Rename profile OLD to NEW, keeping its sign-in, projects and settings."""
    folder = _closed_profile(old)
    target = _profile_dir(new)
    if target.exists():
        raise click.ClickException(f"a profile named {new!r} already exists")
    folder.rename(target)
    Store().rename_profile(old, new)
    click.echo(f"Profile {old!r} is now {new!r}.")


# --- window -------------------------------------------------------------------------------
@main.group()
def window() -> None:
    """Show or hide StudioWright's Chrome window (it keeps working either way)."""


@window.command("show")
@click.pass_obj
def window_show(obj: dict[str, str]) -> None:
    """Bring the window on screen."""
    _run(lambda studio: studio.window(visible=True), obj["profile"])


@window.command("hide")
@click.pass_obj
def window_hide(obj: dict[str, str]) -> None:
    """Move the window off-screen; do not minimize it, which would pause the page."""
    _run(lambda studio: studio.window(visible=False), obj["profile"])


@main.command()
@click.pass_obj
def stop(obj: dict[str, str]) -> None:
    """Close StudioWright's Chrome for this profile (e.g. before `sw auth login`)."""
    port = chrome.debug_port(obj["profile"])
    if not chrome.endpoint_version(port):
        click.echo("StudioWright's Chrome is not running.")
        return

    async def close() -> None:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            cdp = await browser.new_browser_cdp_session()
            await cdp.send("Browser.close")

    with contextlib.suppress(PlaywrightError):  # the connection drops as Chrome closes
        asyncio.run(close())
    click.echo("StudioWright's Chrome is closed.")


# --- projects -----------------------------------------------------------------------------
@main.group()
def project() -> None:
    """Flow projects."""


@project.command("create")
@click.argument("title", required=False)
@click.option(
    "--use/--no-use", default=True, show_default=True, help="Make it the default project."
)
@click.pass_obj
def project_create(obj: dict[str, str], title: str | None, use: bool) -> None:
    """Create a Flow project."""

    async def make(studio: Studio) -> str:
        project_id = await studio.create_project(title)
        if use:
            studio.use_project(project_id)
        return project_id

    click.echo(_run(make, obj["profile"]))


@project.command("use")
@click.argument("project_id")
@click.pass_obj
def project_use(obj: dict[str, str], project_id: str) -> None:
    """Make PROJECT_ID (from its URL) the default project."""
    store = Store()
    studio = Studio(obj["profile"], store)
    try:
        studio.use_project(project_id)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"default project: {project_id}")


@project.command("list")
@click.pass_obj
def project_list(obj: dict[str, str]) -> None:
    """Projects StudioWright knows for this profile."""
    store = Store()
    default = store.get_setting(f"project.default.{obj['profile']}")
    for row in store.projects(obj["profile"]):
        mark = "*" if row["id"] == default else " "
        click.echo(f"{mark} {row['id']}  {row['title'] or ''}")


# --- media --------------------------------------------------------------------------------
@main.command()
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--project", help="Project id; defaults to the default project.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
@click.pass_obj
def upload(obj: dict[str, str], files: tuple[str, ...], project: str | None, as_json: bool) -> None:
    """Upload images to a project once; later jobs reuse them by content."""
    result = _run(lambda studio: studio.upload(list(files), project), obj["profile"])
    if as_json:
        click.echo(json.dumps(result, indent=1))
    else:
        for path, media_id in zip(files, result["media_ids"], strict=False):
            click.echo(f"{media_id}  {path}")


@main.command()
@click.argument("prompt")
@click.option(
    "--model",
    type=click.Choice(IMAGE_MODELS),
    default=ImageModel.NANO_BANANA_2_1.value,
    show_default=True,
)
@click.option("--aspect", type=click.Choice(ASPECTS), default="16:9", show_default=True)
@click.option(
    "--count", type=click.Choice([str(c) for c in COUNTS]), default="1", show_default=True
)
@click.option(
    "--ref",
    "refs",
    multiple=True,
    help="Reference image: a file path or a Flow media id. Repeatable.",
)
@click.option("--project", help="Project id; defaults to the default project.")
@click.option("--out", type=click.Path(file_okay=False, path_type=Path), help="Output folder.")
@click.option(
    "--size",
    help="Download size as Flow labels it, e.g. 2K (image) or 1080p (video); default original.",
)
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
@click.pass_obj
def image(
    obj: dict[str, str],
    prompt: str,
    model: str,
    aspect: str,
    count: str,
    refs: tuple[str, ...],
    project: str | None,
    out: Path | None,
    size: str | None,
    as_json: bool,
) -> None:
    """Generate images from PROMPT."""
    try:
        options = ImageOptions.build(model, aspect, int(count))
    except OptionError as exc:
        raise click.BadParameter(str(exc)) from exc
    request = ImageRequest(prompt, options, refs, project, out, size)
    _emit(_run(lambda studio: studio.image(request), obj["profile"]), as_json)


@main.command()
@click.argument("prompt")
@click.option(
    "--model",
    type=click.Choice(VIDEO_MODELS),
    default=VideoModel.OMNI_FLASH.value,
    show_default=True,
)
@click.option(
    "--mode",
    type=click.Choice([m.value for m in VideoMode]),
    default="frames",
    show_default=True,
    help="frames: start/end frames or text only; ingredients: reference images.",
)
@click.option("--start", help="Start frame: a file path or a Flow media id.")
@click.option("--end", help="End frame: a file path or a Flow media id.")
@click.option(
    "--ref", "refs", multiple=True, help="Ingredient image (mode ingredients). Repeatable."
)
@click.option("--aspect", type=click.Choice(["16:9", "9:16"]), default="16:9", show_default=True)
@click.option(
    "--resolution",
    type=click.Choice(list(RESOLUTIONS)),
    default="720p",
    show_default=True,
    help="Omni only.",
)
@click.option(
    "--duration",
    type=int,
    default=8,
    show_default=True,
    help="Seconds; Omni only: 4/6/8/10 on omni-flash, 3-15 on omni-flash-1.2.",
)
@click.option(
    "--count", type=click.Choice([str(c) for c in COUNTS]), default="1", show_default=True
)
@click.option("--project", help="Project id; defaults to the default project.")
@click.option("--out", type=click.Path(file_okay=False, path_type=Path), help="Output folder.")
@click.option(
    "--size",
    help="Download size as Flow labels it, e.g. 2K (image) or 1080p (video); default original.",
)
@click.option("--max-credits", type=int, help="Do not submit if Flow's stated price is higher.")
@click.option("--dry-run", is_flag=True, help="Show Flow's credit price; submit nothing.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
@click.pass_obj
def video(
    obj: dict[str, str],
    prompt: str,
    model: str,
    mode: str,
    start: str | None,
    end: str | None,
    refs: tuple[str, ...],
    aspect: str,
    resolution: str,
    duration: int,
    count: str,
    project: str | None,
    out: Path | None,
    size: str | None,
    max_credits: int | None,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Generate a video clip from PROMPT."""
    if end and not start:
        raise click.BadParameter("--end needs --start", param_hint="--end")
    if mode == "frames" and refs:
        raise click.BadParameter("--ref needs --mode ingredients", param_hint="--ref")
    if mode == "ingredients" and (start or end):
        raise click.BadParameter("--start/--end need --mode frames", param_hint="--mode")
    try:
        options = VideoOptions.build(model, mode, aspect, resolution, duration, int(count))
    except OptionError as exc:
        raise click.BadParameter(str(exc)) from exc
    request = VideoRequest(
        prompt, options, start, end, refs, project, out, size, max_credits, dry_run
    )
    result = _run(lambda studio: studio.video(request), obj["profile"])
    if dry_run and not as_json:
        click.echo(f"Flow's price: {result.get('credits')} credits (nothing was submitted)")
        return
    _emit(result, as_json)


@main.command()
@click.argument("media_ids", nargs=-1, required=True)
@click.option("--project", help="Project id; defaults to the default project.")
@click.option("--out", type=click.Path(file_okay=False, path_type=Path), help="Output folder.")
@click.option(
    "--size",
    help="Download size as Flow labels it, e.g. 2K (image) or 1080p (video); default original.",
)
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
@click.pass_obj
def download(
    obj: dict[str, str],
    media_ids: tuple[str, ...],
    project: str | None,
    out: Path | None,
    size: str | None,
    as_json: bool,
) -> None:
    """Download existing images or clips by media id, without generating anything."""
    result = _run(
        lambda studio: studio.download(list(media_ids), project, out, size), obj["profile"]
    )
    _emit(result, as_json)
    if result.get("errors") and not as_json:
        for media_id, error in result["errors"].items():
            click.echo(f"failed: {media_id}: {error}", err=True)
        raise SystemExit(1)


@main.command()
@click.argument("batch_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--keep-going", is_flag=True, help="Continue after a failed job.")
@click.pass_obj
def run(obj: dict[str, str], batch_file: Path, keep_going: bool) -> None:
    """Run a batch of image/video jobs (JSON Lines or a JSON list), resumably.

    Results are appended to BATCH_FILE.results.jsonl; running again skips finished jobs.
    A job's start/end/refs may name an earlier job's id to reuse its result.
    """
    from studiowright.batch import BatchError, load_jobs, run_batch

    try:
        load_jobs(batch_file)
    except (BatchError, ValueError) as exc:
        raise click.ClickException(f"invalid batch: {exc}") from exc

    def report(record: dict[str, Any]) -> None:
        files = (record.get("result") or {}).get("files", [])
        detail = ", ".join(files) if files else record.get("error", "")
        click.echo(f"{record['id']}: {record['status']} ({record['seconds']} s) {detail}")

    async def make(studio: Studio) -> Any:
        return await run_batch(
            batch_file.resolve(),
            studio.image,
            studio.video,
            run_music=studio.music_batch,
            rename=studio.rename,
            keep_going=keep_going,
            on_event=report,
        )

    summary = _run(make, obj["profile"])
    counts = f"{summary.done} done, {summary.failed} failed, {summary.skipped} skipped"
    click.echo(f"{counts} of {summary.total}", err=True)


# --- Flow Music -----------------------------------------------------------------------------
class _DefaultGroup(click.Group):
    """A group that runs ``default`` when the first argument is not one of its commands,
    so ``sw music "calm piano"`` stays short for the common case."""

    def __init__(self, *args: Any, default: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.default = default

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if args and args[0] not in self.commands and args[0] not in ("--help", "-h"):
            args = [self.default, *args]
        return super().parse_args(ctx, args)


def _formats() -> click.Choice:
    return click.Choice([f.value for f in AudioFormat])


def _song_options(command: Callable[..., Any]) -> Callable[..., Any]:
    """Output options shared by every music command that saves audio."""
    command = click.option("--json", "as_json", is_flag=True, help="Print JSON.")(command)
    command = click.option(
        "--out", type=click.Path(file_okay=False, path_type=Path), help="Output folder."
    )(command)
    return click.option(
        "--format", "audio_format", type=_formats(), default="wav", show_default=True
    )(command)


def _lyrics_options(command: Callable[..., Any]) -> Callable[..., Any]:
    command = click.option(
        "--lyrics-file",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="Read lyrics from a file.",
    )(command)
    return click.option("--lyrics", help="Lyrics text.")(command)


def _cover_options(command: Callable[..., Any]) -> Callable[..., Any]:
    command = click.option("--cover-prompt", help="Have Flow Music draw cover art from this.")(
        command
    )
    return click.option(
        "--cover",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="Cover art image for the new song.",
    )(command)


def _read_lyrics(lyrics: str | None, lyrics_file: Path | None) -> str | None:
    return lyrics_file.read_text(encoding="utf-8") if lyrics_file else lyrics


def _emit_music(result: dict[str, Any], as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=1))
        return
    if result.get("reply"):
        click.echo(result["reply"])
    if result.get("lyrics") and result["lyrics"] not in (result.get("reply") or ""):
        click.echo("\n" + result["lyrics"])
    for suggestion in result.get("suggestions") or []:
        click.echo(f"  > {suggestion}", err=True)
    for song in result.get("songs") or []:
        click.echo(f"{song['file']}  ({song['id']}, {song.get('seconds')} s)")
    if not result.get("songs"):
        for path in result.get("files", []):
            click.echo(path)
    if result.get("song"):
        click.echo(f"{result['song']}  {result.get('url', '')}")
    if result.get("session"):
        click.echo(f"session: {result['session']}", err=True)
    if result.get("seconds") is not None:
        click.echo(f"done in {result['seconds']} s (job {result.get('job_id')})", err=True)


def _option_error(make: Callable[[], Any]) -> Any:
    try:
        return make()
    except OptionError as exc:
        raise click.BadParameter(str(exc)) from exc


@main.group(cls=_DefaultGroup, default="create")
def music() -> None:
    """Google Flow Music: songs, edits, stems, videos and the Producer chat.

    `sw music "SOUND"` is short for `sw music create "SOUND"`. SONG arguments take a song
    id, a unique id prefix, a flowmusic.app song URL, or `last` for the newest song.
    """


@music.command("create")
@click.argument("sound")
@_lyrics_options
@click.option("--bpm", type=int, help="Tempo; default: Flow decides.")
@click.option("--length", help="Length, 1:00-3:00 (e.g. 1:30 or 90); default: Flow decides.")
@click.option("--seed", type=int, help="Seed for repeatable results.")
@click.option(
    "--model",
    type=click.Choice([m.value for m in MusicModel]),
    default=MusicModel.LYRIA_3_5.value,
    show_default=True,
)
@click.option("--title", help="Song title.")
@_cover_options
@_song_options
@click.pass_obj
def music_create(
    obj: dict[str, str],
    sound: str,
    lyrics: str | None,
    lyrics_file: Path | None,
    bpm: int | None,
    length: str | None,
    seed: int | None,
    model: str,
    title: str | None,
    cover: Path | None,
    cover_prompt: str | None,
    audio_format: str,
    out: Path | None,
    as_json: bool,
) -> None:
    """Make a song (with lyrics) or an instrumental (without) from a SOUND description."""
    request = _option_error(
        lambda: MusicRequest.build(
            sound,
            lyrics=_read_lyrics(lyrics, lyrics_file),
            bpm=bpm,
            length=length,
            seed=seed,
            model=model,
            title=title,
            audio_format=audio_format,
            out_dir=out,
            cover_image=cover,
            cover_prompt=cover_prompt,
        )
    )
    _emit_music(_run(lambda studio: studio.music(request), obj["profile"]), as_json)


def _edit(
    obj: dict[str, str],
    mode: str,
    song: str,
    *,
    prompt: str | None,
    lyrics: str | None,
    start: str | None,
    end: str | None,
    seed: int | None,
    title: str | None,
    cover: Path | None,
    cover_prompt: str | None,
    audio_format: str,
    out: Path | None,
    as_json: bool,
) -> None:
    request = _option_error(
        lambda: EditRequest.build(
            song,
            mode,
            prompt=prompt,
            lyrics=lyrics,
            start=start,
            end=end,
            seed=seed,
            title=title,
            audio_format=audio_format,
            out_dir=out,
            cover_image=cover,
            cover_prompt=cover_prompt,
        )
    )
    _emit_music(_run(lambda studio: studio.music_edit(request), obj["profile"]), as_json)


@music.command("extend")
@click.argument("song")
@click.option("--prompt", help="The sound and lyrics of the extension.")
@_lyrics_options
@click.option("--from", "start", help="Where the extension starts (e.g. 0:45); default: Flow's.")
@click.option("--until", "end", help="Where the extended song ends (e.g. 2:30).")
@click.option("--seed", type=int)
@click.option("--title", help="Title of the new song.")
@_cover_options
@_song_options
@click.pass_obj
def music_extend(obj: dict[str, str], song: str, lyrics_file: Path | None, **kw: Any) -> None:
    """Continue SONG past its end (or from --from) as a new song."""
    kw["lyrics"] = _read_lyrics(kw["lyrics"], lyrics_file)
    _edit(obj, "extend", song, **kw)


@music.command("cover")
@click.argument("song")
@click.option("--prompt", required=True, help="The new style, e.g. 'slow jazz with brushes'.")
@_lyrics_options
@click.option("--seed", type=int)
@click.option("--title", help="Title of the new song.")
@_cover_options
@_song_options
@click.pass_obj
def music_cover(obj: dict[str, str], song: str, lyrics_file: Path | None, **kw: Any) -> None:
    """Make a cover of SONG in another style (optionally with new lyrics)."""
    kw["lyrics"] = _read_lyrics(kw["lyrics"], lyrics_file)
    _edit(obj, "cover", song, start=None, end=None, **kw)


@music.command("replace")
@click.argument("song")
@click.option("--start", required=True, help="Start of the part to replace (e.g. 0:15).")
@click.option("--end", required=True, help="End of the part to replace (e.g. 0:45).")
@click.option("--prompt", help="What the part should become.")
@_lyrics_options
@click.option("--seed", type=int)
@click.option("--title", help="Title of the new song.")
@_cover_options
@_song_options
@click.pass_obj
def music_replace(obj: dict[str, str], song: str, lyrics_file: Path | None, **kw: Any) -> None:
    """Regenerate the part of SONG between --start and --end."""
    kw["lyrics"] = _read_lyrics(kw["lyrics"], lyrics_file)
    _edit(obj, "replace", song, **kw)


@music.command("variation")
@click.argument("song")
@_song_options
@click.pass_obj
def music_variation(
    obj: dict[str, str], song: str, audio_format: str, out: Path | None, as_json: bool
) -> None:
    """New takes of SONG with the same prompt and lyrics."""
    fmt = AudioFormat(audio_format)
    _emit_music(
        _run(lambda studio: studio.music_variation(song, fmt, out), obj["profile"]), as_json
    )


@music.command("trim")
@click.argument("song")
@click.option("--start", required=True, help="Keep from here (e.g. 0:05).")
@click.option("--end", required=True, help="Keep until here (e.g. 0:35).")
@_song_options
@click.pass_obj
def music_trim(
    obj: dict[str, str],
    song: str,
    start: str,
    end: str,
    audio_format: str,
    out: Path | None,
    as_json: bool,
) -> None:
    """Cut SONG to --start/--end; the cut is saved as a new song."""
    fmt = AudioFormat(audio_format)
    _emit_music(
        _run(lambda studio: studio.music_trim(song, start, end, fmt, out), obj["profile"]),
        as_json,
    )


@music.command("stems")
@click.argument("song")
@_song_options
@click.pass_obj
def music_stems(
    obj: dict[str, str], song: str, audio_format: str, out: Path | None, as_json: bool
) -> None:
    """Split SONG into drums, bass, vocals and other, and save each stem."""
    fmt = AudioFormat(audio_format)
    _emit_music(_run(lambda studio: studio.music_stems(song, fmt, out), obj["profile"]), as_json)


@music.command("video")
@click.argument("song")
@click.option(
    "--template",
    type=click.Choice([t.value for t in VideoTemplate]),
    default="simple",
    show_default=True,
)
@click.option("--out", type=click.Path(file_okay=False, path_type=Path), help="Output folder.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
@click.pass_obj
def music_video(
    obj: dict[str, str], song: str, template: str, out: Path | None, as_json: bool
) -> None:
    """Save SONG as a vertical MP4 that plays it over its cover art."""
    chosen = VideoTemplate(template)
    _emit_music(_run(lambda studio: studio.music_video(song, chosen, out), obj["profile"]), as_json)


@music.command("download")
@click.argument("songs", nargs=-1, required=True)
@_song_options
@click.pass_obj
def music_download(
    obj: dict[str, str], songs: tuple[str, ...], audio_format: str, out: Path | None, as_json: bool
) -> None:
    """Save existing SONGS as WAV, M4A or MP3."""
    fmt = AudioFormat(audio_format)
    _emit_music(
        _run(lambda studio: studio.music_download(list(songs), fmt, out), obj["profile"]),
        as_json,
    )


@music.command("list")
@click.option("--limit", default=20, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
@click.pass_obj
def music_list(obj: dict[str, str], limit: int, as_json: bool) -> None:
    """The newest songs in the account, and the credit balance."""
    result = _run(lambda studio: studio.music_library(limit), obj["profile"])
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=1))
        return
    for song in result["songs"]:
        seconds = f"{song['seconds']:.0f}s" if song["seconds"] else ""
        click.echo(f"{song['id']}  {seconds:>4}  {song['kind'] or '':12}  {song['title']}")
    click.echo(f"credits: {result['credits_remaining']} ({result['plan']})", err=True)


@music.command("credits")
@click.pass_obj
def music_credits(obj: dict[str, str]) -> None:
    """Flow Music credit balance and plan."""
    result = _run(lambda studio: studio.music_library(0), obj["profile"])
    click.echo(f"{result['credits_remaining']} credits ({result['plan']})")


@music.command("chat")
@click.argument("message")
@click.option("--session", help="Continue this session (id or URL); default: a new one.")
@click.option(
    "--audio",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Attach an audio file (max 40 MB).",
)
@click.option(
    "--image",
    "images",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Attach an image (repeatable).",
)
@click.option(
    "--confirm-rights",
    is_flag=True,
    help="Confirm you have the rights to attached files (Flow Music asks for this).",
)
@click.option("--timeout", default=900, show_default=True, help="Seconds to wait for the turn.")
@_song_options
@click.pass_obj
def music_chat(
    obj: dict[str, str],
    message: str,
    session: str | None,
    audio: Path | None,
    images: tuple[Path, ...],
    confirm_rights: bool,
    timeout: int,
    audio_format: str,
    out: Path | None,
    as_json: bool,
) -> None:
    """Talk to Flow Music's Producer: lyrics, ideas, remixes of your audio, music videos.

    Songs the Producer makes during the turn are saved. Use --session to continue.
    """
    request = _option_error(
        lambda: ChatRequest.build(
            message,
            session=session,
            audio=audio,
            images=images,
            confirm_rights=confirm_rights,
            audio_format=audio_format,
            out_dir=out,
            timeout_s=timeout,
        )
    )
    _emit_music(_run(lambda studio: studio.music_chat(request), obj["profile"]), as_json)


@music.command("upload")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--confirm-rights",
    is_flag=True,
    required=True,
    help="Confirm you have the rights to the file (Flow Music asks for this).",
)
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
@click.pass_obj
def music_upload(obj: dict[str, str], file: Path, confirm_rights: bool, as_json: bool) -> None:
    """Upload your own audio as a private song you can extend, cover, replace or split."""
    _emit_music(
        _run(
            lambda studio: studio.music_upload(file, confirm_rights=confirm_rights),
            obj["profile"],
        ),
        as_json,
    )


# --- state --------------------------------------------------------------------------------
@main.command()
@click.option("--limit", default=15, show_default=True)
@click.pass_obj
def jobs(obj: dict[str, str], limit: int) -> None:
    """Recent jobs."""
    for row in Store().recent_jobs(obj["profile"], limit):
        when = time.strftime("%m-%d %H:%M", time.localtime(row["created"]))
        took = (
            f"{row['finished'] - row['started']:.0f}s" if row["finished"] and row["started"] else ""
        )
        detail = row["error"] or ""
        click.echo(
            f"{row['id']}  {when}  {row['kind']:5}  {row['status']:7} {took:>5}  {detail[:80]}"
        )


@main.command()
@click.pass_obj
def credits(obj: dict[str, str]) -> None:
    """Credit balances: Flow (as of the last video) and Flow Music (live)."""
    seen = Store().get_setting(f"flow.credits.{obj['profile']}")
    if seen:
        flow = json.loads(seen)
        when = time.strftime("%m-%d %H:%M", time.localtime(flow["at"]))
        click.echo(f"Flow: {flow['credits']} credits (after the video at {when})")
    else:
        click.echo("Flow: unknown until the next video (Flow reports it on submit)")
    music = _run(lambda studio: studio.music_library(0), obj["profile"])
    click.echo(f"Flow Music: {music['credits_remaining']} credits ({music['plan']})")


@main.command()
@click.option("--project", help="Project to check; defaults to the default project.")
@click.option("--music", is_flag=True, help="Also check Flow Music (free).")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
@click.pass_obj
def doctor(obj: dict[str, str], project: str | None, music: bool, as_json: bool) -> None:
    """Check that Flow's page still has everything StudioWright uses. Spends nothing.

    Opens menus and panes, reads them and closes them again; submits, uploads and renames
    nothing. Exits with 1 when a check fails.
    """
    result = _run(lambda studio: studio.doctor(project, music), obj["profile"])
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        for check in result["checks"]:
            click.echo(f"{check['status']:4}  {check['name']}: {check['detail']}")
        for shot in result["screenshots"]:
            click.echo(f"screenshot: {shot}", err=True)
    if not result["ok"]:
        raise SystemExit(1)


@main.group()
def media() -> None:
    """The media in a Flow project."""


@media.command("list")
@click.option("--project", help="Project id; defaults to the default project.")
@click.option("--limit", default=50, show_default=True, help="Newest media to list.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
@click.pass_obj
def media_list(obj: dict[str, str], project: str | None, limit: int, as_json: bool) -> None:
    """List a project's media, newest first; `sw download` fetches any of them."""
    result = _run(lambda studio: studio.media(project, limit), obj["profile"])
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=1))
        return
    for item in result["media"]:
        created = item["created"]
        when = time.strftime("%m-%d %H:%M", time.localtime(created)) if created else ""
        click.echo(
            f"{item['position']:>3}  {item['media_id']}  {item['kind']:6}  {when:11}  "
            f"{item['title'] or ''}"
        )
    shown = len(result["media"])
    click.echo(f"{shown} of {result['total']} media in {result['project_id']}", err=True)


@media.command("rename")
@click.argument("pairs", nargs=-1, required=True)
@click.option("--project", help="Project id; defaults to the default project.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
@click.pass_obj
def media_rename(
    obj: dict[str, str], pairs: tuple[str, ...], project: str | None, as_json: bool
) -> None:
    """Name media in the Flow project grid: MEDIA_ID TITLE [MEDIA_ID TITLE ...].

    A media id may be a unique prefix. Example: sw media rename 1de0ca0c I01 1c61cc15 V01
    """
    if len(pairs) % 2:
        raise click.UsageError("give pairs: MEDIA_ID TITLE [MEDIA_ID TITLE ...]")
    names = dict(zip(pairs[::2], pairs[1::2], strict=True))
    result = _run(lambda studio: studio.rename(names, project), obj["profile"])
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=1))
        return
    for media_id, title in result["renamed"].items():
        click.echo(f"{media_id}  ->  {title}")
    for requested, error in result["errors"].items():
        click.echo(f"{requested}: {error}", err=True)


@main.group(invoke_without_command=True)
@click.pass_context
def pace(ctx: click.Context) -> None:
    """Submit pacing for the profile: show it (the default), or save a slower pace."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(pace_show)


@pace.command("set")
@click.option("--gap", type=float, help="Seconds between two submits.")
@click.option("--jitter", type=float, help="Random extra seconds added to each gap.")
@click.option("--cooldown", type=float, help="Pause in seconds after an unusual-activity refusal.")
@click.option("--reset", is_flag=True, help="Forget the saved values (back to the defaults).")
@click.pass_obj
def pace_set(
    obj: dict[str, str],
    gap: float | None,
    jitter: float | None,
    cooldown: float | None,
    reset: bool,
) -> None:
    """Save the profile's pacing; every CLI call and MCP tool uses it from then on.

    Environment variables (STUDIOWRIGHT_GAP_S, …) still win over saved values.
    """
    from studiowright.pacing import PaceSettings, save_pace

    store = Store()
    if reset:
        store.set_setting(f"pace.{obj['profile']}", "{}")
    try:
        save_pace(store, obj["profile"], {"gap_s": gap, "jitter_s": jitter, "cooldown_s": cooldown})
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    settings = PaceSettings.load(store, obj["profile"])
    click.echo(
        f"{obj['profile']}: {settings.gap_s:.0f} s between submits + up to "
        f"{settings.jitter_s:.0f} s, {settings.cooldown_s / 60:.0f} min cooldown"
    )


@pace.command("show")
@click.pass_obj
def pace_show(obj: dict[str, str]) -> None:
    """Show submit pacing and any cooldown for the profile."""
    from studiowright.pacing import Governor

    governor = Governor(Store(), obj["profile"])
    settings = governor.settings
    click.echo(
        f"pace: {settings.gap_s:.0f} s between submits + up to {settings.jitter_s:.0f} s, "
        f"{settings.cooldown_s / 60:.0f} min cooldown"
    )
    status = governor.status()
    now = time.time()
    click.echo(f"next submit allowed in: {max(0.0, status['next_free'] - now):.0f} s")
    cooling = status["cooldown_until"] - now
    click.echo(f"cooldown: {cooling / 60:.0f} min left" if cooling > 0 else "cooldown: none")
    click.echo(f"unusual-activity refusals so far: {status['refusals']}")
    upscale = Governor(Store(), f"{obj['profile']}:upscale").status()
    paused = upscale["cooldown_until"] - now
    click.echo(f"upscales: paused for {paused / 60:.0f} min" if paused > 0 else "upscales: ok")


@main.command()
def mcp() -> None:
    """Run the MCP server on stdio (for Claude Code and other agents)."""
    from studiowright.mcp_server import run

    run()


if __name__ == "__main__":  # pragma: no cover
    main()
