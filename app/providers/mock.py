from __future__ import annotations

import asyncio
from pathlib import Path
import re
import subprocess
from typing import List, Optional
from PIL import Image, ImageDraw, ImageFont

from app.config import settings
from app.models import (
    Bible,
    BibleStyle,
    CharacterSpec,
    CritiqueResult,
    Plan,
    PlanEdit,
    ProductSpec,
    Scene,
    Shot,
    ShotStatus,
    UpdatedShotSpec,
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


def _hex_to_rgb(hex_code: str, fallback=(18, 22, 34)) -> tuple[int, int, int]:
    try:
        hex_code = hex_code.lstrip("#")
        if len(hex_code) == 6:
            return int(hex_code[0:2], 16), int(hex_code[2:4], 16), int(hex_code[4:6], 16)
    except Exception:
        pass
    return fallback


def _extract_brief_entities(brief: str) -> Dict[str, Any]:
    """
    Deterministically extracts:
    - brand/product name (e.g. 'Chai Bloom', 'Kinetic Zero', 'Aethelgard Chrono')
    - character name & role (e.g. 'Ayesha', 'young architect')
    - location (e.g. 'Islamabad', 'Tokyo', 'Kepler-9')
    - product descriptor / category (e.g. 'premium Pakistani masala chai brand')
    - atmospheric setting (e.g. 'rainy morning')
    """
    keywords = _extract_keywords(brief)
    primary_subject = keywords[0].capitalize() if keywords else "Apex"
    secondary_subject = keywords[1].capitalize() if len(keywords) > 1 else "Odyssey"

    # 1. Brand / Product name
    quoted = re.findall(r"['\"]([^'\"]+)['\"]", brief)
    brand_name = None
    if quoted:
        brand_name = quoted[0].strip()
    else:
        m_brand = re.search(
            r"\b(?:for|titled|named|called|brand)\s+([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)*)",
            brief,
        )
        if m_brand:
            brand_name = m_brand.group(1).strip()

    if not brand_name:
        brand_name = f"{primary_subject} {secondary_subject}" if len(keywords) > 1 else f"{primary_subject} Core"

    # 2. Character name and role
    char_name = None
    char_role = "Lead Protagonist"
    m_char = re.search(
        r"\b(?:featuring|starring|with|tracking|follows|scout|astronaut|runner|agent)\s+([A-Z][a-z]+)(?:,\s*(?:a|an)\s+([^,.]+?)(?:\s+in|\s+on|\s+at|,|\.))?",
        brief,
    )
    if m_char:
        char_name = m_char.group(1).strip()
        if m_char.group(2):
            char_role = m_char.group(2).strip().title()

    if not char_name:
        m_title = re.search(r"\b(Dr\.|Scout|Agent|Pilot)\s+([A-Z][a-z]+)", brief)
        if m_title:
            char_name = m_title.group(2).strip()
            char_role = m_title.group(1).strip()
        else:
            char_name = f"{primary_subject} Hero"
            char_role = "Lead"

    # 3. Location
    m_loc = re.search(
        r"\b(?:in|across|on|over)\s+(?:an?\s+)?([A-Z][a-zA-Z0-9\-]+(?:\s+[A-Z][a-zA-Z0-9\-]+)?)",
        brief,
    )
    location = m_loc.group(1).strip() if m_loc else (secondary_subject if secondary_subject != brand_name else "the city")

    # 4. Setting / Atmosphere
    m_weather = re.search(
        r"\b(rainy morning|rain-slicked|rainy|midnight|dawn|dusk|sunset|winter|ice canyons|storm|morning|foggy)\b",
        brief,
        re.IGNORECASE,
    )
    setting = m_weather.group(1).lower() if m_weather else "morning"

    # 5. Product descriptor / Category
    m_desc = re.search(
        r"for\s+[^,]+,\s*(?:a|an)\s+([^,.]+?(?:brand|chai|tea|drink|watch|beacon|device|technology|core|wearable))",
        brief,
        re.IGNORECASE,
    )
    if m_desc:
        product_desc = m_desc.group(1).strip()
    elif any(w in brief.lower() for w in ["chai", "tea"]):
        product_desc = "premium artisanal masala chai with aromatic spices"
    elif any(w in brief.lower() for w in ["energy", "drink"]):
        product_desc = "high-performance quantum energy beverage"
    elif any(w in brief.lower() for w in ["watch", "chrono"]):
        product_desc = "handcrafted mechanical timepiece of celestial precision"
    else:
        product_desc = f"mastercrafted {primary_subject.lower()} precision edition"

    # Determine genre
    lower_brief = brief.lower()
    if any(w in lower_brief for w in ["chai", "tea", "coffee", "bazaar", "artisan", "islamabad", "lahore", "karachi", "food", "cafe", "brew", "spice", "fragrance", "pakistani"]):
        genre = "chai"
    elif any(w in lower_brief for w in ["cyber", "neon", "tech", "ai", "future", "quantum"]):
        genre = "cyber"
    elif any(w in lower_brief for w in ["nature", "eco", "green", "earth", "sun", "organic"]):
        genre = "nature"
    elif any(w in lower_brief for w in ["space", "odyssey", "sci-fi", "alien", "canyon", "colony", "scout"]):
        genre = "space"
    else:
        genre = "luxury"

    return {
        "brand_name": brand_name,
        "char_name": char_name,
        "char_role": char_role,
        "location": location,
        "setting": setting,
        "product_desc": product_desc,
        "genre": genre,
        "primary_subject": primary_subject,
        "secondary_subject": secondary_subject,
    }


class MockPlannerProvider(BasePlannerProvider):
    """
    Deterministic rule-based video planner for mock mode.
    Synthesizes pacing, character specs, visual style, and shot lists
    calibrated for 30s ad mode (~5 shots) and 2.5m short film mode (~18 shots).
    """

    async def generate_plan(
        self,
        brief: str,
        mode: VideoMode,
        ref_images: Optional[List[str]] = None,
    ) -> Plan:
        entities = _extract_brief_entities(brief)
        brand_name = entities["brand_name"]
        char_name = entities["char_name"]
        char_role = entities["char_role"]
        location = entities["location"]
        setting = entities["setting"]
        product_desc = entities["product_desc"]
        product_name = brand_name
        genre = entities["genre"]
        primary_subject = entities["primary_subject"]
        secondary_subject = entities["secondary_subject"]

        if mode == VideoMode.ad:
            brief_title = f"{brand_name}: {location}" if location and location != brand_name else brand_name
        else:
            brief_title = f"{char_name}: The Journey" if char_name and "Hero" not in char_name else f"{primary_subject}: {secondary_subject}"

        # Style analysis based on genre
        if genre == "chai":
            palette = ["#260F08", "#C85A17", "#E5A030", "#FBF2E3"]
            lighting = f"Warm morning sunbeams filtering through rising steam across {location}"
            lens = "85mm f/1.4 portrait glass with creamy organic flare and golden bokeh"
            mood = f"Aromatic, artisanal, rich cultural heritage of {location}"
            char_appearance = f"Contemporary thoughtful demeanor, tailored silhouette, observing {location} on a {setting}"
        elif genre == "cyber":
            palette = ["#0A0F1D", "#00F0FF", "#7000FF", "#FF007A"]
            lighting = f"Atmospheric volumetric neon haze, high contrast rain-slicked reflections across {location}"
            lens = "Anamorphic 35mm T1.5 with subtle horizontal blue streak flares"
            mood = "Futuristic, visionary, electrifying tension"
            char_appearance = "Sleek tactical attire with subtle bioluminescent accents and focused gaze"
        elif genre == "nature":
            palette = ["#1B3B2B", "#F5EBE1", "#E6A15C", "#2D5A43"]
            lighting = f"Warm golden hour directional backlight with natural lens dust motes in {location}"
            lens = "50mm f/1.2 prime, creamy organic bokeh and warm falloff"
            mood = "Earthy, aspirational, deeply grounding and serene"
            char_appearance = "Weathered linen field jacket, candid warmth, attentive expression"
        elif genre == "space":
            palette = ["#050814", "#38BDF8", "#818CF8", "#F8FAFC"]
            lighting = f"Harsh glacial starlight cutting across icy terrain on {location}"
            lens = "Anamorphic 40mm cinematic prime with wide spatial curvature"
            mood = "Expansive, isolated, awe-inspiring cosmic mystery"
            char_appearance = "Pressurized exploration suit with frosted gold visor and utility harness"
        else:
            palette = ["#09090B", "#D4AF37", "#27272A", "#FFFFFF"]
            lighting = f"Sculptural studio rim-lighting with soft wrap key fill over {location}"
            lens = "Leica Noctilux 50mm f/0.95 cinematic glass"
            mood = "Prestige, confident, sleek and cinematic elegance"
            char_appearance = "Tailored minimalist silhouette with poised demeanor and sharp profile"

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
                    name=brand_name,
                    description=f"Artisanal {product_desc}, steam curling into the crisp {setting} air",
                    reference_image=ref_images[0] if ref_images else None,
                )
            ],
        )

        if mode == VideoMode.ad:
            # 5 shots across 3 scenes totalling ~32 seconds (within 30-45s)
            is_rainy = "rain" in setting or location == "Islamabad"
            vo_hook = (
                f"Rain over {location}. {char_name}'s day starts before the sun."
                if is_rainy and char_name
                else (f"{location}. Where ambition meets the dawn." if location else "In a world accelerating past human limits, compromise was once the only option.")
            )
            vo_reveal = (
                "A tradition perfected across generations. Pure aroma, uncompromised warmth."
                if genre == "chai"
                else f"Meet {brand_name}. Engineered not just to keep pace, but to set the rhythm."
            )
            vo_tagline = (
                f"{brand_name}. Brewed for the bold."
                if genre == "chai"
                else f"{brand_name}. Define your future today."
            )

            scenes = [
                Scene(
                    id="scene_1",
                    scene_number=1,
                    title=f"The Hook: Dawn in {location}",
                    summary=f"Establish the quiet morning atmosphere and {char_name}'s contemplative ritual.",
                    shots=[
                        Shot(
                            id="shot_1_1",
                            scene_id="scene_1",
                            duration_s=6.0,
                            description=f"Wide dynamic establish: city bustle in {location} where {char_name} pauses.",
                            camera="Wide tracking shot moving backwards, low angle, slow tilt up",
                            visual_prompt=f"Cinematic wide establishing shot of {location}, {setting} atmosphere with soft reflections, {char_name} walking with calm purpose, warm ambient interior light contrasting with the street, 8k resolution, cinematic color grade.",
                            vo_line=vo_hook,
                            characters_referenced=[char_name],
                            products_referenced=[],
                            chain_from_previous=False,
                            status=ShotStatus.pending,
                        ),
                        Shot(
                            id="shot_1_2",
                            scene_id="scene_1",
                            duration_s=7.0,
                            description=f"Intense close-up on {char_name} drafting with focused expression.",
                            camera="Extreme close-up pushing slowly into the iris, rack focus",
                            visual_prompt=f"Macro extreme close-up of {char_name}'s focused expression, soft natural rim lighting from window, delicate steam rising in the background, {lens}.",
                            vo_line="In the quiet moments before creation, every detail matters." if char_name else "Until every standard had to be reimagined from the foundation up.",
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
                    title=f"The Reveal: {brand_name}",
                    summary=f"Heroic presentation of {brand_name} with velvety steam and rich aroma.",
                    shots=[
                        Shot(
                            id="shot_2_1",
                            scene_id="scene_2",
                            duration_s=7.0,
                            description=f"Product reveal: {brand_name} freshly poured in dynamic space.",
                            camera="360-degree rotational arc orbiting the product",
                            visual_prompt=f"Hero commercial packshot of {brand_name}, {product_desc}, levitating amidst warm particles illuminated by {palette[1]}, ultra-clean commercial look.",
                            vo_line=vo_reveal,
                            characters_referenced=[],
                            products_referenced=[brand_name],
                            chain_from_previous=False,
                            status=ShotStatus.pending,
                        ),
                        Shot(
                            id="shot_2_2",
                            scene_id="scene_2",
                            duration_s=6.0,
                            description=f"{char_name} interacting with {brand_name} as energy bursts forward.",
                            camera="Medium close-up profile shot, handheld kinetic motion",
                            visual_prompt=f"Dynamic medium shot of {char_name} engaging with {brand_name}, surge of warm golden light across the frame, triumphant energy and renewed focus.",
                            vo_line="Crafted for the creators, the visionaries, the tireless." if char_name else "Instant response. Unmatched craftsmanship. Zero friction.",
                            characters_referenced=[char_name],
                            products_referenced=[brand_name],
                            chain_from_previous=True,
                            status=ShotStatus.pending,
                        ),
                    ],
                ),
                Scene(
                    id="scene_3",
                    scene_number=3,
                    title=f"The Climax: {brand_name} Statement",
                    summary="Iconic brand lockup, final aspirational gaze and signature tagline.",
                    shots=[
                        Shot(
                            id="shot_3_1",
                            scene_id="scene_3",
                            duration_s=6.0,
                            description=f"Hero freeze-frame transition into {brand_name} emblem and brand lockup.",
                            camera="Slow zoom out from product emblem to wide majestic horizon",
                            visual_prompt=f"Aesthetic brand lockup shot, {brand_name} resting proudly at center frame, backdrop of {location} horizon bathed in {palette[0]} gradients, prestigious typography.",
                            vo_line=vo_tagline,
                            characters_referenced=[char_name],
                            products_referenced=[brand_name],
                            chain_from_previous=False,
                            status=ShotStatus.pending,
                        ),
                    ],
                ),
            ]
            target_duration = 32.0

        else:
            # Short film mode: 18 shots across 5 scenes totalling 137 seconds (~150s, 4-8s each)
            scenes = [
                Scene(
                    id="scene_1",
                    scene_number=1,
                    title="Act I: The Boundary of Silence",
                    summary=f"Establish the atmospheric solitude of {char_name} across {location}.",
                    shots=[
                        Shot(
                            id="shot_1_1",
                            scene_id="scene_1",
                            duration_s=7.0,
                            description=f"Sweeping aerial landscape establishing the vast expanse of {location}.",
                            camera="Extreme wide drone descent, sweeping forward over atmospheric terrain",
                            visual_prompt=f"Cinematic wide establishing shot of vast desolate landscape, {lighting}, {mood}, atmospheric fog, 35mm anamorphic film grain.",
                            vo_line="They told us nothing survived past the perimeter wall.",
                            characters_referenced=[],
                            products_referenced=[],
                            chain_from_previous=False,
                        ),
                        Shot(
                            id="shot_1_2",
                            scene_id="scene_1",
                            duration_s=8.0,
                            description=f"{char_name} stands alone on an outpost ledge inspecting telemetry.",
                            camera="Medium wide slowly tracking left to right behind metal girders",
                            visual_prompt=f"Medium wide shot of {char_name}, {char_appearance}, standing at rusty vantage point observing the horizon, {lens}.",
                            vo_line="Seven months of static. Until this morning's harmonic spike.",
                            characters_referenced=[char_name],
                            products_referenced=[],
                            chain_from_previous=True,
                        ),
                        Shot(
                            id="shot_1_3",
                            scene_id="scene_1",
                            duration_s=7.0,
                            description="Close-up on instrument dial flickering erratically with phosphor numbers.",
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
                            description=f"Interior shelter: {char_name} retrieves {product_name} from a sealed case.",
                            camera="Low angle medium shot panning across worktable as locks hiss open",
                            visual_prompt=f"Low angle cinematic shot inside moody workshop, {lighting}, steam venting from container revealing {product_name}.",
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
                            visual_prompt=f"Close-up tactile gear preparation, intricate straps, {char_name}'s determined expression captured in reflection.",
                            vo_line=None,
                            characters_referenced=[char_name],
                            products_referenced=[product_name],
                            chain_from_previous=True,
                        ),
                        Shot(
                            id="shot_2_3",
                            scene_id="scene_2",
                            duration_s=8.0,
                            description="The heavy perimeter blast gate grinds open, revealing twilight beyond.",
                            camera="Ground level track following boots crossing the threshold line",
                            visual_prompt=f"Heavy industrial steel gate opening vertically with sparks, massive silhouette of {char_name} stepping into dense fog.",
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
                            visual_prompt=f"Epic wide shot of monolithic ancient architecture, {palette[0]} and {palette[1]} light shafts slicing through fog.",
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
                            camera="Handheld dutch angle tilting rapidly upward as rocks tumble",
                            visual_prompt="Dynamic handheld action shot, crumbling shale slope, dust explosion caught in beam of tactical light, intense urgency.",
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
                            visual_prompt=f"Over the shoulder shot of {char_name} activating {product_name}, crisp volumetric interface projecting coordinates.",
                            vo_line=f"{product_name} locked the beacon. Less than two hundred meters ahead.",
                            characters_referenced=[char_name],
                            products_referenced=[product_name],
                            chain_from_previous=True,
                        ),
                        Shot(
                            id="shot_3_4",
                            scene_id="scene_3",
                            duration_s=8.0,
                            description="Rapid sprint across a crumbling skybridge suspended over abyss.",
                            camera="Forward tracking dolly matching {char_name}'s sprint with whip pans",
                            visual_prompt=f"Kinetic chase perspective, {char_name} sprinting across suspension structure, lightning flashing in background clouds.",
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
                            description="Reaching a grand subterranean vault chamber where an orb pulses.",
                            camera="Slow majestic crane up revealing the enormous spherical relic",
                            visual_prompt=f"Awe-inspiring wide interior cavern, massive floating crystalline sphere emitting pulsating waves of {palette[1]} light.",
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
                            visual_prompt=f"Medium shot of {char_name} removing face shield, expression of profound revelation and wonder, eyes gleaming with prism light.",
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
                            visual_prompt=f"Intricate macro shot: {product_name} aligning into concentric ring mechanism, magnetic lock engages with shockwave.",
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
                            visual_prompt="Colossal scale wide shot, roof of vault dissolving into starry sky, pillars of light erupting towards the cosmos.",
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
                            visual_prompt="Breathtaking dawn landscape, gentle purple and amber morning hues, mist dissipating to reveal green valleys.",
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
                            visual_prompt=f"Heroic medium profile of {char_name}, calm and determined smile, golden hour glow illuminating face and gear.",
                            vo_line="Now we know it was only the prologue.",
                            characters_referenced=[char_name],
                            products_referenced=[],
                            chain_from_previous=True,
                        ),
                        Shot(
                            id="shot_5_3",
                            scene_id="scene_5",
                            duration_s=8.0,
                            description=f"Close-up of {product_name} glowing steadily with newly acquired coordinates.",
                            camera="Macro shot resting on the device surface as new constellations shift",
                            visual_prompt=f"Close-up macro of {product_name}, active interface displaying rotating stellar map, immaculate craftsmanship.",
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
                            visual_prompt=f"High angle cinematic crane shot receding upwards, lone figure of {char_name} striding confidently towards infinite horizons.",
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

    async def edit_plan(self, plan: Plan, message: str) -> PlanEdit:
        if settings.MOCK_DELAY_S > 0:
            await asyncio.sleep(min(0.1, settings.MOCK_DELAY_S))

        lower_msg = message.lower()

        # 1. Target scene identification
        target_scene = None
        scene_match = re.search(r"scene\s*(\d+)", lower_msg)
        shot_match = re.search(r"shot\s*(\d+)[\._](\d+)", lower_msg)

        if scene_match:
            scene_num = int(scene_match.group(1))
            for sc in plan.scenes:
                if sc.scene_number == scene_num:
                    target_scene = sc
                    break
        elif shot_match:
            s_num = int(shot_match.group(1))
            for sc in plan.scenes:
                if sc.scene_number == s_num:
                    target_scene = sc
                    break

        if not target_scene:
            target_scene = plan.scenes[1] if len(plan.scenes) > 1 else plan.scenes[0]

        target_scene_id = target_scene.id
        target_shot_ids = [s.id for s in target_scene.shots]

        # 2. Extract specific line change if any
        line_match = re.search(r'(?:change\s+(?:the\s+)?line\s+to|say)\s+["\']([^"\']+)["\']', message, re.IGNORECASE)
        new_line = line_match.group(1) if line_match else None

        # 3. Determine aesthetic refinement
        palette_shift = None
        color_tag = ""
        summary_tone = "updated"

        if any(w in lower_msg for w in ["warmer", "warm", "amber", "golden", "sun"]):
            color_tag = "Warmer tone with golden-amber illumination and rich warm palette"
            palette_shift = ["#260D04", "#D84315", "#FF8F00", "#FFF3E0"]
            summary_tone = "warmer golden tone"
        elif any(w in lower_msg for w in ["cooler", "cool", "blue", "teal", "cold", "cyan"]):
            color_tag = "Cooler tone with crisp cyan rim lighting and cold twilight shadows"
            palette_shift = ["#020E1A", "#0091EA", "#00E5FF", "#E1F5FE"]
            summary_tone = "cooler atmospheric blue tone"
        elif any(w in lower_msg for w in ["darker", "shadow", "noir", "moody"]):
            color_tag = "Deep chiaroscuro shadows and moody low-key silhouette lighting"
            palette_shift = ["#050505", "#212121", "#424242", "#FAFAFA"]
            summary_tone = "darker and moodier visual grade"
        elif any(w in lower_msg for w in ["brighter", "vibrant", "sunlight", "pop"]):
            color_tag = "High-key vibrant lighting with vivid color saturation and crisp exposure"
            palette_shift = ["#1A1A1A", "#FFD600", "#FF1744", "#FFFFFF"]
            summary_tone = "brighter high-key lighting"
        elif any(w in lower_msg for w in ["faster", "dynamic", "kinetic", "action"]):
            color_tag = "Kinetic handheld camera motion with whip pans and high energy pacing"
            summary_tone = "more kinetic action pacing"
        elif any(w in lower_msg for w in ["slower", "gentle", "serene", "calm"]):
            color_tag = "Slow meditative dolly gliding motion with soft dreamy focus"
            summary_tone = "slower meditative pacing"
        elif any(w in lower_msg for w in ["dramatic", "epic"]):
            color_tag = "Monumental low angle framing with sweeping cinematic contrast"
            summary_tone = "more dramatic cinematic framing"
        else:
            color_tag = f"Refined style directive: {message}"
            summary_tone = "refined visual tone"

        updated_shots: List[UpdatedShotSpec] = []
        for i, shot in enumerate(target_scene.shots):
            new_prompt = f"{shot.visual_prompt} | Refinement: {color_tag}"
            vo = new_line if (new_line and i == 0) else shot.vo_line
            updated_shots.append(
                UpdatedShotSpec(
                    id=shot.id,
                    visual_prompt=new_prompt,
                    description=f"{shot.description} ({summary_tone})",
                    vo_line=vo,
                )
            )

        new_style = None
        if palette_shift:
            new_style = BibleStyle(
                palette=palette_shift,
                lighting=f"Refined: {color_tag}",
                lens=plan.bible.style.lens,
                mood=f"{summary_tone.capitalize()}, {plan.bible.style.mood}",
            )

        summary = (
            f"Director refinement applied to {target_scene.title} ({target_scene_id}): "
            f"updated {len(target_shot_ids)} shot(s) with {summary_tone}."
        )

        return PlanEdit(
            summary=summary,
            target_scene_ids=[target_scene_id],
            target_shot_ids=target_shot_ids,
            updated_shots=updated_shots,
            style_changes=new_style,
            rationale=f"Requested by director: '{message}'",
        )


class MockImageProvider(BaseImageProvider):
    """Generates mock reference sheets and keyframe PNGs using Pillow."""

    async def generate_ref_sheet(
        self,
        entity_type: str,
        name: str,
        description: str,
        style: BibleStyle,
        job_dir: Path,
        ref_image: Optional[str] = None,
    ) -> str:
        if settings.MOCK_DELAY_S > 0:
            await asyncio.sleep(min(0.1, settings.MOCK_DELAY_S))

        filename = f"ref_{entity_type}_{re.sub(r'[^a-zA-Z0-9_]', '_', name.lower())}.png"
        out_path = job_dir / filename

        bg_rgb = _hex_to_rgb(style.palette[0] if style.palette else "#0a0f1d")
        accent_rgb = _hex_to_rgb(style.palette[1] if len(style.palette) > 1 else "#00f0ff")

        img = Image.new("RGB", (1280, 720), color=bg_rgb)
        draw = ImageDraw.Draw(img)

        # Border and header
        draw.rectangle([20, 20, 1260, 700], outline=accent_rgb, width=3)
        draw.rectangle([20, 20, 1260, 90], fill=(20, 25, 40))
        draw.text((40, 35), "SHOWRUNNER AI // PRODUCTION BIBLE REFERENCE SHEET", fill=(148, 163, 184))
        draw.text((40, 58), f"TYPE: {entity_type.upper()} REFERENCE", fill=accent_rgb)

        # Main Name and details
        draw.text((50, 130), name.upper(), fill=(255, 255, 255))
        draw.rectangle([50, 160, 800, 162], fill=accent_rgb)

        # Description text
        lines = [description[i:i + 70] for i in range(0, len(description), 70)]
        y = 190
        for line in lines:
            draw.text((50, y), line, fill=(226, 232, 240))
            y += 28

        # Style swatches at bottom
        draw.text((50, 590), "STYLE PALETTE ALIGNMENT:", fill=(148, 163, 184))
        x = 50
        for color_hex in style.palette:
            col = _hex_to_rgb(color_hex)
            draw.rectangle([x, 620, x + 60, 660], fill=col, outline=(255, 255, 255), width=1)
            draw.text((x, 668), color_hex, fill=(148, 163, 184))
            x += 80

        img.save(out_path, "PNG")
        return str(out_path)

    async def generate_keyframe(
        self,
        prompt: str,
        style: BibleStyle,
        ref_image: Optional[str] = None,
        job_dir: Optional[Path] = None,
        shot_id: Optional[str] = None,
        scene_num: int = 1,
        shot_num: int = 1,
        attempt: int = 1,
        ref_images: Optional[List[str]] = None,
    ) -> str:
        if settings.MOCK_DELAY_S > 0:
            await asyncio.sleep(min(0.1, settings.MOCK_DELAY_S))

        filename = f"{shot_id or 'shot'}_att{attempt}_keyframe.png"
        out_path = (job_dir or Path("./data")) / filename

        # Vivid gradient background using style palette or prompt refinement tint
        c1 = _hex_to_rgb(style.palette[0] if style.palette else "#1e293b")
        c2 = _hex_to_rgb(style.palette[1] if len(style.palette) > 1 else "#3b82f6")
        lower_prompt = prompt.lower()
        if "warmer" in lower_prompt or "warm" in lower_prompt:
            c1 = (130, 45, 12)
            c2 = (255, 140, 20)
        elif "cooler" in lower_prompt or "cool" in lower_prompt:
            c1 = (8, 35, 75)
            c2 = (15, 160, 230)

        # If ref_image (e.g. from continuity chaining) is provided, load and optionally tint it
        loaded_img = None
        if ref_image:
            ref_path = Path(ref_image)
            if not ref_path.exists() and job_dir:
                ref_path = job_dir / Path(ref_image).name
            if ref_path.exists():
                try:
                    loaded_img = Image.open(ref_path).convert("RGB").resize((1280, 720))
                    if "warmer" in lower_prompt or "warm" in lower_prompt:
                        warm_overlay = Image.new("RGB", (1280, 720), color=(245, 130, 20))
                        loaded_img = Image.blend(loaded_img, warm_overlay, alpha=0.25)
                    elif "cooler" in lower_prompt or "cool" in lower_prompt:
                        cool_overlay = Image.new("RGB", (1280, 720), color=(10, 150, 230))
                        loaded_img = Image.blend(loaded_img, cool_overlay, alpha=0.25)
                except Exception:
                    loaded_img = None

        if loaded_img:
            img = loaded_img
        else:
            img = Image.new("RGB", (1280, 720), color=c1)
            draw_grad = ImageDraw.Draw(img)
            # Draw smooth vertical color gradient
            for y_coord in range(0, 720, 2):
                ratio = y_coord / 720.0
                r = int(c1[0] * (1 - ratio) + c2[0] * ratio)
                g = int(c1[1] * (1 - ratio) + c2[1] * ratio)
                b = int(c1[2] * (1 - ratio) + c2[2] * ratio)
                draw_grad.line([(0, y_coord), (1280, y_coord)], fill=(r, g, b), width=2)

        draw = ImageDraw.Draw(img)

        # Outer framing
        accent_rgb = (255, 200, 50) if "warm" in lower_prompt else (6, 182, 212)
        draw.rectangle([15, 15, 1265, 705], outline=accent_rgb, width=2)

        # Header tag
        tag_bg = (245, 158, 11) if attempt > 1 else accent_rgb
        draw.rectangle([30, 30, 380, 70], fill=tag_bg)
        draw.text((45, 42), f"SCENE {scene_num} · SHOT {shot_num} · ATTEMPT #{attempt}", fill=(0, 0, 0))

        draw.text((30, 95), f"SHOT ID: {shot_id or 'N/A'}", fill=(240, 240, 250))

        # Prompt box
        draw.rectangle([30, 130, 1250, 240], fill=(16, 20, 32), outline=(60, 75, 110))
        draw.text((45, 145), "VISUAL PROMPT BLUEPRINT:", fill=accent_rgb)
        prompt_lines = [prompt[i:i + 85] for i in range(0, min(240, len(prompt)), 85)]
        py = 175
        for pline in prompt_lines:
            draw.text((45, py), pline, fill=(226, 232, 240))
            py += 24

        # Center watermark box
        draw.rectangle([400, 320, 880, 480], fill=(20, 25, 40), outline=accent_rgb, width=2)
        draw.text((450, 375), f"SHOWRUNNER // SHOT {shot_id or ''}", fill=(255, 255, 255))
        draw.text((475, 410), f"1280x720 • 24 FPS • MOOD: {style.mood[:24]}", fill=(180, 195, 215))

        # Lighting & lens footer
        draw.text((30, 640), f"LIGHTING: {style.lighting[:65]}", fill=(220, 230, 245))
        draw.text((30, 665), f"LENS: {style.lens[:65]}", fill=(200, 215, 235))

        img.save(out_path, "PNG")
        return str(out_path)


class MockVideoProvider(BaseVideoProvider):
    """Generates mock video clips animating keyframe images via FFmpeg with continuity chaining."""

    async def generate_clip(
        self,
        prompt: str,
        duration_s: float = 8.0,
        initial_frame: Optional[str] = None,
        job_dir: Optional[Path] = None,
        shot_id: Optional[str] = None,
        scene_num: int = 1,
        shot_num: int = 1,
        attempt: int = 1,
        reference_images: Optional[List[str]] = None,
    ) -> str:
        if settings.MOCK_DELAY_S > 0:
            await asyncio.sleep(min(0.15, settings.MOCK_DELAY_S))

        filename = f"{shot_id or 'shot'}_att{attempt}.mp4"
        out_path = (job_dir or Path("./data")) / filename

        dur_str = f"{duration_s:.2f}"

        # Locate the keyframe PNG to animate
        source_img = None
        if initial_frame and Path(initial_frame).exists():
            source_img = Path(initial_frame)
        elif job_dir and shot_id:
            candidate = job_dir / f"{shot_id}_att{attempt}_keyframe.png"
            if candidate.exists():
                source_img = candidate
            else:
                matches = sorted(list(job_dir.glob(f"{shot_id}*keyframe.png")))
                if matches:
                    source_img = matches[-1]

        if not source_img or not source_img.exists():
            fallback_kf = (job_dir or Path("./data")) / f"{shot_id or 'shot'}_att{attempt}_keyframe.png"
            if not fallback_kf.exists():
                fallback_img = Image.new("RGB", (1280, 720), color=(25, 30, 45))
                d = ImageDraw.Draw(fallback_img)
                d.rectangle([15, 15, 1265, 705], outline=(6, 182, 212), width=2)
                d.text((40, 40), f"SCENE {scene_num} · SHOT {shot_num} · ATTEMPT #{attempt}", fill=(255, 255, 255))
                fallback_img.save(fallback_kf, "PNG")
            source_img = fallback_kf

        is_chained = bool(initial_frame and Path(initial_frame).exists() and "_lastframe" in Path(initial_frame).name)
        chain_label = " [CHAINED]" if is_chained else ""
        safe_header = f"Scene {scene_num} · Shot {shot_num} · Attempt {attempt}{chain_label}"
        safe_prompt = re.sub(r"[^a-zA-Z0-9 _\-]", "", prompt[:40])

        lower_prompt = prompt.lower()
        color_filter = ""
        if "warmer" in lower_prompt or "warm" in lower_prompt:
            accent_col = "yellow"
            color_filter = ",colorbalance=rs=0.30:gs=0.10:bs=-0.22:rm=0.28:gm=0.08:bm=-0.18"
        elif "cooler" in lower_prompt or "cool" in lower_prompt:
            accent_col = "cyan"
            color_filter = ",colorbalance=rs=-0.22:gs=-0.05:bs=0.30:rm=-0.18:gm=-0.05:bm=0.28"
        else:
            accent_col = "yellow"

        vf_chain = (
            f"scale=1280:720,"
            f"zoompan=z='min(zoom+0.0008,1.06)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1280x720:fps=24"
            f"{color_filter},"
            f"drawtext=text='{safe_header}':x=40:y=40:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=6,"
            f"drawtext=text='{safe_prompt}':x=40:y=76:fontsize=18:fontcolor={accent_col}:box=1:boxcolor=black@0.65:boxborderw=4"
        )

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", str(source_img),
            "-t", dur_str,
            "-vf", vf_chain,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-r", "24",
            str(out_path),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await proc.wait()
        return str(out_path)


class MockCriticProvider(BaseCriticProvider):
    """Reviews clips against bible and shot specifications with deterministic quality reviews."""

    async def critique_clip(
        self,
        video_path: str,
        shot: Shot,
        bible: Bible,
        attempt: int = 1,
    ) -> CritiqueResult:
        if settings.MOCK_DELAY_S > 0:
            await asyncio.sleep(min(0.2, settings.MOCK_DELAY_S))

        # Deterministic failure rule:
        # Fail attempt 1 for shot 2 in scene 1 (e.g. shot_1_2), and certain middle shots
        should_fail_first_try = (
            shot.id in ("shot_1_2", "shot_2_2", "shot_3_2")
            or shot.id.endswith("_2")
        )

        if attempt == 1 and should_fail_first_try:
            char_name = bible.characters[0].name if bible.characters else "Protagonist"
            return CritiqueResult(
                shot_id=shot.id,
                passed=False,
                score=0.52,
                reasons=[
                    f"Character {char_name}'s wardrobe drifted from crimson jacket to navy blue (drift vs visual bible)",
                    "Lighting contrast too low compared to style palette requirements",
                    "Volumetric haze density inconsistent across frame boundary",
                ],
                suggested_prompt_fix=(
                    f"Enforce exact crimson jacket #990000 with silver buckles for {char_name}, "
                    f"increase directional rim lighting contrast, align with {bible.style.palette[1]}"
                ),
            )

        # Passes on retry (attempt > 1) or compliant shots
        return CritiqueResult(
            shot_id=shot.id,
            passed=True,
            score=0.94 if attempt > 1 else 0.91,
            reasons=[
                "Visual bible style palette strictly respected",
                "Character facial consistency and costume match reference sheet",
                "Camera perspective and pacing optimal",
            ],
            suggested_prompt_fix=None,
        )


class MockTTSProvider(BaseTTSProvider):
    """Generates mock synthesized voiceover audio files."""

    async def generate_voiceover(
        self,
        text: str,
        duration_s: Optional[float] = None,
        voice_name: Optional[str] = None,
        job_dir: Optional[Path] = None,
        shot_id: Optional[str] = None,
    ) -> str:
        if settings.MOCK_DELAY_S > 0:
            await asyncio.sleep(min(0.1, settings.MOCK_DELAY_S))

        filename = f"{shot_id or 'vo'}_speech.m4a"
        out_path = (job_dir or Path("./data")) / filename

        word_count = len(text.split())
        est_dur = max(1.5, min(duration_s or 8.0, word_count * 0.35))
        dur_str = f"{est_dur:.2f}"

        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"sine=frequency=360:duration={dur_str},volume=0.3,afade=t=in:ss=0:d=0.1,afade=t=out:st={est_dur-0.2:.2f}:d=0.2",
            "-c:a", "aac",
            "-b:a", "128k",
            str(out_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await proc.wait()
        return str(out_path)


class MockMusicProvider(BaseMusicProvider):
    """Generates mock background ambient soundtrack via harmonic synthesis."""

    async def generate_music(
        self,
        prompt: str,
        duration_s: float,
        job_dir: Optional[Path] = None,
    ) -> str:
        if settings.MOCK_DELAY_S > 0:
            await asyncio.sleep(min(0.1, settings.MOCK_DELAY_S))

        filename = "soundtrack.m4a"
        out_path = (job_dir or Path("./data")) / filename

        dur_str = f"{duration_s:.2f}"
        fade_out_start = f"{max(0.5, duration_s - 2.0):.2f}"

        synth_expr = (
            "sin(2*PI*220*t)*0.15 + sin(2*PI*329.63*t)*0.12 + sin(2*PI*440*t)*0.08"
        )
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"aevalsrc={synth_expr}:d={dur_str}:s=44100,volume=0.4,afade=t=in:ss=0:d=1.5,afade=t=out:st={fade_out_start}:d=2.0",
            "-c:a", "aac",
            "-b:a", "192k",
            str(out_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await proc.wait()
        return str(out_path)
