# Troubleshooting

## 1) `No subtitles available for this video`

The video likely has no public captions.

Options:

- Enable whisper fallback in extension (`Subtitle not available -> use local Whisper fallback`).
- Install `faster-whisper` and `ffmpeg`.

## 2) `Missing command: yt-dlp` or `Missing command: ffmpeg`

Install required binaries:

```bash
brew install yt-dlp ffmpeg
```

## 3) Backend API not reachable

Make sure backend is running:

```bash
cd /Users/claire/codex/video-transcript-mvp/backend
source .venv/bin/activate
uvicorn app:app --reload --host 127.0.0.1 --port 8001
```

## 4) Whisper model download is slow

First-time run downloads model files. Use smaller model for faster startup:

```bash
export WHISPER_MODEL=base
```
