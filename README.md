# Voice Memo Processor (Automated AI Transcription & Summarization)

This system provides a fully automated pipeline that detects when a voice recorder USB is connected to your Mac, copies the audio files, transcribes them using local AI (`mlx-whisper`), summarizes the transcriptions with OpenAI's `GPT-4o`, and outputs Markdown reading notes. 

The entire process runs automatically in the background as soon as you plug in your USB device. An icon in your Menu Bar indicates current processing progress.

## Prerequisites
1. **Apple Silicon Mac** (M1/M2/M3/M4 chip), with at least 8GB RAM (16GB+ recommended). 
2. **Homebrew** installed (`/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`)
3. **OpenAI API Key** (for summarization with GPT-4o)

## Setup Instructions
1. Open your Terminal.
2. Navigate to this directory:
   ```bash
   cd /path/to/voicememo-processor
   ```
3. Run the installation script:
   ```bash
   ./install.sh
   ```
4. The installation script will prompt you for:
   - Your **OpenAI API Key**
   - The **Output Directory** where your compiled Markdown summaries should be saved.
   - A **Backup Directory** where raw MP3s should be safely kept permanently.
   - The absolute path to your **USB Recorder's Volume** (e.g. `/Volumes/VOICEMEMO/RECORD`).

5. Once installed, the system will start automatically the next time you plug in your USB device. You will also see a 🎙️ icon in your macOS menu bar.

## Manual Commands (if needed)
- **Retry failed transcriptions**: 
  `./venv/bin/python3 process_voicememo.py retry`
- **Check Status**: 
  `./venv/bin/python3 process_voicememo.py status`
- **Full Run (without connecting USB)**: 
  `./run_voicememo.sh` (or `./venv/bin/python3 process_voicememo.py`)

## Troubleshooting
- If transcriptions are not generating, check the `logs/` directory for detailed error messages.
- If the menu bar monitor drops, you can restart it by unloading and loading `com.voicememo.monitor.plist` using `launchctl`.
