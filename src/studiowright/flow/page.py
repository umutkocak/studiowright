"""Drive one Flow project tab through its own UI and read the replies it receives.

Everything here is something a person could do in the page: click, type, choose a file.
Requests are never intercepted or changed; replies are only read, through passive
listeners, to learn ids, signed URLs and status.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import CDPSession, FileChooser, Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from studiowright.browser import ChromeSession, DownloadCatcher, ensure_rendering
from studiowright.flow import batchexecute as be
from studiowright.flow import replies
from studiowright.flow import selectors as s
from studiowright.flow.options import ImageOptions, VideoOptions, count_pattern, duration_pattern
from studiowright.pacing import env_seconds

log = logging.getLogger("studiowright.flow")

FLOW_ROOT = "https://flow.google.com/"
IMAGE_RPC = "ogiZ0b"
#: The project's media list, loaded with the project page.
LIST_RPC = "Zzl0ze"
#: Server and local clocks may differ a little when telling new media from old.
CLOCK_SLACK_S = 120
UPLOAD_RPC = "maseQ"
VIDEO_SUBMIT_RPCS = ("YhhmEf", "eb1hJf", "MZZa6b", "nprQif")
VIDEO_STATUS_RPCS = ("jwpduf", "as29s")
UNUSUAL = "UNUSUAL_ACTIVITY"


class FlowError(RuntimeError):
    """Flow did not do what was asked."""


class UnusualActivity(FlowError):
    """Flow refused with an unusual-activity error; the profile must cool down."""


class GenerationRefused(FlowError):
    """Flow refused the generation, e.g. for its content policy."""


class UpscaleRefused(FlowError):
    """Flow refused to upscale a download; ``unusual`` when it was an unusual-activity
    refusal, after which upscales must pause."""

    def __init__(self, message: str, unusual: bool) -> None:
        super().__init__(message)
        self.unusual = unusual


class PartialUpload(FlowError):
    """Some files of an upload finished and some did not; ``results`` are the finished ones,
    so they can be recorded and not uploaded again."""

    def __init__(self, message: str, results: list[replies.UploadResult]) -> None:
        super().__init__(message)
        self.results = results


class AssetNotFound(FlowError):
    """The media id is not on this project's grid."""


@dataclass(frozen=True)
class VideoClip:
    media_id: str
    url: str
    poster_url: str | None
    workflow_id: str | None = None


#: How long Flow may upscale before the sized download begins.
UPSCALE_START_S = 420
#: The RPC behind a sized (upscaled) download.
UPSCALE_RPC = "SPrCad"

#: The text of failed tiles (Flow writes its reason there), newest first.
SCROLL_TOP_JS = "(sel) => { const g = document.querySelector(sel); if (g) g.scrollTop = 0; }"
FAILED_TILES_JS = r"""
() => [...document.querySelectorAll('mat-icon')]
  .filter((icon) => ['warning', 'warning_amber', 'error', 'error_outline', 'report']
    .includes(icon.textContent.trim()))
  .map((icon) => {
    let card = icon.closest('flow-video-tile, flow-image-tile') || icon.parentElement;
    for (let i = 0; i < 4 && card && card.textContent.trim().length < 40; i++) {
      card = card.parentElement;
    }
    const copy = card.cloneNode(true);
    copy.querySelectorAll('mat-icon, button').forEach((e) => e.remove());
    return copy.textContent.replace(/\s+/g, ' ').trim();
  })
  .filter(Boolean)
"""


def _rpcs(url: str) -> list[str]:
    """The RPC ids a batchexecute request carries; one request can carry several."""
    if "batchexecute" not in url:
        return []
    ids = parse_qs(urlsplit(url).query).get("rpcids", [""])[0]
    return [rpc for rpc in ids.split(",") if rpc]


#: Scroll the grid's own container until ``img[data-media-id=…]`` exists. The grid lists
#: newest first, so ``hint`` (the share of the project's media newer than the target) says
#: where to start; the scan then runs to the end and, if needed, from the top. The mouse
#: wheel stops long before the end of a large project.
SCAN_JS = r"""
async ([selector, scrollerSelector, hint]) => {
  const scroller = document.querySelector(scrollerSelector) || [...document.querySelectorAll('*')]
    .filter((e) => /(auto|scroll)/.test(getComputedStyle(e).overflowY)
      && e.scrollHeight > e.clientHeight + 50)
    .sort((a, b) => b.scrollHeight - a.scrollHeight)[0];
  if (!scroller) return false;
  const pause = (ms) => new Promise((r) => setTimeout(r, ms));
  const look = async () => {
    for (let wait = 0; wait < 6; wait++) {
      const img = document.querySelector(selector);
      if (img) { img.scrollIntoView({block: 'center'}); await pause(200); return true; }
      await pause(60);
    }
    return false;
  };
  const sweep = async (from, until) => {
    scroller.scrollTop = from;
    await pause(250);
    let still = 0;
    for (let step = 0; step < 2000; step++) {
      if (await look()) return true;
      const before = scroller.scrollTop;
      if (until !== null && before >= until) return false;
      scroller.scrollTop = before + scroller.clientHeight * 0.85;
      await pause(120);
      still = scroller.scrollTop === before ? still + 1 : 0;
      if (still >= 2) return false;
    }
    return false;
  };
  const start = hint === null ? 0
    : Math.max(0, hint * scroller.scrollHeight
        - Math.max(4 * scroller.clientHeight, 0.1 * scroller.scrollHeight));
  if (await sweep(start, null)) return true;
  return start > 0 ? sweep(0, start + scroller.clientHeight) : false;
}
"""


#: A grid tile's title (Flow names each medium), without icon ligatures or the ellipsis.
TILE_TITLE_JS = r"""
(img) => {
  const tile = img.closest('flow-image-tile, flow-video-tile');
  if (!tile) return null;
  const copy = tile.cloneNode(true);
  copy.querySelectorAll('mat-icon, img').forEach((e) => e.remove());
  const text = copy.textContent.replace(/\s+/g, ' ').replace(/…$/, '').trim();
  return text || null;
}
"""


#: The grid's rendered tiles, top first: ``[media_id, "image" | "video", title]``.
GRID_TILES_JS = r"""
() => {
  const seen = new Set();
  return [...document.querySelectorAll('flow-image-tile, flow-video-tile')].flatMap((tile) => {
    const holder = tile.querySelector('[data-media-id]');
    const id = holder && holder.getAttribute('data-media-id');
    if (!id || seen.has(id)) return [];
    seen.add(id);
    const copy = tile.cloneNode(true);
    copy.querySelectorAll('mat-icon, img').forEach((e) => e.remove());
    const title = copy.textContent.replace(/\s+/g, ' ').replace(/…$/, '').trim() || null;
    return [[id, tile.tagName.toLowerCase() === 'flow-video-tile' ? 'video' : 'image', title]];
  });
}
"""


def _asb_token(src: str) -> str | None:
    match = re.search(r"/asb/([^=?]+)", src)
    return match.group(1) if match else None


@dataclass
class _Reply:
    rpcs: list[str]
    status: int
    chunks: list[bytes] = field(default_factory=list)
    streaming: bool = False
    stream: asyncio.Future[None] | None = None


class ReplyWatch:
    """Collect the bodies of selected batchexecute replies while it is attached to a page.

    Replies are read through a DevTools session of its own that streams each reply's data
    as it arrives. Flow's page cancels some replies once it has read them (a project's media
    list, at times an image reply), and Chrome keeps no body for a cancelled request; read
    the usual way, such a reply looks as if it never came. Requests are never intercepted
    or changed. ``unreadable`` lists wanted replies whose body was lost all the same.
    """

    def __init__(self, page: Page, rpcs: Sequence[str] | None) -> None:
        self.page = page
        #: None means every batchexecute reply.
        self.rpcs = set(rpcs) if rpcs is not None else None
        self.bodies: list[tuple[str, int, str]] = []
        self.unreadable: list[str] = []
        self.changed = asyncio.Event()
        self._cdp: CDPSession | None = None
        self._replies: dict[str, _Reply] = {}
        self._tasks: list[asyncio.Future[None]] = []

    def _wanted(self, rpc: str) -> bool:
        return self.rpcs is None or rpc in self.rpcs

    def _on_response(self, event: dict[str, Any]) -> None:
        rpcs = [rpc for rpc in _rpcs(event["response"]["url"]) if self._wanted(rpc)]
        if not rpcs:
            return
        reply = _Reply(rpcs, event["response"]["status"])
        reply.stream = asyncio.ensure_future(self._stream(event["requestId"], reply))
        self._replies[event["requestId"]] = reply

    async def _stream(self, request_id: str, reply: _Reply) -> None:
        assert self._cdp is not None
        try:
            started = await self._cdp.send(
                "Network.streamResourceContent", {"requestId": request_id}
            )
        except Exception:  # already finished (its body is kept), or an older Chrome
            return
        reply.chunks.insert(0, base64.b64decode(started.get("bufferedData", "")))
        reply.streaming = True

    def _on_data(self, event: dict[str, Any]) -> None:
        reply = self._replies.get(event["requestId"])
        if reply is not None and event.get("data"):
            reply.chunks.append(base64.b64decode(event["data"]))

    def _on_end(self, event: dict[str, Any], finished: bool) -> None:
        reply = self._replies.pop(event["requestId"], None)
        if reply is not None:
            read = self._read(event["requestId"], reply, finished)
            self._tasks.append(asyncio.ensure_future(read))

    async def _read(self, request_id: str, reply: _Reply, finished: bool) -> None:
        assert self._cdp is not None
        if reply.stream is not None:
            await reply.stream
        text: str | None = None
        if reply.streaming:
            text = b"".join(reply.chunks).decode("utf-8", "replace")
        elif finished:
            try:
                body = await self._cdp.send("Network.getResponseBody", {"requestId": request_id})
                text = body["body"]
                if body["base64Encoded"]:
                    text = base64.b64decode(text).decode("utf-8", "replace")
            except Exception as exc:
                log.info("reply %s could not be read: %s", ",".join(reply.rpcs), exc)
        if text is None:
            self.unreadable.extend(reply.rpcs)
        else:
            self.bodies.extend((rpc, reply.status, text) for rpc in reply.rpcs)
        self.changed.set()

    async def __aenter__(self) -> ReplyWatch:
        self._cdp = await self.page.context.new_cdp_session(self.page)
        self._cdp.on("Network.responseReceived", self._on_response)
        self._cdp.on("Network.dataReceived", self._on_data)
        self._cdp.on("Network.loadingFinished", lambda event: self._on_end(event, True))
        self._cdp.on("Network.loadingFailed", lambda event: self._on_end(event, False))
        await self._cdp.send("Network.enable")
        return self

    async def __aexit__(self, *_: object) -> None:
        # Replies that already ended are read before the session goes.
        pending = [task for task in self._tasks if not task.done()]
        if pending:
            await asyncio.wait(pending, timeout=5)
        if self._cdp is not None:
            with contextlib.suppress(Exception):
                await self._cdp.detach()

    async def wait(self, timeout_s: float) -> bool:
        """Wait for the next reply; False on timeout."""
        self.changed.clear()
        try:
            await asyncio.wait_for(self.changed.wait(), timeout=timeout_s)
        except TimeoutError:
            return False
        return True


def _raise_for_refusal(exc: be.RpcError) -> None:
    if any(UNUSUAL in reason for reason in exc.reasons):
        raise UnusualActivity(str(exc)) from exc
    raise GenerationRefused(str(exc)) from exc


class FlowPage:
    def __init__(self, page: Page, project_id: str, session: ChromeSession | None = None) -> None:
        self.page = page
        self.project_id = project_id
        self._session = session
        #: Flow's display names of media, where known (uploads keep their file name), so
        #: the prompt box's picker can find them by search instead of scrolling the grid.
        self.names: dict[str, str] = {}
        #: Where media sit in the newest-first grid (0 top, 1 bottom), where known.
        self.positions: dict[str, float] = {}
        #: Flow's credit balance after the last video submit, when its reply says.
        self.credits_left: int | None = None

    @property
    def session(self) -> ChromeSession:
        if self._session is None:
            raise RuntimeError("this FlowPage was created without a Chrome session")
        return self._session

    @property
    def url(self) -> str:
        return f"{FLOW_ROOT}project/{self.project_id}"

    @classmethod
    async def open(cls, session: ChromeSession, project_id: str, role: str = "") -> FlowPage:
        """The project page in its own tab; ``role`` gives a second tab (e.g. downloads)."""
        page = await session.page_for(f"{FLOW_ROOT}project/{project_id}", role)
        flow = cls(page, project_id, session)
        await ensure_rendering(session, flow.page)
        # Anything but the project page itself (an edit view left by an interrupted job,
        # say) does not show the grid and prompt box StudioWright works with.
        if flow.page.url.split("?")[0].split("#")[0].rstrip("/") != flow.url:
            await flow.page.goto(flow.url, wait_until="domcontentloaded")
        await flow.ready()
        return flow

    # --- page state -------------------------------------------------------------------
    async def ready(self, timeout_s: float = 30) -> None:
        try:
            await self._leave_agent_mode(timeout_s)
            await self.page.locator(s.READY).first.wait_for(
                state="visible", timeout=timeout_s * 1000
            )
        except Exception as exc:
            raise FlowError(
                f"the Flow project did not become ready ({self.page.url}); "
                "is the profile signed in?"
            ) from exc
        await self.clear_blockers()

    async def _leave_agent_mode(self, timeout_s: float) -> None:
        """Close the agent panel and leave agent mode, where the account has them."""
        ready = self.page.locator(s.READY).first
        for _ in range(int(timeout_s * 2)):
            if await ready.is_visible():
                return
            for selector in (s.AGENT_PANEL_CLOSE, s.AGENT_CHIP_ON):
                button = self.page.locator(selector).first
                if await button.count() and await button.is_visible():
                    await button.click()
                    await self.page.wait_for_timeout(500)
            await self.page.wait_for_timeout(500)

    async def clear_blockers(self) -> None:
        for selector in (s.COOKIE_REJECT, s.DIALOG_CLOSE):
            button = self.page.locator(selector).first
            if await button.count() and await button.is_visible():
                await button.click()
                await self.page.wait_for_timeout(300)

    async def reload(self) -> None:
        await self.page.goto(self.url, wait_until="domcontentloaded")
        await self.ready()

    async def attachments(self) -> int:
        return (
            await self.page.locator(s.BOUND_CHIP).count()
            + await self.page.locator(s.MENTION_CHIP).count()
        )

    async def reset(self) -> None:
        """Close popups and empty the prompt box: text and every attached reference."""
        await self.page.keyboard.press("Escape")
        clear = self.page.locator(s.BOX_CLEAR).first
        if await clear.count() and await clear.is_visible():
            await clear.click()
        deadline = time.monotonic() + 3
        while await self.attachments() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if await self.attachments():
            await self.reload()

    # --- settings ---------------------------------------------------------------------
    async def chip_lines(self) -> list[str]:
        text = await self.page.locator(s.READY).first.inner_text()
        return [line.strip() for line in text.split("\n") if line.strip()]

    async def _settings_pane(self) -> Locator:
        pane = self.page.locator(s.SETTINGS_PANE)
        for _ in range(4):
            if await pane.count():
                return pane.first
            await self.page.mouse.move(5, 5)
            await self.page.locator(s.READY).first.click()
            try:
                await pane.first.wait_for(state="visible", timeout=1500)
            except Exception:
                await self.page.wait_for_timeout(300)
        await pane.first.wait_for(state="visible", timeout=3000)
        return pane.first

    async def _radio(
        self, pane: Locator, *, icon: str | None = None, text: str | re.Pattern[str] | None = None
    ) -> None:
        radios = pane.locator(s.RADIO)
        target = (
            radios.filter(has=self.page.locator(f"mat-icon:text-is('{icon}')"))
            if icon
            else radios.filter(has_text=text)
        )
        if not await target.count():
            raise FlowError(f"Flow offers no option {icon or text!r} here")
        radio = target.first
        if await radio.get_attribute("aria-checked") != "true":
            await radio.click()
            await self.page.wait_for_timeout(150)

    async def _model(self, pane: Locator, label: str) -> None:
        button = pane.locator(s.MODEL_MENU_BUTTON).first
        current = " ".join((await button.inner_text()).replace("arrow_drop_down", "").split())
        if current.endswith(label):
            return
        await button.click()
        item = self.page.locator(s.MENU_ITEM).filter(
            has_text=re.compile(re.escape(label) + r"\s*$")
        )
        try:
            await item.first.wait_for(state="visible", timeout=5000)
        except Exception as exc:
            await self.page.keyboard.press("Escape")
            raise FlowError(f"the model {label!r} is not offered on this account") from exc
        await item.first.click()
        await self.page.wait_for_timeout(200)

    async def apply_image_options(self, options: ImageOptions) -> None:
        lines = await self.chip_lines()
        if (
            len(lines) >= 3
            and lines[0].endswith(options.model.label)
            and lines[1] == options.aspect.icon
            and lines[2] == f"x{options.count}"
        ):
            return
        pane = await self._settings_pane()
        await self._radio(pane, icon=s.MODE_IMAGE)
        await self._model(pane, options.model.label)
        await self._radio(pane, icon=options.aspect.icon)
        await self._radio(pane, text=count_pattern(options.count))
        await self.page.keyboard.press("Escape")
        await self.page.wait_for_timeout(250)

    async def apply_video_options(self, options: VideoOptions) -> int | None:
        """Set every video option; returns the credit price the pane shows, if readable."""
        pane = await self._settings_pane()
        await self._radio(pane, icon=s.MODE_VIDEO)
        await self._radio(pane, icon=options.mode.icon)
        await self._model(pane, options.model.label)
        await self._radio(pane, icon=options.aspect.icon)
        if options.resolution:
            await self._radio(pane, text=options.resolution)
        if options.duration:
            await self._radio(pane, text=duration_pattern(options.duration))
        await self._radio(pane, text=count_pattern(options.count))
        await self.page.wait_for_timeout(200)
        price = credit_price(await pane.inner_text())
        await self.page.keyboard.press("Escape")
        await self.page.wait_for_timeout(250)
        return price

    # --- media ------------------------------------------------------------------------
    async def _open_file_chooser(self) -> FileChooser:
        """Ctrl+U opens the upload chooser, but not while the prompt box has focus (there it
        means underline). Leave the box first; fall back to the toolbar's add menu."""
        await self.page.keyboard.press("Escape")
        await self.page.evaluate("() => document.activeElement && document.activeElement.blur()")
        try:
            async with self.page.expect_file_chooser(timeout=2500) as chooser_info:
                await self.page.keyboard.press("Control+U")
            return await chooser_info.value
        except Exception:
            pass
        await self.page.locator(s.TOOLBAR_ADD).first.click()
        item = self.page.locator(s.MENU_UPLOAD).first
        await item.wait_for(state="visible", timeout=5000)
        async with self.page.expect_file_chooser(timeout=8000) as chooser_info:
            await item.click()
        return await chooser_info.value

    async def upload(
        self, files: Sequence[Path], timeout_s: float = 90
    ) -> list[replies.UploadResult]:
        """Upload files through one file chooser, in the order given."""
        log.info("upload %d file(s)", len(files))
        async with ReplyWatch(self.page, [UPLOAD_RPC]) as watch:
            chooser = await self._open_file_chooser()
            await chooser.set_files([str(f) for f in files])
            deadline = time.monotonic() + timeout_s
            while len(watch.bodies) < len(files) and time.monotonic() < deadline:
                if await self.page.locator("[role='dialog']").count():
                    raise FlowError(
                        "Flow is asking for upload consent; answer it once in the Chrome window"
                    )
                await watch.wait(1.0)
        results: list[replies.UploadResult] = []
        for _rpc_id, _status, text in watch.bodies:
            try:
                results.append(replies.upload_result(be.parse_reply(text, UPLOAD_RPC)))
            except be.RpcError as exc:
                _raise_for_refusal(exc)
        if len(results) < len(files):
            raise PartialUpload(f"only {len(results)} of {len(files)} uploads finished", results)
        await self._wait_for_tiles([r.media_id for r in results])
        by_name = {r.name: r for r in results}
        ordered = [by_name.get(Path(f).name) for f in files]
        return [r for r in ordered if r] if all(ordered) else results

    async def _wait_for_tiles(self, media_ids: Sequence[str], timeout_s: float = 60) -> None:
        """Wait until new uploads have their own grid tiles.

        Flow first shows an upload as a "100%" placeholder without its media id, so the
        tile menus (start frame, add to prompt) cannot reach it yet. In a new project the
        placeholder stayed for over 10 s. New tiles appear at the top of the grid.
        """
        started = time.monotonic()
        waiting = list(media_ids)
        while True:
            waiting = [m for m in waiting if not await self.page.locator(s.media_image(m)).count()]
            if not waiting:
                break
            if time.monotonic() - started > timeout_s:
                log.warning("uploads still without a tile after %.0f s: %s", timeout_s, waiting)
                return
            await self.page.evaluate(SCROLL_TOP_JS, s.GRID_SCROLLER)
            await asyncio.sleep(0.5)
        log.info("uploads on the grid after %.1f s", time.monotonic() - started)

    async def _media_img(self, media_id: str) -> Locator:
        """The grid image of ``media_id``, scrolling the grid until it is rendered."""
        img = self.page.locator(s.media_image(media_id)).first
        if await img.count():
            return img
        started = time.monotonic()
        hint = self.positions.get(media_id)
        found = await self.page.evaluate(SCAN_JS, [s.media_image(media_id), s.GRID_SCROLLER, hint])
        if found:
            log.info("found %s on the grid in %.1f s", media_id, time.monotonic() - started)
            if await img.count():
                return img
        raise AssetNotFound(f"media {media_id} is not on the project grid")

    async def _pick_by_name(self, media_id: str, slot: Locator | None = None) -> bool:
        """Attach ``media_id`` from the prompt box's picker by searching its display name.

        Only used when the name is known and unique (uploads are named after their
        content hash), which avoids scrolling a large grid. ``slot`` opens a frame slot's
        picker instead of the add button's. False when the picker does not offer exactly
        one match; nothing is attached then.
        """
        name = self.names.get(media_id)
        if not name:
            return False
        await (slot or self.page.locator(s.PICKER_OPEN).first).click()
        search = self.page.locator(s.PICKER_SEARCH).first
        try:
            await search.wait_for(state="visible", timeout=5000)
        except Exception:
            await self.page.keyboard.press("Escape")
            return False
        await search.fill(name)
        items = self.page.locator(s.PICKER_ITEM).filter(has_text=name)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and await items.count() != 1:
            await asyncio.sleep(0.2)
        if await items.count() != 1:
            await self.page.keyboard.press("Escape")
            return False
        await items.first.click()
        add = self.page.locator(s.PICKER_ADD)
        with contextlib.suppress(Exception):
            await add.first.wait_for(state="visible", timeout=1500)
        if await add.count():
            await add.first.click()
        log.info("attached %s (%s) from the picker", media_id, name)
        return True

    async def _tile_menu(self, media_id: str, item_selector: str) -> None:
        img = await self._media_img(media_id)
        tile = img.locator(f"xpath=ancestor::{s.IMAGE_TILE}[1]")
        await img.hover()
        await tile.locator(s.TILE_MENU).first.click()
        item = self.page.locator(item_selector).first
        await item.wait_for(state="visible", timeout=5000)
        await item.click()

    async def _wait_attachments(self, at_least: int, timeout_s: float = 6) -> None:
        deadline = time.monotonic() + timeout_s
        while await self.attachments() < at_least:
            if time.monotonic() > deadline:
                raise FlowError("the reference did not attach to the prompt box")
            await asyncio.sleep(0.05)

    async def attach_reference(self, media_id: str) -> None:
        log.info("attach reference %s", media_id)
        before = await self.attachments()
        visible = await self.page.locator(s.media_image(media_id)).count()
        if not visible and await self._pick_by_name(media_id):
            await self._wait_attachments(before + 1)
            return
        await self._tile_menu(media_id, s.MENU_ADD_TO_PROMPT)
        await self._wait_attachments(before + 1)

    async def set_start_frame(self, media_id: str) -> None:
        """Animate from this image: switches to video / Frames with it as the start frame."""
        log.info("start frame %s", media_id)
        await self._tile_menu(media_id, s.MENU_ANIMATE)
        await self._wait_attachments(1)

    async def set_end_frame(self, media_id: str) -> None:
        log.info("end frame %s", media_id)
        before = await self.attachments()
        visible = await self.page.locator(s.media_image(media_id)).count()
        if not visible and await self._pick_by_name(media_id, self.page.locator(s.EMPTY_SLOT).last):
            await self._wait_attachments(before + 1)
            return
        img = await self._media_img(media_id)
        # A freshly generated medium's thumbnail can still be loading; give it a moment.
        token = None
        deadline = time.monotonic() + 10
        while token is None:
            token = _asb_token(await img.get_attribute("src") or "")
            if token is None:
                if time.monotonic() > deadline:
                    break
                with contextlib.suppress(Exception):
                    await img.scroll_into_view_if_needed(timeout=1000)
                await asyncio.sleep(0.25)
        title = await img.evaluate(TILE_TITLE_JS)
        if token is None and not title:
            raise AssetNotFound(f"media {media_id} has no thumbnail or title to find it by")
        before = await self.attachments()
        await self.page.locator(s.EMPTY_SLOT).last.click()
        items = self.page.locator(s.PICKER_ITEM)
        await items.first.wait_for(state="visible", timeout=5000)
        if token:
            match = items.filter(has=self.page.locator(f"img[src*='{token}']"))
        else:
            # No thumbnail to compare: accept the title only when it is unambiguous.
            match = items.filter(has_text=title)
        if not await match.count() and title:
            # The picker lists recent media first; search older ones by their title.
            await self.page.locator(s.PICKER_SEARCH).first.fill(title)
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline and not await match.count():
                await asyncio.sleep(0.2)
        if token is None and await match.count() != 1:
            await self.page.keyboard.press("Escape")
            raise AssetNotFound(f"media {media_id} could not be told apart in the frame picker")
        if not await match.count():
            await self.page.keyboard.press("Escape")
            raise AssetNotFound(f"media {media_id} is not offered in the frame picker")
        await match.first.click()
        await self.page.locator(s.PICKER_ADD).first.click()
        await self._wait_attachments(before + 1)

    # --- generate ---------------------------------------------------------------------
    async def _type_and_submit(self, prompt: str) -> None:
        composer = self.page.locator(s.COMPOSER).first
        await composer.click()
        await self.page.keyboard.press("Control+End")
        await self.page.keyboard.insert_text(prompt)
        submit = self.page.locator(s.SUBMIT).first
        deadline = time.monotonic() + 5
        while await submit.is_disabled():
            if await self.page.locator(s.CREDITS_WARNING).count():
                raise FlowError("Flow reports there are not enough credits for this generation")
            if time.monotonic() > deadline:
                raise FlowError("the generate button stayed disabled")
            await asyncio.sleep(0.05)
        await submit.click()

    async def generate_images(
        self, prompt: str, count: int = 1, timeout_s: float | None = None
    ) -> list[replies.ImageResult]:
        """Submit and return the new images.

        Flow answers with ``ogiZ0b``. If no readable answer arrives in time (the page can
        cancel it once read, which once looked like a lost reply), the new images are taken
        from the project's media list instead and marked ``recovered``.
        """
        if timeout_s is None:
            timeout_s = env_seconds("STUDIOWRIGHT_IMAGE_TIMEOUT_S", 240)
        before = await self.grid_media_ids()
        submitted = time.time()
        async with ReplyWatch(self.page, [IMAGE_RPC]) as watch:
            await self._type_and_submit(prompt)
            log.info("image submitted; waiting for Flow's reply")
            deadline = time.monotonic() + timeout_s
            while not watch.bodies and not watch.unreadable and time.monotonic() < deadline:
                await watch.wait(1.0)
        if watch.bodies:
            _rpc_id, status, text = watch.bodies[0]
            try:
                payload = be.parse_reply(text, IMAGE_RPC)
            except be.RpcError as exc:
                _raise_for_refusal(exc)
                raise
            except be.MissingReply as exc:
                raise FlowError(f"unreadable image reply (HTTP {status})") from exc
            results = replies.image_results(payload)
            if not results:
                raise GenerationRefused("Flow answered without any image")
            return results
        why = (
            "Flow's image reply could not be read"
            if watch.unreadable
            else f"no image reply within {timeout_s:.0f} s"
        )
        log.warning("%s; looking for the new image in the project", why)
        found = replies.new_media(before, await self.list_media(), submitted - CLOCK_SLACK_S, count)
        results = []
        for item in found:
            urls = await self.edit_view_urls(item.media_id)
            if "image" in urls:
                results.append(replies.ImageResult(item.media_id, urls["image"], recovered=True))
        await self.reload()
        if not results:
            raise FlowError(f"{why}, and no new image is in the project")
        return results

    async def grid_media_ids(self) -> set[str]:
        """The media ids of the tiles the grid shows now (the newest, near the top)."""
        return {media_id for media_id, _kind, _title in await self.page.evaluate(GRID_TILES_JS)}

    async def list_media(self) -> list[replies.MediaItem]:
        """The project's media, newest first.

        Read from the list Flow loads with the project page (the page is reloaded for it);
        when that reply is missing, from the grid's tiles near the top instead.
        """
        async with ReplyWatch(self.page, [LIST_RPC]) as watch:
            await self.page.goto(self.url, wait_until="domcontentloaded")
            await self.ready()
            deadline = time.monotonic() + 20
            while not watch.bodies and not watch.unreadable and time.monotonic() < deadline:
                await watch.wait(0.5)
        for _rpc_id, _status, text in watch.bodies:
            try:
                items = replies.project_media(be.parse_reply(text, LIST_RPC), self.project_id)
            except (be.RpcError, be.MissingReply):
                continue
            if items:
                return items
        log.info("no readable media list; reading the grid's tiles instead")
        await self.page.evaluate(SCROLL_TOP_JS, s.GRID_SCROLLER)
        await asyncio.sleep(0.5)
        return [
            replies.MediaItem(media_id, kind, title)
            for media_id, kind, title in await self.page.evaluate(GRID_TILES_JS)
        ]

    async def generate_video(
        self,
        prompt: str,
        timeout_s: float = 600,
        *,
        on_ids: Callable[[list[str]], None] | None = None,
    ) -> list[VideoClip]:
        """Submit and follow the clip(s) until their signed URLs arrive.

        Flow's submit RPC differs per mode and can change, so the new clips are recognised
        by shape: any record of this project that appears queued or running after the click.
        ``on_ids`` receives a snapshot whenever a new media id is accepted, before URLs
        arrive. Observer errors are logged without interrupting the generation.
        """
        async with ReplyWatch(self.page, None) as watch:
            await self._type_and_submit(prompt)
            log.info("video submitted; following the project's replies")
            deadline = time.monotonic() + timeout_s
            new_ids: set[str] = set()
            records: dict[str, replies.VideoRecord] = {}
            urls: dict[str, dict[str, str]] = {}
            seen = 0
            finished_at: float | None = None
            while time.monotonic() < deadline:
                await watch.wait(2.0)
                for rpc_id, _status, text in watch.bodies[seen:]:
                    seen += 1
                    try:
                        payload = be.parse_reply(text, rpc_id)
                    except be.RpcError as exc:
                        if rpc_id in VIDEO_SUBMIT_RPCS or not new_ids:
                            log.info("refusal in %s: %s", rpc_id, exc)
                            if any(UNUSUAL in reason for reason in exc.reasons):
                                raise UnusualActivity(str(exc)) from exc
                            if rpc_id in VIDEO_SUBMIT_RPCS:
                                raise GenerationRefused(str(exc)) from exc
                        continue
                    except be.MissingReply:
                        continue
                    if rpc_id in VIDEO_SUBMIT_RPCS:
                        balance = replies.credit_balance(payload)
                        if balance is not None:
                            self.credits_left = balance
                            log.info("credits left: %s", balance)
                    for record in replies.video_records(payload, self.project_id):
                        active = record.status in (replies.STATUS_QUEUED, replies.STATUS_RUNNING)
                        if record.media_id not in new_ids and (
                            active or rpc_id in VIDEO_SUBMIT_RPCS
                        ):
                            new_ids.add(record.media_id)
                            log.info("clip %s accepted via %s", record.media_id, rpc_id)
                            if on_ids is not None:
                                try:
                                    on_ids(sorted(new_ids))
                                except Exception:
                                    log.exception("could not report accepted clip ids")
                        if record.media_id in new_ids:
                            previous = records.get(record.media_id)
                            if previous is None or previous.status != record.status:
                                log.info("clip %s status %s", record.media_id, record.status)
                            records[record.media_id] = record
                    for media_id, found in replies.media_urls(payload).items():
                        urls.setdefault(media_id, {}).update(found)
                done = bool(new_ids) and all(
                    records.get(i) and records[i].status == replies.STATUS_DONE for i in new_ids
                )
                if done and finished_at is None:
                    finished_at = time.monotonic()
                if (
                    finished_at is not None
                    and time.monotonic() - finished_at > 8
                    and not all("video" in urls.get(i, {}) for i in new_ids)
                ):
                    # Some modes never fetch the finished clip's URL on their own; opening
                    # the edit view makes the page fetch fresh URLs for every medium.
                    log.info("clip done; fetching its URL through the edit view")
                    for media_id, found in (await self.fresh_media_urls()).items():
                        urls.setdefault(media_id, {}).update(found)
                if new_ids and all("video" in urls.get(i, {}) for i in new_ids):
                    log.info("clip URLs received")
                    return [
                        VideoClip(
                            i,
                            urls[i]["video"],
                            urls[i].get("image"),
                            records[i].workflow_id if i in records else urls[i].get("workflow"),
                        )
                        for i in sorted(new_ids)
                    ]
                failed = [
                    r
                    for r in records.values()
                    if r.status
                    not in (
                        None,
                        replies.STATUS_QUEUED,
                        replies.STATUS_RUNNING,
                        replies.STATUS_DONE,
                    )
                ]
                if failed:
                    # Flow writes its reason (a policy refusal, say) on the failed tile.
                    await asyncio.sleep(2)
                    with contextlib.suppress(Exception):
                        await self.page.evaluate(SCROLL_TOP_JS, s.GRID_SCROLLER)
                    reasons: list[str] = []
                    with contextlib.suppress(Exception):
                        reasons = await self.page.evaluate(FAILED_TILES_JS)
                    reason = f": {reasons[0]}" if reasons else ""
                    raise GenerationRefused(
                        f"Flow reported the clip failed (status {failed[0].status}){reason}"
                    )
        pending = " ".join(sorted(new_ids))
        hint = f"; it may still finish: sw download {pending}" if pending else ""
        raise FlowError(f"the clip was not ready within {timeout_s:.0f} s{hint}")

    async def fresh_media_urls(self) -> dict[str, dict[str, str]]:
        """Signed URLs for every medium in the project, keyed by media id.

        Opening any tile's edit view makes the page fetch fresh URLs for the whole project
        (``as29s``); they are read from that reply, then the page returns to the grid.
        """
        tile = self.page.locator(f"{s.VIDEO_TILE}, {s.IMAGE_TILE}").first
        if not await tile.count():
            raise AssetNotFound("the project has no media yet")
        urls: dict[str, dict[str, str]] = {}
        async with ReplyWatch(self.page, ["as29s"]) as watch:
            await tile.click()
            await self.page.wait_for_url(re.compile(r".*/edit/.*"), timeout=10_000)
            # The page answers in several batches; wait until the replies go quiet.
            started = last = time.monotonic()
            while time.monotonic() - started < 20:
                if await watch.wait(0.5):
                    last = time.monotonic()
                elif watch.bodies and time.monotonic() - last > 2.5:
                    break
        for rpc_id, _status, text in watch.bodies:
            try:
                payload = be.parse_reply(text, rpc_id)
            except (be.RpcError, be.MissingReply):
                continue
            for media_id, found in replies.media_urls(payload).items():
                urls.setdefault(media_id, {}).update(found)
            for media_id, workflow_id in replies.workflow_ids(payload).items():
                urls.setdefault(media_id, {}).setdefault("workflow", workflow_id)
        await self.page.goto(self.url, wait_until="domcontentloaded")
        await self.ready()
        return urls

    async def edit_view_urls(self, media_id: str) -> dict[str, str]:
        """Fresh signed URLs of one medium, from its own edit view (no grid needed)."""
        found: dict[str, str] = {}
        async with ReplyWatch(self.page, ["as29s"]) as watch:
            await self.page.goto(f"{self.url}/edit/{media_id}", wait_until="domcontentloaded")
            started = last = time.monotonic()
            while time.monotonic() - started < 20:
                if await watch.wait(0.5):
                    last = time.monotonic()
                elif watch.bodies and time.monotonic() - last > 2.0:
                    break
        for rpc_id, _status, text in watch.bodies:
            try:
                payload = be.parse_reply(text, rpc_id)
            except (be.RpcError, be.MissingReply):
                continue
            found.update(replies.media_urls(payload).get(media_id, {}))
        return found

    async def rename(self, media_id: str, title: str) -> None:
        """Set one medium's display name through its edit view's title field.

        Flow saves the name on Enter (the grid and the media list show it); the edit view itself
        can keep showing the old name until the project is opened again (seen 2026-10-26).
        """
        await self.page.goto(f"{self.url}/edit/{media_id}", wait_until="domcontentloaded")
        box = self.page.locator(s.EDIT_TITLE).first
        try:
            await box.wait_for(timeout=30_000)
        except PlaywrightTimeout as exc:
            raise AssetNotFound(f"no title field in the edit view of {media_id}") from exc
        await box.click()
        await box.fill(title)
        await box.press("Enter")
        # Give the save request time to leave before the next navigation (the last of a run
        # was lost once when the tab left after 1.5 s), then blur the field as a second save.
        await self.page.wait_for_timeout(2500)
        with contextlib.suppress(Exception):
            await box.blur()
        await self.page.wait_for_timeout(1000)

    async def download_size(
        self, item_id: str, size: str, folder: Path, timeout_s: float = 900, *, back: bool = True
    ) -> Path:
        """Download through the edit view's own menu, e.g. ``2K`` or ``1080p`` (upscaled).

        The size token is the start of the menu entry, which Flow does not translate.
        Upscaling happens in Flow and can take a while before the file arrives.
        """
        # The edit view opens by media id or by workflow id alike.
        await self.page.goto(f"{self.url}/edit/{item_id}", wait_until="domcontentloaded")
        button = self.page.locator(s.EDIT_DOWNLOAD).first
        try:
            await button.wait_for(state="visible", timeout=15_000)
            # The button stays disabled until the edit view has loaded the medium.
            deadline = time.monotonic() + 45
            while await button.is_disabled():
                if time.monotonic() > deadline:
                    raise FlowError("the download button stayed disabled in the edit view")
                await asyncio.sleep(0.25)
            async with DownloadCatcher(self.session, folder) as catcher:
                await button.click()
                # Uploads have no size menu: the button saves the original straight away.
                if await catcher.started(2.5):
                    saved = await catcher.saved(timeout_s)
                    if size.upper() in ("1K", "ORIGINAL"):
                        return saved
                    saved.unlink(missing_ok=True)
                    raise FlowError(
                        f"Flow offers no {size} for {item_id}: it downloads only at its own "
                        "size (uploaded images are not upscaled)"
                    )
                entries = self.page.locator(f"{s.OVERLAY} {s.MENU_ITEM}, {s.OVERLAY} button")
                await entries.first.wait_for(state="visible", timeout=5000)
                entry = entries.filter(
                    has_text=re.compile(rf"^\s*{re.escape(size)}(?![0-9A-Za-z])", re.IGNORECASE)
                )
                if not await entry.count():
                    offered = [" ".join(t.split()) for t in await entries.all_inner_texts()]
                    raise FlowError(f"size {size!r} is not offered; Flow offers: {offered}")
                if await entry.first.is_disabled():
                    raise FlowError(f"size {size!r} is not available on this account")
                async with ReplyWatch(self.page, [UPSCALE_RPC]) as upscale:
                    await entry.first.click()
                    # Upscaling runs in Flow first: seconds for an image, minutes for a 4K
                    # clip. Flow can also refuse it, which shows only in the RPC's reply.
                    deadline = time.monotonic() + UPSCALE_START_S
                    while not await catcher.started(1.0):
                        for rpc_id, _status, text in upscale.bodies:
                            try:
                                be.parse_reply(text, rpc_id)
                            except be.RpcError as exc:
                                unusual = any(UNUSUAL in r for r in exc.reasons)
                                why = "unusual activity" if unusual else str(exc)
                                raise UpscaleRefused(
                                    f"Flow refused the {size} upscale of {item_id}: {why}",
                                    unusual,
                                ) from exc
                            except be.MissingReply:
                                continue
                        if time.monotonic() > deadline:
                            raise FlowError(
                                f"Flow did not start the {size} download within {UPSCALE_START_S} s"
                            )
                return await catcher.saved(timeout_s)
        finally:
            # Several downloads in a row skip the (slow, in a large project) grid between them.
            if back:
                await self.page.goto(self.url, wait_until="domcontentloaded")
                await self.ready()

    async def download(self, url: str, target: Path) -> Path:
        response = await self.page.context.request.get(url)
        if response.status != 200:
            raise FlowError(f"download failed with HTTP {response.status}")
        body = await response.body()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        return target


_PRICE = re.compile(
    r"(\d+)\s*(?:kredi|credits?|crédits?|créditos?|Credits?|Kredite?)", re.IGNORECASE
)


def credit_price(pane_text: str) -> int | None:
    """The credit cost the settings pane states, e.g. "uses 12 credits" / "12 kredi"."""
    matches = _PRICE.findall(pane_text)
    return int(matches[-1]) if matches else None


def suffix_for(body_start: bytes, default: str) -> str:
    if body_start[:4] == b"\x89PNG":
        return ".png"
    if body_start[:2] == b"\xff\xd8":
        return ".jpg"
    if body_start[4:8] == b"ftyp":
        return ".mp4"
    return default
