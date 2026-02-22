# Voice Memo Processor (Automated AI Transcription & Summarization)

This system provides an automated pipeline that detects a voice recorder USB, copies the audio files, transcribes them using local AI, summarizes the transcriptions with OpenAI's `GPT-4o`, and outputs Markdown reading notes. 

---

## 🇬🇧 English Manual

### Prerequisites
- **OpenAI API Key** (for summarization with GPT-4o)
- **FFmpeg** installed (Used for converting WAV to MP3).
- **macOS (Recommended):** Apple Silicon Mac (M1/M2/M3/M4) with at least 8GB RAM.
- **Windows:** Python 3 installed. *(Note: Full automation and local GPU transcription are currently optimized for macOS. Windows users will need to run the script manually and modify the AI model import).*

### 📁 How to Get Absolute Paths (For USB Drives / External Storage)
During setup, you will be asked for the absolute path of your USB Voice Recorder, Markdown output folder, and MP3 backup folder.

**On macOS:**
1. Connect your USB drive. Open **Finder** and locate the target folder (e.g., the `RECORD` folder inside your USB).
2. Right-click the folder, hold down the **`Option (⌥)`** key, and click **"Copy '...' as Pathname"**.
   *(Example: `/Volumes/VOICEMEMO/RECORD`)*
   - *Alternative:* Open Terminal and drag & drop the folder into the Terminal window to reveal the path.

**On Windows:**
1. Connect your USB drive. Open **File Explorer** and locate the target folder.
2. Hold down the **`Shift`** key, right-click the folder, and select **"Copy as path"**.
   *(Example: `"D:\RECORD"` or `"E:\VoiceMemos"`)*

### Installation & Setup

#### macOS Setup (Fully Automated)
1. Open Terminal.
2. Clone or download this repository, then navigate to the folder:
   ```bash
   cd /path/to/voicememo-processor
   ```
3. Run the installation script:
   ```bash
   ./install.sh
   ```
4. The setup wizard will prompt you for your absolute paths.
5. **Done!** The system will now automatically run in the background whenever you plug in the USB device.

#### Windows Setup (Manual Execution)
*Note: Due to system differences, Windows does not support `LaunchAgent` (USB auto-detection) or `mlx-whisper` (Apple Silicon optimized AI).*
1. Install [FFmpeg for Windows](https://ffmpeg.org/download.html) and add it to your system PATH.
2. Replace `mlx-whisper` with your preferred Whisper library (e.g., `openai-whisper` or `faster-whisper`) in `process_voicememo.py`.
3. Open Command Prompt or PowerShell and install dependencies:
   ```cmd
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```
4. Run the setup script to configure your paths interactively:
   ```cmd
   python setup.py
   ```
5. **To Run Data:** Execute the main script manually whenever your USB is connected:
   ```cmd
   python process_voicememo.py
   ```

---

## 🇯🇵 日本語マニュアル

ボイスレコーダー（USB）をPCに接続するだけで、自動的に音声をコピーし、ローカルAIで文字起こしを行い、OpenAIの `GPT-4o` で要約してMarkdown形式で日報を出力する自動化システムです。

### 必須条件
- **OpenAI API Key**（GPT-4oでの要約やハイライト作成に必要です）
- **FFmpegインストール済み**（WAVからMP3への変換に使用します）
- **macOS（推奨）:** Apple Silicon搭載Mac (M1/M2/M3/M4チップ)、メモリ8GB以上。
- **Windows:** Python3インストール済み。*(※注意事項: 現在のバージョンはmacOSに特化（USB接続検知機能・Mac専用の高速AI）して構築されています。Windowsで利用する場合は、手動実行するかコードの一部改変が必要です)。*

### 📁 絶対パス（Absolute Path）の取得方法
インストール（初期設定）時に、USBドライブのフォルダや、バックアップ先のフォルダの「絶対パス（PC内部での正確な情報・住所）」を聞かれます。以下の方法で取得してターミナルに貼り付けてください。

**macOSの場合:**
1. USBをMacに接続し、**Finder** で対象のフォルダ（USB内の `RECORD` フォルダなど）を開きます。
2. フォルダを右クリックし、キーボードの **`Option (⌥)`** キーを押し続けます。
3. メニューが変わり **「"〇〇"のパス名をコピー」** と表示されるので、それをクリックします。
   *(例: `/Volumes/VOICEMEMO/RECORD`)*
   - *別ルート:* 「ターミナル」アプリを開き、対象のフォルダを黒い画面にそのままドラッグ＆ドロップするとパスが表示されます。

**Windowsの場合:**
1. USBをPCに接続し、**エクスプローラー** で対象のフォルダを開きます。
2. キーボードの **`Shift`** キーを押しながら対象フォルダを右クリックし、表示されたメニューから **「パスのコピー」** をクリックします。
   *(例: `"D:\RECORD"` や `"E:\VoiceMemos"`)*

### インストールと初期設定

#### macOSでのセットアップ（完全自動化）
1. 「ターミナル」を開き、ダウンロードしたフォルダに移動します。
   ```bash
   cd /path/to/voicememo-processor
   ```
2. インストールスクリプトを実行します。
   ```bash
   ./install.sh
   ```
3. 画面の指示に従って、APIキーや各フォルダの絶対パスを入力してください。
4. **完了です！** 今後はUSBをMacに挿すだけで、全自動でバックグラウンド処理が開始されます。画面右上のメニューバーの 🎙️ アイコンで進捗が確認できます。

#### Windowsでのセットアップ（手動実行）
*※注意: WindowsではUSBの自動検知（LaunchAgent）や、Mac専用AI（mlx-whisper）がそのままでは動作しません。*
1. [FFmpeg（Windows版）](https://ffmpeg.org/download.html) をインストールし、システム環境変数(PATH)に通します。
2. コード内の `process_voicememo.py` を開き、インポートされている `mlx-whisper` をWindows対応の `whisper` や `faster-whisper` に書き換える＆処理部分を書き換える必要があります。
3. コマンドプロンプトまたはPowerShellを開き、仮想環境を構築します。
   ```cmd
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```
4. セットアップスクリプトを起動し、ターミナルの指示に従ってパスを設定します。
   ```cmd
   python setup.py
   ```
5. **使い方:** ボイスレコーダーを接続後、手動で以下のコマンドを実行して処理を開始します。
   ```cmd
   python process_voicememo.py
   ```

### トラブルシューティング（手動コマンド）
- 何らかの原因で文字起こしに失敗した分だけを再試行する:
  `./venv/bin/python3 process_voicememo.py retry`
- 現在の処理状況を確認する:
  `./venv/bin/python3 process_voicememo.py status`
