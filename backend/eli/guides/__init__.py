"""Guided workflows: data-driven, step-by-step procedures Eli walks the user through with on-screen
indicators and narration. Each workflow has one variant per CAD application (the logical steps are the
same, the UI labels and shortcuts differ). Workflows come from two places: the built-ins registered
below, and eli/guides/planner.py, which writes one on the fly for any goal in any app by looking at
the live screen (same step schema, so the engine runs both identically).

Step fields
    id           short identifier
    title        2-5 words shown on the step card
    say          narration (spoken)
    instruction  precise on-screen text: exact button names, shortcuts, what to click
    target       where to point:
                   {"kind": "ui_text", "text": "TRIM", "alt": ["Trim"], "region": "top"|"left"|"right"|"bottom"|None}
                   {"kind": "geometry", "query": "<what to find in the viewport>", "multi": bool}
                   {"kind": "key", "keys": "T"}                       (a key-cap badge next to the heart)
                   {"kind": "window_region", "rel": [x0, y0, x1, y1]}  (fractions of the app window)
    expect       any-of completion conditions:
                   {"kind": "ocr_contains", "text": "FINISH SKETCH"}   (text visible in the app window)
                   {"kind": "ocr_absent", "text": "..."}
                   {"kind": "title_contains", "text": "..."}
                   {"kind": "vision", "question": "Is ...? "}           (vision model yes/no, rate-limited)
                   {"kind": "manual"}                                   (user says next / done)
    timeout      seconds before Eli offers help (default 90)
    alt          alternative way, offered when stuck
    optional     can be skipped with "skip"
"""
from __future__ import annotations

from .merge_holes import MERGE_HOLES

WORKFLOWS = {MERGE_HOLES["id"]: MERGE_HOLES}


def find_workflow(text: str):
    """Match a user request to a workflow by keywords (all keyword groups must hit)."""
    low = text.lower()
    best = None
    for wf in WORKFLOWS.values():
        groups = wf.get("keywords", [])
        if groups and all(any(k in low for k in group) for group in groups):
            best = wf
            break
    return best
