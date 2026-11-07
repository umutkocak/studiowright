from __future__ import annotations

import pytest

from studiowright.flow import options as o


def test_image_defaults_are_nano_banana_2_1_landscape_x1() -> None:
    opts = o.ImageOptions.build()
    assert (opts.model, opts.aspect, opts.count) == (
        o.ImageModel.NANO_BANANA_2_1,
        o.Aspect.LANDSCAPE,
        1,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("nano-banana-pro", o.ImageModel.NANO_BANANA_PRO),
        ("NANO_BANANA_2_LITE", o.ImageModel.NANO_BANANA_2_LITE),
    ],
)
def test_image_model_accepts_value_or_name(value: str, expected: o.ImageModel) -> None:
    assert o.ImageOptions.build(model=value).model is expected


@pytest.mark.parametrize("aspect", ["16:9", "4:3", "1:1", "3:4", "9:16"])
def test_every_image_aspect_has_an_icon(aspect: str) -> None:
    assert o.ImageOptions.build(aspect=aspect).aspect.icon.startswith("crop_")


def test_unknown_values_name_the_choices() -> None:
    with pytest.raises(o.OptionError, match="16:9"):
        o.ImageOptions.build(aspect="2:1")
    with pytest.raises(o.OptionError, match="count"):
        o.ImageOptions.build(count=5)


def test_video_rejects_image_only_aspects() -> None:
    with pytest.raises(o.OptionError, match="16:9 or 9:16"):
        o.VideoOptions.build(aspect="1:1")


def test_omni_validates_duration_and_resolution() -> None:
    opts = o.VideoOptions.build(model="omni-flash", resolution="360p", duration=4)
    assert (opts.resolution, opts.duration) == ("360p", 4)
    with pytest.raises(o.OptionError, match="duration"):
        o.VideoOptions.build(duration=5)
    with pytest.raises(o.OptionError, match="resolution"):
        o.VideoOptions.build(resolution="1080p")


def test_veo_drops_length_and_resolution() -> None:
    opts = o.VideoOptions.build(model="veo-quality", resolution="360p", duration=4)
    assert (opts.resolution, opts.duration) == (None, None)


@pytest.mark.parametrize(
    ("text", "matches"), [("8s", True), ("8 sn.", True), ("18 sn.", False), ("80s", False)]
)
def test_duration_pattern_is_locale_proof(text: str, matches: bool) -> None:
    assert bool(o.duration_pattern(8).match(text)) is matches


def test_count_pattern_is_exact() -> None:
    assert o.count_pattern(2).match(" x2 ")
    assert not o.count_pattern(2).match("x20")


@pytest.mark.parametrize(
    ("text", "price"),
    [
        ("x1 x2 x3 x4 Üretme işlemi için 4 kredi kullanılır", 4),
        ("4s 6s 8s 10s Generating will use 12 credits", 12),
        ("no price here 8s 720p", None),
    ],
)
def test_credit_price(text: str, price: int | None) -> None:
    from studiowright.flow.page import credit_price

    assert credit_price(text) == price


def test_omni_1_2_takes_any_whole_second_from_3_to_15() -> None:
    for seconds in (3, 5, 7, 12, 15):
        opts = o.VideoOptions.build(model="omni-flash-1.2", resolution="720p", duration=seconds)
        assert (opts.model.label, opts.duration) == ("Omni 1.2 Flash", seconds)
    for seconds in (2, 16):
        with pytest.raises(o.OptionError, match="3-15 s"):
            o.VideoOptions.build(model="omni-flash-1.2", duration=seconds)
    with pytest.raises(o.OptionError, match="resolution"):
        o.VideoOptions.build(model="omni-flash-1.2", resolution="1080p")
    # Omni 1.1 keeps its four radios.
    assert o.VideoModel.OMNI_FLASH.durations == (4, 6, 8, 10)
    with pytest.raises(o.OptionError, match=r"\(4, 6, 8, 10\)"):
        o.VideoOptions.build(model="omni-flash", duration=5)
    assert o.VideoModel.VEO_LITE.durations == ()
