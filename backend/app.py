from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(title="Video Transcript API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TranscriptRequest(BaseModel):
    url: str = Field(..., description="Video page URL")
    language: Optional[str] = Field(
        default=None,
        description="Preferred subtitle/transcription language, e.g. zh, zh-Hans, en",
    )
    fallback_transcribe: bool = Field(
        default=False,
        description="If subtitles are unavailable, run local whisper transcription",
    )


class TranscriptChunk(BaseModel):
    start: float
    end: float
    text: str


class TranscriptResponse(BaseModel):
    source: str
    title: str
    language: Optional[str]
    text: str
    srt: str
    chunks: List[TranscriptChunk]


TIMECODE_RE = re.compile(
    r"(?P<start>[\d:.]+)\s*-->\s*(?P<end>[\d:.]+)"
)


def _run(cmd: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Missing command: {cmd[0]}. Please install it first.",
        ) from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or f"Command failed: {' '.join(cmd)}"
        raise HTTPException(status_code=502, detail=detail)

    return result


def _parse_timestamp(raw: str) -> float:
    raw = raw.strip().replace(",", ".")
    parts = raw.split(":")
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h = "0"
        m, s = parts
    else:
        return 0.0

    try:
        return int(h) * 3600 + int(m) * 60 + float(s)
    except ValueError:
        return 0.0


def _sec_to_srt(sec: float) -> str:
    if sec < 0:
        sec = 0
    total_ms = int(round(sec * 1000))
    h = total_ms // 3_600_000
    m = (total_ms % 3_600_000) // 60_000
    s = (total_ms % 60_000) // 1000
    ms = total_ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _chunks_to_srt(chunks: List[TranscriptChunk]) -> str:
    lines: List[str] = []
    for idx, c in enumerate(chunks, start=1):
        lines.append(str(idx))
        lines.append(f"{_sec_to_srt(c.start)} --> {_sec_to_srt(c.end)}")
        lines.append(c.text)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _parse_subtitle_file(path: Path) -> List[TranscriptChunk]:
    chunks: List[TranscriptChunk] = []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        match = TIMECODE_RE.search(line)
        if not match:
            i += 1
            continue

        start = _parse_timestamp(match.group("start"))
        end = _parse_timestamp(match.group("end"))
        i += 1

        text_lines: List[str] = []
        while i < len(lines):
            raw = lines[i].rstrip("\n")
            if not raw.strip():
                break
            # Remove WebVTT cue settings/tags quickly for cleaner text.
            cleaned = re.sub(r"<[^>]+>", "", raw).strip()
            if cleaned and "-->" not in cleaned:
                text_lines.append(cleaned)
            i += 1

        text = " ".join(text_lines).strip()
        if text:
            chunks.append(TranscriptChunk(start=start, end=end, text=text))

        i += 1

    return chunks


def _pick_subtitle_file(files: List[Path], language: Optional[str]) -> Path:
    if not files:
        raise HTTPException(status_code=404, detail="No subtitle file found")

    if not language:
        return sorted(files)[0]

    lang = language.lower()

    def score(p: Path) -> int:
        name = p.name.lower()
        if f".{lang}." in name or name.endswith(f".{lang}"):
            return 0
        if lang in name:
            return 1
        return 9

    best = sorted(files, key=score)[0]
    return best


def _extract_video_info(url: str) -> Dict[str, Any]:
    result = _run(["yt-dlp", "--dump-single-json", "--skip-download", url])
    try:
        data = result.stdout.strip().splitlines()[-1]
        parsed = __import__("json").loads(data)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Failed to parse yt-dlp metadata") from exc

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="Unexpected yt-dlp metadata format")

    return parsed


def _resolve_ffmpeg_location() -> Optional[str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        return str(Path(ffmpeg).parent)

    candidates = [
        Path("/Users/claire/.homebrew/bin"),
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        Path("/usr/bin"),
    ]
    for candidate in candidates:
        if (candidate / "ffmpeg").exists() and (candidate / "ffprobe").exists():
            return str(candidate)

    return None


def _try_subtitle_path(url: str, language: Optional[str]) -> Tuple[str, List[TranscriptChunk], str]:
    with tempfile.TemporaryDirectory(prefix="video_subs_") as tmp:
        tmpdir = Path(tmp)
        out_template = str(tmpdir / "media.%(ext)s")

        sub_lang = language if language else "all"
        cmd = [
            "yt-dlp",
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            sub_lang,
            "--sub-format",
            "srt/vtt/best",
            "-o",
            out_template,
            url,
        ]
        ffmpeg_location = _resolve_ffmpeg_location()
        if ffmpeg_location:
            cmd.extend(["--ffmpeg-location", ffmpeg_location])
        _run(cmd)

        files = list(tmpdir.glob("*.srt")) + list(tmpdir.glob("*.vtt"))
        if not files:
            raise HTTPException(status_code=404, detail="No subtitles available for this video")

        subtitle_path = _pick_subtitle_file(files, language)
        chunks = _parse_subtitle_file(subtitle_path)
        if not chunks:
            raise HTTPException(status_code=404, detail="Subtitle file is empty or unparseable")

        lang_guess = subtitle_path.stem.split(".")[-1] if "." in subtitle_path.stem else "unknown"
        return subtitle_path.name, chunks, lang_guess


def _normalize_whisper_language(language: Optional[str]) -> Optional[str]:
    if not language:
        return None
    lang = language.lower()
    if "zh" in lang:
        return "zh"
    if "en" in lang:
        return "en"
    if "ja" in lang:
        return "ja"
    if "ko" in lang:
        return "ko"
    return None


def _try_whisper_path(url: str, language: Optional[str]) -> Tuple[List[TranscriptChunk], str]:
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        raise HTTPException(
            status_code=501,
            detail="Whisper fallback not installed. Run: pip install faster-whisper",
        ) from exc

    model_size = os.getenv("WHISPER_MODEL", "small")

    with tempfile.TemporaryDirectory(prefix="video_audio_") as tmp:
        tmpdir = Path(tmp)
        out_template = str(tmpdir / "audio.%(ext)s")

        cmd = [
            "yt-dlp",
            "-f",
            "bestaudio/best",
            "-x",
            "--audio-format",
            "mp3",
            "-o",
            out_template,
            url,
        ]
        ffmpeg_location = _resolve_ffmpeg_location()
        if ffmpeg_location:
            cmd.extend(["--ffmpeg-location", ffmpeg_location])
        _run(cmd)

        audio_files = list(tmpdir.glob("audio.*"))
        if not audio_files:
            raise HTTPException(status_code=502, detail="Failed to download audio for transcription")

        audio = sorted(audio_files)[0]
        model = WhisperModel(model_size, compute_type="int8")
        whisper_lang = _normalize_whisper_language(language)
        segments, info = model.transcribe(str(audio), language=whisper_lang)

        chunks: List[TranscriptChunk] = []
        for seg in segments:
            text = (seg.text or "").strip()
            if not text:
                continue
            chunks.append(
                TranscriptChunk(start=float(seg.start or 0.0), end=float(seg.end or 0.0), text=text)
            )

        if not chunks:
            raise HTTPException(status_code=502, detail="Whisper produced empty transcript")

        detected_lang = getattr(info, "language", None) or whisper_lang or "unknown"
        return chunks, detected_lang


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/api/transcript", response_model=TranscriptResponse)
def create_transcript(req: TranscriptRequest) -> TranscriptResponse:
    if not req.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")

    meta = _extract_video_info(req.url)
    title = str(meta.get("title") or "Untitled video")

    try:
        subtitle_file, chunks, lang = _try_subtitle_path(req.url, req.language)
        source = f"subtitle:{subtitle_file}"
    except HTTPException as subtitle_error:
        if not req.fallback_transcribe:
            # Keep subtitle-specific message when fallback is disabled.
            raise subtitle_error
        chunks, lang = _try_whisper_path(req.url, req.language)
        source = "whisper:faster-whisper"

    text = "\n".join(c.text for c in chunks)
    srt = _chunks_to_srt(chunks)

    return TranscriptResponse(
        source=source,
        title=title,
        language=lang,
        text=text,
        srt=srt,
        chunks=chunks,
    )
