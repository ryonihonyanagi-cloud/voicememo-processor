#!/usr/bin/env python3
import os
import sys
from pathlib import Path

def print_step(msg):
    print(f"\033[0;32m[OK]\033[0m {msg}")

def print_warning(msg):
    print(f"\033[1;33m[!]\033[0m {msg}")

def main():
    print("\n--- Environment Setup Wizard ---")
    script_dir = Path(__file__).parent.resolve()
    env_file = script_dir / ".env"
    
    # Load existing to not prompt again if already set
    env_dict = {}
    if env_file.exists():
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    env_dict[k] = v.strip('"\'')
    
    # OpenAI API Key
    api_key = env_dict.get("OPENAI_API_KEY", "")
    if not api_key or api_key == "your-openai-api-key-here":
        api_key = input("\nEnter your OpenAI API Key (leave blank to skip): ").strip()

    # Markdown Output Dir
    default_md_dir = env_dict.get("MARKDOWN_OUTPUT_DIR", str(Path.home() / "Documents/transcripts"))
    print(f"\nWhere do you want the generated Markdown files to be saved?")
    md_dir = input(f"Path [{default_md_dir}]: ").strip()
    md_dir = md_dir if md_dir else default_md_dir
    
    # MP3 Base Dir
    default_mp3_dir = env_dict.get("MP3_BASE_DIR", str(Path.home() / "Documents/Voicememo_MP3_Backup"))
    print(f"\nWhere do you want to back up the converted MP3 files? (e.g. Google Drive folder or any local folder)")
    mp3_dir = input(f"Path [{default_mp3_dir}]: ").strip()
    mp3_dir = mp3_dir if mp3_dir else default_mp3_dir

    # Voicememo Mount
    default_mount = env_dict.get("VOICEMEMO_MOUNT", "/Volumes/VOICEMEMO/RECORD")
    print(f"\nWhat is the absolute path to the RECORD folder on the voice recorder USB?")
    mount_dir = input(f"Path [{default_mount}]: ").strip()
    mount_dir = mount_dir if mount_dir else default_mount

    # Write .env
    with open(env_file, 'w', encoding='utf-8') as f:
        f.write("# Voice Memo Processor Configuration\n")
        f.write(f'OPENAI_API_KEY="{api_key}"\n')
        f.write(f'MARKDOWN_OUTPUT_DIR="{md_dir}"\n')
        f.write(f'MP3_BASE_DIR="{mp3_dir}"\n')
        f.write(f'VOICEMEMO_MOUNT="{mount_dir}"\n')
    
    # Ensure dirs exist
    Path(md_dir).mkdir(parents=True, exist_ok=True)
    Path(mp3_dir).mkdir(parents=True, exist_ok=True)
    
    print_step("Configuration saved and output directories created.")

if __name__ == '__main__':
    main()
