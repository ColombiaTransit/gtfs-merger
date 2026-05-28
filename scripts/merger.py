"""
merger.py — concatenate all normalized GTFS feeds into a single merged.zip.

Merge strategy per file
-----------------------
agency.txt          concat, deduplicate on agency_id
stops.txt           concat, deduplicate on stop_id (keep first occurrence)
routes.txt          concat
trips.txt           concat
stop_times.txt      concat
calendar.txt        concat
calendar_dates.txt  concat
shapes.txt          concat
frequencies.txt     concat
transfers.txt       concat
fare_attributes.txt concat
fare_rules.txt      concat

Files that exist in some but not all feeds are safely omitted or partially filled.
"""

import logging
import zipfile
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

# Files to merge and their dedup key (None = no dedup, just concat)
MERGE_FILES: list[tuple[str, Optional[str]]] = [
    ("agency.txt",          "agency_id"),
    ("stops.txt",           "stop_id"),
    ("routes.txt",          None),
    ("trips.txt",           None),
    ("stop_times.txt",      None),
    ("calendar.txt",        None),
    ("calendar_dates.txt",  None),
    ("shapes.txt",          None),
    ("frequencies.txt",     None),
    ("transfers.txt",       None),
    ("fare_attributes.txt", None),
    ("fare_rules.txt",      None),
]

from typing import Optional


def merge_feeds(feeds_dir: str, out_path: str) -> None:
    feeds = Path(feeds_dir)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    zips = sorted(feeds.glob("*.zip"))
    if not zips:
        raise RuntimeError(f"No zip files found in {feeds_dir}")

    log.info("Merging %d feed(s) → %s", len(zips), out)

    # Load all tables from all feeds
    all_tables: dict[str, list[pd.DataFrame]] = {}
    for zp in zips:
        name = zp.stem
        try:
            with zipfile.ZipFile(zp) as zf:
                file_names = {Path(n).name: n for n in zf.namelist()}
                for bname, _ in MERGE_FILES:
                    if bname not in file_names:
                        continue
                    with zf.open(file_names[bname]) as f:
                        df = pd.read_csv(f, dtype=str, keep_default_na=False)
                    all_tables.setdefault(bname, []).append(df)
        except Exception as e:
            log.error("[%s] Failed to read during merge: %s — skipping", name, e)

    # Concatenate and deduplicate
    merged: dict[str, pd.DataFrame] = {}
    for bname, dedup_col in MERGE_FILES:
        frames = all_tables.get(bname)
        if not frames:
            continue
        combined = pd.concat(frames, ignore_index=True)
        if dedup_col and dedup_col in combined.columns:
            before = len(combined)
            combined = combined.drop_duplicates(subset=[dedup_col], keep="first")
            dupes = before - len(combined)
            if dupes:
                log.info("  %s: removed %d duplicate row(s) on '%s'", bname, dupes, dedup_col)
        merged[bname] = combined
        log.info("  %s: %d row(s)", bname, len(combined))

    # Write merged zip
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for bname, df in merged.items():
            zf.writestr(bname, df.to_csv(index=False))

    size_mb = out.stat().st_size / 1e6
    log.info("Merge complete → %s (%.2f MB)", out, size_mb)
    _print_summary(merged)


def _print_summary(merged: dict[str, pd.DataFrame]) -> None:
    log.info("─" * 50)
    log.info("%-30s %10s", "File", "Rows")
    log.info("─" * 50)
    for bname, df in merged.items():
        log.info("%-30s %10d", bname, len(df))
    log.info("─" * 50)
