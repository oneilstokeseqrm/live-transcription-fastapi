"""Pydantic models for Granola HTTP API responses.

Shapes match the **empirically-verified** Granola API at
``https://public-api.granola.ai/v1`` as probed during Phase 0
(see ``docs/superpowers/specs/2026-05-22-granola-integration-brainstorm-decisions.md``
§"Empirical Granola API findings" and ``tasks/granola-integration-plan.md``
§Phase 0 step 6).

Notes on the schema:

* Folder IDs use the ``fol_`` prefix; note IDs use the ``not_`` prefix.
* ``GET /v1/folders`` returns ``{folders, hasMore, cursor}``; the client
  unwraps the ``folders`` array before returning to callers.
* Speaker labels in transcript turns are audio-source labels only
  (``microphone`` for the API key holder; ``speaker`` for everything
  else) — no name-level diarization in the transcript itself. Use the
  separate ``attendees`` list for who-was-there.
* ``attendees`` carries names + emails when the meeting was linked to a
  Google Calendar event; only the API key holder for ad-hoc captures.
* ``calendar_event`` is present when Granola matched the meeting to a
  calendar entry, otherwise ``None``.

Every model uses ``model_config = ConfigDict(extra="allow")`` so a
future additive change to Granola's response shape won't fail
validation here; new fields just won't be exposed to callers until
they're added to the model explicitly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Sub-models (referenced by GranolaNoteDetail)
# ---------------------------------------------------------------------------


class Attendee(BaseModel):
    """A meeting attendee, as Granola returns it in
    ``GET /v1/notes/{id}`` ``attendees``.

    Both fields are optional because the shape varies with the source:
    calendar-linked meetings carry both; ad-hoc captures carry only the
    API key holder, and Granola may omit the email when the platform
    couldn't resolve one.
    """

    model_config = ConfigDict(extra="allow")

    name: Optional[str] = None
    email: Optional[str] = None


class CalendarEvent(BaseModel):
    """A Google Calendar event Granola linked the meeting to.

    Present when Granola successfully matched the captured meeting to a
    calendar entry; ``None`` on the parent model otherwise. Only ``id``
    is currently relied on downstream (LOCKED-36 emits it as
    ``extras.granola_calendar_event_id``).
    """

    model_config = ConfigDict(extra="allow")

    id: str


class TranscriptTurn(BaseModel):
    """One turn from Granola's per-meeting transcript array.

    Returned by ``GET /v1/notes/{id}?include=transcript``. ``speaker``
    is the audio-source label (``microphone`` for the API key holder;
    ``speaker`` for everything else) — Granola does not name-resolve
    turns. ``start_time`` / ``end_time`` are seconds from the start of
    the recording.
    """

    model_config = ConfigDict(extra="allow")

    text: str
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    speaker: Optional[dict] = None  # {"source": "microphone" | "speaker"}


class FolderMembership(BaseModel):
    """A note's membership in a folder, as nested inside note responses.

    Returned in both ``GET /v1/notes/{id}`` and ``GET /v1/notes``
    item bodies. The list shape lets a single note belong to multiple
    folders simultaneously (Granola supports this).
    """

    model_config = ConfigDict(extra="allow")

    id: str
    name: Optional[str] = None
    parent_folder_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Top-level models exposed by GranolaAPIClient
# ---------------------------------------------------------------------------


class GranolaFolder(BaseModel):
    """A folder in the Granola account — what ``list_folders()`` returns.

    The Granola ``/v1/folders`` endpoint returns a wrapper object
    ``{folders, hasMore, cursor}``; the client unwraps the ``folders``
    array. ``parent_folder_id`` is ``None`` for top-level folders.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    parent_folder_id: Optional[str] = None


class GranolaNoteSummary(BaseModel):
    """Lightweight note metadata — what ``list_notes()`` returns per item.

    Used to decide which notes need a full ``get_note_detail()`` fetch
    in the next adapter step. ``folder_membership`` is included so the
    adapter can re-confirm the note is still in the expected folder
    before doing the heavier per-note work.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    title: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    folder_membership: list[FolderMembership] = Field(default_factory=list)


class GranolaNoteDetail(BaseModel):
    """Full note payload — what ``get_note_detail()`` returns.

    Returned by ``GET /v1/notes/{id}?include=transcript``. Every field
    Phase 2d's adapter consumes (per LOCKED-36 envelope construction)
    is modeled here so the parse failure is loud and structured if
    Granola changes the shape upstream.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    title: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    attendees: list[Attendee] = Field(default_factory=list)
    calendar_event: Optional[CalendarEvent] = None
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    summary_markdown: Optional[str] = None
    summary_text: Optional[str] = None
    web_url: Optional[str] = None
    folder_membership: list[FolderMembership] = Field(default_factory=list)
