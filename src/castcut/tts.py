"""Volcengine Doubao Speech Synthesis v3 TTS client, polyphone disambiguation, subtitle alignment, and cache.

Environment variables and protocol mirror the sibling `coolbot` project so credentials and voice IDs
can be shared verbatim between both repos.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from castcut.media import probe_video, resolve_ffmpeg, resolve_ffprobe
from castcut.models import SubtitleCue

DEFAULT_VOLCENGINE_TTS_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
DEFAULT_VOLCENGINE_TTS_RESOURCE_ID = "seed-tts-2.0"
DEFAULT_VOLCENGINE_TTS_VOICE = "zh_male_m191_uranus_bigtts"
ALLOWED_SAMPLE_RATES = frozenset({8000, 16000, 22050, 24000, 32000, 44100, 48000})

# Protected lure terminology kept unbroken during 16-char subtitle splitting (aligned with coolbot)
PROTECTED_LURE_TERMS = (
    "超快调", "中快调", "中慢调", "快慢调", "强力精细", "平行搜边", "先静后闹",
    "转动惯量", "启动惯量", "中间速比", "2500SHG", "2500型", "机械刹车", "磁力刹车",
    "微调旋钮", "锁死泄力", "触地即停", "德州钓组", "复合亮片", "大口黑鲈",
    "三本钩", "曲柄钩", "铅头钩", "浅线杯", "深线杯", "重线杯", "微物轮", "泛用轮",
    "高速比", "纺车轮", "水滴轮", "碳前导", "强扰流", "低扰流", "高比重", "无舌板",
    "练杆坑", "管理场", "面条虫", "之字狗", "刀疤鲈", "碎嘴翘", "藏钩尖", "调性",
    "快调", "中调", "慢调", "速比", "7速", "泄力", "PE线", "BFS", "VIB", "波爬",
    "米诺", "软虫", "软饵", "硬饵", "铁板", "亮片", "德州", "倒钓", "无铅", "标点",
    "结构", "挂底", "炸线", "炒粉", "空军", "破龟", "滑口", "爆口", "刺鱼", "溜鱼",
    "洗鳃", "放流", "抄网", "微物", "泛用", "精细", "雷强", "岸钓", "船钓",
)

_PROTECTED_TOKEN_RE = re.compile(
    "(" + "|".join(re.escape(t) for t in sorted(PROTECTED_LURE_TERMS, key=len, reverse=True))
    + r"|[A-Za-z0-9]+(?:[.'/-][A-Za-z0-9]+)*)"
)
_EDGE_PUNCT_RE = re.compile(
    r"^[\s，。！？；：、,.!?;:～~—…·]+|[\s，。！？；：、,.!?;:～~—…·]+$"
)
_CLAUSE_SPLIT_RE = re.compile(r"(?:(?<=[，。！？；：、!?;:])|(?<=[.,])(?!\d))\s*")

# Rod-action polyphone disambiguation ("调" = diao4 vs tuning "调" = tiao2), aligned with coolbot
_POLYPHONE_PHRASES: tuple[tuple[str, str], ...] = (
    ("超快调", "超快吊"),
    ("中快调", "中快吊"),
    ("中慢调", "中慢吊"),
    ("快慢调", "快慢吊"),
    ("调性", "吊性"),
    ("先调子", "先吊子"),
    ("本调子", "本吊子"),
    ("胴调子", "胴吊子"),
)
_ROD_ACTION_CHAR_RE = re.compile(
    r"(?<![微细乱瞎先再水空场])([快中慢软硬])调(?![整节试好高低松紧档位控])"
)
_RESTORE_ACTION_CHAR_RE = re.compile(
    r"(?<![微细乱瞎先再水空场])([快中慢软硬])吊(?![整节试好高低松紧档位控])"
)


@dataclass
class DoubaoTtsConfig:
    endpoint: str = DEFAULT_VOLCENGINE_TTS_ENDPOINT
    api_key: str | None = None
    app_id: str | None = None
    access_key: str | None = None
    resource_id: str = DEFAULT_VOLCENGINE_TTS_RESOURCE_ID
    voice: str = DEFAULT_VOLCENGINE_TTS_VOICE
    speech_rate: int = 0
    loudness_rate: int = 0
    sample_rate: int = 24000
    context_text: str | None = None
    uid: str = "castcut-user"

    @property
    def has_credentials(self) -> bool:
        return bool((self.api_key or "").strip() or ((self.app_id or "").strip() and (self.access_key or "").strip()))

    @property
    def auth_mode(self) -> str:
        if (self.api_key or "").strip():
            return "X-Api-Key"
        if (self.app_id or "").strip() and (self.access_key or "").strip():
            return "X-Api-App-Id+Access-Key"
        return "missing"


@dataclass
class VolcengineWord:
    word: str
    start_time: float
    end_time: float


@dataclass
class VolcengineSentence:
    text: str
    words: list[VolcengineWord] = field(default_factory=list)


@dataclass
class SynthesizedVoiceover:
    audio_path: Path
    duration_sec: float
    subtitles: list[SubtitleCue]
    from_cache: bool = False
    text_words: int | None = None


def load_dotenv(env_path: Path | None = None) -> Path | None:
    """Load .env key=value entries into os.environ without overwriting existing variables."""
    candidates = (
        [env_path]
        if env_path is not None
        else [
            Path.cwd() / ".env",
            Path(__file__).resolve().parents[2] / ".env",
        ]
    )
    for path in candidates:
        if path is None or not path.exists() or not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, raw_val = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            val = raw_val.strip()
            if (len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'")):
                val = val[1:-1]
            elif " #" in val:
                val = val.split(" #", 1)[0].strip()
            os.environ[key] = val
        return path
    return None


def _parse_ranged_int(raw: str | None, fallback: int, lo: int, hi: int, label: str) -> int:
    if not raw or not raw.strip():
        return fallback
    s = raw.strip()
    if not re.fullmatch(r"-?\d+", s):
        raise ValueError(f"无效的 {label}: {raw}（需为 {lo}..{hi} 的整数）")
    val = int(s)
    if val < lo or val > hi:
        raise ValueError(f"无效的 {label}: {raw}（允许范围 {lo}..{hi}）")
    return val


def _parse_sample_rate(raw: str | None, fallback: int = 24000) -> int:
    if not raw or not raw.strip():
        return fallback
    s = raw.strip()
    if not s.isdigit() or int(s) not in ALLOWED_SAMPLE_RATES:
        raise ValueError(
            f"无效的 VOLCENGINE_TTS_SAMPLE_RATE: {raw}（支持 {sorted(ALLOWED_SAMPLE_RATES)}）"
        )
    return int(s)


def load_doubao_tts_config(env_path: Path | None = None, *, load_env_file: bool = True) -> DoubaoTtsConfig:
    """Build DoubaoTtsConfig from environment variables (and optional .env), mirroring coolbot."""
    if load_env_file:
        load_dotenv(env_path)

    return DoubaoTtsConfig(
        endpoint=(os.environ.get("VOLCENGINE_TTS_ENDPOINT") or DEFAULT_VOLCENGINE_TTS_ENDPOINT).strip(),
        api_key=(os.environ.get("VOLCENGINE_TTS_API_KEY") or "").strip() or None,
        app_id=(os.environ.get("VOLCENGINE_TTS_APP_ID") or "").strip() or None,
        access_key=(os.environ.get("VOLCENGINE_TTS_ACCESS_KEY") or "").strip() or None,
        resource_id=(
            os.environ.get("VOLCENGINE_TTS_RESOURCE_ID") or DEFAULT_VOLCENGINE_TTS_RESOURCE_ID
        ).strip(),
        voice=(os.environ.get("VOLCENGINE_TTS_VOICE") or DEFAULT_VOLCENGINE_TTS_VOICE).strip(),
        speech_rate=_parse_ranged_int(
            os.environ.get("VOLCENGINE_TTS_SPEECH_RATE"), 0, -50, 100, "VOLCENGINE_TTS_SPEECH_RATE"
        ),
        loudness_rate=_parse_ranged_int(
            os.environ.get("VOLCENGINE_TTS_LOUDNESS_RATE"), 0, -50, 100, "VOLCENGINE_TTS_LOUDNESS_RATE"
        ),
        sample_rate=_parse_sample_rate(os.environ.get("VOLCENGINE_TTS_SAMPLE_RATE"), 24000),
        context_text=(os.environ.get("VOLCENGINE_TTS_CONTEXT_TEXT") or "").strip() or None,
        uid=(os.environ.get("VOLCENGINE_TTS_UID") or "castcut-user").strip(),
    )


def apply_polyphone_aliases(text: str) -> tuple[str, bool]:
    """Disambiguate rod-action '调' (diao4) as '吊' for TTS while preserving tuning '调' (tiao2)."""
    has_orig_diao = "吊" in text
    out = text
    for orig, alias in _POLYPHONE_PHRASES:
        out = out.replace(orig, alias)
    out = _ROD_ACTION_CHAR_RE.sub(r"\1吊", out)
    return out, has_orig_diao


def restore_polyphone_text(spoken_text: str, has_orig_diao: bool) -> str:
    if not has_orig_diao:
        return spoken_text.replace("吊", "调")
    out = spoken_text
    for orig, alias in _POLYPHONE_PHRASES:
        out = out.replace(alias, orig)
    return _RESTORE_ACTION_CHAR_RE.sub(r"\1调", out)


def trim_subtitle_edge_punctuation(text: str) -> str:
    return _EDGE_PUNCT_RE.sub("", text).strip()


def _tokenize_protected(text: str) -> list[str]:
    tokens: list[str] = []
    last = 0
    for m in _PROTECTED_TOKEN_RE.finditer(text):
        if m.start() > last:
            tokens.extend(list(text[last : m.start()]))
        tokens.append(m.group(0))
        last = m.end()
    if last < len(text):
        tokens.extend(list(text[last:]))
    return tokens


def _split_raw_chunks(text: str, max_chars: int = 16) -> list[str]:
    clean = text.strip()
    if not clean:
        return []
    raw_parts = [p.strip() for p in _CLAUSE_SPLIT_RE.split(clean) if p.strip()]
    chunks: list[str] = []
    for part in raw_parts or [clean]:
        vis_len = len(trim_subtitle_edge_punctuation(part))
        if vis_len <= max_chars:
            if (
                chunks
                and not re.search(r"[。！？；：!?;:]$", chunks[-1])
                and len(trim_subtitle_edge_punctuation(chunks[-1] + part)) <= max_chars
            ):
                chunks[-1] += part
            else:
                chunks.append(part)
            continue

        tokens = _tokenize_protected(part)
        n_chunks = max(1, (vis_len + max_chars - 1) // max_chars)
        target_len = (vis_len + n_chunks - 1) // n_chunks
        sub: list[str] = []
        cur = ""
        for tok in tokens:
            if re.fullmatch(r"[，。！？；：、,.!?;:）】》”’]", tok):
                cur += tok
                continue
            if cur and (
                len(cur) + len(tok) > max_chars
                or (len(cur) + len(tok) > target_len and len(cur) >= max(4, int(target_len * 0.6)))
            ):
                sub.append(cur)
                cur = tok
            else:
                cur += tok
        if cur:
            sub.append(cur)
        if len(sub) >= 2:
            if (
                len(trim_subtitle_edge_punctuation(sub[-1])) <= 3
                and len(trim_subtitle_edge_punctuation(sub[-2] + sub[-1])) <= max_chars + 2
            ):
                sub[-2] += sub[-1]
                sub.pop()
        chunks.extend(sub)
    return chunks


def parse_volcengine_chunked_response(
    raw_body: str,
    *,
    log_id: str = "unknown",
    speaker: str = "unknown",
    resource_id: str = "unknown",
) -> tuple[bytes, list[VolcengineSentence], int | None]:
    """Parse Volcengine TTS v3 HTTP chunked NDJSON / SSE response into MP3 bytes + word timestamps."""
    audio_parts: list[bytes] = []
    sentences: list[VolcengineSentence] = []
    text_words: int | None = None

    for raw_line in raw_body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith((":", "event:", "id:", "retry:")):
            continue
        payload = line[5:].strip() if line.startswith("data:") else line
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"豆包 TTS 响应 JSON 解析失败 (logid={log_id}): {exc}; snippet={payload[:200]}"
            ) from exc

        code = obj.get("code", 0)
        usage = obj.get("usage")
        if isinstance(usage, dict) and isinstance(usage.get("text_words"), int):
            text_words = usage["text_words"]

        if code == 20000000:
            continue
        if code != 0:
            msg = obj.get("message") or "未知服务端错误"
            hint = ""
            if code == 45000000 or re.search(r"mismatch|permission|denied", str(msg), re.I):
                hint = "；请检查音色 speaker 与 VOLCENGINE_TTS_RESOURCE_ID（如 seed-tts-2.0 对应 *_uranus_bigtts）是否匹配且控制台已开通"
            raise RuntimeError(
                f"豆包 TTS 合成失败 (code={code}, logid={log_id}, speaker={speaker}, "
                f"resourceId={resource_id}): {msg}{hint}"
            )

        data_b64 = obj.get("data")
        if isinstance(data_b64, str) and data_b64:
            audio_parts.append(base64.b64decode(data_b64))

        sent = obj.get("sentence")
        if isinstance(sent, dict):
            words: list[VolcengineWord] = []
            for item in sent.get("words") or []:
                if (
                    isinstance(item, dict)
                    and isinstance(item.get("word"), str)
                    and isinstance(item.get("startTime"), (int, float))
                    and isinstance(item.get("endTime"), (int, float))
                ):
                    st = max(0.0, float(item["startTime"]))
                    et = max(st, float(item["endTime"]))
                    words.append(VolcengineWord(word=item["word"], start_time=st, end_time=et))
            sentences.append(
                VolcengineSentence(
                    text=str(sent.get("text") or ""),
                    words=words,
                )
            )

    return b"".join(audio_parts), sentences, text_words


def build_subtitle_cues_from_sentences(
    sentences: list[VolcengineSentence],
    fallback_text: str,
    duration_sec: float,
    max_chars: int = 16,
) -> list[SubtitleCue]:
    """Convert Volcengine sentence/word timestamps into readable, protected-term SubtitleCues."""
    raw_cues: list[tuple[float, float, str]] = []
    for sent in sentences:
        valid_words = [w for w in sent.words if w.word.strip()]
        if not valid_words:
            continue
        sent_start = valid_words[0].start_time
        sent_end = max(sent_start + 0.3, valid_words[-1].end_time)
        display_text = (sent.text or "".join(w.word for w in valid_words)).strip()
        if not display_text:
            continue

        chunks = _split_raw_chunks(display_text, max_chars=max_chars)
        compact_disp = re.sub(r"\s+", "", display_text)
        compact_words = "".join(re.sub(r"\s+", "", w.word) for w in valid_words)

        if compact_disp and compact_disp == compact_words:
            char_timings: list[tuple[float, float]] = []
            for w in valid_words:
                chars = list(re.sub(r"\s+", "", w.word))
                if not chars:
                    continue
                span = max(0.01, w.end_time - w.start_time)
                step = span / len(chars)
                for i in range(len(chars)):
                    cs = w.start_time + step * i
                    ce = w.end_time if i == len(chars) - 1 else w.start_time + step * (i + 1)
                    char_timings.append((cs, ce))
            total_chunk_chars = sum(len(re.sub(r"\s+", "", c)) for c in chunks)
            if len(char_timings) == total_chunk_chars and char_timings:
                cursor = 0
                for chunk in chunks:
                    clen = len(re.sub(r"\s+", "", chunk))
                    if clen == 0:
                        continue
                    s_t = char_timings[cursor][0]
                    e_t = char_timings[cursor + clen - 1][1]
                    cursor += clen
                    cleaned = trim_subtitle_edge_punctuation(chunk)
                    if cleaned:
                        raw_cues.append((s_t, max(s_t + 0.25, e_t), cleaned))
                continue

        # Fallback: proportional split across sentence bounds (handles Seed-TTS 1.0 TN expansion)
        span = max(0.4, sent_end - sent_start)
        weight_sum = sum(max(1, len(c)) for c in chunks)
        cur = sent_start
        for idx, chunk in enumerate(chunks):
            share = max(1, len(chunk)) / weight_sum
            s_t = sent_start if idx == 0 else cur
            e_t = sent_end if idx == len(chunks) - 1 else cur + span * share
            cur = e_t
            cleaned = trim_subtitle_edge_punctuation(chunk)
            if cleaned:
                raw_cues.append((s_t, max(s_t + 0.25, e_t), cleaned))

    if not raw_cues:
        chunks = _split_raw_chunks(fallback_text, max_chars=max_chars)
        span = max(0.8, duration_sec - 0.2)
        weight_sum = sum(max(1, len(c)) for c in chunks) or 1
        cur = 0.05
        for idx, chunk in enumerate(chunks):
            share = max(1, len(chunk)) / weight_sum
            s_t = 0.05 if idx == 0 else cur
            e_t = max(s_t + 0.25, duration_sec - 0.1 if idx == len(chunks) - 1 else cur + span * share)
            cur = e_t
            cleaned = trim_subtitle_edge_punctuation(chunk)
            if cleaned:
                raw_cues.append((s_t, e_t, cleaned))

    out: list[SubtitleCue] = []
    prev_end = 0.0
    for idx, (s_t, e_t, text) in enumerate(raw_cues, start=1):
        start = round(max(prev_end, max(0.0, s_t)), 3)
        end = round(max(start + 0.25, e_t), 3)
        out.append(SubtitleCue(index=idx, start=start, end=end, text=text))
        prev_end = end
    return out


def _probe_audio_duration(audio_path: Path) -> float | None:
    """Measure exact MP3 duration via ffprobe or ffmpeg fallback."""
    import subprocess

    fp = resolve_ffprobe()
    if fp is not None:
        proc = subprocess.run(
            [
                fp.path,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            try:
                val = float(proc.stdout.strip())
                if val > 0:
                    return round(val, 3)
            except ValueError:
                pass
    try:
        ff = resolve_ffmpeg()
        proc = subprocess.run(
            [ff.path, "-hide_banner", "-i", str(audio_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr or "")
        if m:
            val = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            if val > 0:
                return round(val, 3)
    except Exception:
        pass
    try:
        probed = probe_video(audio_path, media_id="VO", rel_path=audio_path.name)
        return probed.duration if probed.duration > 0 else None
    except Exception:
        return None


class DoubaoTtsClient:
    """Volcengine Doubao Speech Synthesis v3 client with content-addressed local cache."""

    def __init__(
        self,
        config: DoubaoTtsConfig,
        *,
        cache_dir: Path | None = None,
        timeout_sec: float = 45.0,
        probe_duration_fn=None,
    ) -> None:
        self.config = config
        self.cache_dir = cache_dir
        self.timeout_sec = timeout_sec
        self._probe_duration = probe_duration_fn or _probe_audio_duration

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Api-Resource-Id": self.config.resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Control-Require-Usage-Tokens-Return": "text_words",
        }
        if self.config.api_key:
            headers["X-Api-Key"] = self.config.api_key
            return headers
        if self.config.app_id and self.config.access_key:
            headers["X-Api-App-Id"] = self.config.app_id
            headers["X-Api-Access-Key"] = self.config.access_key
            return headers
        raise RuntimeError(
            "缺少豆包语音 TTS 鉴权配置：请在 .env 设置 VOLCENGINE_TTS_API_KEY，"
            "或同时设置 VOLCENGINE_TTS_APP_ID 与 VOLCENGINE_TTS_ACCESS_KEY（与 coolbot 相同）。"
        )

    def _cache_key(self, speaker: str, spoken_text: str, context_text: str | None) -> str:
        payload = json.dumps(
            [
                self.config.resource_id,
                speaker,
                self.config.speech_rate,
                self.config.loudness_rate,
                self.config.sample_rate,
                context_text or "",
                spoken_text,
            ],
            ensure_ascii=False,
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]

    def synthesize(
        self,
        text: str,
        dest_mp3: Path,
        *,
        speaker: str | None = None,
        context_text: str | None = None,
    ) -> SynthesizedVoiceover:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("TTS 合成文本不能为空")

        resolved_speaker = (speaker or self.config.voice).strip()
        resolved_ctx = (context_text if context_text is not None else self.config.context_text) or None
        tts_text, has_orig_diao = apply_polyphone_aliases(clean_text)

        dest_mp3.parent.mkdir(parents=True, exist_ok=True)
        cache_key = self._cache_key(resolved_speaker, tts_text, resolved_ctx)

        if self.cache_dir is not None:
            cached_mp3 = self.cache_dir / f"{cache_key}.mp3"
            cached_meta = self.cache_dir / f"{cache_key}.json"
            if cached_mp3.exists() and cached_meta.exists():
                meta = json.loads(cached_meta.read_text(encoding="utf-8"))
                shutil.copy2(cached_mp3, dest_mp3)
                cues = [SubtitleCue.model_validate(c) for c in meta.get("subtitles", [])]
                return SynthesizedVoiceover(
                    audio_path=dest_mp3,
                    duration_sec=float(meta["duration_sec"]),
                    subtitles=cues,
                    from_cache=True,
                    text_words=meta.get("text_words"),
                )

        headers = self._build_headers()
        is_v2 = "2.0" in self.config.resource_id
        additions: dict[str, object] = {"disable_markdown_filter": True}
        if is_v2 and resolved_ctx:
            additions["context_texts"] = [resolved_ctx]

        audio_params: dict[str, object] = {
            "format": "mp3",
            "sample_rate": self.config.sample_rate,
            "speech_rate": self.config.speech_rate,
            "loudness_rate": self.config.loudness_rate,
        }
        if is_v2:
            audio_params["enable_subtitle"] = True
        else:
            audio_params["enable_timestamp"] = True

        body = {
            "user": {"uid": self.config.uid},
            "req_params": {
                "text": tts_text,
                "speaker": resolved_speaker,
                "audio_params": audio_params,
                "additions": json.dumps(additions, ensure_ascii=False),
            },
        }

        req = urllib.request.Request(
            self.config.endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                log_id = resp.headers.get("X-Tt-Logid") or "unknown"
                raw_body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            log_id = exc.headers.get("X-Tt-Logid") if exc.headers else "unknown"
            err_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"豆包 TTS HTTP 请求失败 (status={exc.code}, logid={log_id}, "
                f"speaker={resolved_speaker}, resourceId={self.config.resource_id}): {err_body[:400]}"
            ) from exc

        audio_bytes, sentences, text_words = parse_volcengine_chunked_response(
            raw_body,
            log_id=log_id,
            speaker=resolved_speaker,
            resource_id=self.config.resource_id,
        )
        if len(audio_bytes) < 512:
            raise RuntimeError(
                f"豆包 TTS 返回音频异常或为空 (bytes={len(audio_bytes)}, logid={log_id}, speaker={resolved_speaker})"
            )

        dest_mp3.write_bytes(audio_bytes)

        probed_sec = self._probe_duration(dest_mp3)
        last_word_end = max(
            (s.words[-1].end_time for s in sentences if s.words),
            default=0.0,
        )
        approx_sec = len(audio_bytes) / 16000.0
        fallback_dur = max(last_word_end + 0.25, approx_sec + 0.2, 1.0)

        restored_sentences = [
            VolcengineSentence(
                text=restore_polyphone_text(s.text, has_orig_diao),
                words=[
                    VolcengineWord(
                        word=restore_polyphone_text(w.word, has_orig_diao),
                        start_time=w.start_time,
                        end_time=w.end_time,
                    )
                    for w in s.words
                ],
            )
            for s in sentences
        ]
        cues = build_subtitle_cues_from_sentences(
            restored_sentences,
            clean_text,
            probed_sec if probed_sec is not None else fallback_dur,
        )
        last_cue_end = cues[-1].end if cues else 0.0
        duration_sec = round(
            max(probed_sec, last_cue_end, 0.5)
            if probed_sec is not None
            else max(last_cue_end + 0.2, approx_sec + 0.2, 1.0),
            3,
        )

        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest_mp3, self.cache_dir / f"{cache_key}.mp3")
            (self.cache_dir / f"{cache_key}.json").write_text(
                json.dumps(
                    {
                        "duration_sec": duration_sec,
                        "text_words": text_words,
                        "subtitles": [c.model_dump(mode="json") for c in cues],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        return SynthesizedVoiceover(
            audio_path=dest_mp3,
            duration_sec=duration_sec,
            subtitles=cues,
            from_cache=False,
            text_words=text_words,
        )
