#!/usr/bin/env python3
"""
Voice Memo Processor v2.1
  Phase 1: USB mount → WAV to MP3 → Google Drive (date folders)
  Phase 2: Google Drive MP3 → mlx-whisper local → GPT-4o summary → Markdown

Changes from v2:
- Anti-hallucination: condition_on_previous_text=False, hallucination_silence_threshold
- Post-processing filter for repetitive/hallucinated segments
- macOS push notifications for progress tracking
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import dotenv
import mlx_whisper
import openai

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

VOICEMEMO_MOUNT = Path(os.environ.get(
    "VOICEMEMO_MOUNT", 
    "/Volumes/VOICEMEMO/RECORD"
))
FFMPEG_PATH = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE_PATH = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"

MARKDOWN_OUTPUT_DIR = Path(os.environ.get(
    "MARKDOWN_OUTPUT_DIR",
    str(Path.home() / "Documents/GitHub/llm-knowledge-base/0-inbox/voicememo")
))
MP3_BASE_DIR = Path(os.environ.get(
    "MP3_BASE_DIR",
    str(Path.home() / "Library/CloudStorage/GoogleDrive-ryo.nihonyanagi@10xc.jp/マイドライブ/Voicememo")
))

SCRIPT_DIR = Path(__file__).parent.resolve()
MANIFEST_PATH = SCRIPT_DIR / "processed_files.json"
STATUS_PATH = SCRIPT_DIR / "status.json"
LOG_DIR = SCRIPT_DIR / "logs"
STAGING_DIR = SCRIPT_DIR / "staging"  # Local MP3 copies to avoid FUSE deadlock

MP3_BITRATE = "64k"
WHISPER_MODEL_REPO = "mlx-community/whisper-large-v3-turbo"
GPT_MODEL = "gpt-4o"

# File naming: 2026-02-11-17-53-58.WAV
FILENAME_PATTERN = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})\.(WAV|mp3)", re.IGNORECASE
)

logger = logging.getLogger("voicememo")


# ──────────────────────────────────────────────
# macOS Notifications
# ──────────────────────────────────────────────


def notify(title: str, message: str, sound: str = ""):
    """Send a macOS notification via osascript."""
    try:
        sound_part = f' sound name "{sound}"' if sound else ""
        script = (
            f'display notification "{message}" '
            f'with title "{title}"{sound_part}'
        )
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass  # Notifications are best-effort, never block processing


def update_status(
    status: str = "idle",
    phase: int = 0,
    phase_label: str = "",
    current_file: str = "",
    files_total: int = 0,
    files_completed: int = 0,
    last_error: str | None = None,
):
    """Write current processing status to status.json for the menu bar monitor."""
    try:
        data = {
            "status": status,
            "phase": phase,
            "phase_label": phase_label,
            "current_file": current_file,
            "files_total": files_total,
            "files_completed": files_completed,
            "last_error": last_error,
            "last_updated": datetime.now().isoformat(),
        }
        tmp = STATUS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATUS_PATH)
    except Exception:
        pass  # Status updates are best-effort


# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"voicememo-{datetime.now().strftime('%Y%m%d')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


# ──────────────────────────────────────────────
# Manifest (processed files tracking)
# ──────────────────────────────────────────────


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {"version": 2, "copied": {}, "transcribed": {}}


def save_manifest(manifest: dict):
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def migrate_manifest(manifest: dict) -> dict:
    """Migrate v1 manifest to v2 format if needed."""
    if manifest.get("version") == 2:
        return manifest

    new_manifest = {"version": 2, "copied": {}, "transcribed": {}}

    # Migrate v1 "processed" entries
    for filename, entry in manifest.get("processed", {}).items():
        mp3_name = filename.replace(".WAV", ".mp3").replace(".wav", ".mp3")

        # Mark as copied
        new_manifest["copied"][filename] = {
            "size_bytes": entry.get("size_bytes", 0),
            "copied_at": entry.get("processed_at", ""),
            "mp3_name": mp3_name,
            "date": entry.get("date", ""),
            "time": entry.get("time", ""),
            "time_full": entry.get("time_full", ""),
        }

        # If it was fully transcribed, mark that too
        if entry.get("status") == "completed" and "transcript_text" in entry:
            new_manifest["transcribed"][mp3_name] = {
                "transcribed_at": entry.get("processed_at", ""),
                "duration_seconds": entry.get("duration_seconds", 0),
                "date": entry.get("date", ""),
                "time": entry.get("time", ""),
                "time_full": entry.get("time_full", ""),
                "transcript_text": entry.get("transcript_text", ""),
                "segments": entry.get("segments", []),
            }

    return new_manifest


# ──────────────────────────────────────────────
# Phase 1: USB → MP3 → Google Drive
# ──────────────────────────────────────────────


def discover_wav_files() -> list[dict]:
    """Scan USB device for WAV files."""
    files = []
    for wav_path in sorted(VOICEMEMO_MOUNT.glob("*.WAV")):
        match = FILENAME_PATTERN.match(wav_path.name)
        if match:
            y, m, d, hh, mm, ss, _ext = match.groups()
            files.append(
                {
                    "path": wav_path,
                    "filename": wav_path.name,
                    "date": f"{y}-{m}-{d}",
                    "time": f"{hh}:{mm}",
                    "time_full": f"{hh}:{mm}:{ss}",
                    "size": wav_path.stat().st_size,
                }
            )
    return files


def convert_wav_to_mp3(wav_path: Path, mp3_path: Path) -> Path:
    """Convert WAV to MP3 using ffmpeg."""
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG_PATH,
        "-y",
        "-i",
        str(wav_path),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-acodec", "libmp3lame",
        "-b:a", MP3_BITRATE,
        str(mp3_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {result.stderr[-500:]}")
    return mp3_path


def _copy_to_google_drive(src_path: Path, dest_path: Path):
    """Copy a file to Google Drive using raw byte write (avoids fcopyfile deadlock).

    Write-only operation — Google Drive FUSE handles writes fine,
    the deadlock only occurs on reads while Drive is syncing.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(src_path, "rb") as src, open(dest_path, "wb") as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)


def phase1_copy_from_usb(manifest: dict) -> set[str]:
    """
    Phase 1: Convert WAV files from USB to MP3.
    Saves to local staging dir first (for reliable Phase 2 reads),
    then copies to Google Drive (for backup/sync).
    Returns set of dates that had new files copied.
    """
    if not VOICEMEMO_MOUNT.exists():
        logger.info("VOICEMEMO not mounted, skipping Phase 1")
        return set()

    wav_files = discover_wav_files()
    new_files = [
        f for f in wav_files
        if f["filename"] not in manifest["copied"]
    ]

    if not new_files:
        logger.info("Phase 1: No new WAV files on device")
        return set()

    logger.info(f"Phase 1: {len(new_files)} new WAV file(s) to convert")
    notify("Voice Memo", f"Phase 1: {len(new_files)}件のWAVを変換中...")
    update_status("processing", 1, "MP3変換中", files_total=len(new_files))
    new_dates = set()

    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    for i, file_info in enumerate(new_files, 1):
        filename = file_info["filename"]
        date = file_info["date"]
        mp3_name = filename.replace(".WAV", ".mp3").replace(".wav", ".mp3")

        # Convert to local staging first (fast, reliable local disk)
        staging_path = STAGING_DIR / mp3_name

        # Google Drive: organized by date folder
        gdrive_path = MP3_BASE_DIR / date / mp3_name

        logger.info(f"  Converting: {filename}")
        notify("Voice Memo", f"MP3変換中 ({i}/{len(new_files)}): {filename}")
        update_status("processing", 1, "MP3変換中", filename, len(new_files), i - 1)

        try:
            # Step 1: Convert WAV → MP3 to local staging
            convert_wav_to_mp3(file_info["path"], staging_path)
            mp3_size_mb = staging_path.stat().st_size / (1024 * 1024)
            logger.info(f"  → {staging_path.name} ({mp3_size_mb:.1f} MB) [staging]")

            # Step 2: Copy to Google Drive (write-only, non-blocking)
            try:
                _copy_to_google_drive(staging_path, gdrive_path)
                logger.info(f"  → Copied to Google Drive: {gdrive_path.parent.name}/{gdrive_path.name}")
            except OSError as e:
                # Google Drive write failure is non-fatal — staging copy is enough
                logger.warning(f"  Google Drive copy failed (will retry later): {e}")

            manifest["copied"][filename] = {
                "size_bytes": file_info["size"],
                "copied_at": datetime.now().isoformat(),
                "mp3_name": mp3_name,
                "mp3_path": str(gdrive_path),
                "staging_path": str(staging_path),
                "date": date,
                "time": file_info["time"],
                "time_full": file_info["time_full"],
            }
            save_manifest(manifest)
            new_dates.add(date)

        except Exception as e:
            logger.error(f"  FAILED converting {filename}: {e}")
            update_status("processing", 1, "MP3変換中", filename, len(new_files), i - 1, last_error=str(e)[:100])
            continue

    if new_dates:
        update_status("processing", 1, "MP3変換完了", files_total=len(new_files), files_completed=len(new_files))
        notify(
            "Voice Memo",
            f"Phase 1完了: {len(new_files)}件を変換済み。USBは安全に取り外せます",
            sound="Glass",
        )

    return new_dates


# ──────────────────────────────────────────────
# Phase 2: Transcribe from Google Drive MP3
# ──────────────────────────────────────────────


def get_audio_duration(file_path: Path) -> float:
    """Get duration in seconds using ffprobe."""
    cmd = [
        FFPROBE_PATH,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        str(file_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr[-500:]}")
    return float(result.stdout.strip())


# Known Whisper hallucination phrases (from YouTube training data leakage etc.)
HALLUCINATION_PHRASES = {
    "ご視聴ありがとうございました",
    "チャンネル登録お願いします",
    "チャンネル登録よろしくお願いします",
    "いいねボタンを押してください",
    "グッドボタンお願いします",
    "ご清聴ありがとうございました",
    "字幕は自動生成されています",
    "この動画が気に入ったら",
    "次回もお楽しみに",
    "Thanks for watching",
    "Please subscribe",
    "Like and subscribe",
}

# Common short hallucination tokens (nonsense fragments Whisper generates)
HALLUCINATION_TOKENS = {"arte", "artearte", "arteartearte"}


def is_hallucination(text: str) -> bool:
    """Detect hallucinated/repetitive segments from Whisper."""
    t = text.strip()
    if not t:
        return True

    # Too short to be meaningful (single char or just punctuation/filler)
    if len(t) <= 2:
        return True

    # Known hallucination phrases (YouTube training data leakage)
    if t in HALLUCINATION_PHRASES:
        return True

    # Short nonsense tokens Whisper hallucinates (e.g. "arte", "artearte")
    # Catch standalone, embedded ("っartearte"), or mixed ("今回artearte")
    if re.search(r"(arte){1,}", t):
        return True

    # Whisper hallucination: "oud" / "oudoud..." repetitions
    if re.search(r"(oud){1,}", t) and len(re.sub(r"(oud)+", "", t).strip()) < 5:
        return True

    # Whisper hallucination: "amb" repeated ("amb amb amb...")
    if re.search(r"(amb[\s]*){2,}", t):
        return True

    # Whisper hallucination: "Honor" repeated
    if re.search(r"(Honor[\s]*){2,}", t):
        return True

    # Whisper hallucination: "SCO" repeated
    if re.search(r"(SCO[\s]*){2,}", t):
        return True

    # Detect repeated characters like "ああああ", "うううう", "えええ"
    if re.match(r"^(.)\1{2,}$", t):
        return True

    # Detect patterns like "ああああああ はた" (repeated chars + short word)
    if re.match(r"^(.)\1{3,}(\s+.{0,4})?$", t):
        return True

    # Detect short phrase repetition (2-6 chars repeated 3+ times)
    # Catches: "質問は質問は質問は...", "お店のお店のお店の...", "書けて書けて書けて..."
    if re.search(r"(.{2,6})\1{2,}", t):
        # Only flag if the repeated part dominates the text (>50%)
        m = re.search(r"(.{2,6})\1{2,}", t)
        if m and len(m.group(0)) > len(t) * 0.5:
            return True

    # Detect long strings with >60% same character (e.g. "ヨーナヨヨヨヨヨヨヨ...")
    if len(t) > 10:
        from collections import Counter
        char_counts = Counter(t.replace(" ", ""))
        if char_counts and char_counts.most_common(1)[0][1] / len(t.replace(" ", "")) > 0.6:
            return True

    return False


def _normalize_text(text: str) -> str:
    """Normalize text for comparison (strip punctuation variants)."""
    # Remove trailing punctuation differences: "おやすみなさい" vs "おやすみなさい。"
    return re.sub(r"[。、！？!?.,\s]+$", "", text.strip())


def filter_hallucinated_segments(segments: list[dict]) -> list[dict]:
    """Remove hallucinated/repetitive segments from transcription output."""
    if not segments:
        return segments

    filtered = []
    # Track recent texts (sliding window) to catch non-consecutive repetition
    recent_texts: list[str] = []  # normalized texts of last N segments
    WINDOW_SIZE = 10
    MAX_REPEATS_IN_WINDOW = 2  # Allow max 2 same texts in a window of 10

    for seg in segments:
        text = seg["text"].strip()

        # Skip individually hallucinated segments
        if is_hallucination(text):
            continue

        # Check repetition within sliding window
        norm = _normalize_text(text)
        occurrences = recent_texts.count(norm)
        if occurrences >= MAX_REPEATS_IN_WINDOW:
            continue  # Too many repeats in recent window, skip

        recent_texts.append(norm)
        if len(recent_texts) > WINDOW_SIZE:
            recent_texts.pop(0)

        filtered.append(seg)

    before = len(segments)
    after = len(filtered)
    if before != after:
        logger.info(
            f"  Hallucination filter: {before} → {after} segments "
            f"({before - after} removed)"
        )

    return filtered


def _run_whisper(audio_path: str) -> dict:
    """Run mlx-whisper transcription on an audio file path."""
    return mlx_whisper.transcribe(
        audio_path,
        path_or_hf_repo=WHISPER_MODEL_REPO,
        language="ja",
        word_timestamps=True,
        condition_on_previous_text=False,   # Prevent hallucination cascading
        compression_ratio_threshold=2.4,     # Reject overly repetitive output
        no_speech_threshold=0.6,             # Detect non-speech segments
        # NOTE: hallucination_silence_threshold intentionally omitted (22x slower)
        # Post-processing filter handles hallucination cleanup instead
    )


def _find_local_mp3(mp3_path: Path) -> Path | None:
    """Check if a local staging copy exists for this MP3."""
    staging_path = STAGING_DIR / mp3_path.name
    if staging_path.exists() and staging_path.stat().st_size > 1024:
        return staging_path
    return None


def transcribe_local(mp3_path: Path) -> dict:
    """Transcribe audio using mlx-whisper locally (Apple Silicon GPU).

    Prefers local staging copy over Google Drive to avoid FUSE deadlock.
    Falls back to copying from Google Drive with retries if no staging copy.
    """
    logger.info(f"  Transcribing locally: {mp3_path.name}")
    start_time = time.time()

    # Check if file is on Google Drive (FUSE mount) — may need local copy
    is_cloud = "CloudStorage" in str(mp3_path) or "GoogleDrive" in str(mp3_path)

    if is_cloud:
        # Prefer local staging copy (written in Phase 1, no FUSE issues)
        local_path = _find_local_mp3(mp3_path)
        if local_path:
            logger.info(f"  Using staging copy: {local_path}")
            result = _run_whisper(str(local_path))
        else:
            # No staging copy — fall back to copying from Google Drive with retries
            tmp_dir = Path(tempfile.mkdtemp(prefix="voicememo_"))
            tmp_path = tmp_dir / mp3_path.name
            try:
                max_cp_retries = 5
                for cp_attempt in range(1, max_cp_retries + 1):
                    logger.info(f"  Copying from Google Drive to local temp (attempt {cp_attempt}/{max_cp_retries})...")
                    try:
                        with open(mp3_path, "rb") as src, open(tmp_path, "wb") as dst:
                            while True:
                                chunk = src.read(1024 * 1024)
                                if not chunk:
                                    break
                                dst.write(chunk)
                        break  # Success
                    except OSError as e:
                        logger.warning(f"  Copy failed: {e}")
                        tmp_path.unlink(missing_ok=True)
                        if cp_attempt < max_cp_retries:
                            delay = 30 * cp_attempt  # 30s, 60s, 90s, 120s
                            logger.info(f"  Waiting {delay}s before retry...")
                            time.sleep(delay)
                        else:
                            raise RuntimeError(
                                f"Copy failed after {max_cp_retries} attempts: {e}"
                            )
                logger.info(f"  Transcribing from: {tmp_path}")
                result = _run_whisper(str(tmp_path))
            finally:
                tmp_path.unlink(missing_ok=True)
                tmp_dir.rmdir()
                logger.info(f"  Cleaned up temp copy")
    else:
        result = _run_whisper(str(mp3_path))

    elapsed = time.time() - start_time
    duration = result.get("segments", [{}])[-1].get("end", 0) if result.get("segments") else 0

    # If segments didn't give us duration, use ffprobe
    if duration == 0:
        try:
            duration = get_audio_duration(mp3_path)
        except Exception:
            pass

    segments = [
        {
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"],
        }
        for seg in result.get("segments", [])
        if seg.get("text", "").strip()
    ]

    # Post-processing: filter out hallucinated/repetitive segments
    segments = filter_hallucinated_segments(segments)

    # Rebuild clean text from filtered segments
    text = " ".join(seg["text"].strip() for seg in segments)

    logger.info(
        f"  Transcribed: {len(segments)} segments, "
        f"{duration:.0f}s audio in {elapsed:.1f}s"
    )

    return {
        "text": text,
        "segments": segments,
        "duration": duration,
    }


def discover_untranscribed_mp3s(manifest: dict) -> list[dict]:
    """Find MP3 files in Google Drive that haven't been transcribed yet."""
    untranscribed = []

    for wav_name, copy_entry in manifest["copied"].items():
        mp3_name = copy_entry["mp3_name"]

        # Already transcribed?
        if mp3_name in manifest["transcribed"]:
            continue

        # Find the MP3 file
        date = copy_entry["date"]
        mp3_path = MP3_BASE_DIR / date / mp3_name

        # Also check flat directory (legacy from v1)
        if not mp3_path.exists():
            mp3_path = MP3_BASE_DIR / mp3_name

        if not mp3_path.exists():
            logger.warning(f"  MP3 not found: {mp3_name} (skipping)")
            continue

        untranscribed.append(
            {
                "mp3_path": mp3_path,
                "mp3_name": mp3_name,
                "date": copy_entry["date"],
                "time": copy_entry["time"],
                "time_full": copy_entry.get("time_full", copy_entry["time"] + ":00"),
            }
        )

    return untranscribed


def phase2_transcribe(manifest: dict) -> set[str]:
    """
    Phase 2: Transcribe untranscribed MP3 files from Google Drive using mlx-whisper.
    Returns set of dates that had new transcriptions.
    """
    untranscribed = discover_untranscribed_mp3s(manifest)

    if not untranscribed:
        logger.info("Phase 2: No untranscribed MP3 files")
        return set()

    logger.info(f"Phase 2: {len(untranscribed)} file(s) to transcribe")
    notify("Voice Memo", f"Phase 2: {len(untranscribed)}件の文字起こし開始...")
    update_status("processing", 2, "文字起こし中", files_total=len(untranscribed))
    new_dates = set()
    success_count = 0
    fail_count = 0

    # Count expected files per date for partial-failure detection
    date_file_counts: dict[str, int] = {}
    for mp3_info in untranscribed:
        d = mp3_info["date"]
        date_file_counts[d] = date_file_counts.get(d, 0) + 1

    for i, mp3_info in enumerate(untranscribed, 1):
        mp3_name = mp3_info["mp3_name"]
        logger.info(f"Processing: {mp3_name}")
        notify("Voice Memo", f"文字起こし中 ({i}/{len(untranscribed)}): {mp3_info['time']}")
        update_status("processing", 2, "文字起こし中", mp3_name, len(untranscribed), i - 1)

        try:
            transcript = transcribe_local(mp3_info["mp3_path"])

            manifest["transcribed"][mp3_name] = {
                "transcribed_at": datetime.now().isoformat(),
                "duration_seconds": transcript["duration"],
                "date": mp3_info["date"],
                "time": mp3_info["time"],
                "time_full": mp3_info["time_full"],
                "transcript_text": transcript["text"],
                "segments": transcript["segments"],
            }
            save_manifest(manifest)
            new_dates.add(mp3_info["date"])
            success_count += 1

            # Clean up staging copy after successful transcription
            staging_path = STAGING_DIR / mp3_name
            if staging_path.exists():
                staging_path.unlink()
                logger.info(f"  Cleaned up staging file: {mp3_name}")

        except Exception as e:
            logger.error(f"  FAILED transcribing {mp3_name}: {e}", exc_info=True)
            update_status("processing", 2, "文字起こし中", mp3_name, len(untranscribed), i - 1, last_error=str(e)[:100])
            fail_count += 1
            continue

    if fail_count > 0:
        logger.warning(f"Phase 2 completed with {fail_count} failures out of {len(untranscribed)} files")
        notify("Voice Memo", f"⚠ 文字起こし: {success_count}成功 / {fail_count}失敗（次回自動リトライ）")

    return new_dates


# ──────────────────────────────────────────────
# Phase 3: GPT-4o summary + Markdown
# ──────────────────────────────────────────────


def retry_with_backoff(func, max_retries=3, base_delay=5):
    for attempt in range(max_retries):
        try:
            return func()
        except (openai.RateLimitError, openai.APITimeoutError) as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2**attempt)
            logger.warning(
                f"API error ({type(e).__name__}), retrying in {delay}s "
                f"(attempt {attempt + 1}/{max_retries})"
            )
            time.sleep(delay)
        except openai.APIError as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2**attempt)
            logger.warning(f"API error ({e}), retrying in {delay}s")
            time.sleep(delay)
    raise RuntimeError(f"Failed after {max_retries} retries")


def _build_transcript_block(recordings: list[dict]) -> str:
    """Build a transcript text block from a list of recordings."""
    block = ""
    for rec in recordings:
        block += (
            f"\n--- Recording at {rec['time']} "
            f"({rec['duration_min']:.0f} min) ---\n"
        )
        clean_segs = filter_hallucinated_segments(rec["segments"])
        clean_text = " ".join(s["text"].strip() for s in clean_segs if s["text"].strip())
        block += (clean_text or rec["transcript_text"]) + "\n"
    return block


def _call_summary_api(client: openai.OpenAI, prompt: str) -> dict:
    """Call GPT-4o with a summary prompt and return parsed JSON."""
    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a helpful assistant that summarizes voice memos "
                    "in Japanese. Always respond with valid JSON."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
        max_tokens=2000,
    )
    return json.loads(response.choices[0].message.content)


MAX_CHARS_PER_CHUNK = 20000  # ~7,000 tokens — safe for 30k TPM limit


def summarize_transcripts(
    client: openai.OpenAI, date: str, recordings: list[dict]
) -> dict:
    """Call GPT-4o to generate summary and highlights.

    If the transcript is too long for a single API call, it is split into
    chunks, each chunk is summarized individually, and the partial summaries
    are merged in a final API call.
    """
    transcript_block = _build_transcript_block(recordings)

    # If short enough, summarize in one shot
    if len(transcript_block) <= MAX_CHARS_PER_CHUNK:
        prompt = f"""以下は{date}のボイスメモの文字起こしです。
これを分析して、詳細な日報として整理してください。

{transcript_block}

以下の形式でJSON出力してください:
{{
  "summary": "この日1日の活動の流れ（5〜8文、時系列で。何をして、どう動いて、どんなことを考えていたかを具体的に）",
  "time_breakdown": [
    {{
      "time": "09:00〜11:30",
      "duration_min": 150,
      "category": "カテゴリ（例: 仕事・打合せ・移動・食事・プライベート・学習など）",
      "activity": "活動内容（簡潔に）",
      "details": "具体的な内容・話題・成果など（3〜5文。何を話したか、どんな意思決定があったか、どんな結果や気づきが生まれたかまで詳しく書く）"
    }}
  ],
  "deep_conversations": [
    {{
      "topic": "話題のタイトル",
      "insight": "この会話・考えのエッセンス（2〜4文）。抽象度が高い、考えが深い、ユニークな視点など価値あるもの。",
      "quote": "会話から印象的・本質的なひとことを原文に近い形で抜粋（あれば）"
    }}
  ],
  "action_items": ["今後やるべきこと", "決定事項", "フォローアップ"],
  "x_threads_posts": [
    {{
      "platform": "X",
      "content": "ポスト文（140文字以内、日本語。この日の気づきや考えを発信できる形に。ハッシュタグもあれば）"
    }},
    {{
      "platform": "Threads",
      "content": "Threads投稿文（500文字以内。やや長めで、思考の流れや背景も含めて。X版より深く書く）"
    }}
  ]
}}

ルール:
- summaryはこの日1日の流れを時系列で具体的にまとめてください
- time_breakdownは録音時刻をもとに時間帯ごとの活動を列挙。移動中・雑談・環境音のみの時間帯は含めなくてOKです
- deep_conversationsは「抽象度が高い」「本質的」「ユニークな視点がある」「学びや気づきがある」会話・思考を2〜5件抜粋。なければ1件以上は無理に入れなくてOK
- x_threads_postsはこの日のボイスメモの内容から、SNSで発信する価値がある気づき・意見・出来事をポスト案として提案してください。2〜3案あると良い
- 全て日本語で出力してください"""
        return _call_summary_api(client, prompt)

    # --- Chunked summarization for long transcripts ---
    logger.info(
        f"  Transcript too long ({len(transcript_block)} chars), "
        f"splitting into chunks of {MAX_CHARS_PER_CHUNK} chars"
    )

    # Split recordings into chunks that fit under the limit
    chunks: list[list[dict]] = []
    current_chunk: list[dict] = []
    current_len = 0

    for rec in recordings:
        rec_block = _build_transcript_block([rec])
        rec_len = len(rec_block)

        if current_len + rec_len > MAX_CHARS_PER_CHUNK and current_chunk:
            chunks.append(current_chunk)
            current_chunk = []
            current_len = 0

        current_chunk.append(rec)
        current_len += rec_len

    if current_chunk:
        chunks.append(current_chunk)

    logger.info(f"  Split into {len(chunks)} chunks")

    # Summarize each chunk
    partial_summaries = []
    for ci, chunk_recs in enumerate(chunks, 1):
        chunk_block = _build_transcript_block(chunk_recs)
        time_range = f"{chunk_recs[0]['time']}〜{chunk_recs[-1]['time']}"
        logger.info(f"  Summarizing chunk {ci}/{len(chunks)} ({time_range})")

        chunk_prompt = f"""以下は{date}のボイスメモの一部（{time_range}）の文字起こしです。
この時間帯に何をしていたか、どんな会話や考えがあったかを詳しく抽出してください。

{chunk_block}

以下の形式でJSON出力してください:
{{
  "summary": "この時間帯の活動・思考の要約（3〜5文）",
  "time_breakdown": [
    {{
      "time": "開始〜終了",
      "duration_min": 60,
      "category": "カテゴリ",
      "activity": "活動内容",
      "details": "具体的な内容（3〜5文。何を話したか、どんな意思決定があったか、どんな結果や気づきが生まれたかまで詳しく書く）"
    }}
  ],
  "deep_conversations": [
    {{
      "topic": "話題",
      "insight": "エッセンス（2〜3文）",
      "quote": "印象的な一言（あれば）"
    }}
  ],
  "action_items": ["TODO"]
}}

ルール:
- time_breakdownは録音時刻をもとに活動を列挙
- deep_conversationsは抽象度が高い・本質的・ユニークな会話や思考を抽出
- action_itemsがなければ空配列
- 全て日本語で出力してください"""

        partial = retry_with_backoff(
            lambda p=chunk_prompt: _call_summary_api(client, p)
        )
        partial_summaries.append(partial)
        time.sleep(2)  # Avoid TPM burst

    # Merge partial summaries into final summary
    logger.info("  Merging partial summaries...")
    merge_input = ""
    for ci, ps in enumerate(partial_summaries, 1):
        merge_input += f"\n--- パート{ci} ---\n"
        merge_input += f"要約: {ps.get('summary', '')}\n"
        if ps.get("time_breakdown"):
            merge_input += "活動:\n"
            for act in ps["time_breakdown"]:
                merge_input += (
                    f"- {act.get('time', '?')} [{act.get('category', '')}]: {act.get('activity', '')} "
                    f"({act.get('duration_min', '?')}分) — {act.get('details', '')}\n"
                )
        # Legacy support
        if ps.get("activities"):
            merge_input += "活動(旧形式):\n"
            for act in ps["activities"]:
                merge_input += (
                    f"- {act.get('time', '?')}: {act.get('activity', '')} "
                    f"({act.get('duration_min', '?')}分) — {act.get('details', '')}\n"
                )
        if ps.get("deep_conversations"):
            merge_input += "深い会話・考察:\n"
            for dc in ps["deep_conversations"]:
                merge_input += f"- [{dc.get('topic','')}] {dc.get('insight','')}\n"
                if dc.get("quote"):
                    merge_input += f"  引用: 「{dc.get('quote','')}」\n"
        if ps.get("action_items"):
            merge_input += "アクションアイテム:\n"
            for ai in ps["action_items"]:
                merge_input += f"- {ai}\n"

    merge_prompt = f"""以下は{date}のボイスメモを複数パートに分けて分析した結果です。
これらを統合して、1日全体の詳細な日報を作成してください。

{merge_input}

以下の形式でJSON出力してください:
{{
  "summary": "この日1日の活動の流れ（5〜8文）",
  "time_breakdown": [
    {{
      "time": "開始〜終了",
      "duration_min": 60,
      "category": "カテゴリ",
      "activity": "活動内容",
      "details": "詳細（2〜3文）"
    }}
  ],
  "deep_conversations": [
    {{
      "topic": "話題",
      "insight": "エッセンス（2〜4文）",
      "quote": "印象的な一言（あれば）"
    }}
  ],
  "action_items": ["TODO1", "TODO2"],
  "x_threads_posts": [
    {{
      "platform": "X",
      "content": "ポスト文（140文字以内）"
    }},
    {{
      "platform": "Threads",
      "content": "Threads投稿文（500文字以内、やや詳しく）"
    }}
  ]
}}

ルール:
- summaryは1日の流れを時系列で具体的にまとめてください
- time_breakdownは全パートの活動を統合して時系列で並べ、重複を排除してください
- deep_conversationsは全パートから本質的・ユニーク・学びのある会話を2〜5件選んでください
- x_threads_postsはこの日の内容から発信価値のある気づきや意見を提案してください
- 全て日本語で出力してください"""

    return _call_summary_api(client, merge_prompt)


def format_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _format_duration(minutes: int) -> str:
    """Format duration in minutes to a human-readable string."""
    if minutes >= 60:
        h = minutes // 60
        m = minutes % 60
        return f"約{h}時間{m}分" if m else f"約{h}時間"
    return f"約{minutes}分"


def generate_markdown(
    date: str, recordings: list[dict], summary_data: dict
) -> str:
    lines = []
    lines.append(f"# 📓 日報 — {date}")
    lines.append("")

    # ── Summary ─────────────────────────────────
    lines.append("## 🗓 サマリー")
    lines.append("")
    lines.append(summary_data.get("summary", "(要約なし)"))
    lines.append("")

    # ── Time breakdown ───────────────────────────
    time_breakdown = summary_data.get("time_breakdown", []) or summary_data.get("activities", [])
    if time_breakdown:
        lines.append("## ⏱ 時間の使い方")
        lines.append("")
        for act in time_breakdown:
            time_str = act.get("time", "—")
            dur = act.get("duration_min", 0)
            dur_str = _format_duration(dur) if dur else "—"
            category = act.get("category", "")
            activity = act.get("activity", "")
            details = act.get("details", "")
            # Card-style: subheading with time + category badge, then details paragraph
            badge = f" `{category}`" if category else ""
            lines.append(f"### 🕐 {time_str}  ({dur_str}){badge}")
            lines.append(f"**{activity}**")
            lines.append("")
            if details:
                lines.append(details)
            lines.append("")

    # ── Deep conversations / Highlights ──────────
    deep_convs = summary_data.get("deep_conversations", [])
    if deep_convs:
        lines.append("## 💡 深い会話・気づき")
        lines.append("")
        for dc in deep_convs:
            topic = dc.get("topic", "")
            insight = dc.get("insight", "")
            quote = dc.get("quote", "")
            lines.append(f"### {topic}")
            lines.append(insight)
            if quote:
                lines.append("")
                lines.append(f"> 「{quote}」")
            lines.append("")

    # ── Backward compat: old highlights field ────
    highlights = summary_data.get("highlights", [])
    if highlights and not deep_convs:
        lines.append("## 💡 ハイライト")
        lines.append("")
        for h in highlights:
            lines.append(f"- {h}")
        lines.append("")

    # ── Action items ─────────────────────────────
    action_items = summary_data.get("action_items", [])
    if action_items:
        lines.append("## ✅ アクションアイテム")
        lines.append("")
        for item in action_items:
            lines.append(f"- [ ] {item}")
        lines.append("")

    # ── SNS Post Suggestions ─────────────────────
    posts = summary_data.get("x_threads_posts", [])
    if posts:
        lines.append("## 📣 情報発信・投稿案")
        lines.append("")
        for post in posts:
            platform = post.get("platform", "SNS")
            content = post.get("content", "")
            lines.append(f"### {platform}")
            lines.append("")
            lines.append(content)
            lines.append("")

    # ── Transcript ───────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## 📝 文字起こし")
    lines.append("")

    for rec in recordings:
        duration_str = format_timestamp(rec["duration"])
        lines.append(f"### {rec['time']} Recording ({duration_str})")
        lines.append("")

        clean_segments = filter_hallucinated_segments(rec["segments"])
        for seg in clean_segments:
            ts = format_timestamp(seg["start"])
            text = seg["text"].strip()
            if text:
                lines.append(f"`[{ts}]` {text}")
        lines.append("")

    return "\n".join(lines)


def collect_date_transcripts(manifest: dict, date: str) -> list[dict]:
    """Collect all transcripts for a given date from manifest."""
    results = []
    for mp3_name, entry in manifest["transcribed"].items():
        if entry.get("date") != date:
            continue
        results.append(
            {
                "time": entry["time"],
                "time_full": entry.get("time_full", entry["time"] + ":00"),
                "segments": entry.get("segments", []),
                "transcript_text": entry.get("transcript_text", ""),
                "duration": entry.get("duration_seconds", 0),
                "duration_min": entry.get("duration_seconds", 0) / 60,
                "mp3_name": mp3_name,
            }
        )
    return results


def phase3_generate_markdown(
    manifest: dict, dates_to_regenerate: set[str], client: openai.OpenAI
):
    """Phase 3: Generate Markdown reports with GPT-4o summaries."""
    MARKDOWN_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for date in sorted(dates_to_regenerate):
        logger.info(f"Generating Markdown for {date}")

        recordings = collect_date_transcripts(manifest, date)
        recordings.sort(key=lambda r: r["time"])

        if not recordings:
            logger.warning(f"  No transcripts found for {date}, skipping")
            continue

        logger.info(f"  {len(recordings)} recording(s) for this date")

        # Guard: don't overwrite an existing Markdown with fewer recordings
        md_path = MARKDOWN_OUTPUT_DIR / f"voicememo-{date}.md"
        if md_path.exists():
            existing_count = md_path.read_text(encoding="utf-8").count("### ")
            if existing_count > len(recordings):
                logger.warning(
                    f"  Existing Markdown has {existing_count} recordings, "
                    f"but only {len(recordings)} available now — skipping to avoid data loss"
                )
                continue

        notify("Voice Memo", f"Phase 3: {date} の要約とMarkdown生成中...")
        update_status("processing", 3, "要約・Markdown生成中", date, len(dates_to_regenerate), list(sorted(dates_to_regenerate)).index(date))

        # Summarize with GPT-4o
        try:
            summary_data = retry_with_backoff(
                lambda: summarize_transcripts(client, date, recordings)
            )
        except Exception as e:
            logger.warning(
                f"  GPT-4o summarization failed ({e}), generating without summary"
            )
            summary_data = {
                "summary": "(要約の生成に失敗しました。APIクォータ復帰後に再実行してください。)",
                "highlights": [],
            }

        # Generate and write Markdown
        try:
            md_content = generate_markdown(date, recordings, summary_data)
            md_path = MARKDOWN_OUTPUT_DIR / f"voicememo-{date}.md"
            md_path.write_text(md_content, encoding="utf-8")
            logger.info(f"  Written: {md_path}")
        except Exception as e:
            logger.error(
                f"  Failed to generate Markdown for {date}: {e}", exc_info=True
            )


# ──────────────────────────────────────────────
# Main orchestration
# ──────────────────────────────────────────────


LOCK_FILE = Path("/tmp/voicememo-processor.lock")


def acquire_lock() -> bool:
    """Prevent concurrent runs. Returns True if lock acquired."""
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            # Check if process is still running
            os.kill(pid, 0)
            return False  # Another instance is running
        except (ValueError, ProcessLookupError, PermissionError):
            LOCK_FILE.unlink(missing_ok=True)  # Stale lock

    LOCK_FILE.write_text(str(os.getpid()))
    return True


def release_lock():
    LOCK_FILE.unlink(missing_ok=True)


def _init_env():
    """Common initialization: logging, env, manifest."""
    setup_logging()
    dotenv.load_dotenv(SCRIPT_DIR / ".env")
    
    # Fallback for the original author
    author_env = Path.home() / "Documents/GitHub/llm-knowledge-base/.env"
    if not os.environ.get("OPENAI_API_KEY") and author_env.exists():
        dotenv.load_dotenv(author_env)
        
    if not os.environ.get("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY not found in .env (needed for GPT-4o summary)")
        return None
    manifest = load_manifest()
    manifest = migrate_manifest(manifest)
    return manifest


def _finish(all_dates: set[str], remaining: list):
    """Send final notification based on results."""
    if all_dates and not remaining:
        dates_str = ", ".join(sorted(all_dates))
        update_status("done", phase_label=f"完了: {dates_str}")
        notify(
            "Voice Memo",
            f"全処理完了! {dates_str} のMarkdownを生成しました",
            sound="Hero",
        )
    elif all_dates and remaining:
        dates_str = ", ".join(sorted(all_dates))
        update_status("done", phase_label=f"一部完了: {len(remaining)}件未処理")
        notify(
            "Voice Memo",
            f"{dates_str} のMarkdownを生成（{len(remaining)}件は次回リトライ）",
            sound="Glass",
        )
    elif remaining:
        update_status("done", phase_label=f"{len(remaining)}件未処理")
        notify(
            "Voice Memo",
            f"⚠ {len(remaining)}件の文字起こしに失敗。次回自動リトライします",
            sound="Basso",
        )
    else:
        update_status("idle", phase_label="新しいデータなし")


def main():
    setup_logging()

    # Acquire lock to prevent concurrent runs
    if not acquire_lock():
        logger.info("Another instance is running, exiting")
        return

    try:
        # Wait for USB volume to stabilize (if triggered by launchd)
        time.sleep(3)

        logger.info("=" * 60)
        logger.info("Voice Memo Processor v2 started")
        update_status("starting", phase_label="初期化中...")

        manifest = _init_env()
        if manifest is None:
            return

        # Phase 1: Copy WAV → MP3 to Google Drive (only if USB mounted)
        dates_from_copy = phase1_copy_from_usb(manifest)

        # Phase 2: Transcribe untranscribed MP3s from Google Drive (local mlx-whisper)
        dates_from_transcribe = phase2_transcribe(manifest)

        # Check for remaining untranscribed files (failures in this run)
        remaining = discover_untranscribed_mp3s(manifest)

        # Phase 3: Generate Markdown for all affected dates
        all_dates = dates_from_copy | dates_from_transcribe
        if all_dates:
            client = openai.OpenAI()
            phase3_generate_markdown(manifest, all_dates, client)
        else:
            logger.info("No new data to generate Markdown for")

        logger.info("Processing complete")
        logger.info("=" * 60)
        _finish(all_dates, remaining)

    finally:
        release_lock()


def retry():
    """Retry failed transcriptions (Phase 2 + 3 only). No USB needed."""
    setup_logging()

    if not acquire_lock():
        logger.info("Another instance is running, exiting")
        return

    try:
        logger.info("=" * 60)
        logger.info("Voice Memo Processor — RETRY mode")
        update_status("starting", phase_label="リトライ中...")

        manifest = _init_env()
        if manifest is None:
            return

        # Check what's pending
        untranscribed = discover_untranscribed_mp3s(manifest)
        if not untranscribed:
            logger.info("No untranscribed files to retry")
            notify("Voice Memo", "リトライ対象なし — 全ファイル処理済み")
            update_status("idle", phase_label="全ファイル処理済み")
            return

        logger.info(f"Retrying {len(untranscribed)} untranscribed file(s)")
        notify("Voice Memo", f"リトライ開始: {len(untranscribed)}件の文字起こし")

        # Phase 2: Transcribe
        dates_from_transcribe = phase2_transcribe(manifest)

        # Check remaining
        remaining = discover_untranscribed_mp3s(manifest)

        # Phase 3: Generate Markdown
        if dates_from_transcribe:
            client = openai.OpenAI()
            phase3_generate_markdown(manifest, dates_from_transcribe, client)

        logger.info("Retry complete")
        logger.info("=" * 60)
        _finish(dates_from_transcribe, remaining)

    finally:
        release_lock()


def status():
    """Show current processing status."""
    manifest = load_manifest()
    manifest = migrate_manifest(manifest)

    copied = set(e["mp3_name"] for e in manifest["copied"].values())
    transcribed = set(manifest["transcribed"].keys())
    untranscribed = copied - transcribed

    print(f"📊 Voice Memo Status")
    print(f"  Copied:        {len(copied)} files")
    print(f"  Transcribed:   {len(transcribed)} files")
    print(f"  Untranscribed: {len(untranscribed)} files")

    if untranscribed:
        print(f"\n⏳ Pending files:")
        for f in sorted(untranscribed):
            # Find date from manifest
            for _, entry in manifest["copied"].items():
                if entry["mp3_name"] == f:
                    print(f"  {entry['date']} {entry['time']} — {f}")
                    break

    # Check staging dir
    if STAGING_DIR.exists():
        staging_files = list(STAGING_DIR.glob("*.mp3"))
        if staging_files:
            print(f"\n📁 Staging files: {len(staging_files)}")
            for sf in sorted(staging_files):
                size_mb = sf.stat().st_size / (1024 * 1024)
                print(f"  {sf.name} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "retry":
            retry()
        elif cmd == "status":
            status()
        else:
            print(f"Usage: {sys.argv[0]} [retry|status]")
            print(f"  (no args)  Full pipeline (Phase 1+2+3)")
            print(f"  retry      Retry failed transcriptions (Phase 2+3)")
            print(f"  status     Show processing status")
            sys.exit(1)
    else:
        main()
