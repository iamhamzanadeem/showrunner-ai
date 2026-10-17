"""Fix #1: reference sheets reach keyframe generation; user uploads reach the planner."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.config import settings
from app.models import (
    Bible,
    BibleStyle,
    CharacterSpec,
    Job,
    Plan,
    ProductSpec,
    Scene,
    Shot,
    VideoMode,
)
from app.pipeline.orchestrator import _shot_reference_sheets, run_pipeline
from app.providers.google.image import GoogleImageProvider
from app.providers.google.planner import GooglePlannerProvider
from app.providers.mock import MockImageProvider

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n"
    b"-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

STYLE = BibleStyle(palette=["#000000", "#FFFFFF"], lighting="Soft", lens="35mm", mood="Calm")


def _image_client():
    part = MagicMock()
    part.as_image.return_value = None
    part.inline_data.data = PNG_BYTES
    cand = MagicMock()
    cand.content.parts = [part]
    resp = MagicMock()
    resp.candidates = [cand]
    client = MagicMock()
    client.models.generate_content.return_value = resp
    return client


def _image_parts(contents):
    return [c for c in contents if not isinstance(c, str)]


@pytest.mark.asyncio
async def test_keyframe_attaches_chain_frame_first_and_caps_at_three(tmp_path: Path):
    files = {}
    for name in ["chain", "a", "b", "c"]:
        p = tmp_path / f"{name}.png"
        p.write_bytes(PNG_BYTES + name.encode())
        files[name] = str(p)

    client = _image_client()
    sent = []
    with patch("app.providers.google.image.get_genai_client", return_value=client), patch(
        "app.providers.google.image.types.Part.from_bytes",
        side_effect=lambda data, mime_type: sent.append(data) or ("PART", data),
    ):
        provider = GoogleImageProvider(api_key="k", model="img")
        await provider.generate_keyframe(
            prompt="Hero walks",
            style=STYLE,
            ref_image=files["chain"],
            ref_images=[files["a"], files["b"], files["c"]],
            job_dir=tmp_path,
            shot_id="shot_1_2",
        )
    contents = client.models.generate_content.call_args.kwargs["contents"]
    parts = _image_parts(contents)
    assert len(parts) == 3
    assert sent[0].endswith(b"chain")  # continuity frame first
    assert sent[1].endswith(b"a") and sent[2].endswith(b"b")
    assert "previous shot's last frame" in contents[0]
    assert "EXACTLY consistent" in contents[0]


@pytest.mark.asyncio
async def test_keyframe_dedupes_and_skips_unresolvable(tmp_path: Path):
    p = tmp_path / "sheet.png"
    p.write_bytes(PNG_BYTES)
    client = _image_client()
    with patch("app.providers.google.image.get_genai_client", return_value=client):
        provider = GoogleImageProvider(api_key="k", model="img")
        await provider.generate_keyframe(
            prompt="x", style=STYLE, ref_images=[str(p), str(p), "/media/nope/missing.png"],
            job_dir=tmp_path, shot_id="s",
        )
    contents = client.models.generate_content.call_args.kwargs["contents"]
    assert len(_image_parts(contents)) == 1
    assert "previous shot's last frame" not in contents[0]


@pytest.mark.asyncio
async def test_ref_sheet_grounded_on_user_upload(tmp_path: Path):
    upload = tmp_path / "upload.webp"
    upload.write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    client = _image_client()
    mimes = []
    with patch("app.providers.google.image.get_genai_client", return_value=client), patch(
        "app.providers.google.image.types.Part.from_bytes",
        side_effect=lambda data, mime_type: mimes.append(mime_type) or "PART",
    ):
        provider = GoogleImageProvider(api_key="k", model="img")
        await provider.generate_ref_sheet(
            entity_type="product", name="Lumina", description="Glass", style=STYLE,
            job_dir=tmp_path, ref_image=str(upload),
        )
    contents = client.models.generate_content.call_args.kwargs["contents"]
    assert len(contents) == 2 and "REAL product" in contents[0]
    assert mimes == ["image/webp"]


def _job_with_plan() -> Job:
    chars = [
        CharacterSpec(name="Kaelen", appearance="armor", reference_image="/media/j/ref_character_kaelen.png"),
        CharacterSpec(name="Mira", appearance="coat", reference_image="/media/j/ref_character_mira.png"),
    ]
    prods = [ProductSpec(name="Pulse Shard", description="crystal", reference_image="/media/j/ref_product_pulse.png")]
    shot = Shot(id="shot_1_1", scene_id="scene_1", duration_s=4, description="d", camera="c", visual_prompt="v")
    plan = Plan(
        title="t", logline="l", mode=VideoMode.ad, target_duration_s=8,
        bible=Bible(style=STYLE, characters=chars, products=prods),
        scenes=[Scene(id="scene_1", scene_number=1, title="s", summary="s", shots=[shot])],
    )
    return Job(id="job_0000000000", brief="b", mode=VideoMode.ad, plan=plan)


def test_shot_reference_sheets_relevance_and_limit():
    job = _job_with_plan()
    mk = lambda **kw: Shot(id="s", scene_id="sc", duration_s=4, description=kw.get("d", ""), camera="c",
                           visual_prompt=kw.get("v", ""), characters_referenced=kw.get("cr", []),
                           products_referenced=kw.get("pr", []))
    # Name match in prompt (case-insensitive)
    assert _shot_reference_sheets(job, mk(v="mira holds the PULSE SHARD")) == [
        "/media/j/ref_character_mira.png", "/media/j/ref_product_pulse.png"]
    # Explicit lists
    assert _shot_reference_sheets(job, mk(cr=["Kaelen"])) == ["/media/j/ref_character_kaelen.png"]
    # Fallback to all characters then products, capped by limit
    assert _shot_reference_sheets(job, mk(v="empty skyline"), limit=2) == [
        "/media/j/ref_character_kaelen.png", "/media/j/ref_character_mira.png"]
    assert _shot_reference_sheets(job, mk(v="x"), limit=0) == []


@pytest.mark.asyncio
async def test_planner_receives_user_upload(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    upload = tmp_path / "jobs" / "job_abc" / "ref_images" / "u.png"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(PNG_BYTES)

    client = MagicMock()
    resp = MagicMock()
    resp.parsed = None
    resp.text = '{"title": "T", "logline": "L", "target_duration_s": 8, "bible": {"style": {}, "characters": [], "products": [{"name": "Lumina", "description": "glass"}]}, "scenes": []}'
    client.models.generate_content.return_value = resp
    with patch("app.providers.google.planner.get_genai_client", return_value=client):
        planner = GooglePlannerProvider(api_key="k")
        plan = await planner.generate_plan(
            brief="Ad for Lumina", mode=VideoMode.ad, ref_images=["/media/job_abc/ref_images/u.png"]
        )
    contents = client.models.generate_content.call_args.kwargs["contents"]
    assert any(not isinstance(c, str) and getattr(c, "inline_data", None) is not None for c in contents)
    # Single upload in ad mode is mapped to the product
    assert plan.bible.products[0].reference_image == "/media/job_abc/ref_images/u.png"


@pytest.mark.asyncio
async def test_orchestrator_passes_sheets_to_keyframes(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "MOCK_DELAY_S", 0.0)
    calls = []
    orig = MockImageProvider.generate_keyframe

    async def rec(self, **kw):
        calls.append(kw)
        return await orig(self, **kw)

    monkeypatch.setattr(MockImageProvider, "generate_keyframe", rec)
    job = Job(id="job_1111111111", brief="30s ad for Lumina smart glasses worn by a runner", mode=VideoMode.ad)
    await run_pipeline(job)
    sheet_urls = set(job.character_refs.values()) | set(job.product_refs.values())
    assert calls and sheet_urls
    for kw in calls:
        refs = kw["ref_images"]
        assert refs and set(refs) <= sheet_urls
        total = len(refs) + (1 if kw.get("ref_image") else 0)
        assert total <= 3
