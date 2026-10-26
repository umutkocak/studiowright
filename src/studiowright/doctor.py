"""``sw doctor``: check that every part of Flow's page StudioWright relies on is still there.

Google changes Flow's UI without notice. This finds a moved button before a production run
does. Nothing is submitted, uploaded, renamed or downloaded: menus and panes are opened, read
and closed again, and the project page is reloaded at most twice.
"""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from playwright.async_api import Page

from studiowright import paths
from studiowright.flow import selectors as s
from studiowright.flow.options import (
    COUNTS,
    Aspect,
    ImageModel,
    VideoModel,
    VideoOptions,
    count_pattern,
)
from studiowright.flow.page import GRID_TILES_JS, SCROLL_TOP_JS, FlowError, FlowPage

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str = ""
    seconds: float = 0.0


Probe = Callable[[], Awaitable[str | tuple[str, str]]]


@dataclass
class Doctor:
    checks: list[Check] = field(default_factory=list)
    #: The page to photograph when a check fails, once one is open.
    page: Page | None = None
    screenshots: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(check.status == FAIL for check in self.checks)

    def add(self, name: str, status: str, detail: str) -> None:
        self.checks.append(Check(name, status, detail))

    async def run(self, name: str, probe: Probe) -> bool:
        """Run one check; an exception is a failure. False when the check failed."""
        started = time.monotonic()
        try:
            outcome = await probe()
        except Exception as exc:
            text = str(exc).strip()
            detail = text.splitlines()[0] if text else type(exc).__name__
            self.checks.append(Check(name, FAIL, detail, round(time.monotonic() - started, 1)))
            await self._photograph(name)
            return False
        status, detail = outcome if isinstance(outcome, tuple) else (OK, outcome)
        self.checks.append(Check(name, status, detail, round(time.monotonic() - started, 1)))
        return status != FAIL

    async def _photograph(self, name: str) -> None:
        if self.page is None:
            return
        try:
            paths.incidents_dir().mkdir(parents=True, exist_ok=True)
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            shot = paths.incidents_dir() / f"doctor-{time.strftime('%Y%m%d-%H%M%S')}-{slug}.png"
            await self.page.screenshot(path=str(shot))
            self.screenshots.append(str(shot))
        except Exception:
            pass


def missing_labels(offered: Sequence[str], wanted: Sequence[str]) -> list[str]:
    """Model labels with no menu entry ending in them (entries may start with an icon)."""
    return [
        label
        for label in wanted
        if not any(re.search(re.escape(label) + r"\s*$", entry) for entry in offered)
    ]


async def check_project_page(doctor: Doctor, flow: FlowPage) -> None:
    """Every check that needs the project page; ``flow`` is open and ready."""
    page = flow.page

    async def need(selector: str, what: str, timeout_s: float = 5) -> None:
        try:
            await page.locator(selector).first.wait_for(state="attached", timeout=timeout_s * 1000)
        except Exception as exc:
            raise FlowError(f"{what} is missing ({selector})") from exc

    async def close_popups() -> None:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(250)

    async def prompt_box() -> str:
        await need(s.COMPOSER, "the prompt text field")
        await need(s.SUBMIT, "the generate button")
        # The picker button is there in image mode; a video "frames" box has start/end slots.
        await page.locator(s.PICKER_OPEN).first.click(timeout=5000)
        await need(s.PICKER_SEARCH, "the media picker's search field")
        await close_popups()
        return "text field, generate button, media picker with search"

    async def offered_models(mode_icon: str) -> list[str]:
        pane = await flow._settings_pane()
        await flow._radio(pane, icon=mode_icon)
        await pane.locator(s.MODEL_MENU_BUTTON).first.click()
        items = page.locator(s.MENU_ITEM)
        await items.first.wait_for(state="visible", timeout=5000)
        offered = [" ".join(text.split()) for text in await items.all_inner_texts()]
        await close_popups()
        return offered

    async def image_options() -> str:
        pane = await flow._settings_pane()
        await flow._radio(pane, icon=s.MODE_IMAGE)
        radios = pane.locator(s.RADIO)
        missing = [
            aspect.value
            for aspect in Aspect
            if not await radios.filter(
                has=page.locator(f"mat-icon:text-is('{aspect.icon}')")
            ).count()
        ]
        for n in COUNTS:
            if not await radios.filter(has_text=count_pattern(n)).count():
                missing.append(f"x{n}")
        await close_popups()
        if missing:
            raise FlowError(f"image options missing: {', '.join(missing)}")
        return f"{len(Aspect)} aspects, 1-{max(COUNTS)} images"

    async def models(mode_icon: str, wanted: list[str]) -> str | tuple[str, str]:
        missing = missing_labels(await offered_models(mode_icon), wanted)
        if missing:
            return WARN, f"not offered on this account: {', '.join(missing)}"
        return f"all {len(wanted)} offered"

    async def video_options() -> str | tuple[str, str]:
        # Sets the default clip options (frames, Omni, 16:9, 720p, 8 s, x1) and reads the price.
        price = await flow.apply_video_options(VideoOptions())
        if price is None:
            return WARN, "options set, but the credit price could not be read"
        return f"modes, Omni options and the price ({price} credits for the default clip)"

    async def grid() -> str | tuple[str, str]:
        await page.evaluate(SCROLL_TOP_JS, s.GRID_SCROLLER)
        await page.wait_for_timeout(400)
        tiles = await page.evaluate(GRID_TILES_JS)
        image = next((tile for tile in tiles if tile[1] == "image"), None)
        if image is None:
            return WARN, "no image in the project; the tile menu was not checked"
        img = await flow._media_img(image[0])
        await img.hover()
        await img.locator(f"xpath=ancestor::{s.IMAGE_TILE}[1]").locator(s.TILE_MENU).first.click()
        await need(s.MENU_ADD_TO_PROMPT, "the tile menu's 'add to prompt'")
        missing = [
            what
            for what, selector in (("animate", s.MENU_ANIMATE), ("download", s.MENU_DOWNLOAD))
            if not await page.locator(selector).count()
        ]
        await close_popups()
        if missing:
            raise FlowError(f"the tile menu has no {' or '.join(missing)} entry")
        return f"{len(tiles)} tiles shown; tile menu has add to prompt, animate, download"

    async def upload_menu() -> str:
        await page.locator(s.TOOLBAR_ADD).first.click()
        await need(s.MENU_UPLOAD, "the add menu's upload entry")
        await close_popups()
        return "the toolbar's add menu offers upload"

    media_id: str | None = None

    async def media_list() -> str | tuple[str, str]:
        nonlocal media_id
        items = await flow.list_media()
        media_id = next((i.media_id for i in items if i.kind == "image"), None)
        if not items:
            return WARN, "the project lists no media"
        if items[0].created is None:
            return WARN, "Flow's media list reply was not readable; read from the grid instead"
        return f"{len(items)} media read from Flow's own list"

    async def edit_view() -> str | tuple[str, str]:
        if media_id is None:
            return WARN, "no image in the project; the edit view was not checked"
        try:
            urls = await flow.edit_view_urls(media_id)
            await need(s.EDIT_DOWNLOAD, "the edit view's download button", 15)
            await need(s.EDIT_TITLE, "the edit view's title field")
        finally:
            await flow.reload()
        if "image" not in urls:
            raise FlowError("the edit view's replies carried no signed image URL")
        return "download button, title field and a signed URL"

    async def step(name: str, probe: Probe) -> None:
        if not await doctor.run(name, probe):
            await close_popups()  # a failed check can leave a menu or pane open

    await flow.reset()
    await step("image options", image_options)  # also leaves the prompt box in image mode
    await step("prompt box", prompt_box)
    await step("image models", lambda: models(s.MODE_IMAGE, [m.label for m in ImageModel]))
    await step("video options and price", video_options)
    await step("video models", lambda: models(s.MODE_VIDEO, [m.label for m in VideoModel]))
    await flow.reset()
    await step("grid and tile menu", grid)
    await step("upload menu", upload_menu)
    await step("media list", media_list)
    await step("edit view", edit_view)
