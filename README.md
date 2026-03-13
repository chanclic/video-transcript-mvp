# Video Transcript MVP

Chrome extension + local FastAPI service to convert video pages into transcript text.

## What this MVP does

- Reads video URL from current tab.
- Calls local backend API to fetch subtitles via `yt-dlp`.
- Returns transcript text + generated SRT.
- If subtitle is unavailable, optional local Whisper fallback (`faster-whisper`).

## Project structure

- `backend/`: FastAPI service
- `extension/`: Chrome extension (MV3 popup)
- `docs/`: setup notes

## Quick start

### 1) Backend setup

```bash
cd /Users/claire/codex/video-transcript-mvp/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Optional: enable local audio transcription fallback
pip install -r requirements-whisper.txt
```

Install runtime tools (required by backend):

```bash
# macOS
brew install yt-dlp ffmpeg
```

Run API:

```bash
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Health check:

```bash
curl -s http://127.0.0.1:8000/health
```

### 2) Load extension

1. Open Chrome `chrome://extensions`
2. Enable `Developer mode`
3. Click `Load unpacked`
4. Choose folder: `/Users/claire/codex/video-transcript-mvp/extension`

### 3) Use

1. Open a video page (Bilibili/YouTube supported by `yt-dlp`).
2. Click extension icon.
3. Keep API Base as `http://127.0.0.1:8000`.
4. Click `Generate Transcript`.
5. Download `.txt` / `.srt`.

## API example

```bash
curl -sS -X POST 'http://127.0.0.1:8000/api/transcript' \
  -H 'content-type: application/json' \
  -d '{"url":"https://www.bilibili.com/video/BV1xx411c7mD","language":"zh","fallback_transcribe":false}'
```

## Notes

- Some videos do not publish subtitles. In that case:
  - set `fallback_transcribe=true` and install `faster-whisper`
  - or provide another source video with captions
- For private/login-only videos, you may need cookies with `yt-dlp` (not included in this MVP UI).
