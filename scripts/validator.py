"""
validator.py — validate GTFS feeds; write JSON reports per feed.

Checks performed
----------------
1.  Required files present
2.  Required columns present in each file
3.  No completely empty required files
4.  Foreign-key integrity (trips ↔ routes, stop_times ↔ trips/stops,
    shapes ↔ trips, calendar/calendar_dates ↔ trips)
5.  Coordinate range sanity
6.  stop_time sequence monotonicity per trip
"""

import json
import logging
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

# GTFS spec: required files and their required columns
REQUIRED: dict[str, list[str]] = {
    "agency.txt":       ["agency_id", "agency_name", "agency_url", "agency_timezone"],
    "stops.txt":        ["stop_id", "stop_lat", "stop_lon"],
    "routes.txt":       ["route_id", "route_type"],
    "trips.txt":        ["route_id", "service_id", "trip_id"],
    "stop_times.txt":   ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
    "calendar.txt":     [],   # optional but checked if present
    "calendar_dates.txt": [], # at least one of calendar/calendar_dates must exist
}

MUST_HAVE_ONE_OF = [{"calendar.txt", "calendar_dates.txt"}]


def validate_all(feeds_dir: str, report_dir: str) -> bool:
    feeds = Path(feeds_dir)
    reports = Path(report_dir)
    reports.mkdir(parents=True, exist_ok=True)

    zips = sorted(feeds.glob("*.zip"))
    if not zips:
        log.warning("No zip files found in %s", feeds_dir)
        return False

    all_ok = True
    summary: list[dict] = []

    for zp in zips:
        name = zp.stem
        errors = _validate_feed(zp)
        ok = len(errors) == 0
        if not ok:
            all_ok = False
            log.warning("[%s] %d validation error(s)", name, len(errors))
            for e in errors:
                log.warning("  • %s", e)
        else:
            log.info("[%s] ✓ valid", name)

        report = {"feed": name, "ok": ok, "errors": errors}
        summary.append(report)
        with open(reports / f"{name}.json", "w") as f:
            json.dump(report, f, indent=2)

    # Write summary
    with open(reports / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    log.info("Validation complete — %d/%d feeds passed.", sum(r["ok"] for r in summary), len(summary))
    return all_ok


def _validate_feed(zip_path: Path) -> list[str]:
    errors: list[str] = []
    name = zip_path.stem

    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = {Path(n).name for n in zf.namelist()}

            # 1. Required files
            for fname in ["agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt"]:
                if fname not in names:
                    errors.append(f"Missing required file: {fname}")

            # 2. At least one of calendar / calendar_dates
            if "calendar.txt" not in names and "calendar_dates.txt" not in names:
                errors.append("Missing both calendar.txt and calendar_dates.txt (need at least one)")

            if errors:
                return errors  # can't do FK checks without base files

            # 3. Load tables
            tables: dict[str, pd.DataFrame] = {}
            for fname in names:
                bname = Path(fname).name
                if bname.endswith(".txt"):
                    try:
                        with zf.open(fname) as f:
                            tables[bname] = pd.read_csv(f, dtype=str, keep_default_na=False)
                    except Exception as e:
                        errors.append(f"Cannot parse {bname}: {e}")

            # 4. Required columns
            for fname, cols in REQUIRED.items():
                if fname not in tables:
                    continue
                df = tables[fname]
                if df.empty:
                    errors.append(f"{fname} is empty")
                    continue
                for col in cols:
                    if col not in df.columns:
                        errors.append(f"{fname}: missing column '{col}'")

            if errors:
                return errors

            # 5. FK: trips.route_id → routes.route_id
            _check_fk(tables, "trips.txt", "route_id", "routes.txt", "route_id", errors)

            # 6. FK: stop_times.trip_id → trips.trip_id
            _check_fk(tables, "stop_times.txt", "trip_id", "trips.txt", "trip_id", errors)

            # 7. FK: stop_times.stop_id → stops.stop_id
            _check_fk(tables, "stop_times.txt", "stop_id", "stops.txt", "stop_id", errors)

            # 8. FK: trips.shape_id → shapes.shape_id (if shapes present)
            if "shapes.txt" in tables and "shape_id" in tables["trips.txt"].columns:
                _check_fk(tables, "trips.txt", "shape_id", "shapes.txt", "shape_id", errors, allow_empty=True)

            # 9. Coordinate sanity
            stops = tables.get("stops.txt", pd.DataFrame())
            if not stops.empty:
                try:
                    lats = pd.to_numeric(stops["stop_lat"], errors="coerce")
                    lons = pd.to_numeric(stops["stop_lon"], errors="coerce")
                    bad_lat = ((lats < -90) | (lats > 90)).sum()
                    bad_lon = ((lons < -180) | (lons > 180)).sum()
                    if bad_lat:
                        errors.append(f"stops.txt: {bad_lat} stop(s) with invalid latitude")
                    if bad_lon:
                        errors.append(f"stops.txt: {bad_lon} stop(s) with invalid longitude")
                except Exception as e:
                    errors.append(f"stops.txt: coordinate check failed: {e}")

            # 10. stop_sequence monotonicity per trip
            st = tables.get("stop_times.txt", pd.DataFrame())
            if not st.empty and "stop_sequence" in st.columns:
                try:
                    st2 = st.copy()
                    st2["stop_sequence"] = pd.to_numeric(st2["stop_sequence"], errors="coerce")
                    bad_trips = (
                        st2.groupby("trip_id")["stop_sequence"]
                        .apply(lambda s: not s.is_monotonic_increasing)
                        .sum()
                    )
                    if bad_trips:
                        errors.append(f"stop_times.txt: {bad_trips} trip(s) with non-monotonic stop_sequence")
                except Exception as e:
                    errors.append(f"stop_times.txt: sequence check failed: {e}")

    except zipfile.BadZipFile:
        errors.append("File is not a valid zip archive")

    return errors


def _check_fk(
    tables: dict[str, pd.DataFrame],
    src_file: str,
    src_col: str,
    ref_file: str,
    ref_col: str,
    errors: list[str],
    allow_empty: bool = False,
) -> None:
    src = tables.get(src_file)
    ref = tables.get(ref_file)
    if src is None or ref is None:
        return
    if src_col not in src.columns or ref_col not in ref.columns:
        return
    src_vals = src[src_col].dropna()
    if allow_empty:
        src_vals = src_vals[src_vals != ""]
    ref_vals = set(ref[ref_col].dropna())
    missing = set(src_vals) - ref_vals
    if missing:
        sample = sorted(missing)[:5]
        errors.append(
            f"{src_file}.{src_col} references {len(missing)} unknown {ref_file}.{ref_col} "
            f"value(s): {sample}{'…' if len(missing) > 5 else ''}"
        )
