"""DemoSpec — the canonical contract for a Captur'd walkthrough.

Everything downstream (recorder, AI pipeline, viewer, MCP surface, exporters)
speaks this schema. This is the ONE authoritative definition; nothing else
defines these types.

Aligned with ARCHITECTURE.md §1 (from the prev PR #17 salvage) with additions:

* ``ContentMode``       — DOM / video / hybrid, so canvas / Three.js / games get
                           a video-mode fallback instead of a silent break.
* ``ContentMetadata``   — per-step detection: canvas area %, video presence,
                           iframe presence, DOM mutation rate.
* ``ZoomTarget``        — semantic camera: anchor to a selector, not a pixel,
                           so demos survive site font/layout changes.
* ``WordTimestamp``     — for voice-synced camera (TTS word offsets align to
                           camera keyframes).
* ``AnimationKeyframe`` — the LLM-directed camera timeline (JSON the viewer
                           executes via panzoom).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Content-mode detection — the "canvas / Three.js / game" answer
# ---------------------------------------------------------------------------


class ContentMode(str, Enum):
    DOM = "dom"        # rrweb + selectors — full stack works
    VIDEO = "video"    # MediaRecorder screen capture — cursor-anchored camera only
    HYBRID = "hybrid"  # rrweb around a canvas rect, video inside it


@dataclass
class ContentMetadata:
    """Per-step signals the recorder emits so the pipeline picks the right mode.

    Thresholds live in the pipeline, not here — this is raw evidence.
    """

    hasCanvas: bool = False
    canvasAreaPct: float = 0.0     # % of viewport occupied by the largest canvas
    hasVideo: bool = False          # <video> element present
    hasIframe: bool = False         # embedded iframe (YouTube etc.)
    mutationRate: float = 0.0       # DOM mutations / second at step time (rrweb)


# ---------------------------------------------------------------------------
# Click / hotspot geometry
# ---------------------------------------------------------------------------


@dataclass
class Hotspot:
    """Percentage-based click point (survives viewport resize)."""

    xPct: float
    yPct: float


@dataclass
class BoundingRect:
    x: float
    y: float
    width: float
    height: float


@dataclass
class Interaction:
    """Single user (or agent) action on a page."""

    type: str  # 'click' | 'input' | 'submit' | 'navigate' | 'scroll'
    target: dict
    hotspot: dict
    value: str | None = None


# ---------------------------------------------------------------------------
# Cursor + voice timing
# ---------------------------------------------------------------------------


@dataclass
class CursorPoint:
    """Point on a smoothed cursor bezier — viewport-relative."""

    x: float
    y: float
    t: int  # ms offset within the step


@dataclass
class WordTimestamp:
    """One word of the narration + its ms window in the voiceover audio.

    Camera keyframes align to these so the camera arrives on 'Buy Now' at
    the moment the narrator says 'Buy Now'. This is the voice-synced-camera
    primitive nobody else in the space has.
    """

    word: str
    tStartMs: int
    tEndMs: int


# ---------------------------------------------------------------------------
# Camera choreography (LLM as director; viewer executes)
# ---------------------------------------------------------------------------


class CameraAction(str, Enum):
    ZOOM_TO = "zoomTo"
    PAN_TO = "panTo"
    ZOOM_TO_FIT = "zoomToFit"
    RESET = "reset"
    HOLD = "hold"
    SPOTLIGHT_ON = "spotlightOn"
    SPOTLIGHT_OFF = "spotlightOff"


@dataclass
class ZoomTarget:
    """Semantic camera target — an element selector + percentage inside it.

    Anchoring to the selector (not the pixel) is why our demos survive
    typography / layout / color changes on the underlying site. Screen
    Studio can't do this because it only has the mouse trail; Supademo
    can't do this because it's a static screenshot.
    """

    selector: str
    xPct: float = 50.0
    yPct: float = 50.0
    level: float = 1.5      # 1.0 = 100%, 1.5 = 150%
    duration: int = 500     # ms
    easing: str = "ease-in-out"


@dataclass
class AnimationKeyframe:
    """One camera move on the timeline. LLM writes these; viewer executes."""

    stepIndex: int
    action: str            # a CameraAction value (kept as str for JSON round-trip)
    target: str | None = None      # CSS selector when action needs one
    offset: dict | None = None     # {x, y} pct inside target
    zoomLevel: float | None = None
    duration: int = 500
    easing: str | None = "ease-in-out"
    tStartMs: int | None = None    # optional: absolute offset from step start (voice-sync)


# ---------------------------------------------------------------------------
# AI enrichment payload
# ---------------------------------------------------------------------------


@dataclass
class AIAnnotations:
    """Everything the AI pipeline adds to a raw recording."""

    summary: str = ""                 # 2-3 sentence flow summary
    style: str = "snappy"             # 'snappy' | 'smooth' | 'professional' | 'cinematic'
    generatedAt: str = ""
    animationTimeline: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Step + spec
# ---------------------------------------------------------------------------


@dataclass
class DemoStep:
    """One recorded interaction. Assets referenced by path, not inlined here."""

    index: int
    timestamp: int
    pageUrl: str
    pageTitle: str
    interaction: Interaction

    # Visual state — one of these is populated per step
    screenshotBase64: str | None = None   # deprecated — prefer screenshotPath
    screenshotPath: str | None = None     # local path (relative to demo dir)
    screenshotError: str | None = None    # capture failure surface

    # Content-mode detection (recorder fills in; pipeline picks DOM/video)
    contentMode: str = ContentMode.DOM.value
    contentMetadata: dict | None = None

    # rrweb event stream reference (for HYBRID / DOM modes)
    rrwebSnapshotRef: str | None = None

    # Video-mode assets (used when contentMode == video or hybrid)
    videoChunkPath: str | None = None
    videoChunkStartMs: int | None = None
    videoChunkEndMs: int | None = None

    # Voice direction (push-to-talk transcript for the LLM to consume)
    userDirection: str | None = None

    # AI enrichment
    annotation: str | None = None
    voiceoverPath: str | None = None      # local path to per-step audio
    voiceoverBase64: str | None = None    # deprecated — prefer voiceoverPath
    voiceoverWords: list[dict] | None = None  # WordTimestamp[] as dicts
    cursorPath: list[dict] | None = None  # CursorPoint[] as dicts


@dataclass
class DemoSpec:
    """The whole recording. Round-trips through JSON via ``to_dict`` / ``from_dict``."""

    version: int = 1
    id: str = ""
    name: str = ""
    goal: str = ""
    createdAt: str = ""
    viewport: dict = field(default_factory=lambda: {"width": 1440, "height": 900})
    startUrl: str = ""
    steps: list[DemoStep] = field(default_factory=list)
    aiAnnotations: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = [
    # Content-mode
    "ContentMode",
    "ContentMetadata",
    # Geometry
    "Hotspot",
    "BoundingRect",
    "Interaction",
    # Cursor / voice
    "CursorPoint",
    "WordTimestamp",
    # Camera
    "CameraAction",
    "ZoomTarget",
    "AnimationKeyframe",
    # AI payload
    "AIAnnotations",
    # Step + spec
    "DemoStep",
    "DemoSpec",
]
