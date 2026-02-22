#!/usr/bin/env python3
"""
Voice Memo Processor - macOS Menu Bar Monitor

Polls status.json written by process_voicememo.py and displays
real-time progress in the macOS menu bar.
"""

import json
import os
import subprocess
from pathlib import Path

import rumps

SCRIPT_DIR = Path(__file__).parent.resolve()
STATUS_PATH = SCRIPT_DIR / "status.json"
LOG_DIR = SCRIPT_DIR / "logs"
MARKDOWN_OUTPUT_DIR = (
    Path.home() / "Documents/GitHub/llm-knowledge-base/0-inbox/voicememo"
)

PHASE_LABELS = {
    1: "MP3変換",
    2: "文字起こし",
    3: "要約生成",
}


class VoiceMemoMonitor(rumps.App):
    def __init__(self):
        super().__init__(name="VoiceMemo", title="🎙", quit_button=None)

        self.status_item = rumps.MenuItem("ステータス: 待機中")
        self.status_item.set_callback(None)
        self.phase_item = rumps.MenuItem("フェーズ: --")
        self.phase_item.set_callback(None)
        self.file_item = rumps.MenuItem("ファイル: --")
        self.file_item.set_callback(None)
        self.progress_item = rumps.MenuItem("進捗: --")
        self.progress_item.set_callback(None)
        self.error_item = rumps.MenuItem("エラー: なし")
        self.error_item.set_callback(None)

        self.menu = [
            self.status_item,
            self.phase_item,
            self.file_item,
            self.progress_item,
            None,
            self.error_item,
            None,
            rumps.MenuItem("ログフォルダを開く", callback=self.open_logs),
            rumps.MenuItem("出力フォルダを開く", callback=self.open_output),
            None,
            rumps.MenuItem("終了", callback=self.quit_app),
        ]

        self._last_status = None

    @rumps.timer(2)
    def poll_status(self, _):
        try:
            if not STATUS_PATH.exists():
                self.title = "🎙"
                self.status_item.title = "ステータス: 待機中"
                self.phase_item.title = "フェーズ: --"
                self.file_item.title = "ファイル: --"
                self.progress_item.title = "進捗: --"
                return

            data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
            status = data.get("status", "idle")
            phase = data.get("phase", 0)
            phase_label = data.get("phase_label", "")
            current_file = data.get("current_file", "")
            total = data.get("files_total", 0)
            completed = data.get("files_completed", 0)
            last_error = data.get("last_error")

            # Menu bar title
            if status == "processing":
                p_name = PHASE_LABELS.get(phase, f"P{phase}")
                self.title = f"🎙 {p_name} {completed}/{total}"
            elif status == "done":
                self.title = "🎙 ✓"
            elif status == "starting":
                self.title = "🎙 ..."
            elif status == "error":
                self.title = "🎙 ⚠"
            else:
                self.title = "🎙"

            # Dropdown items
            status_labels = {
                "idle": "待機中",
                "starting": "起動中",
                "processing": "処理中",
                "done": "完了",
                "error": "エラー",
            }
            self.status_item.title = f"ステータス: {status_labels.get(status, status)}"

            if phase and phase_label:
                self.phase_item.title = f"Phase {phase}: {phase_label}"
            elif phase_label:
                self.phase_item.title = phase_label
            else:
                self.phase_item.title = "フェーズ: --"

            if current_file:
                self.file_item.title = f"ファイル: {current_file}"
            else:
                self.file_item.title = "ファイル: --"

            if total > 0:
                self.progress_item.title = f"進捗: {completed}/{total} ファイル"
            else:
                self.progress_item.title = "進捗: --"

            if last_error:
                self.error_item.title = f"⚠ {last_error[:60]}"
            else:
                self.error_item.title = "エラー: なし"

        except (json.JSONDecodeError, OSError):
            self.title = "🎙"

    def open_logs(self, _):
        subprocess.Popen(["open", str(LOG_DIR)])

    def open_output(self, _):
        subprocess.Popen(["open", str(MARKDOWN_OUTPUT_DIR)])

    def quit_app(self, _):
        rumps.quit_application()


if __name__ == "__main__":
    VoiceMemoMonitor().run()
