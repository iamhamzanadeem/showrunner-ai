from __future__ import annotations

import re
from typing import List, Optional
from app.models import (
    Bible,
    BibleStyle,
    CharacterSpec,
    CritiqueResult,
    Plan,
    ProductSpec,
    Scene,
    Shot,
    ShotStatus,
    VideoMode,
)
from app.providers.base import (
    BaseCriticProvider,
    BaseImageProvider,
    BaseMusicProvider,
    BasePlannerProvider,
    BaseTTSProvider,
    BaseVideoProvider,
)


def _extract_keywords(brief: str) -> List[str]:
    """Extracts salient words and proper nouns from the brief for canned plan customization."""
    # 1. Check for quoted brand or project names
    quotes = re.findall(r"['\"]([^'\"]+)['\"]", brief)
    if quotes:
        parts = [w for w in re.findall(r"[A-Za-z0-9]+", quotes[0]) if len(w) > 1]
        if parts:
            return parts

    # 2. Extract capitalized proper nouns
    proper = re.findall(r"\b[A-Z][a-z0-9]+\b", brief)
    generic_stops = {
        "A", "An", "The", "In", "On", "At", "To", "For", "Of", "With", "By", "From",
        "This", "That", "It", "We", "They", "He", "She", "And", "Or", "Is", "Are",
    }
    salient_proper = [w for w in proper if w not in generic_stops]
    if salient_proper:
        return salient_proper

    # 3. Fallback to general salient non-stopwords
    words = re.findall(r"[A-Za-z0-9]+", brief)
    stopwords = {
        "a", "an", "the", "and", "or", "in", "on", "at", "to", "for", "of", "with",
        "by", "from", "about", "is", "are", "was", "were", "this", "that", "it",
        "make", "create", "video", "ad", "film", "short", "commercial", "production",
        "futuristic", "moody", "sleek", "cinematic", "high", "impact",
    }
    keywords = [w for w in words if w.lower() not in stopwords]
    return keywords if keywords else ["Nova", "Horizon", "Kinetic"]


class MockPlannerProvider(BasePlannerProvider):
    """Deterministic, brief-aware mock planner generating realistic production blueprints."""

    async def generate_plan(
        self,
        brief: str,
        mode: VideoMode,
        ref_images: Optional[List[str]] = None,
    ) -> Plan:
        keywords = _extract_keywords(brief)
        primary_subject = keywords[0].capitalize() if keywords else "Apex"
        secondary_subject = (
            keywords[1].capitalize() if len(keywords) > 1 else "Odyssey"
        )
        brief_title = f"{primary_subject}: {secondary_subject}"

        # Style analysis based on brief text
        lower_brief = brief.lower()
        if any(w in lower_brief for w in ["cyber", "neon", "tech", "ai", "future", "space"]):
            palette = ["#0A0F1D", "#00F0FF", "#7000FF", "#FF007A"]
            lighting = "Atmospheric volumetric neon haze, high contrast deep shadows"
            lens = "Anamorphic 35mm T1.5 with subtle horizontal blue streak flares"
            mood = "Futuristic, visionary, electrifying tension"
            char_name = f"Agent {primary_subject}"
            char_role = "Pioneering Operative"
            char_appearance = "Sleek tactical attire with subtle bioluminescent accents and focused gaze"
            product_name = f"{primary_subject} Core"
            product_desc = f"Next-generation precision device glowing with cyan edge-illumination"
        elif any(w in lower_brief for w in ["nature", "eco", "green", "earth", "sun", "organic"]):
            palette = ["#1B3B2B", "#F5EBE1", "#E6A15C", "#2D5A43"]
            lighting = "Warm golden hour directional backlight with natural lens dust motes"
            lens = "50mm f/1.2 prime, creamy organic bokeh and warm falloff"
            mood = "Earthy, aspirational, deeply grounding and serene"
            char_name = f"Dr. {primary_subject}"
            char_role = "Field Researcher"
            char_appearance = "Weathered linen field jacket, candid warmth, attentive expression"
            product_name = f"{primary_subject} Natural"
            product_desc = "Sustainably crafted artisan form factor with matte mineral texture"
        else:
            palette = ["#09090B", "#D4AF37", "#27272A", "#FFFFFF"]
            lighting = "Sculptural studio rim-lighting with soft wrap key fill"
            lens = "Leica Noctilux 50mm f/0.95 cinematic glass"
            mood = "Prestige, confident, sleek and cinematic elegance"
            char_name = f"{primary_subject} Hero"
            char_role = "Lead Protagonist"
            char_appearance = "Tailored minimalist silhouette with poised demeanor and sharp profile"
            product_name = f"{primary_subject} Signature"
            product_desc = "Precision engineered brushed-alloy silhouette with mirror accents"

        bible = Bible(
            style=BibleStyle(
                palette=palette,
                lighting=lighting,
                lens=lens,
                mood=mood,
            ),
            characters=[
                CharacterSpec(
                    name=char_name,
                    appearance=char_appearance,
                    role=char_role,
                )
            ],
            products=[
                ProductSpec(
                    name=product_name,
                    description=product_desc,
                    reference_image=ref_images[0] if ref_images else None,
                )
            ],
        )

        if mode == VideoMode.ad:
            # 5 shots across 3 scenes totalling ~32 seconds (within 30-45s)
            scenes = [
                Scene(
                    id="scene_1",
                    scene_number=1,
                    title="The Hook: Chaos of Ordinary",
                    summary=f"Establish the friction and inertia before discovering {product_name}.",
                    shots=[
                        Shot(
                            id="shot_1_1",
                            scene_id="scene_1",
                            duration_s=6.0,
                            description=f"Wide dynamic establish: city bustle or sterile environment where {char_name} pauses.",
                            camera="Wide tracking shot moving backwards, low angle, slow tilt up",
                            visual_prompt=f"Cinematic wide shot, {mood.lower()}, {lighting}, {char_name} walking against the crowd in monochrome urban architecture, 8k resolution, cinematic color grade.",
                            vo_line=f"In a world accelerating past human limits, compromise was once the only option.",
                            characters_referenced=[char_name],
                            products_referenced=[],
                            chain_from_previous=False,
                            status=ShotStatus.pending,
                        ),
                        Shot(
                            id="shot_1_2",
                            scene_id="scene_1",
                            duration_s=7.0,
                            description=f"Intense close-up on {char_name}'s eyes as reflection shimmers.",
                            camera="Extreme close-up pushing slowly into the iris, rack focus",
                            visual_prompt=f"Macro extreme close-up of {char_name}'s eye reflecting luminous glow of {palette[1]}, intense focus, subtle breath vapor, cinematic realism, {lens}.",
                            vo_line=f"Until every standard had to be reimagined from the foundation up.",
                            characters_referenced=[char_name],
                            products_referenced=[],
                            chain_from_previous=True,
                            status=ShotStatus.pending,
                        ),
                    ],
                ),
                Scene(
                    id="scene_2",
                    scene_number=2,
                    title="The Reveal: Engineering Mastery",
                    summary=f"Heroic presentation of {product_name} in dynamic action.",
                    shots=[
                        Shot(
                            id="shot_2_1",
                            scene_id="scene_2",
                            duration_s=7.0,
                            description=f"Product reveal: {product_name} floating effortlessly in dynamic studio space.",
                            camera="360-degree rotational arc orbiting the product, speed ramp from fast to slow",
                            visual_prompt=f"Hero commercial packshot of {product_name}, {product_desc}, levitating amidst floating dust particles illuminated by {palette[1]} and {palette[2]}, ultra-clean hyper-realistic rendering.",
                            vo_line=f"Meet {product_name}. Engineered not just to keep pace, but to set the rhythm.",
                            characters_referenced=[],
                            products_referenced=[product_name],
                            chain_from_previous=False,
                            status=ShotStatus.pending,
                        ),
                        Shot(
                            id="shot_2_2",
                            scene_id="scene_2",
                            duration_s=6.0,
                            description=f"{char_name} interacting with {product_name} as energy bursts forward.",
                            camera="Medium close-up profile shot, handheld kinetic motion with stabilization",
                            visual_prompt=f"Dynamic medium shot of {char_name} engaging with {product_name}, surge of {palette[1]} light across the frame, triumphant energy, cinematic film texture.",
                            vo_line=f"Instant response. Unmatched craftsmanship. Zero friction.",
                            characters_referenced=[char_name],
                            products_referenced=[product_name],
                            chain_from_previous=True,
                            status=ShotStatus.pending,
                        ),
                    ],
                ),
                Scene(
                    id="scene_3",
                    scene_number=3,
                    title="The Climax: The Brand Statement",
                    summary="Iconic lockup, final aspirational gaze and tagline.",
                    shots=[
                        Shot(
                            id="shot_3_1",
                            scene_id="scene_3",
                            duration_s=6.0,
                            description=f"Hero freeze-frame transition into {product_name} emblem and brand lockup.",
                            camera="Slow zoom out from product emblem to wide majestic horizon",
                            visual_prompt=f"Aesthetic brand lockup shot, {product_name} resting proudly at center frame, backdrop of endless horizon bathed in {palette[0]} and {palette[1]} gradients, pristine typography space.",
                            vo_line=f"{primary_subject}. Define your future today.",
                            characters_referenced=[char_name],
                            products_referenced=[product_name],
                            chain_from_previous=False,
                            status=ShotStatus.pending,
                        ),
                    ],
                ),
            ]
            target_duration = 32.0

        else:
            # Short film mode: 18 shots across 5 scenes totalling 148 seconds (~150s, 4-8s each)
            scenes = [
                Scene(
                    id="scene_1",
                    scene_number=1,
                    title="Act I: The Boundary of Silence",
                    summary=f"Establish the atmospheric solitude of {char_name}'s territory.",
                    shots=[
                        Shot(
                            id="shot_1_1",
                            scene_id="scene_1",
                            duration_s=7.0,
                            description=f"Sweeping aerial landscape establishing the vast expanse of {primary_subject}.",
                            camera="Extreme wide drone descent, sweeping forward over atmospheric terrain",
                            visual_prompt=f"Cinematic wide establishing shot of vast desolate landscape, {lighting}, {mood}, atmospheric fog rolling across mountain ridgelines, 35mm anamorphic film grain.",
                            vo_line="They told us nothing survived past the perimeter wall.",
                            characters_referenced=[],
                            products_referenced=[],
                            chain_from_previous=False,
                        ),
                        Shot(
                            id="shot_1_2",
                            scene_id="scene_1",
                            duration_s=8.0,
                            description=f"{char_name} stands alone on an outpost ledge inspecting a telemetry unit.",
                            camera="Medium wide slowly tracking left to right behind metal girders",
                            visual_prompt=f"Medium wide shot of {char_name}, {char_appearance}, standing at rusty vantage point observing the horizon, wind flapping clothing, {lens}.",
                            vo_line="Seven months of static. Until this morning's harmonic spike.",
                            characters_referenced=[char_name],
                            products_referenced=[],
                            chain_from_previous=True,
                        ),
                        Shot(
                            id="shot_1_3",
                            scene_id="scene_1",
                            duration_s=7.0,
                            description="Close-up on instrument dial flickering erratically with blue phosphor numbers.",
                            camera="Macro shot tilting from needle gauge to flickering digital readout",
                            visual_prompt=f"Extreme close-up of analog dial vibrating, neon phosphor illumination in {palette[1]}, scratched glass face, cinematic texture.",
                            vo_line="It wasn't an echo. It was a coordinates pulse.",
                            characters_referenced=[],
                            products_referenced=[product_name],
                            chain_from_previous=True,
                        ),
                    ],
                ),
                Scene(
                    id="scene_2",
                    scene_number=2,
                    title="Act I: The Call to Cross",
                    summary=f"{char_name} equips {product_name} and prepares to cross into the forbidden zone.",
                    shots=[
                        Shot(
                            id="shot_2_1",
                            scene_id="scene_2",
                            duration_s=8.0,
                            description=f"Interior shelter: {char_name} retrieves {product_name} from a sealed reinforced case.",
                            camera="Low angle medium shot panning across worktable as locks hiss open",
                            visual_prompt=f"Low angle cinematic shot inside moody workshop, {lighting}, steam venting from pressurized container revealing {product_name}, gleaming metal finishes.",
                            vo_line="Protocol said report it and retreat. Protocol didn't know what was buried out there.",
                            characters_referenced=[char_name],
                            products_referenced=[product_name],
                            chain_from_previous=False,
                        ),
                        Shot(
                            id="shot_2_2",
                            scene_id="scene_2",
                            duration_s=7.0,
                            description=f"{char_name} buckles gear and secures {product_name} to chest harness.",
                            camera="Close-up dynamic montage pan following gloved hands securing tactical buckles",
                            visual_prompt=f"Close-up tactile gear preparation, intricate straps, mechanical clicks, {char_name}'s determined expression captured in side mirror reflection.",
                            vo_line=None,
                            characters_referenced=[char_name],
                            products_referenced=[product_name],
                            chain_from_previous=True,
                        ),
                        Shot(
                            id="shot_2_3",
                            scene_id="scene_2",
                            duration_s=8.0,
                            description="The heavy perimeter blast gate grinds open, revealing misty twilight beyond.",
                            camera="Ground level track following boots crossing the threshold line",
                            visual_prompt=f"Heavy industrial steel gate opening vertically with sparks, massive silhouette of {char_name} stepping into dense billowing fog, backlit by sodium floodlights.",
                            vo_line="The moment your boots hit the unmapped dust, there is no turning back.",
                            characters_referenced=[char_name],
                            products_referenced=[],
                            chain_from_previous=True,
                        ),
                    ],
                ),
                Scene(
                    id="scene_3",
                    scene_number=3,
                    title="Act II: The Deep Sector",
                    summary="The trek through treacherous ruins as the signal intensifies.",
                    shots=[
                        Shot(
                            id="shot_3_1",
                            scene_id="scene_3",
                            duration_s=8.0,
                            description=f"{char_name} navigates a labyrinth of overgrown megalithic structures.",
                            camera="Wide tracking shot sliding sideways parallel to towering monoliths",
                            visual_prompt=f"Epic wide shot of monolithic ancient architecture swallowed by glowing moss, {palette[0]} and {palette[1]} light shafts slicing through fog, {char_name} tiny in scale.",
                            vo_line="Every kilometer further, the air grew colder and the silence heavier.",
                            characters_referenced=[char_name],
                            products_referenced=[],
                            chain_from_previous=False,
                        ),
                        Shot(
                            id="shot_3_2",
                            scene_id="scene_3",
                            duration_s=7.0,
                            description="A sudden seismic tremor rattles loose gravel down a steep canyon wall.",
                            camera="Handheld dutch angle tilting rapidly upward as rocks tumble past lens",
                            visual_prompt=f"Dynamic handheld action shot, crumbling shale slope, dust explosion caught in beam of tactical light, dynamic motion blur, intense cinematic urgency.",
                            vo_line="The sector wasn't deserted. It was waking up.",
                            characters_referenced=[],
                            products_referenced=[],
                            chain_from_previous=True,
                        ),
                        Shot(
                            id="shot_3_3",
                            scene_id="scene_3",
                            duration_s=8.0,
                            description=f"{char_name} activates the sensor array on {product_name} to stabilize coordinates.",
                            camera="Over the shoulder shot pushing past {char_name} onto holographic readout",
                            visual_prompt=f"Over the shoulder shot of {char_name} activating {product_name}, crisp volumetric interface projecting geometric ring coordinates in mid-air, face illuminated by neon glow.",
                            vo_line=f"{product_name} locked the beacon. Less than two hundred meters ahead.",
                            characters_referenced=[char_name],
                            products_referenced=[product_name],
                            chain_from_previous=True,
                        ),
                        Shot(
                            id="shot_3_4",
                            scene_id="scene_3",
                            duration_s=8.0,
                            description=f"Rapid sprint across a crumbling skybridge suspended over abyss.",
                            camera="Forward tracking dolly matching {char_name}'s sprint with whip pans",
                            visual_prompt=f"Kinetic chase perspective, {char_name} sprinting across suspension structure, lightning flashing in background clouds, wind howling through steel cables.",
                            vo_line=None,
                            characters_referenced=[char_name],
                            products_referenced=[],
                            chain_from_previous=True,
                        ),
                    ],
                ),
                Scene(
                    id="scene_4",
                    scene_number=4,
                    title="Act II: The Nexus Discovery",
                    summary="Arrival at the heart of the anomaly and the revelation.",
                    shots=[
                        Shot(
                            id="shot_4_1",
                            scene_id="scene_4",
                            duration_s=7.0,
                            description="Reaching a grand subterranean vault chamber where an orb pulses with power.",
                            camera="Slow majestic crane up revealing the enormous spherical relic",
                            visual_prompt=f"Awe-inspiring wide interior cavern, massive floating crystalline sphere emitting pulsating waves of {palette[1]} and {palette[2]} light, reflections on wet obsidian floor.",
                            vo_line="It wasn't an abandoned transmission. It was an invitation.",
                            characters_referenced=[],
                            products_referenced=[],
                            chain_from_previous=False,
                        ),
                        Shot(
                            id="shot_4_2",
                            scene_id="scene_4",
                            duration_s=8.0,
                            description=f"{char_name} steps onto the central platform, lowering protective visor.",
                            camera="Medium close-up moving steadily forward with protagonist",
                            visual_prompt=f"Medium shot of {char_name} removing face shield, expression of profound revelation and wonder, eyes gleaming with reflected prism lights, {lighting}.",
                            vo_line="The builders never left. They were waiting for someone who could decipher the key.",
                            characters_referenced=[char_name],
                            products_referenced=[],
                            chain_from_previous=True,
                        ),
                        Shot(
                            id="shot_4_3",
                            scene_id="scene_4",
                            duration_s=8.0,
                            description=f"{char_name} presents {product_name} into the core receiver slot.",
                            camera="Close-up orbiting the alignment of device and ancient mechanism",
                            visual_prompt=f"Intricate macro shot: {product_name} aligning into ancient concentric ring mechanism, magnetic lock engages with radiant shockwave of golden particles.",
                            vo_line=None,
                            characters_referenced=[char_name],
                            products_referenced=[product_name],
                            chain_from_previous=True,
                        ),
                        Shot(
                            id="shot_4_4",
                            scene_id="scene_4",
                            duration_s=8.0,
                            description="The entire cavern illuminates in blinding harmonic luminescence.",
                            camera="Rapid pull-back expanding to cosmic scale as ceiling opens",
                            visual_prompt=f"Colossal scale wide shot, roof of vault dissolving into celestial starry sky, pillars of light erupting towards the cosmos, epic grandeur.",
                            vo_line="The circuit closed. The map of the outer sectors unfolded all at once.",
                            characters_referenced=[],
                            products_referenced=[],
                            chain_from_previous=True,
                        ),
                    ],
                ),
                Scene(
                    id="scene_5",
                    scene_number=5,
                    title="Act III: The New Horizon",
                    summary=f"The aftermath and {char_name}'s realization of what lies ahead.",
                    shots=[
                        Shot(
                            id="shot_5_1",
                            scene_id="scene_5",
                            duration_s=7.0,
                            description="Dawn breaks across the valley as the strange light settles into steady stars.",
                            camera="Slow tilt down from vibrant sunrise clouds to the valley floor",
                            visual_prompt=f"Breathtaking dawn landscape, gentle purple and amber morning hues, mist dissipating to reveal revitalized green valleys, peace restored.",
                            vo_line="We thought the frontier was an empty wasteland.",
                            characters_referenced=[],
                            products_referenced=[],
                            chain_from_previous=False,
                        ),
                        Shot(
                            id="shot_5_2",
                            scene_id="scene_5",
                            duration_s=7.0,
                            description=f"{char_name} stands looking out at the transformed world with new resolve.",
                            camera="Medium shot profile, golden rim light accentuating shoulders and gaze",
                            visual_prompt=f"Heroic medium profile of {char_name}, calm and determined smile, wind in hair, golden hour glow illuminating face and gear, 50mm f/1.2.",
                            vo_line="Now we know it was only the prologue.",
                            characters_referenced=[char_name],
                            products_referenced=[],
                            chain_from_previous=True,
                        ),
                        Shot(
                            id="shot_5_3",
                            scene_id="scene_5",
                            duration_s=8.0,
                            description=f"Close-up of {product_name} glowing steadily with newly acquired stellar coordinates.",
                            camera="Macro shot resting on the device surface as new constellations shift",
                            visual_prompt=f"Close-up macro of {product_name}, active interface displaying rotating stellar map, immaculate craftsmanship, subtle mechanical hum.",
                            vo_line=None,
                            characters_referenced=[],
                            products_referenced=[product_name],
                            chain_from_previous=True,
                        ),
                        Shot(
                            id="shot_5_4",
                            scene_id="scene_5",
                            duration_s=7.0,
                            description=f"Wide trailing shot as {char_name} walks into the boundless dawn.",
                            camera="Slow high crane shot pulling up and away into the sunrise sky",
                            visual_prompt=f"High angle cinematic crane shot receding upwards, lone figure of {char_name} striding confidently along sunlit ridge towards infinite horizons, fading slowly to black.",
                            vo_line=f"{brief_title}.",
                            characters_referenced=[char_name],
                            products_referenced=[],
                            chain_from_previous=True,
                        ),
                    ],
                ),
            ]
            target_duration = 137.0

        return Plan(
            title=brief_title,
            logline=f"An expedition into uncharted realms driven by {primary_subject} and the pursuit of {secondary_subject}.",
            mode=mode,
            target_duration_s=target_duration,
            bible=bible,
            scenes=scenes,
        )


class MockImageProvider(BaseImageProvider):
    """Generates mock keyframe SVG/data urls for visual validation."""

    async def generate_keyframe(
        self,
        prompt: str,
        style: BibleStyle,
        ref_image: Optional[str] = None,
        job_dir: Optional[str] = None,
        shot_id: Optional[str] = None,
    ) -> str:
        color = style.palette[0] if style.palette else "#1e293b"
        return f"/media/{shot_id or 'keyframe'}.png"


class MockVideoProvider(BaseVideoProvider):
    """Generates mock video references."""

    async def generate_clip(
        self,
        prompt: str,
        initial_frame: Optional[str] = None,
        duration_s: float = 8.0,
        job_dir: Optional[str] = None,
        shot_id: Optional[str] = None,
    ) -> str:
        return f"/media/{shot_id or 'clip'}.mp4"


class MockCriticProvider(BaseCriticProvider):
    """Reviews clips against bible and shot specifications with mock critiques."""

    async def critique_clip(
        self,
        video_path: str,
        shot: Shot,
        bible: Bible,
    ) -> CritiqueResult:
        # Mock passes shots on retry or first attempt with high probability
        return CritiqueResult(
            shot_id=shot.id,
            passed=True,
            score=0.92,
            reasons=["Lighting matches style bible", "Character continuity preserved"],
            suggested_prompt_fix=None,
        )


class MockTTSProvider(BaseTTSProvider):
    """Generates mock audio voiceover files."""

    async def generate_voiceover(
        self,
        text: str,
        voice_name: Optional[str] = None,
        job_dir: Optional[str] = None,
        shot_id: Optional[str] = None,
    ) -> str:
        return f"/media/{shot_id or 'vo'}.wav"


class MockMusicProvider(BaseMusicProvider):
    """Generates mock background music tracks."""

    async def generate_music(
        self,
        prompt: str,
        duration_s: float,
        job_dir: Optional[str] = None,
    ) -> str:
        return "/media/soundtrack.mp3"
