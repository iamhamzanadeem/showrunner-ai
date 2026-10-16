import pytest
from app.models import VideoMode
from app.providers.mock import MockPlannerProvider


@pytest.mark.asyncio
async def test_mock_planner_ad_mode():
    planner = MockPlannerProvider()
    brief = "A futuristic cyber commercial for Kinetic Zero energy drink with neon aesthetics."
    plan = await planner.generate_plan(brief=brief, mode=VideoMode.ad)

    # 1. Title and brief awareness
    assert "Kinetic" in plan.title or "Zero" in plan.title
    assert plan.mode == VideoMode.ad

    # 2. Structure specs for Ad: ~5 shots, 2-3 scenes, 30-45s total duration
    assert 2 <= len(plan.scenes) <= 3
    assert 4 <= plan.total_shots <= 6
    assert 30.0 <= plan.total_duration_s <= 45.0

    # 3. Individual shot checks
    for scene in plan.scenes:
        for idx, shot in enumerate(scene.shots):
            assert shot.id
            assert shot.scene_id == scene.id
            assert 4.0 <= shot.duration_s <= 8.0
            assert shot.description
            assert shot.camera
            assert shot.visual_prompt
            # Check continuity chain flag: first shot in scene False, subsequent True
            if idx == 0:
                assert shot.chain_from_previous is False
            else:
                assert shot.chain_from_previous is True

    # 4. Bible checks
    assert plan.bible.style.palette
    assert plan.bible.style.lighting
    assert plan.bible.style.lens
    assert plan.bible.style.mood
    assert len(plan.bible.characters) >= 1
    assert len(plan.bible.products) >= 1
    assert "Kinetic" in plan.bible.products[0].name or "Zero" in plan.bible.products[0].name


@pytest.mark.asyncio
async def test_mock_planner_short_film_mode():
    planner = MockPlannerProvider()
    brief = "A moody space odyssey short film tracking astronaut Maya across an icy canyon."
    plan = await planner.generate_plan(brief=brief, mode=VideoMode.short_film)

    # 1. Mode and title
    assert plan.mode == VideoMode.short_film
    assert "Maya" in plan.title or "Astronaut" in plan.title or "Odyssey" in plan.title

    # 2. Structure specs for Short Film: ~16-20 shots, 4-6 scenes, ~150s total duration
    assert 4 <= len(plan.scenes) <= 6
    assert 15 <= plan.total_shots <= 22
    assert 120.0 <= plan.total_duration_s <= 180.0

    # 3. Shot parameters and continuity chaining
    for scene in plan.scenes:
        for idx, shot in enumerate(scene.shots):
            assert 4.0 <= shot.duration_s <= 8.0
            assert shot.visual_prompt
            assert shot.camera
            if idx == 0:
                assert shot.chain_from_previous is False
            else:
                assert shot.chain_from_previous is True

    # 4. Bible checks
    assert plan.bible.style.mood
    assert plan.bible.style.lens
    assert len(plan.bible.characters) >= 1
    assert any("Maya" in c.name or "Hero" in c.name or "Operative" in c.name for c in plan.bible.characters)


@pytest.mark.asyncio
async def test_mock_planner_brief_aware_chai():
    planner = MockPlannerProvider()
    brief = "A 30s ad for Chai Bloom, a premium Pakistani masala chai brand, featuring Ayesha, a young architect in Islamabad on a rainy morning"
    plan = await planner.generate_plan(brief=brief, mode=VideoMode.ad)

    # 1. Title and entities
    assert plan.title == "Chai Bloom: Islamabad"
    assert len(plan.bible.characters) >= 1
    assert plan.bible.characters[0].name == "Ayesha"
    assert "Architect" in plan.bible.characters[0].role
    assert len(plan.bible.products) >= 1
    assert plan.bible.products[0].name == "Chai Bloom"

    # 2. VO lines templated around entities
    shot_1_1 = plan.scenes[0].shots[0]
    assert shot_1_1.vo_line == "Rain over Islamabad. Ayesha's day starts before the sun."

    shot_3_1 = plan.scenes[2].shots[0]
    assert shot_3_1.vo_line == "Chai Bloom. Brewed for the bold."
