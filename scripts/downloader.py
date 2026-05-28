"""
downloader.py — fetch GTFS zip files listed in feeds.yml
"""

import logging
import os
import shutil
from pathlib import Path

import requests
import yaml

log = logging.getLogger(__name__)

TIMEOUT = 60  # seconds per download
CHUNK = 1 << 20  # 1 MB


def download_feeds(config: str, out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    with open(config) as f:
        cfg = yaml.safe_load(f)

    feeds = cfg.get("feeds", [])
    if not feeds:
        raise ValueError(f"No feeds found in {config}")

    log.info("Downloading %d feed(s) → %s", len(feeds), out)

    for feed in feeds:
        name = feed["name"]
        dest = out / f"{name}.zip"

        if "url" in feed:
            _download_url(name, feed["url"], dest)
        elif "path" in feed:
            _copy_local(name, feed["path"], dest)
        else:
            log.warning("[%s] No url or path — skipping", name)

    log.info("Download complete.")


def _download_url(name: str, url: str, dest: Path) -> None:
    # Resolve env-var tokens in the URL (e.g. ${FEED_A_TOKEN})
    import re
    url = re.sub(
        r"\$\{([^}]+)\}",
        lambda m: os.environ.get(m.group(1), m.group(0)),
        url,
    )

    log.info("[%s] Downloading %s", name, url)
    try:
        with requests.get(url, stream=True, timeout=TIMEOUT) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=CHUNK):
                    f.write(chunk)
        log.info("[%s] Saved → %s (%.1f MB)", name, dest, dest.stat().st_size / 1e6)
    except requests.RequestException as e:
        log.error("[%s] Download failed: %s", name, e)
        raise


def _copy_local(name: str, src: str, dest: Path) -> None:
    src_path = Path(src)
    if not src_path.exists():
        raise FileNotFoundError(f"[{name}] Local file not found: {src}")
    log.info("[%s] Copying %s → %s", name, src, dest)
    shutil.copy2(src_path, dest)
