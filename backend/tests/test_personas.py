"""Tests for the 5-Avatar Commercial Persona System and red-flag feature purge."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from eli.personas import PERSONAS, get_persona, list_personas, find_persona_by_query
from eli.intents import extract_persona_switch, match


def test_personas_registry_count_and_keys():
    assert len(PERSONAS) == 5
    expected_keys = {"classic", "agency", "hotel", "event", "mentor"}
    assert set(PERSONAS.keys()) == expected_keys


def test_personas_attributes():
    # Eli Classic
    classic = PERSONAS["classic"]
    assert classic.id == "classic"
    assert classic.name == "Eli"
    assert classic.gender == "feminine"
    assert "Jenny" in classic.voice
    assert classic.shape == "heart"
    assert classic.color_accent.upper() == "#F0567A"

    # Atlas (Agency)
    atlas = PERSONAS["agency"]
    assert atlas.id == "agency"
    assert atlas.name == "Atlas"
    assert atlas.gender == "masculine"
    assert "Guy" in atlas.voice
    assert atlas.shape == "shield"
    assert atlas.color_accent.upper() == "#3A86FF"

    # Aria (Hotel)
    aria = PERSONAS["hotel"]
    assert aria.id == "hotel"
    assert aria.name == "Aria"
    assert aria.gender == "feminine"
    assert "Aria" in aria.voice
    assert aria.shape == "crest"
    assert aria.color_accent.upper() == "#E0A96D"

    # Zephyr (Event)
    zephyr = PERSONAS["event"]
    assert zephyr.id == "event"
    assert zephyr.name == "Zephyr"
    assert zephyr.gender == "feminine"
    assert "Sara" in zephyr.voice
    assert zephyr.shape == "spark"
    assert zephyr.color_accent.upper() == "#8338EC"

    # Mentor
    mentor = PERSONAS["mentor"]
    assert mentor.id == "mentor"
    assert mentor.name == "Mentor"
    assert "Christopher" in mentor.voice
    assert mentor.shape == "compass"
    assert mentor.color_accent.upper() == "#2EC4B6"


def test_get_persona():
    # Both ID and name/alias resolve cleanly
    assert get_persona("agency").id == "agency"
    assert get_persona("atlas").id == "agency"
    assert get_persona("hotel").id == "hotel"
    assert get_persona("aria").id == "hotel"
    assert get_persona("event").id == "event"
    assert get_persona("zephyr").id == "event"
    assert get_persona("mentor").id == "mentor"
    assert get_persona("classic").id == "classic"
    assert get_persona("eli").id == "classic"
    # Fallback to classic
    assert get_persona("unknown_id_xyz").id == "classic"
    assert get_persona(None).id == "classic"


def test_list_personas():
    lst = list_personas()
    assert len(lst) == 5
    ids = [p["id"] for p in lst]
    assert "classic" in ids
    assert "agency" in ids
    assert "hotel" in ids
    assert "event" in ids
    assert "mentor" in ids


def test_find_persona_by_query():
    assert find_persona_by_query("masculine").id == "agency"
    assert find_persona_by_query("a male character").id == "agency"
    assert find_persona_by_query("eli for agencys").id == "agency"
    assert find_persona_by_query("tech lead avatar").id == "agency"

    assert find_persona_by_query("feminine").id == "classic"
    assert find_persona_by_query("hotel management").id == "hotel"
    assert find_persona_by_query("hospitality website").id == "hotel"
    assert find_persona_by_query("concierge").id == "hotel"

    assert find_persona_by_query("event management").id == "event"
    assert find_persona_by_query("event coordinator").id == "event"

    assert find_persona_by_query("teach me").id == "mentor"
    assert find_persona_by_query("mentor avatar").id == "mentor"
    assert find_persona_by_query("procedural guide instructor").id == "mentor"


def test_extract_persona_switch():
    # Masculine / Agency
    res = extract_persona_switch("switch to masculine")
    assert res is not None
    kind, args = res
    assert kind == "switch_persona"
    assert args == ["agency"]

    res = extract_persona_switch("eli for agencys")
    assert res is not None
    assert res[1] == ["agency"]

    # Hotel / Hospitality
    res = extract_persona_switch("ellie for hotel management website")
    assert res is not None
    assert res[1] == ["hotel"]

    res = extract_persona_switch("switch avatar to aria")
    assert res is not None
    assert res[1] == ["hotel"]

    # Event management
    res = extract_persona_switch("elli for event management")
    assert res is not None
    assert res[1] == ["event"]

    # Teach me / Mentor
    res = extract_persona_switch("elli to teach me")
    assert res is not None
    assert res[1] == ["mentor"]

    res = extract_persona_switch("switch to mentor")
    assert res is not None
    assert res[1] == ["mentor"]

    # Companion / Classic
    res = extract_persona_switch("switch back to classic eli")
    assert res is not None
    assert res[1] == ["classic"]


def test_intent_matching_personas():
    m = match("switch to masculine")
    assert m is not None
    kind, args = m
    assert kind == "switch_persona"
    assert args == ["agency"]

    m = match("ellie for hotel management website")
    assert m is not None
    assert m[0] == "switch_persona"
    assert m[1] == ["hotel"]

    m = match("elli for event management")
    assert m is not None
    assert m[0] == "switch_persona"
    assert m[1] == ["event"]

    m = match("elli to teach me")
    assert m is not None
    assert m[0] == "switch_persona"
    assert m[1] == ["mentor"]


def test_purged_red_flags_no_matches():
    # 1. auto_allow must not match auto_allow action
    m1 = match("auto allow antigravity")
    if m1:
        assert m1[0] != "auto_allow_on"
        assert m1[0] != "auto_allow_off"
        assert m1[0] != "click_allow"

    # 2. skip ad must not match skip_ad action
    m2 = match("skip ad")
    if m2:
        assert m2[0] != "skip_ad"

    # 3. Complaints must not match user_complaint actions
    m3 = match("why did you open that")
    if m3:
        assert m3[0] != "user_complaint_wrong_action"
