"""StudioWright's operations: one entry point shared by the CLI and the MCP server.

A :class:`Studio` attaches to the profile's Chrome, runs one operation at a time per profile
(a cross-process lock), paces submits through the :class:`Governor`, and records projects,
assets and jobs in the local store.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import shutil
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from filelock import FileLock
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Playwright, async_playwright

from studiowright import paths
from studiowright.browser import ChromeSession, attach, place_window
from studiowright.flow import batchexecute as be
from studiowright.flow.options import ImageOptions, OptionError, VideoMode, VideoOptions
from studiowright.flow.page import (
    FLOW_ROOT,
    FlowError,
    FlowPage,
    PartialUpload,
    UnusualActivity,
    UpscaleRefused,
    suffix_for,
)
from studiowright.flow.replies import is_uuid
from studiowright.images import stage_for_upload
from studiowright.music.options import (
    AudioFormat,
    ChatRequest,
    EditRequest,
    MusicJob,
    MusicRequest,
    VideoTemplate,
    format_time,
    parse_time,
)
from studiowright.music.page import EDIT_KINDS, SONG_KINDS, MusicPage, NotStarted, stem_name
from studiowright.music.page import LIBRARY_URL as MUSIC_LIBRARY
from studiowright.music.page import ROOT as MUSIC_ROOT
from studiowright.music.page import SESSION_URL as MUSIC_SESSION
from studiowright.music.wire import Clip
from studiowright.pacing import CoolingDown, Governor, PaceSettings
from studiowright.store import Store

log = logging.getLogger("studiowright.studio")

SESSION_JS = r"""
() => {
  const wiz = window.WIZ_global_data || {};
  return { sid: wiz.FdrFJe || null, build: wiz.cfb2h || null, csrf: wiz.SNlM0e || null,
           locale: document.documentElement.lang || 'en' };
}
"""

FETCH_JS = r"""
async ([url, body]) => {
  const response = await fetch(url, {
    method: 'POST', credentials: 'include',
    headers: {
      'content-type': 'application/x-www-form-urlencoded;charset=UTF-8',
      'x-same-domain': '1',
    },
    body,
  });
  return { status: response.status, text: await response.text() };
}
"""


@dataclass(frozen=True)
class ImageRequest:
    prompt: str
    options: ImageOptions = field(default_factory=ImageOptions)
    refs: Sequence[str] = ()
    project: str | None = None
    out_dir: Path | None = None
    #: Flow's download size token (e.g. ``2K`` or ``1080p``); None keeps the original.
    size: str | None = None


@dataclass(frozen=True)
class VideoRequest:
    prompt: str
    options: VideoOptions = field(default_factory=VideoOptions)
    start: str | None = None
    end: str | None = None
    refs: Sequence[str] = ()
    project: str | None = None
    out_dir: Path | None = None
    #: Flow's download size token (e.g. ``2K`` or ``1080p``); None keeps the original.
    size: str | None = None
    #: Refuse to submit when Flow's stated price is higher than this.
    max_credits: int | None = None
    #: Set everything up and report Flow's price, but do not submit.
    dry_run: bool = False


def _unique(path: Path) -> Path:
    candidate, number = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{number}{path.suffix}")
        number += 1
    return candidate


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Studio:
    def __init__(self, profile: str = paths.DEFAULT_PROFILE, store: Store | None = None) -> None:
        self.profile = profile
        self.store = store or Store()
        self.governor = Governor(self.store, profile)
        self._playwright: Playwright | None = None
        self._session: ChromeSession | None = None
        paths.locks_dir().mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(paths.locks_dir() / f"{profile}.lock"), thread_local=False)
        # Flow Music runs in its own tab, so it has its own lock and pacing.
        self._music_lock = FileLock(
            str(paths.locks_dir() / f"{profile}-music.lock"), thread_local=False
        )
        self.music_governor = Governor(self.store, f"{profile}:music")
        # Downloads submit nothing, so they run in a tab of their own beside generations.
        self._download_lock = FileLock(
            str(paths.locks_dir() / f"{profile}-download.lock"), thread_local=False
        )
        # Flow refuses bulk upscaling with "unusual activity" (seen after ~45 2K downloads
        # in 37 min), so upscales are spaced and pause on a refusal, apart from generations.
        self.upscale_governor = Governor(
            self.store, f"{profile}:upscale", PaceSettings.for_upscales()
        )

    async def __aenter__(self) -> Studio:
        self._playwright = await async_playwright().start()
        self._session = await attach(self._playwright, self.profile)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._session:
            await self._session.detach()
        if self._playwright:
            await self._playwright.stop()

    @property
    def session(self) -> ChromeSession:
        if self._session is None:
            raise RuntimeError("use `async with Studio(...)`")
        return self._session

    async def _locked(self) -> None:
        await asyncio.to_thread(self._lock.acquire)
        # Only one job runs per profile, so any job still marked running died mid-way.
        self.store.mark_interrupted(self.profile)

    # --- projects ---------------------------------------------------------------------
    def default_project(self) -> str | None:
        return self.store.get_setting(f"project.default.{self.profile}")

    def use_project(self, project_id: str) -> None:
        if not is_uuid(project_id):
            raise ValueError(f"not a Flow project id: {project_id!r}")
        self.store.add_project(project_id, self.profile, None)
        self.store.set_setting(f"project.default.{self.profile}", project_id)

    async def create_project(self, title: str | None = None) -> str:
        """Create a Flow project with the page's own project RPC (it carries no token)."""
        title = title or f"StudioWright {time.strftime('%Y-%m-%d %H:%M')}"
        page = await self.session.page_for(FLOW_ROOT)
        if not page.url.startswith(FLOW_ROOT):
            await page.goto(FLOW_ROOT, wait_until="domcontentloaded")
        info = await page.evaluate(SESSION_JS)
        if not info["csrf"]:
            raise FlowError("Flow did not load a signed-in session; run `sw auth login`")
        session = be.Session(info["sid"], info["build"], info["csrf"], info["locale"])
        payload = ["projects/*", [None, [title]], [None, 22]]
        url = be.request_url(
            "jHPbke", session, source_path="/", request_id=random.randint(10_000, 99_999)
        )
        result = await page.evaluate(
            FETCH_JS, [url, be.request_body("jHPbke", payload, session.csrf)]
        )
        reply = be.parse_reply(result["text"], "jHPbke")
        project_id = reply[0]
        if not is_uuid(project_id):
            raise FlowError("Flow did not return a project id")
        self.store.add_project(project_id, self.profile, title)
        if not self.default_project():
            self.store.set_setting(f"project.default.{self.profile}", project_id)
        return project_id

    async def _project(self, requested: str | None) -> str:
        project = requested or self.default_project()
        if project:
            return project
        return await self.create_project()

    # --- window -----------------------------------------------------------------------
    async def window(self, *, visible: bool) -> None:
        page = await self.session.page_for(FLOW_ROOT)
        await place_window(self.session, page, visible=visible)

    # --- assets -----------------------------------------------------------------------
    async def _media_ids(self, flow: FlowPage, refs: Sequence[str]) -> list[str]:
        """Media ids for refs that are ids already or local files (uploaded when new)."""
        resolved: list[str | None] = []
        pending: list[tuple[int, Path, str]] = []
        for index, ref in enumerate(refs):
            if is_uuid(ref):
                resolved.append(ref)
                self._remember_name(flow, self.store.asset(flow.project_id, ref))
                continue
            path = Path(ref).expanduser()
            if not path.is_file():
                raise FileNotFoundError(f"reference not found: {ref}")
            digest = file_sha256(path)
            known = self.store.asset_by_hash(flow.project_id, digest)
            resolved.append(known.media_id if known else None)
            self._remember_name(flow, known)
            if not known:
                pending.append((index, path, digest))
        if pending:
            paths.staging_dir().mkdir(parents=True, exist_ok=True)
            # Large photos are scaled down first: Flow does not finish very large uploads.
            staged = []
            for entry in pending:
                _index, path, digest = entry
                staged.append(
                    (stage_for_upload(path, paths.staging_dir() / f"sw-{digest[:10]}"), entry)
                )
            try:
                results = await flow.upload([copy for copy, _ in staged])
            except PartialUpload as exc:
                # Remember what did arrive, so a retry does not upload it a second time.
                self._record_uploads(flow, staged, exc.results, resolved)
                raise
            self._record_uploads(flow, staged, results, resolved)
            # No reload: new uploads appear on the grid at once with their final id, while a
            # freshly loaded grid can lag one upload behind.
        for media_id in resolved:
            if media_id and media_id not in flow.positions:
                position = self.store.grid_position(flow.project_id, media_id)
                if position is not None:
                    flow.positions[media_id] = position
        return [media_id for media_id in resolved if media_id]

    def _record_uploads(
        self,
        flow: FlowPage,
        staged: list[tuple[Path, tuple[int, Path, str]]],
        results: Sequence[Any],
        resolved: list[str | None],
    ) -> None:
        """Store finished uploads by content hash, matched to their staged file by name."""
        by_name = {copy.name: entry for copy, entry in staged}
        if all(r.name in by_name for r in results):
            pairs = [(by_name[r.name], r) for r in results]
        elif len(results) == len(staged):
            pairs = [(entry, r) for (_copy, entry), r in zip(staged, results, strict=True)]
        else:
            pairs = []
        for (index, path, digest), result in pairs:
            self.store.add_asset(
                flow.project_id, result.media_id, "image", result.name, digest, str(path)
            )
            resolved[index] = result.media_id
            flow.names[result.media_id] = result.name

    @staticmethod
    def _remember_name(flow: FlowPage, asset: Any) -> None:
        """Tell the page an upload's Flow name, so it can find it by search."""
        if asset is not None and asset.name and asset.source != "generated":
            flow.names[asset.media_id] = asset.name

    def _full_media_id(self, project_id: str, requested: str) -> str | None:
        """A full media id for an id or a unique id prefix this profile has seen."""
        if is_uuid(requested):
            return requested.lower()
        matches = self.store.assets_by_prefix(project_id, requested.lower())
        if len(matches) > 1:
            raise FlowError(f"{requested!r} matches {len(matches)} media; give more characters")
        return matches[0] if matches else None

    async def upload(self, files: Sequence[str], project: str | None = None) -> dict[str, Any]:
        await self._locked()
        try:
            project_id = await self._project(project)
            flow = await FlowPage.open(self.session, project_id)
            media_ids = await self._media_ids(flow, files)
            return {"project_id": project_id, "media_ids": media_ids}
        finally:
            self._lock.release()

    async def download(
        self,
        media_ids: Sequence[str],
        project: str | None = None,
        out_dir: Path | None = None,
        size: str | None = None,
    ) -> dict[str, Any]:
        """Download existing media by id (e.g. a clip that finished after a timeout).

        Runs in a tab of its own under its own lock, so a slow upscale does not hold up
        generations; nothing is submitted for a plain download.
        """
        await asyncio.to_thread(self._download_lock.acquire)
        try:
            project_id = await self._project(project)
            flow = await FlowPage.open(self.session, project_id, role="download")
            out = self._out_dir(out_dir)
            files = []
            grid_urls: dict[str, dict[str, str]] | None = None
            errors: dict[str, str] = {}
            paused: str | None = None
            for requested in media_ids:
                try:
                    media_id = self._full_media_id(project_id, requested)
                    if media_id is None:
                        # An unknown prefix: look it up among the media the grid page knows.
                        grid_urls = grid_urls or await flow.fresh_media_urls()
                        matches = [m for m in grid_urls if m.startswith(requested.lower())]
                        if len(matches) != 1:
                            raise FlowError(
                                f"{requested!r} matches {len(matches)} media; give the full id"
                            )
                        media_id = matches[0]
                    # Each medium's own edit view opens by id, however far down the grid.
                    if size:
                        if paused:
                            raise UpscaleRefused(paused, unusual=True)
                        try:
                            saved = await self._upscaled(flow, media_id, size, out)
                        except UpscaleRefused as exc:
                            if exc.unusual:
                                # Do not keep asking: the rest would be refused too.
                                paused = str(exc)
                            raise
                        target = _unique(out / f"{media_id}-{size}{saved.suffix}")
                        saved.rename(target)
                    else:
                        found = await flow.edit_view_urls(media_id)
                        url = found.get("video") or found.get("image")
                        if not url:
                            raise FlowError(f"no downloadable file for {media_id} (yet)")
                        default = ".mp4" if "video" in found else ".jpg"
                        target = await flow.download(url, out / f"{media_id}{default}")
                    files.append(str(target))
                except FlowError as exc:
                    # One medium's problem must not cost the others in a bulk download.
                    errors[requested] = str(exc)
                    log.warning("download of %s failed: %s", requested, exc)
            await flow.page.goto(flow.url, wait_until="domcontentloaded")
            if errors and not files:
                raise FlowError("; ".join(f"{k}: {v}" for k, v in errors.items()))
            return {"project_id": project_id, "files": files, "errors": errors}
        finally:
            self._download_lock.release()

    async def _fail(self, job_id: str, exc: BaseException, site: str) -> None:
        """Record a failed job with a screenshot of the page it failed on."""
        shot = None
        try:
            page = self.session.find_page(site)
            if page is not None:
                paths.incidents_dir().mkdir(parents=True, exist_ok=True)
                shot = paths.incidents_dir() / f"{job_id}.png"
                await page.screenshot(path=str(shot))
        except Exception:
            shot = None
        detail = f"{type(exc).__name__}: {exc}"
        if shot is not None:
            detail += f" (screenshot: {shot})"
            if isinstance(exc, FlowError | PlaywrightError):
                exc.args = (f"{exc.args[0] if exc.args else exc} (screenshot: {shot})",)
        self.store.update_job(job_id, status="failed", error=detail, finished=time.time())

    # --- generation -------------------------------------------------------------------
    def _progress(self, job_id: str, stage: str, media_ids: Sequence[str]) -> None:
        """Merge Flow progress into the existing result under the profile's job lock."""
        job = self.store.job(job_id)
        result = json.loads(job["result"]) if job and job["result"] else {}
        stages = ("submitted", "generated", "delivered")
        previous = result.get("stage")
        if previous not in stages or stages.index(stage) > stages.index(previous):
            result["stage"] = stage
        result["media_ids"] = list(dict.fromkeys([*result.get("media_ids", []), *media_ids]))
        self.store.update_job(job_id, result=result)

    def _out_dir(self, requested: Path | None) -> Path:
        out = requested or paths.output_dir()
        out.mkdir(parents=True, exist_ok=True)
        return out

    async def _upscaled(self, flow: FlowPage, item_id: str, size: str, out: Path) -> Path:
        """A sized download, paced apart from generations; a refusal for unusual activity
        pauses further upscales (not generations)."""
        try:
            await self.upscale_governor.wait_turn()
        except CoolingDown as exc:
            minutes = max(1, round((exc.until - time.time()) / 60))
            raise UpscaleRefused(
                f"upscales are paused for ~{minutes} min after Flow reported unusual activity",
                unusual=True,
            ) from exc
        try:
            return await flow.download_size(item_id, size, out, back=False)
        except UpscaleRefused as exc:
            if exc.unusual:
                self.upscale_governor.report_unusual_activity()
            raise

    async def _download_all(
        self,
        flow: FlowPage,
        items: list[tuple[str, str, str | None]],
        out: Path,
        stem: str,
        default_suffix: str,
        size: str | None = None,
        notes: list[str] | None = None,
    ) -> list[str]:
        files = []
        upscaled = False
        for number, (media_id, url, workflow_id) in enumerate(items, start=1):
            saved = None
            if size:
                try:
                    saved = await self._upscaled(flow, workflow_id or media_id, size, out)
                    upscaled = True
                except UpscaleRefused as exc:
                    # The generation itself succeeded; keep it at its own size.
                    if notes is not None:
                        notes.append(f"{exc}; saved at the original size instead")
            if saved is not None:
                target = _unique(out / f"{stem}-{number}-{size}{saved.suffix or default_suffix}")
                saved.rename(target)
            else:
                target = out / f"{stem}-{number}{default_suffix}"
                await flow.download(url, target)
                real_suffix = suffix_for(target.read_bytes()[:12], default_suffix)
                if real_suffix != default_suffix:
                    target = target.rename(target.with_suffix(real_suffix))
            files.append(str(target))
            # The content hash lets a copy of this file (say, a chosen frame) be passed back
            # as a reference without uploading it again.
            self.store.add_asset(
                flow.project_id,
                media_id,
                "video" if default_suffix == ".mp4" else "image",
                target.name,
                file_sha256(target),
                "generated",
            )
        if upscaled:
            await flow.page.goto(flow.url, wait_until="domcontentloaded")
        return files

    async def _submit_guarded(self, call: Any) -> Any:
        await self.governor.wait_turn()
        try:
            return await call()
        except UnusualActivity:
            self.governor.report_unusual_activity()
            raise

    async def image(self, request: ImageRequest) -> dict[str, Any]:
        await self._locked()
        try:
            project_id = await self._project(request.project)
        except BaseException:
            # The main try below starts after this; a failed lookup must not keep the lock.
            self._lock.release()
            raise
        job_id = self.store.create_job(
            "image",
            self.profile,
            project_id,
            {
                "prompt": request.prompt,
                "model": request.options.model.value,
                "aspect": request.options.aspect.value,
                "count": request.options.count,
                "refs": list(request.refs),
            },
        )
        started = time.time()
        self.store.update_job(job_id, status="running", started=started)
        try:
            flow = await FlowPage.open(self.session, project_id)
            media_ids = await self._media_ids(flow, request.refs)
            await flow.reset()
            await flow.apply_image_options(request.options)
            for media_id in media_ids:
                await flow.attach_reference(media_id)
            results = await self._submit_guarded(lambda: flow.generate_images(request.prompt))
            self._progress(job_id, "generated", [r.media_id for r in results])
            stem = time.strftime("%Y%m%d-%H%M%S") + f"-image-{job_id[:6]}"
            notes: list[str] = []
            files = await self._download_all(
                flow,
                [(r.media_id, r.url, r.workflow_id) for r in results],
                self._out_dir(request.out_dir),
                stem,
                ".jpg",
                request.size,
                notes,
            )
            result = {
                "job_id": job_id,
                "project_id": project_id,
                "files": files,
                "media_ids": [r.media_id for r in results],
                "stage": "delivered",
                "sizes": [[r.width, r.height] for r in results],
                "seconds": round(time.time() - started, 1),
            }
            if notes:
                result["notes"] = notes
            self.store.update_job(job_id, status="done", result=result, finished=time.time())
            return result
        except BaseException as exc:
            await self._fail(job_id, exc, FLOW_ROOT)
            raise
        finally:
            self._lock.release()

    async def video(self, request: VideoRequest) -> dict[str, Any]:
        await self._locked()
        try:
            project_id = await self._project(request.project)
        except BaseException:
            # The main try below starts after this; a failed lookup must not keep the lock.
            self._lock.release()
            raise
        options = request.options
        job_id = self.store.create_job(
            "video",
            self.profile,
            project_id,
            {
                "prompt": request.prompt,
                "model": options.model.value,
                "mode": options.mode.value,
                "aspect": options.aspect.value,
                "resolution": options.resolution,
                "duration": options.duration,
                "count": options.count,
                "start": request.start,
                "end": request.end,
                "refs": list(request.refs),
            },
        )
        started = time.time()
        self.store.update_job(job_id, status="running", started=started)
        try:
            flow = await FlowPage.open(self.session, project_id)
            frames = [ref for ref in (request.start, request.end) if ref]
            frame_ids = await self._media_ids(flow, frames)
            ref_ids = await self._media_ids(flow, request.refs)
            await flow.reset()
            if options.mode is VideoMode.FRAMES and request.start:
                await flow.set_start_frame(frame_ids[0])
            credits = await flow.apply_video_options(options)
            if request.dry_run:
                result = {
                    "job_id": job_id,
                    "project_id": project_id,
                    "credits": credits,
                    "submitted": False,
                    "seconds": round(time.time() - started, 1),
                }
                self.store.update_job(job_id, status="dry-run", result=result, finished=time.time())
                await flow.reset()
                return result
            if (
                request.max_credits is not None
                and credits is not None
                and credits > request.max_credits
            ):
                raise FlowError(
                    f"Flow prices this clip at {credits} credits, above the limit of "
                    f"{request.max_credits}; nothing was submitted"
                )
            if options.mode is VideoMode.FRAMES and request.end:
                await flow.set_end_frame(frame_ids[-1])
            if options.mode is VideoMode.INGREDIENTS:
                for media_id in ref_ids:
                    await flow.attach_reference(media_id)
            clips = await self._submit_guarded(
                lambda: flow.generate_video(
                    request.prompt, on_ids=lambda ids: self._progress(job_id, "submitted", ids)
                )
            )
            self._progress(job_id, "generated", [c.media_id for c in clips])
            stem = time.strftime("%Y%m%d-%H%M%S") + f"-video-{job_id[:6]}"
            notes: list[str] = []
            files = await self._download_all(
                flow,
                [(c.media_id, c.url, c.workflow_id) for c in clips],
                self._out_dir(request.out_dir),
                stem,
                ".mp4",
                request.size,
                notes,
            )
            result = {
                "job_id": job_id,
                "project_id": project_id,
                "files": files,
                "media_ids": [c.media_id for c in clips],
                "stage": "delivered",
                "credits": credits,
                "credits_left": flow.credits_left,
                "seconds": round(time.time() - started, 1),
            }
            if notes:
                result["notes"] = notes
            if flow.credits_left is not None:
                self.store.set_setting(
                    f"flow.credits.{self.profile}",
                    json.dumps({"credits": flow.credits_left, "at": time.time()}),
                )
            self.store.update_job(job_id, status="done", result=result, finished=time.time())
            return result
        except BaseException as exc:
            await self._fail(job_id, exc, FLOW_ROOT)
            raise
        finally:
            self._lock.release()

    # --- Flow Music -------------------------------------------------------------------
    async def _music_job(
        self,
        kind: str,
        params: dict[str, Any],
        work: Callable[[str], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Run one Flow Music job under the music lock, recorded in the job store."""
        await asyncio.to_thread(self._music_lock.acquire)
        job_id = self.store.create_job(
            kind, self.profile, None, {k: v for k, v in params.items() if v is not None}
        )
        started = time.time()
        self.store.update_job(job_id, status="running", started=started)
        try:
            result = await work(job_id)
            result = {"job_id": job_id, **result, "seconds": round(time.time() - started, 1)}
            self.store.update_job(job_id, status="done", result=result, finished=time.time())
            return result
        except BaseException as exc:
            await self._fail(job_id, exc, MUSIC_ROOT)
            raise
        finally:
            self._music_lock.release()

    async def _save_songs(
        self,
        music: MusicPage,
        clips: Sequence[Clip],
        audio_format: AudioFormat,
        out_dir: Path | None,
        job_id: str,
        *,
        names: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        out = self._out_dir(out_dir)
        stem = time.strftime("%Y%m%d-%H%M%S") + f"-music-{job_id[:6]}"
        songs = []
        for number, clip in enumerate(clips, start=1):
            suffix = names[number - 1] if names else str(number)
            target = _unique(out / f"{stem}-{suffix}.{audio_format.value}")
            await music.download(clip, audio_format, target)
            songs.append({**clip.summary(), "file": str(target)})
        return songs

    @staticmethod
    def _song_result(music: MusicPage, songs: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "files": [s["file"] for s in songs],
            "songs": songs,
            "credits_remaining_before": music.credits_remaining,
            "plan": music.plan,
        }

    async def _song(self, music: MusicPage, song: str) -> str:
        resolved = await music.resolve(song)
        return resolved if isinstance(resolved, str) else resolved.clip_id

    async def music(self, request: MusicRequest) -> dict[str, Any]:
        """Make a song or instrumental on flowmusic.app and save its audio."""

        async def work(job_id: str) -> dict[str, Any]:
            music = await MusicPage.open(self.session)
            try:
                await music.compose(request)
            except PlaywrightError:
                # Nothing is sent before Generate, so a fresh page can be filled again.
                music = await MusicPage.open(self.session)
                await music.compose(request)
            await self.music_governor.wait_turn()
            submitted = time.time()
            try:
                clips = (await music.generate(kinds=SONG_KINDS)).ready_clips()
            except NotStarted:
                raise
            except FlowError as exc:
                # The song may have finished after the wait; it is in the library then.
                clips = [
                    c
                    for c in await music.library()
                    if c.kind in SONG_KINDS
                    and c.complete
                    and c.created is not None
                    and c.created.timestamp() >= submitted - 30
                    and (not request.title or c.title == request.title)
                ]
                if not clips:
                    raise
                log.warning("%s; found %d finished song(s) in the library", exc, len(clips))
            songs = await self._save_songs(
                music, clips, request.audio_format, request.out_dir, job_id
            )
            return self._song_result(music, songs)

        params = {
            "sound": request.sound,
            "lyrics": request.lyrics,
            "bpm": request.bpm,
            "length_s": request.length_s,
            "seed": request.seed,
            "model": request.model.value,
            "title": request.title,
            "format": request.audio_format.value,
        }
        return await self._music_job("music", params, work)

    async def music_edit(self, request: EditRequest) -> dict[str, Any]:
        """Extend, cover or replace part of an existing song; saves the new takes."""

        async def work(job_id: str) -> dict[str, Any]:
            music = await MusicPage.open(self.session, MUSIC_LIBRARY)
            clip_id = await self._song(music, request.song)
            try:
                await music.compose_edit(request, clip_id)
            except PlaywrightError:
                # As above: nothing is sent before Generate.
                await music.compose_edit(request, clip_id)
            region = await music.region_label()
            await self.music_governor.wait_turn()
            watch = await music.generate(known={clip_id}, kinds=EDIT_KINDS)
            songs = await self._save_songs(
                music, watch.ready_clips(), request.audio_format, request.out_dir, job_id
            )
            return {"source": clip_id, "region": region, **self._song_result(music, songs)}

        params = {
            "song": request.song,
            "mode": request.mode.value,
            "prompt": request.prompt,
            "lyrics": request.lyrics,
            "start_s": request.start_s,
            "end_s": request.end_s,
            "seed": request.seed,
            "title": request.title,
        }
        return await self._music_job(f"music-{request.mode.value}", params, work)

    async def music_variation(
        self, song: str, audio_format: AudioFormat = AudioFormat.WAV, out_dir: Path | None = None
    ) -> dict[str, Any]:
        """New takes of a song with the same prompt and lyrics."""

        async def work(job_id: str) -> dict[str, Any]:
            music = await MusicPage.open(self.session, MUSIC_LIBRARY)
            clip_id = await self._song(music, song)
            await self.music_governor.wait_turn()
            watch = await music.variation(clip_id)
            songs = await self._save_songs(
                music, watch.ready_clips(), audio_format, out_dir, job_id
            )
            return {"source": clip_id, **self._song_result(music, songs)}

        return await self._music_job("music-variation", {"song": song}, work)

    async def music_trim(
        self,
        song: str,
        start: str | float,
        end: str | float,
        audio_format: AudioFormat = AudioFormat.WAV,
        out_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Cut a song to start-end; the cut is saved as a new song."""
        start_s, end_s = parse_time(start), parse_time(end)
        if start_s is None or end_s is None or end_s <= start_s:
            raise OptionError("trim needs a start and an end after it")

        async def work(job_id: str) -> dict[str, Any]:
            music = await MusicPage.open(self.session, MUSIC_LIBRARY)
            clip_id = await self._song(music, song)
            await self.music_governor.wait_turn()
            watch = await music.trim(clip_id, format_time(start_s), format_time(end_s))
            songs = await self._save_songs(
                music, watch.ready_clips(), audio_format, out_dir, job_id
            )
            return {"source": clip_id, **self._song_result(music, songs)}

        params = {"song": song, "start_s": start_s, "end_s": end_s}
        return await self._music_job("music-trim", params, work)

    async def music_stems(
        self, song: str, audio_format: AudioFormat = AudioFormat.WAV, out_dir: Path | None = None
    ) -> dict[str, Any]:
        """Split a song into drums, bass, vocals and other; saves each stem."""

        async def work(job_id: str) -> dict[str, Any]:
            music = await MusicPage.open(self.session, MUSIC_LIBRARY)
            clip_id = await self._song(music, song)
            await self.music_governor.wait_turn()
            watch = await music.stems(clip_id)
            clips = sorted(watch.ready_clips(), key=stem_name)
            names = [stem_name(c) for c in clips]
            songs = await self._save_songs(music, clips, audio_format, out_dir, job_id, names=names)
            for entry, name in zip(songs, names, strict=True):
                entry["stem"] = name
            return {"source": clip_id, **self._song_result(music, songs)}

        return await self._music_job("music-stems", {"song": song}, work)

    async def music_video(
        self,
        song: str,
        template: VideoTemplate = VideoTemplate.SIMPLE,
        out_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Render a song as an MP4 with one of Flow Music's video templates."""

        async def work(job_id: str) -> dict[str, Any]:
            music = await MusicPage.open(self.session, MUSIC_LIBRARY)
            clip_id = await self._song(music, song)
            await self.music_governor.wait_turn()
            folder = paths.staging_dir() / f"music-{job_id}"
            saved = await music.video(clip_id, template, folder)
            name = f"{time.strftime('%Y%m%d-%H%M%S')}-music-video-{job_id[:6]}.mp4"
            target = _unique(self._out_dir(out_dir) / name)
            shutil.move(str(saved), target)
            shutil.rmtree(folder, ignore_errors=True)
            return {
                "source": clip_id,
                "template": template.value,
                "files": [str(target)],
                "credits_remaining_before": music.credits_remaining,
            }

        params = {"song": song, "template": template.value}
        return await self._music_job("music-video", params, work)

    async def music_download(
        self,
        songs: Sequence[str],
        audio_format: AudioFormat = AudioFormat.WAV,
        out_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Save existing songs by id, id prefix or ``last``."""

        async def work(job_id: str) -> dict[str, Any]:
            music = await MusicPage.open(self.session, MUSIC_LIBRARY)
            library = {c.clip_id: c for c in await music.library()}
            out = self._out_dir(out_dir)
            saved = []
            for song in songs:
                resolved = await music.resolve(song)
                clip = library.get(resolved, resolved) if isinstance(resolved, str) else resolved
                clip_id = clip.clip_id if isinstance(clip, Clip) else clip
                title = clip.title if isinstance(clip, Clip) and clip.title else ""
                safe = re.sub(r"[^\w\- ]+", "", title).strip() or "song"
                target = _unique(out / f"{safe}-{clip_id[:6]}.{audio_format.value}")
                await music.download(clip, audio_format, target)
                saved.append(str(target))
            return {"files": saved}

        return await self._music_job("music-download", {"songs": list(songs)}, work)

    async def music_library(self, limit: int = 20) -> dict[str, Any]:
        """The newest songs in the account, with the credit balance and plan."""
        music = await MusicPage.open(self.session, MUSIC_LIBRARY)
        songs = await music.library()
        return {
            "credits_remaining": music.credits_remaining,
            "plan": music.plan,
            "songs": [c.summary() for c in songs[:limit]],
        }

    async def music_upload(self, file: str | Path, *, confirm_rights: bool) -> dict[str, Any]:
        """Upload your own audio; it becomes a private song you can remix, extend or split."""
        path = Path(file).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"audio file not found: {path}")
        if not confirm_rights:
            raise OptionError(
                "Flow Music asks you to confirm you have the rights to uploaded files; "
                "pass confirm_rights / --confirm-rights if you do"
            )

        async def work(job_id: str) -> dict[str, Any]:
            music = await MusicPage.open(self.session)
            await self.music_governor.wait_turn()
            song = await music.upload_audio(path, confirm_rights=True)
            return {"song": song, "url": f"{MUSIC_ROOT}/song/{song}"}

        return await self._music_job("music-upload", {"file": str(path)}, work)

    async def music_chat(self, request: ChatRequest) -> dict[str, Any]:
        """Talk to the Producer agent; saves any songs it makes during the turn."""

        async def work(job_id: str) -> dict[str, Any]:
            url = f"{MUSIC_ROOT}/session/{request.session}" if request.session else MUSIC_SESSION
            music = await MusicPage.open(self.session, url)
            uploaded = None
            if request.audio:
                uploaded = await music.upload_audio(
                    request.audio, confirm_rights=request.confirm_rights
                )
            for image in request.images:
                await music.attach_image(image, confirm_rights=request.confirm_rights)
            await self.music_governor.wait_turn()
            watch = await music.chat(request.message, timeout_s=request.timeout_s)
            songs = await self._save_songs(
                music, watch.ready_clips(), request.audio_format, request.out_dir, job_id
            )
            return {
                **watch.turn.summary(),
                "uploaded": uploaded,
                **self._song_result(music, songs),
            }

        params = {"message": request.message, "session": request.session}
        return await self._music_job("music-chat", params, work)

    async def music_batch(self, job: MusicJob) -> dict[str, Any]:
        """Run one music step of a batch file."""
        if isinstance(job.request, MusicRequest):
            return await self.music(job.request)
        if isinstance(job.request, EditRequest):
            return await self.music_edit(job.request)
        song = job.song or "last"
        if job.action == "variation":
            return await self.music_variation(song, job.audio_format, job.out_dir)
        if job.action == "trim":
            return await self.music_trim(
                song, job.start or "", job.end or "", job.audio_format, job.out_dir
            )
        if job.action == "stems":
            return await self.music_stems(song, job.audio_format, job.out_dir)
        if job.action == "video":
            return await self.music_video(song, job.template, job.out_dir)
        raise OptionError(f"unknown music action {job.action!r}")
