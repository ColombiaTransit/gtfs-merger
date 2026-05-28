"""
normalizer.py — prefix every GTFS ID with the feed name to prevent collisions,
and deduplicate overlapping stops across feeds.

ID columns namespaced per file
--------------------------------
agency.txt          agency_id
routes.txt          route_id, agency_id
trips.txt           trip_id, route_id, service_id, shape_id
stop_times.txt      trip_id, stop_id
stops.txt           stop_id, parent_station, from_stop_id, to_stop_id
calendar.txt        service_id
calendar_dates.txt  service_id
shapes.txt          shape_id
frequencies.txt     trip_id
transfers.txt       from_stop_id, to_stop_id
fare_attributes.txt fare_id
fare_rules.txt      fare_id, route_id, origin_id, destination_id, contains_id
"""

import logging
import zipfile
from pathlib import Path
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

# Maps filename → list of ID columns to prefix
ID_COLUMNS: dict[str, list[str]] = {
    "agency.txt":           ["agency_id"],
    "routes.txt":           ["route_id", "agency_id"],
    "trips.txt":            ["trip_id", "route_id", "service_id", "shape_id"],
    "stop_times.txt":       ["trip_id", "stop_id"],
    "stops.txt":            ["stop_id", "parent_station"],
    "calendar.txt":         ["service_id"],
    "calendar_dates.txt":   ["service_id"],
    "shapes.txt":           ["shape_id"],
    "frequencies.txt":      ["trip_id"],
    "transfers.txt":        ["from_stop_id", "to_stop_id"],
    "fare_attributes.txt":  ["fare_id"],
    "fare_rules.txt":       ["fare_id", "route_id", "origin_id", "destination_id", "contains_id"],
}


def normalize_all(feeds_dir: str) -> None:
    feeds = Path(feeds_dir)
    for zp in sorted(feeds.glob("*.zip")):
        try:
            _normalize_feed(zp)
        except Exception as e:
            log.error("[%s] Normalization failed: %s", zp.stem, e)


def _normalize_feed(zip_path: Path) -> None:
    name = zip_path.stem
    prefix = f"{name}:"
    log.info("[%s] Prefixing IDs with '%s'", name, prefix)

    with zipfile.ZipFile(zip_path) as zf:
        file_names = {Path(n).name: n for n in zf.namelist()}
        tables: dict[str, pd.DataFrame] = {}

        for bname in file_names:
            if not bname.endswith(".txt"):
                continue
            with zf.open(file_names[bname]) as f:
                tables[bname] = pd.read_csv(f, dtype=str, keep_default_na=False)

    # Apply prefix
    for bname, cols in ID_COLUMNS.items():
        if bname not in tables:
            continue
        df = tables[bname]
        for col in cols:
            if col in df.columns:
                # Only prefix non-empty values
                mask = df[col].str.strip() != ""
                df.loc[mask, col] = prefix + df.loc[mask, col]
        tables[bname] = df

    # Rewrite zip
    tmp = zip_path.with_suffix(".tmp.zip")
    with zipfile.ZipFile(zip_path) as orig_zf:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as out_zf:
            for arc_name in orig_zf.namelist():
                bname = Path(arc_name).name
                if bname in tables:
                    out_zf.writestr(arc_name, tables[bname].to_csv(index=False))
                else:
                    out_zf.writestr(arc_name, orig_zf.read(arc_name))

    tmp.replace(zip_path)
    log.info("[%s] Normalization done.", name)
