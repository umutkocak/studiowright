"""Progress survives waits and failures without a live browser or Flow account."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from studiowright import paths
from studiowright.flow import page as flow_page
from studiowright.flow import replies
from studiowright.flow.page import FlowError, FlowPage, VideoClip
from studiowright.studio import ImageRequest, Studio, VideoRequest

PROJECT = "00000000-0000-4000-8000-000000000001"
FIRST = "00000000-0000-4000-8000-000000000002"
SECOND = "00000000-0000-4000-8000-000000000003"
OLD = "00000000-0000-4000-8000-000000000004"


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path))
    return Studio("test")


def progress(studio, job_id):
    # A new SQLite connection reads the persisted value, not an in-memory snapshot.
    row = studio.store.job(job_id)
    return row, json.loads(row["result"])


def test_progress_merges_ids_preserves_files_and_never_regresses(studio):
    job_id = studio.store.create_job("video", "test", PROJECT, {})
    studio.store.update_job(job_id, result={"files": ["saved.mp4"], "credits": 10})
    for stage, ids, expected in [
        ("submitted", [FIRST], "submitted"),
        ("generated", [SECOND, FIRST], "generated"),
        ("submitted", [], "generated"),
        ("delivered", [], "delivered"),
        ("generated", [FIRST], "delivered"),
    ]:
        studio._progress(job_id, stage, ids)
        _, result = progress(studio, job_id)
        assert result["stage"] == expected
        assert result["files"] == ["saved.mp4"]
        assert result["credits"] == 10
    assert result["media_ids"] == [FIRST, SECOND]


@pytest.mark.parametrize(
    ("kind", "failure"),
    [
        ("image", None),
        ("video", None),
        ("video", "wait"),
        ("image", "download"),
        ("video", "download"),
    ],
)
async def test_generation_persists_before_download_and_keeps_ids_on_failure(
    studio, monkeypatch, tmp_path, kind, failure
):
    studio._session = SimpleNamespace(find_page=lambda _: None)
    monkeypatch.setattr(studio, "_project", AsyncMock(return_value=PROJECT))
    monkeypatch.setattr(studio.governor, "wait_turn", AsyncMock())
    flow = SimpleNamespace(
        reset=AsyncMock(),
        apply_image_options=AsyncMock(),
        apply_video_options=AsyncMock(return_value=10),
        credits_left=None,
    )
    monkeypatch.setattr(FlowPage, "open", AsyncMock(return_value=flow))

    def current():
        return studio.store.recent_jobs("test")[0]["id"]

    async def generate_video(prompt, *, on_ids=None):
        assert prompt == "test"
        assert on_ids is not None
        on_ids([FIRST])
        row, result = progress(studio, current())
        assert row["status"] == "running"
        assert row["finished"] is None
        assert result["stage"] == "submitted"
        assert result["media_ids"] == [FIRST]
        if failure == "wait":
            raise FlowError("wait failed")
        return [VideoClip(FIRST, "url", None)]

    flow.generate_video = generate_video
    flow.generate_images = AsyncMock(return_value=[replies.ImageResult(FIRST, "url")])

    async def download(*args):
        row, result = progress(studio, current())
        assert row["status"] == "running"
        assert result["stage"] == "generated"
        assert result["media_ids"] == [FIRST]
        if failure == "download":
            raise FlowError("download failed")
        return ["saved.mp4"]

    monkeypatch.setattr(studio, "_download_all", download)
    request = (ImageRequest if kind == "image" else VideoRequest)("test", out_dir=tmp_path)
    if failure:
        with pytest.raises(FlowError, match=f"{failure} failed"):
            await getattr(studio, kind)(request)
    else:
        returned = await getattr(studio, kind)(request)
        assert returned["stage"] == "delivered"
    row, result = progress(studio, current())
    assert result["media_ids"] == [FIRST]
    assert row["status"] == ("failed" if failure else "done")
    assert result["stage"] == (
        "submitted" if failure == "wait" else "generated" if failure else "delivered"
    )
    assert row["finished"] is not None
    assert not studio._lock.is_locked


def record(media_id, status, with_url=False):
    details = [None] * 14
    details[8] = [status]
    media = [[None] * 13]
    if with_url:
        media[0][8] = f"https://flow-content.google/video/{media_id}?Signature=x"
    return [media_id, PROJECT, FIRST, None, None, details, None, media]


@pytest.fixture
def watched_flow(monkeypatch):
    clock = [0]
    monkeypatch.setattr(flow_page, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    batches = [
        ("poll", [record(OLD, replies.STATUS_DONE)]),
        ("submit", [record(SECOND, replies.STATUS_QUEUED)]),
        ("poll", [record(SECOND, replies.STATUS_RUNNING)]),
        ("submit", [record(FIRST, replies.STATUS_QUEUED)]),
        (
            "poll",
            [record(FIRST, replies.STATUS_DONE, True), record(SECOND, replies.STATUS_DONE, True)],
        ),
    ]

    class Watch:
        def __init__(self, *args):
            self.bodies = []
            self.unreadable = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def wait(self, _):
            clock[0] += 1
            assert batches, "generation did not consume the completed clips"
            rpc, payload = batches.pop(0)
            self.bodies.append(
                (flow_page.VIDEO_SUBMIT_RPCS[0] if rpc == "submit" else "poll", 200, payload)
            )

    monkeypatch.setattr(flow_page, "ReplyWatch", Watch)
    monkeypatch.setattr(flow_page.be, "parse_reply", lambda text, _: text)
    flow = FlowPage(SimpleNamespace(), PROJECT)
    monkeypatch.setattr(flow, "_type_and_submit", AsyncMock())
    return flow, batches


@pytest.mark.parametrize("callback", ["record", "raises", "omitted"])
async def test_video_callback_runs_on_new_accepted_ids_and_is_optional(
    watched_flow, callback, caplog
):
    flow, batches = watched_flow
    snapshots = []
    remaining = []

    def on_ids(ids):
        # URLs have not arrived yet, and a duplicate status caused no extra callback.
        remaining.append(len(batches))
        snapshots.append(list(ids))
        ids.clear()  # A callback cannot mutate the page's accepted set.
        if callback == "raises":
            raise RuntimeError("observer failed")

    kwargs = {} if callback == "omitted" else {"on_ids": on_ids}
    clips = await flow.generate_video("test", 600, **kwargs)
    assert [clip.media_id for clip in clips] == [FIRST, SECOND]
    assert snapshots == ([] if callback == "omitted" else [[SECOND], [FIRST, SECOND]])
    assert remaining == ([] if callback == "omitted" else [3, 1])
    if callback == "raises":
        assert "observer failed" in caplog.text


async def test_video_ids_are_reported_even_when_url_wait_times_out(watched_flow):
    flow, _ = watched_flow
    snapshots = []
    with pytest.raises(FlowError, match="not ready within 2 s"):
        await flow.generate_video("test", 2, on_ids=lambda ids: snapshots.append(ids))
    assert snapshots == [[SECOND]]


async def test_unaccepted_existing_record_does_not_report_ids(watched_flow):
    flow, _ = watched_flow
    snapshots = []
    with pytest.raises(FlowError, match="not ready within 1 s"):
        await flow.generate_video("test", 1, on_ids=lambda ids: snapshots.append(ids))
    assert snapshots == []
