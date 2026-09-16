"""
Single-file launcher for panels that just want one file uploaded.

What it does:
  1. Downloads the latest code from the GitHub repo (as a zip - no git
     binary required on the host).
  2. Installs requirements.txt.
  3. Runs main.py as a subprocess.
  4. If the bot process crashes, restarts it automatically.
  5. Periodically checks GitHub for new commits; if found, re-downloads
     the code and restarts the bot with the update.

Just upload this file, set the start command to "python app.py", and set
the same environment variables (TELEGRAM_BOT_TOKEN, GEMINI_API_KEY,
GITHUB_TOKEN, GITHUB_REPO, GITHUB_BRANCH) on the panel.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile

REPO_OWNER_NAME = "LowkeyIsHim/sian-bot"
BRANCH = "main"

REPO_ZIP_URL = f"https://github.com/{REPO_OWNER_NAME}/archive/refs/heads/{BRANCH}.zip"
API_COMMIT_URL = f"https://api.github.com/repos/{REPO_OWNER_NAME}/commits/{BRANCH}"

SRC_DIR = "bot_src"
CHECK_INTERVAL_SECONDS = 300  # how often to check for new commits
RESTART_DELAY_SECONDS = 5     # wait before restarting a crashed bot

_last_sha = None
_process = None
_process_lock = threading.Lock()


def _log(msg: str) -> None:
    print(f"[launcher] {msg}", flush=True)


def get_latest_sha():
    try:
        req = urllib.request.Request(
            API_COMMIT_URL, headers={"User-Agent": "sian-bot-launcher"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
        return data["sha"]
    except Exception as e:
        _log(f"Could not check latest commit: {e}")
        return None


def download_and_extract() -> None:
    _log("Downloading latest code from GitHub...")
    req = urllib.request.Request(
        REPO_ZIP_URL, headers={"User-Agent": "sian-bot-launcher"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        zip_bytes = resp.read()

    temp_dir = "bot_src_new"
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        z.extractall(temp_dir)

    # GitHub zips extract into a single subfolder, e.g. "sian-bot-main"
    extracted_root = os.path.join(temp_dir, os.listdir(temp_dir)[0])

    if os.path.exists(SRC_DIR):
        shutil.rmtree(SRC_DIR)
    shutil.move(extracted_root, SRC_DIR)
    shutil.rmtree(temp_dir, ignore_errors=True)
    _log("Code updated.")


def install_requirements() -> None:
    req_file = os.path.join(SRC_DIR, "requirements.txt")
    if os.path.exists(req_file):
        _log("Installing requirements...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", req_file],
            check=False,
        )


def start_bot_process() -> subprocess.Popen:
    main_file = os.path.join(SRC_DIR, "main.py")
    _log("Starting bot process...")
    return subprocess.Popen([sys.executable, main_file], cwd=SRC_DIR)


def update_checker() -> None:
    """Background thread: periodically checks for new commits and
    restarts the bot with fresh code when one is found."""
    global _last_sha, _process

    while True:
        time.sleep(CHECK_INTERVAL_SECONDS)
        sha = get_latest_sha()
        if not sha or sha == _last_sha:
            continue

        _log("New commit detected. Updating and restarting bot...")
        with _process_lock:
            if _process and _process.poll() is None:
                _process.terminate()
                try:
                    _process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    _process.kill()
            try:
                download_and_extract()
                install_requirements()
            except Exception as e:
                _log(f"Update failed, keeping previous code: {e}")
            _last_sha = sha
            _process = start_bot_process()


def main() -> None:
    global _last_sha, _process

    try:
        _last_sha = get_latest_sha()
        download_and_extract()
        install_requirements()
    except Exception as e:
        _log(f"Initial setup failed: {e}")
        sys.exit(1)

    _process = start_bot_process()

    threading.Thread(target=update_checker, daemon=True).start()

    # crash-watch loop: restarts the bot if it exits unexpectedly
    while True:
        with _process_lock:
            proc = _process
        if proc is None:
            time.sleep(1)
            continue

        exit_code = proc.wait()

        with _process_lock:
            if _process is proc:  # wasn't already replaced by an update
                _log(f"Bot process exited (code {exit_code}). Restarting in {RESTART_DELAY_SECONDS}s...")
                time.sleep(RESTART_DELAY_SECONDS)
                _process = start_bot_process()


if __name__ == "__main__":
    main()
