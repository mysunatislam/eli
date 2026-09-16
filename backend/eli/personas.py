"""Eli Multi-Character / Avatar Persona System.

Supports 5 distinct avatars (feminine, masculine, and industry-specific commercial domains):
1. Eli Classic (Personal Companion - Feminine)
2. Atlas (Agency & Software Engineering - Masculine)
3. Aria (Hospitality & Hotel Management - Refined Concierge)
4. Zephyr (Event & Project Management - Dynamic Masculine)
5. Mentor (Teach Me & Procedural Guide - Instructional Tutor)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Persona:
    id: str
    name: str
    display: str
    tagline: str
    gender: str
    voice: str
    color_accent: str
    color_light: str
    color_dark: str
    shape: str  # 'heart', 'shield', 'crest', 'spark', 'compass'
    system_prompt: str
    suggested_skills: list[str] = field(default_factory=list)


PERSONAS: dict[str, Persona] = {
    "classic": Persona(
        id="classic",
        name="Eli",
        display="Eli (Companion)",
        tagline="Your warm, helpful desktop companion",
        gender="feminine",
        voice="en-US-JennyNeural",
        color_accent="#F0567A",
        color_light="#FF7A98",
        color_dark="#C93E5E",
        shape="heart",
        system_prompt=(
            "You are Eli, a warm, charming, and attentive personal companion on the user's desktop. "
            "You speak with a natural, friendly tone, care about the user's wellbeing and workflow, "
            "and assist seamlessly with daily tasks, research, and focus."
        ),
        suggested_skills=["reminders", "ambient_memory", "quick_answers", "desktop_control"],
    ),
    "agency": Persona(
        id="agency",
        name="Atlas",
        display="Atlas (Agency & Tech)",
        tagline="High-velocity agency engineering & operations",
        gender="masculine",
        voice="en-US-GuyNeural",
        color_accent="#3A86FF",
        color_light="#60A5FA",
        color_dark="#1D4ED8",
        shape="shield",
        system_prompt=(
            "You are Atlas, a sharp, decisive, and pragmatic software & technical agency partner. "
            "You communicate with crisp technical clarity, prioritize code correctness, git hygiene, "
            "automated testing, rapid debugging, and client project delivery without unnecessary fluff."
        ),
        suggested_skills=["code_review", "git_operations", "terminal_tasks", "api_integration"],
    ),
    "hotel": Persona(
        id="hotel",
        name="Aria",
        display="Aria (Hospitality Concierge)",
        tagline="Hotel, guest & reservation management concierge",
        gender="feminine",
        voice="en-US-AriaNeural",
        color_accent="#E0A96D",
        color_light="#F3C68F",
        color_dark="#B87D3B",
        shape="crest",
        system_prompt=(
            "You are Aria, an executive hospitality concierge specializing in hotel management, "
            "front-desk operations, reservation systems, guest communications, and VIP services. "
            "You are exceedingly polite, proactive, detail-oriented, and master guest delight, "
            "room status tracking, and vendor coordination."
        ),
        suggested_skills=["guest_communications", "booking_workflows", "crm_updates", "vendor_contacts"],
    ),
    "event": Persona(
        id="event",
        name="Zephyr",
        display="Zephyr (Event Coordinator)",
        tagline="Dynamic event planning & timeline producer",
        gender="masculine",
        voice="en-US-DavisNeural",
        color_accent="#8338EC",
        color_light="#A855F7",
        color_dark="#6B21A8",
        shape="spark",
        system_prompt=(
            "You are Zephyr, an energetic, proactive event producer and operations manager. "
            "You excel at run-of-show schedules, vendor timelines, venue setups, budgeting checklists, "
            "and keeping chaotic live events running like clockwork with high energy, accountability, "
            "and calm execution."
        ),
        suggested_skills=["run_of_show", "timeline_tracking", "budget_checklists", "vendor_coordination"],
    ),
    "mentor": Persona(
        id="mentor",
        name="Mentor",
        display="Mentor (Teach Me & Guide)",
        tagline="Procedural software tutor & workflow author",
        gender="masculine",
        voice="en-US-ChristopherNeural",
        color_accent="#2EC4B6",
        color_light="#5EEAD4",
        color_dark="#0F766E",
        shape="compass",
        system_prompt=(
            "You are Mentor, a patient, structured, and pedagogical procedural instructor. "
            "You specialize in teaching complex desktop software (CAD, 3D modeling, medical imaging, "
            "coding suites) step-by-step. You explain the 'why' behind each button or tool, "
            "monitor progress visually, guide learners through mistakes, and author crystal-clear procedural guides."
        ),
        suggested_skills=["workflow_guidance", "teach_me_recording", "visual_verification", "cad_mentoring"],
    ),
}

DEFAULT_PERSONA = "classic"

PERSONA_ALIASES: dict[str, str] = {
    "atlas": "agency",
    "aria": "hotel",
    "zephyr": "event",
    "eli": "classic",
    "companion": "classic",
    "teach": "mentor",
}


def get_persona(persona_id: Optional[str]) -> Persona:
    """Retrieve persona by ID or name/alias, falling back to DEFAULT_PERSONA."""
    if not persona_id:
        return PERSONAS[DEFAULT_PERSONA]
    pid = persona_id.lower().strip()
    if pid in PERSONAS:
        return PERSONAS[pid]
    if pid in PERSONA_ALIASES and PERSONA_ALIASES[pid] in PERSONAS:
        return PERSONAS[PERSONA_ALIASES[pid]]
    for p in PERSONAS.values():
        if p.name.lower() == pid or p.id.lower() == pid:
            return p
    return PERSONAS[DEFAULT_PERSONA]


def list_personas() -> list[dict]:
    """Return serialized personas for UI and client consumers."""
    return [
        {
            "id": p.id,
            "name": p.name,
            "display": p.display,
            "tagline": p.tagline,
            "gender": p.gender,
            "voice": p.voice,
            "color": p.color_accent,
            "color_light": p.color_light,
            "color_dark": p.color_dark,
            "shape": p.shape,
        }
        for p in PERSONAS.values()
    ]


def find_persona_by_query(query: str) -> Optional[Persona]:
    """Resolve a user's natural language request to a persona."""
    import re
    low = query.lower().strip()
    # Direct ID match
    if low in PERSONAS:
        return PERSONAS[low]
    if low in PERSONA_ALIASES and PERSONA_ALIASES[low] in PERSONAS:
        return PERSONAS[PERSONA_ALIASES[low]]

    # Check persona names directly
    for p in PERSONAS.values():
        if p.name.lower() == low:
            return p

    # Domain keywords first (specific domains take priority over general gender hints)
    if re.search(r"\b(hotel|hotels|hospitality|concierge|front desk|guest|resort|aria)\b", low):
        return PERSONAS["hotel"]

    if re.search(r"\b(event|events|coordinator|producer|wedding|conference|party|zephyr)\b", low):
        return PERSONAS["event"]

    if re.search(r"\b(teach|teacher|mentor|guide|tutor|instructor|professor|learn|how to)\b", low):
        return PERSONAS["mentor"]

    if re.search(r"\b(agency|agencys|agencies|tech|developer|coding|software engineer|dev|atlas)\b", low):
        return PERSONAS["agency"]

    # Masculine / Feminine general requests
    if re.search(r"\b(masculine|male|guy|guys|boy|man|men|bro|dude)\b", low):
        return PERSONAS["agency"]

    if re.search(r"\b(feminine|female|girl|woman|women|original|default|classic|heart|companion)\b", low):
        return PERSONAS["classic"]

    return None
