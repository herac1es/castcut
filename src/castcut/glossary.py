"""Lure fishing glossary loading, ASR text correction, and highlight keyword scoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from castcut.models import ClipCategory


@dataclass
class LureGlossary:
    asr_corrections: dict[str, str] = field(default_factory=dict)
    signal_keywords: dict[str, list[str]] = field(default_factory=dict)

    def correct_text(self, text: str) -> str:
        """Apply deterministic longest-match-first lure terminology fixes to ASR output."""
        if not text:
            return text
        out = text
        for wrong, right in sorted(
            self.asr_corrections.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            if wrong and wrong != right and wrong in out:
                out = out.replace(wrong, right)
        return out

    def match_keywords(self, text: str) -> tuple[list[str], ClipCategory | None]:
        """Return matched signal keywords and best-fit ClipCategory hint from transcript text."""
        if not text:
            return [], None
        hits: list[str] = []
        scores: dict[str, int] = {}
        priority = ["fight_catch", "blooper", "gear_rig", "talking_strategy"]
        for cat in priority:
            cat_words = self.signal_keywords.get(cat, [])
            count = 0
            for kw in cat_words:
                if kw and kw in text:
                    hits.append(kw)
                    count += 1
            if count > 0:
                scores[cat] = count

        if not scores:
            return [], None

        # Fight/catch and bloopers win ties because they are primary vlog story beats.
        best_cat = max(
            scores.keys(),
            key=lambda c: (scores[c] * (2 if c in ("fight_catch", "blooper") else 1), -priority.index(c)),
        )
        return sorted(set(hits)), ClipCategory(best_cat)


def default_glossary_path() -> Path:
    """Locate bundled knowledge/lure_glossary.yaml relative to repo root or cwd."""
    candidates = [
        Path(__file__).resolve().parents[2] / "knowledge" / "lure_glossary.yaml",
        Path.cwd() / "knowledge" / "lure_glossary.yaml",
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def load_glossary(custom_path: str | Path | None = None) -> LureGlossary:
    path = Path(custom_path) if custom_path else default_glossary_path()
    if not path.exists():
        return LureGlossary()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return LureGlossary(
        asr_corrections=dict(raw.get("asr_corrections") or {}),
        signal_keywords={
            k: list(v or []) for k, v in (raw.get("signal_keywords") or {}).items()
        },
    )
