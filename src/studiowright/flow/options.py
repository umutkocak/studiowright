"""Every option Flow's composer offers, with the locale-proof handle that selects it.

Handles are icon ligatures or locale-neutral tokens (``16:9``, ``x2``, ``720p``) and model
names, which Flow does not translate. Measured on flow.google.com on 2026-10-10.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class OptionError(ValueError):
    """A combination Flow does not offer."""


class ImageModel(Enum):
    NANO_BANANA_2_1 = "nano-banana-2.1"
    NANO_BANANA_PRO = "nano-banana-pro"
    NANO_BANANA_2_LITE = "nano-banana-2-lite"

    @property
    def label(self) -> str:
        return {
            ImageModel.NANO_BANANA_2_1: "Nano Banana 2.1",
            ImageModel.NANO_BANANA_PRO: "Nano Banana Pro",
            ImageModel.NANO_BANANA_2_LITE: "Nano Banana 2 Lite",
        }[self]


class VideoModel(Enum):
    OMNI_FLASH = "omni-flash"
    OMNI_FLASH_1_2 = "omni-flash-1.2"
    VEO_LITE = "veo-lite"
    VEO_FAST = "veo-fast"
    VEO_QUALITY = "veo-quality"

    @property
    def label(self) -> str:
        return {
            VideoModel.OMNI_FLASH: "Omni 1.1 Flash",
            VideoModel.OMNI_FLASH_1_2: "Omni 1.2 Flash",
            VideoModel.VEO_LITE: "Veo 3.1 - Lite",
            VideoModel.VEO_FAST: "Veo 3.1 - Fast",
            VideoModel.VEO_QUALITY: "Veo 3.1 - Quality",
        }[self]

    @property
    def has_length_and_resolution(self) -> bool:
        """Only Omni offers duration and resolution choices; Veo shows neither."""
        return self in (VideoModel.OMNI_FLASH, VideoModel.OMNI_FLASH_1_2)

    @property
    def durations(self) -> tuple[int, ...]:
        """Clip lengths Flow offers: Omni 1.1 as 4/6/8/10 s radios, Omni 1.2 as a 3-15 s
        slider (measured 2026-11-07); Veo none."""
        if self is VideoModel.OMNI_FLASH_1_2:
            return tuple(range(3, 16))
        return DURATIONS if self.has_length_and_resolution else ()


class Aspect(Enum):
    LANDSCAPE = "16:9"
    LANDSCAPE_4_3 = "4:3"
    SQUARE = "1:1"
    PORTRAIT_3_4 = "3:4"
    PORTRAIT = "9:16"

    @property
    def icon(self) -> str:
        return {
            Aspect.LANDSCAPE: "crop_16_9",
            Aspect.LANDSCAPE_4_3: "crop_landscape",
            Aspect.SQUARE: "crop_square",
            Aspect.PORTRAIT_3_4: "crop_portrait",
            Aspect.PORTRAIT: "crop_9_16",
        }[self]


VIDEO_ASPECTS = (Aspect.LANDSCAPE, Aspect.PORTRAIT)


class VideoMode(Enum):
    FRAMES = "frames"
    INGREDIENTS = "ingredients"

    @property
    def icon(self) -> str:
        return {VideoMode.FRAMES: "crop_free", VideoMode.INGREDIENTS: "chrome_extension"}[self]


RESOLUTIONS = ("360p", "720p")
DURATIONS = (4, 6, 8, 10)
COUNTS = (1, 2, 3, 4)


def _parse_enum[E: Enum](kind: type[E], value: E | str) -> E:
    if isinstance(value, kind):
        return value
    text = str(value).strip().lower()
    for member in kind:
        if text in (str(member.value).lower(), member.name.lower()):
            return member
    choices = ", ".join(str(m.value) for m in kind)
    raise OptionError(f"unknown {kind.__name__} {value!r}; choose one of: {choices}")


@dataclass(frozen=True)
class ImageOptions:
    model: ImageModel = ImageModel.NANO_BANANA_2_1
    aspect: Aspect = Aspect.LANDSCAPE
    count: int = 1

    @classmethod
    def build(
        cls,
        model: ImageModel | str = ImageModel.NANO_BANANA_2_1,
        aspect: Aspect | str = Aspect.LANDSCAPE,
        count: int = 1,
    ) -> ImageOptions:
        if count not in COUNTS:
            raise OptionError(f"count must be one of {COUNTS}, got {count}")
        return cls(_parse_enum(ImageModel, model), _parse_enum(Aspect, aspect), count)


@dataclass(frozen=True)
class VideoOptions:
    model: VideoModel = VideoModel.OMNI_FLASH
    mode: VideoMode = VideoMode.FRAMES
    aspect: Aspect = Aspect.LANDSCAPE
    resolution: str | None = "720p"
    duration: int | None = 8
    count: int = 1

    @classmethod
    def build(
        cls,
        model: VideoModel | str = VideoModel.OMNI_FLASH,
        mode: VideoMode | str = VideoMode.FRAMES,
        aspect: Aspect | str = Aspect.LANDSCAPE,
        resolution: str | None = "720p",
        duration: int | None = 8,
        count: int = 1,
    ) -> VideoOptions:
        model_ = _parse_enum(VideoModel, model)
        aspect_ = _parse_enum(Aspect, aspect)
        if aspect_ not in VIDEO_ASPECTS:
            raise OptionError(f"video aspect must be 16:9 or 9:16, got {aspect_.value}")
        if count not in COUNTS:
            raise OptionError(f"count must be one of {COUNTS}, got {count}")
        if model_.has_length_and_resolution:
            if resolution not in RESOLUTIONS:
                raise OptionError(f"resolution must be one of {RESOLUTIONS}, got {resolution}")
            if duration not in model_.durations:
                offered = model_.durations
                raise OptionError(
                    f"duration for {model_.value} must be {offered[0]}-{offered[-1]} s"
                    if len(offered) > 4
                    else f"duration for {model_.value} must be one of {offered} s",
                )
        else:
            resolution, duration = None, None
        return cls(model_, _parse_enum(VideoMode, mode), aspect_, resolution, duration, count)


def duration_pattern(seconds: int) -> re.Pattern[str]:
    """Matches a duration radio in any locale ("8s", "8 sn.", "8 Sek.")."""
    return re.compile(rf"^\s*{seconds}(?!\d)")


def count_pattern(count: int) -> re.Pattern[str]:
    return re.compile(rf"^\s*x{count}\s*$")
