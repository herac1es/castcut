"""Episode workspace directory conventions and YAML/JSON persistence helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from castcut.models import (
    AspectMode,
    ClipCatalog,
    EditPlan,
    MediaIndex,
    MediaTranscript,
    MixedOrientationPolicy,
    ProjectConfig,
    ResolutionTier,
    TemplateType,
)


@dataclass
class EpisodePaths:
    root: Path

    def __post_init__(self) -> None:
        self.root = self.root.resolve()

    @property
    def project_yaml(self) -> Path:
        return self.root / "project.yaml"

    @property
    def raw_dir(self) -> Path:
        return self.root / "raw"

    @property
    def work_dir(self) -> Path:
        return self.root / "work"

    @property
    def proxies_dir(self) -> Path:
        return self.work_dir / "proxies"

    @property
    def contacts_dir(self) -> Path:
        return self.work_dir / "contacts"

    @property
    def transcripts_dir(self) -> Path:
        return self.work_dir / "transcripts"

    @property
    def tts_cache_dir(self) -> Path:
        return self.work_dir / "tts_cache"

    @property
    def media_index_json(self) -> Path:
        return self.work_dir / "media_index.json"

    @property
    def clips_json(self) -> Path:
        return self.work_dir / "clips.json"

    @property
    def rough_cut_yaml(self) -> Path:
        return self.work_dir / "rough_cut.yaml"

    @property
    def fine_cut_yaml(self) -> Path:
        return self.work_dir / "fine_cut.yaml"

    @property
    def exports_dir(self) -> Path:
        return self.root / "exports"

    @property
    def overlays_dir(self) -> Path:
        return self.exports_dir / "overlays"

    @property
    def voiceovers_dir(self) -> Path:
        return self.exports_dir / "voiceovers"

    @property
    def rough_preview_mp4(self) -> Path:
        return self.exports_dir / "rough_preview.mp4"

    @property
    def fine_preview_mp4(self) -> Path:
        return self.exports_dir / "fine_preview.mp4"

    @property
    def subtitles_srt(self) -> Path:
        return self.exports_dir / "subtitles.srt"

    @property
    def timeline_otio(self) -> Path:
        return self.exports_dir / "timeline.otio"

    @property
    def timeline_fcpxml(self) -> Path:
        return self.exports_dir / "timeline.fcpxml"

    @property
    def jianying_draft_dir(self) -> Path:
        return self.exports_dir / "jianying_draft"

    @property
    def qc_report_json(self) -> Path:
        return self.exports_dir / "qc_report.json"

    @property
    def qc_contact_jpg(self) -> Path:
        return self.exports_dir / "qc_contact.jpg"

    def ensure_dirs(self) -> None:
        for d in (
            self.root,
            self.raw_dir,
            self.work_dir,
            self.proxies_dir,
            self.contacts_dir,
            self.transcripts_dir,
            self.tts_cache_dir,
            self.exports_dir,
            self.overlays_dir,
            self.voiceovers_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


def slugify_episode_id(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip()).strip("-").lower()
    return cleaned or "episode"


def init_episode_workspace(
    episode_dir: Path,
    title: str,
    aspect: AspectMode = AspectMode.AUTO,
    resolution: ResolutionTier = ResolutionTier.FHD_1080P,
    mixed_orientation: MixedOrientationPolicy = MixedOrientationPolicy.BLUR_PILLARBOX,
    template: TemplateType = TemplateType.FISHING_VLOG,
    target_duration_sec: float | None = None,
    location: str = "",
    bgm_path: str | None = None,
    channel_handle: str = "@路亚知马力",
) -> ProjectConfig:
    paths = EpisodePaths(episode_dir)
    paths.ensure_dirs()
    default_dur = 120.0 if template == TemplateType.SHORT_HIGHLIGHT else 360.0
    cfg = ProjectConfig(
        episode_id=slugify_episode_id(episode_dir.name),
        title=title,
        aspect=aspect,
        resolution=resolution,
        mixed_orientation=mixed_orientation,
        template=template,
        target_duration_sec=target_duration_sec if target_duration_sec is not None else default_dur,
        location=location,
        bgm_path=bgm_path,
        channel_handle=channel_handle,
    )
    save_yaml(paths.project_yaml, cfg.model_dump(mode="json"))
    return cfg


def save_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=110),
        encoding="utf-8",
    )


def load_project_config(path: Path) -> ProjectConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return ProjectConfig.model_validate(raw)


def load_media_index(path: Path) -> MediaIndex:
    return MediaIndex.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_clip_catalog(path: Path) -> ClipCatalog:
    return ClipCatalog.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save_clip_catalog(path: Path, catalog: ClipCatalog) -> None:
    path.write_text(
        json.dumps(catalog.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_transcripts(transcripts_dir: Path) -> dict[str, MediaTranscript]:
    out: dict[str, MediaTranscript] = {}
    if not transcripts_dir.exists():
        return out
    for p in sorted(transcripts_dir.glob("*.json")):
        tr = MediaTranscript.model_validate(json.loads(p.read_text(encoding="utf-8")))
        out[tr.media_id] = tr
    return out


def save_edit_plan(path: Path, plan: EditPlan) -> None:
    save_yaml(path, plan.model_dump(mode="json"))


def load_edit_plan(path: Path) -> EditPlan:
    return EditPlan.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
