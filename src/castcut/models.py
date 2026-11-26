"""Core Pydantic schemas for CastCut episode workspaces, media index, edit plans, and QC."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class AspectMode(StrEnum):
    AUTO = "auto"
    LANDSCAPE = "landscape"
    PORTRAIT = "portrait"


class ResolutionTier(StrEnum):
    FHD_1080P = "1080p"
    UHD_4K = "4k"


class MixedOrientationPolicy(StrEnum):
    BLUR_PILLARBOX = "blur_pillarbox"
    CROP_FILL = "crop_fill"
    FIT = "fit"


class TemplateType(StrEnum):
    FISHING_VLOG = "fishing-vlog"
    SHORT_HIGHLIGHT = "short-highlight"


class ClipCategory(StrEnum):
    HOOK_HIGHLIGHT = "hook_highlight"
    FIGHT_CATCH = "fight_catch"
    TALKING_STRATEGY = "talking_strategy"
    GEAR_RIG = "gear_rig"
    CASTING_ACTION = "casting_action"
    BROLL_SCENERY = "broll_scenery"
    BLOOPER = "blooper"
    WASTE = "waste"
    UNCLASSIFIED = "unclassified"


class ProjectConfig(BaseModel):
    """Per-episode configuration persisted in project.yaml."""

    episode_id: str
    title: str
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )
    aspect: AspectMode = AspectMode.AUTO
    resolution: ResolutionTier = ResolutionTier.FHD_1080P
    fps: float = 30.0
    mixed_orientation: MixedOrientationPolicy = MixedOrientationPolicy.BLUR_PILLARBOX
    template: TemplateType = TemplateType.FISHING_VLOG
    target_duration_sec: float = 360.0
    location: str = ""
    target_species: str = "大口黑鲈"
    water_type: Literal["managed_pond", "open_water", "unknown"] = "managed_pond"
    bgm_path: str | None = None
    bgm_volume_db: float = -18.0
    bgm_duck_db: float = -30.0
    burn_subtitles_in_preview: bool = False
    glossary_path: str | None = None
    notes: str = ""

    def resolve_dimensions(self, detected_orientation: Literal["landscape", "portrait"]) -> tuple[int, int]:
        orient = detected_orientation if self.aspect == AspectMode.AUTO else self.aspect.value
        if self.resolution == ResolutionTier.UHD_4K:
            return (3840, 2160) if orient == "landscape" else (2160, 3840)
        return (1920, 1080) if orient == "landscape" else (1080, 1920)


class SilenceInterval(BaseModel):
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class AudioPeak(BaseModel):
    time: float
    db: float


class MediaFile(BaseModel):
    """Metadata probed for one raw media clip inside raw/."""

    media_id: str
    filename: str
    rel_path: str
    abs_path: str
    duration: float
    width: int
    height: int
    rotation: int = 0
    orientation: Literal["landscape", "portrait", "square"]
    fps: float
    video_codec: str = ""
    audio_codec: str = ""
    has_audio: bool = True
    pix_fmt: str = ""
    is_10bit: bool = False
    file_size_bytes: int = 0
    creation_time: str | None = None
    proxy_rel_path: str | None = None
    contact_sheet_rel_path: str | None = None
    scene_cuts: list[float] = Field(default_factory=list)
    silences: list[SilenceInterval] = Field(default_factory=list)
    audio_peaks: list[AudioPeak] = Field(default_factory=list)
    davinci_free_warning: str | None = None


class MediaIndex(BaseModel):
    """All probed raw media files for an episode (work/media_index.json)."""

    episode_id: str
    generated_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )
    dominant_orientation: Literal["landscape", "portrait"] = "landscape"
    landscape_duration: float = 0.0
    portrait_duration: float = 0.0
    total_duration: float = 0.0
    has_10bit_media: bool = False
    files: list[MediaFile] = Field(default_factory=list)


class TranscriptWord(BaseModel):
    start: float
    end: float
    word: str
    probability: float | None = None


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    raw_text: str | None = None
    words: list[TranscriptWord] = Field(default_factory=list)


class MediaTranscript(BaseModel):
    media_id: str
    source: Literal["faster-whisper", "sidecar-srt", "sidecar-json", "none"]
    language: str = "zh"
    segments: list[TranscriptSegment] = Field(default_factory=list)


class CandidateClip(BaseModel):
    """A candidate sub-clip sliced from a raw MediaFile during triage (work/clips.json)."""

    clip_id: str
    media_id: str
    source_rel_path: str
    start: float
    end: float
    duration: float
    orientation: Literal["landscape", "portrait", "square"]
    category: ClipCategory = ClipCategory.UNCLASSIFIED
    score: float = 0.5
    transcript_text: str = ""
    keywords_hit: list[str] = Field(default_factory=list)
    peak_db: float | None = None
    has_speech: bool = False
    keep: bool = True
    agent_note: str = ""
    hud_hint: str | None = None


class ClipCatalog(BaseModel):
    """All triaged candidate clips for an episode (work/clips.json)."""

    episode_id: str
    generated_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )
    clips: list[CandidateClip] = Field(default_factory=list)


class HudOverlaySpec(BaseModel):
    """Transparent PNG HUD card rendered on Track V2 and composited in previews."""

    overlay_id: str
    kind: Literal["chapter", "catch_counter", "lure_info", "spot_note", "callout"]
    title: str
    subtitle: str = ""
    start: float
    duration: float = 3.2
    position: Literal["top_left", "top_right", "bottom_left", "center_banner"] = "top_left"
    png_rel_path: str | None = None


class SubtitleCue(BaseModel):
    """Subtitle cue timed to the output timeline."""

    index: int
    start: float
    end: float
    text: str


class TimelineClipItem(BaseModel):
    """One placed clip on the main video timeline."""

    item_id: str
    section: str
    clip_id: str
    media_id: str
    source_rel_path: str
    source_abs_path: str
    source_start: float
    source_end: float
    speed: float = 1.0
    timeline_start: float = 0.0
    timeline_end: float = 0.0
    orientation: Literal["landscape", "portrait", "square"] = "landscape"
    framing: MixedOrientationPolicy = MixedOrientationPolicy.BLUR_PILLARBOX
    category: ClipCategory = ClipCategory.UNCLASSIFIED
    audio_gain_db: float = 0.0
    mute_audio: bool = False
    marker_label: str | None = None
    marker_color: Literal["Blue", "Cyan", "Green", "Yellow", "Red", "Purple"] = "Blue"
    note: str = ""

    @property
    def source_duration(self) -> float:
        return max(0.0, self.source_end - self.source_start)

    @property
    def output_duration(self) -> float:
        return self.source_duration / max(self.speed, 0.05)


class DuckingInterval(BaseModel):
    start: float
    end: float
    target_db: float = -30.0


class BgmSpec(BaseModel):
    path: str
    base_volume_db: float = -18.0
    duck_volume_db: float = -30.0
    fade_in_sec: float = 1.2
    fade_out_sec: float = 2.0
    ducking_intervals: list[DuckingInterval] = Field(default_factory=list)


class EditPlan(BaseModel):
    """Editable timeline plan persisted as work/rough_cut.yaml or work/fine_cut.yaml."""

    episode_id: str
    title: str
    stage: Literal["rough", "fine"]
    template: TemplateType
    width: int
    height: int
    fps: float
    mixed_orientation: MixedOrientationPolicy
    total_duration: float = 0.0
    items: list[TimelineClipItem] = Field(default_factory=list)
    overlays: list[HudOverlaySpec] = Field(default_factory=list)
    subtitles: list[SubtitleCue] = Field(default_factory=list)
    bgm: BgmSpec | None = None
    notes: str = ""

    def recompute_timings(self) -> None:
        cursor = 0.0
        for item in self.items:
            item.timeline_start = round(cursor, 3)
            cursor += item.output_duration
            item.timeline_end = round(cursor, 3)
        self.total_duration = round(cursor, 3)


class QcCheck(BaseModel):
    name: str
    status: Literal["pass", "warn", "fail"]
    detail: str


class QcReport(BaseModel):
    episode_id: str
    checked_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )
    passed: bool
    checks: list[QcCheck] = Field(default_factory=list)
    preview_path: str | None = None
    qc_contact_sheet: str | None = None
