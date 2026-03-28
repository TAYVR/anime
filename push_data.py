"""
push_data.py — commit & push all scraped data to the GitHub repo.
Called automatically by the GitHub Actions workflow.
"""

import os
import subprocess
import sys
from datetime import datetime


def run(cmd: str):
    result = subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    return result


def main():
    # Git identity (needed in CI)
    run('git config user.email "bot@animelek-scraper.ci"')
    run('git config user.name "AnimeLek Bot"')

    # Stage all changes
    run("git add -A")

    # Check if there is anything to commit
    status = subprocess.run("git status --porcelain", shell=True, capture_output=True, text=True)
    if not status.stdout.strip():
        print("Nothing to commit — data unchanged.")
        return

    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    run(f'git commit -m "chore: auto-scrape data {timestamp}"')
    run("git push")
    print("Data pushed to GitHub ✓")


if __name__ == "__main__":
    main()
