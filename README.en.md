# Auto Clipper — Local AI Video-to-Shorts Studio

**Turn a video file or a YouTube link into editable vertical clips for Shorts, Reels and TikTok, on your own Windows PC.**

**[Français](README.md)** · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

Auto Clipper combines **faster-whisper transcription**, **Ollama**, **FFmpeg**, and audio/visual signals to suggest video highlights. It includes a **local web editor** rather than requiring command-line editing. Once the necessary software and models have been downloaded, analysis and rendering run locally; importing a YouTube link, dependency updates and initial model downloads require network access.

> **Status:** Windows-focused personal project, under development. No hosted service or online demo is provided. The local API has no user authentication and must not be exposed to your LAN or the internet.

## What it does

- **Import:** YouTube URLs or local MP4, MOV, MKV and WEBM video files; chunked uploads with resumption support.
- **Highlight discovery:** Ollama analyses transcribed segments; audio peaks, scene cuts and frame motion contribute additional signals.
- **Gameplay without commentary:** a no-speech scoring profile switches emphasis toward sound, scenes and movement.
- **Automatic layouts:** facecam-above-gameplay, blurred background, or subject-tracking crop; per-clip detection with manual override.
- **Subtitles:** word-timed karaoke captions, editable text, emphasis colours and style presets.
- **Edit before export:** browser preview, trim handles, timeline, subtitle editing, hook titles, metadata and re-rendering.
- **Local project history:** FastAPI backend, SQLite state, processing queue, resumable jobs and final MP4/ZIP exports.
- **CLI:** batch processing is available alongside the web interface.

The interface and source code are presently documented primarily in French.

## Requirements

- Windows 10 or 11 and **Python 3.12**
- **FFmpeg** with the required filters (the installer can fetch a Windows build)
- **Ollama**, with the configured local model (default configuration: `qwen2.5:7b`)
- A JavaScript runtime for the current yt-dlp YouTube workflow (Node.js or Deno)
- An NVIDIA GPU is recommended for speed; some processing has CPU fallback
- Approximately **15 GB of free disk space** for tools, dependencies and models, before working-video storage

Installation may take time and download several gigabytes. The repository does **not** contain bundled AI model weights.

## Install and run (Windows)

1. Clone or download the source code.
2. Install Python 3.12 if needed: `winget install -e --id Python.Python.3.12`.
3. Double-click **`setup.bat`**. It creates a Python virtual environment, installs/checks tools and dependencies, and fetches configured models.
4. Double-click **`start.bat`**.
5. Open **http://localhost:8000**. The launcher attempts to open the browser automatically.

The app is designed to be used on the same PC as the server. Do not forward port 8000 or change the bind address to `0.0.0.0` without adding proper authentication and reviewing security.

### Command line

```powershell
.venv\Scripts\python.exe clipper.py "D:\Videos\recording.mp4" --clips 5 --min 20 --max 60 --lang fr
.venv\Scripts\python.exe clipper.py doctor
```

Use `clipper.py --help` for available options; video range, subtitle style and layout can also be configured. Avoid running CLI and web GPU processing concurrently.

## How highlight selection works

| Step | Implementation |
| --- | --- |
| Speech transcription | faster-whisper with word timestamps |
| Narrative selection | Structured Ollama responses for transcript windows |
| Audio intensity | librosa-based signal analysis |
| Editing pace | SceneDetect shot boundaries |
| Visual motion | Motion indicators from video frames |
| Candidate ranking | Configurable weighted signals and minimum spacing |
| Vertical rendering | FFmpeg, with NVENC when available or CPU encode fallback |

Scoring values are **heuristics for selecting candidate clips**; they are not validated predictions of social-media engagement.

## Architecture

```text
setup.bat / start.bat
           |
      FastAPI server  ──> frontend/ (HTML/CSS/vanilla JS editor)
           |
     SQLite + job queue
           |
      clipper/ pipeline
 download → prepare → transcription → audio/scene/motion analysis
                  → highlight selection (Ollama + other signals)
                  → reframe → subtitles → render → MP4/ZIP
```

| Location | Responsibility |
| --- | --- |
| `backend/` | API, jobs, persistence, security middleware |
| `frontend/` | Browser editor, timeline, preview, project history |
| `clipper/` | Reusable video-processing pipeline |
| `config.yaml` | Models, paths, scoring weights, rendering and subtitle presets |
| `tests/` | Unit tests and an optional offline-video end-to-end scenario |

## Tests and development

The GitHub workflow runs **Python 3.12 unit tests**, Python compilation checks and a **gitleaks scan**. It does not run the full video/AI pipeline on a GPU.

```bash
python -m pip install -r requirements-ci.txt
python -m pytest tests/test_units.py -q
python -m compileall -q clipper backend clipper.py server.py
```

A separate end-to-end test exists at `tests/test_e2e_offline.py` and requires a correctly configured local FFmpeg/media environment. For help with contributing, read [CONTRIBUTING.md](CONTRIBUTING.md).

## Privacy and responsible use

Processing is designed to occur locally; **YouTube downloading, package/model downloads and online update checks are network activities**. The source also uses yt-dlp to obtain externally hosted videos. Only use videos you own or have permission to download, edit and redistribute. You are responsible for compliance with platform terms and copyright rules.

Security model and disclosure guidance: [SECURITY.md](SECURITY.md). The server listens on loopback by default and has browser-origin/host checks, but does not have user authentication.

## License and release status

The author has **not yet selected a source-code license**, so redistribution and modification rights are not granted by default. Before opening the repository for community contributions, choose an appropriate license and document any third-party assets and attribution.

A stable installer, screenshots/demo assets and release archive are potential next steps; they are **not yet claimed as published releases**.
