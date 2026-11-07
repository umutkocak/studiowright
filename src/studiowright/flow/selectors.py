"""Locale-proof handles for Flow's UI: icon ligatures, roles, component tags and classes.

Visible text and aria-labels are translated, so they are never used except for tokens
Flow does not translate (model names, ``x2``, ``720p``, ``2K``). Measured on
flow.google.com on 2026-10-10.
"""

from __future__ import annotations


def icon(name: str) -> str:
    """Anything containing the Material icon ligature ``name``."""
    return f":has(mat-icon:text-is('{name}'))"


READY = ".settings-trigger-button"
#: Newer Flow accounts (seen 15 Oct 2026) open projects in "agent" mode: a side chat panel and an
#: "Agent" chip in the prompt box. The classic settings button appears once both are off.
AGENT_PANEL_CLOSE = f"flow-agent-panel button.header-action{icon('close')}"
AGENT_CHIP_ON = "button.agent-mode-chip.agent-mode-chip-checked"
OVERLAY = ".cdk-overlay-pane"
SETTINGS_PANE = f"{OVERLAY}:has([role='radiogroup'])"
RADIO = "[role='radio']"
MENU_ITEM = "[role='menuitem']"
MODEL_MENU_BUTTON = f"button{icon('arrow_drop_down')}"
#: A model menu entry's name; a caption beside it may say when the model retires.
MODEL_LABEL = "span.label"
MODEL_CAPTION = "span.caption"
#: Omni 1.2 picks its length (3-15 s) with a slider instead of radios (seen 2026-11-07).
DURATION_SLIDER = "flow-duration-slider input[type='range']"
DURATION_VALUE = "flow-duration-slider .duration-slider-value"

BOX = "flow-prompt-box"
COMPOSER = f"{BOX} [contenteditable='true']"
BOX_CLEAR = f"{BOX} button{icon('close')}"
SUBMIT = f"{BOX} button{icon('arrow_forward')}"
BOUND_CHIP = f"{BOX} button.chip-container:has(img)"
MENTION_CHIP = f"{BOX} .mention-chip"
EMPTY_SLOT = f"{BOX} button.empty-chip"

PICKER_ITEM = f"{OVERLAY} .asset-item"
#: The prompt box's own add button: a picker of the project's media, with search.
PICKER_OPEN = f"{BOX} button{icon('add')}"
PICKER_SEARCH = f"{OVERLAY} input"
PICKER_ADD = f"{OVERLAY} button.detail-add-to-prompt-btn"

#: The project grid is a virtual scroller: only tiles near the viewport exist.
GRID_SCROLLER = ".cdk-virtual-scrollable"
IMAGE_TILE = "flow-image-tile"
VIDEO_TILE = "flow-video-tile"
TILE_MENU = f"button{icon('more_vert')}"
MENU_ADD_TO_PROMPT = f"{OVERLAY} {MENU_ITEM}{icon('add_2')}"
MENU_ANIMATE = f"{OVERLAY} {MENU_ITEM}{icon('motion_blur')}"
MENU_DOWNLOAD = f"{OVERLAY} {MENU_ITEM}{icon('download')}"
#: The page toolbar's add button (not the one inside the prompt box) and its upload entry.
TOOLBAR_ADD = f"xpath=//button[.//mat-icon[normalize-space()='add']][not(ancestor::{BOX})]"
MENU_UPLOAD = f"{OVERLAY} {MENU_ITEM}{icon('upload')}"

#: The download button in a medium's edit view (top toolbar).
EDIT_DOWNLOAD = f"button{icon('download')}"
#: The medium's display name in its edit view (an inline text field; Flow saves it on Enter).
EDIT_TITLE = "input.editable-text-input"

COOKIE_REJECT = "button.glue-cookie-notification-bar__reject"
DIALOG_CLOSE = f"[role='dialog'] button{icon('close')}"
CREDITS_WARNING = "button.prompt-warning-button"

MODE_IMAGE = "image"
MODE_VIDEO = "videocam"


def media_image(media_id: str) -> str:
    return f"img[data-media-id='{media_id}']"
