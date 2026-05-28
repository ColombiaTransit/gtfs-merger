"""
shape_generator.py — add shapes.txt to feeds that are missing it.

Two methods
-----------
straight  : straight lines between consecutive stops (always works, no deps)
osrm      : road-snapped polylines via OSRM /route/v1/driving (requires OSRM)
"""

import io
import logging
import zipfile
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

log = logging.getLogger(__name__)


def add_shapes_to_all(feeds_dir: str, method: str = "straight", osrm_url: str = "http://localhost:5000") -> None:
    feeds = Path(feeds_dir)
    for zp in sorted(feeds.glob("*.zip")):
        try:
            _process_feed(zp, method=method, osrm_url=osrm_url)
        except Exception as e:
            log.error("[%s] Shape generation failed: %s", zp.stem, e)


def _process_feed(zip_path: Path, method: str, osrm_url: str) -> None:
    name = zip_path.stem

    with zipfile.ZipFile(zip_path) as zf:
        file_names = {Path(n).name: n for n in zf.namelist()}

        if "shapes.txt" in file_names:
            log.info("[%s] shapes.txt already present — skipping", name)
            return

        log.info("[%s] shapes.txt missing — generating via '%s' method", name, method)

        trips = _read(zf, file_names, "trips.txt")
        stop_times = _read(zf, file_names, "stop_times.txt")
        stops = _read(zf, file_names, "stops.txt")

        if trips is None or stop_times is None or stops is None:
            log.warning("[%s] Missing required files for shape generation", name)
            return

        stops["stop_lat"] = pd.to_numeric(stops["stop_lat"], errors="coerce")
        stops["stop_lon"] = pd.to_numeric(stops["stop_lon"], errors="coerce")

        # Build ordered stop-coordinate sequence per trip
        st = (
            stop_times[["trip_id", "stop_id", "stop_sequence"]]
            .copy()
            .assign(stop_sequence=lambda d: pd.to_numeric(d["stop_sequence"], errors="coerce"))
            .sort_values(["trip_id", "stop_sequence"])
            .merge(stops[["stop_id", "stop_lat", "stop_lon"]], on="stop_id", how="left")
        )

        # Deduplicate: trips with identical stop-sequences share one shape
        shape_rows: list[dict] = []
        trip_shape_map: dict[str, str] = {}

        # Key = tuple of (stop_id,…) — same stop pattern → same shape
        pattern_to_shape: dict[tuple, str] = {}
        shape_counter = 0

        for trip_id, grp in st.groupby("trip_id", sort=False):
            pattern = tuple(grp["stop_id"])
            if pattern not in pattern_to_shape:
                shape_counter += 1
                shape_id = f"shape_{shape_counter}"
                pattern_to_shape[pattern] = shape_id

                if method == "osrm":
                    coords = list(zip(grp["stop_lat"], grp["stop_lon"]))
                    pts = _osrm_shape(shape_id, coords, osrm_url)
                else:
                    pts = _straight_shape(shape_id, grp)

                shape_rows.extend(pts)

            trip_shape_map[trip_id] = pattern_to_shape[pattern]

        shapes_df = pd.DataFrame(shape_rows)
        trips["shape_id"] = trips["trip_id"].map(trip_shape_map)

        # Re-write zip with updated trips.txt and new shapes.txt
        _rewrite_zip(zip_path, zf, file_names, trips=trips, shapes=shapes_df)

    log.info("[%s] Generated %d shape(s)", name, shape_counter)


def _straight_shape(shape_id: str, grp: pd.DataFrame) -> list[dict]:
    rows = []
    for seq, (_, row) in enumerate(grp.iterrows()):
        rows.append({
            "shape_id": shape_id,
            "shape_pt_lat": row["stop_lat"],
            "shape_pt_lon": row["stop_lon"],
            "shape_pt_sequence": seq,
            "shape_dist_traveled": "",
        })
    return rows


def _osrm_shape(shape_id: str, coords: list[tuple], osrm_url: str) -> list[dict]:
    """Call OSRM /route/v1/driving and decode the geometry polyline."""
    try:
        import polyline  # pip install polyline
    except ImportError:
        log.warning("polyline package not installed — falling back to straight line for shape %s", shape_id)
        return [
            {"shape_id": shape_id, "shape_pt_lat": lat, "shape_pt_lon": lon,
             "shape_pt_sequence": i, "shape_dist_traveled": ""}
            for i, (lat, lon) in enumerate(coords)
        ]

    coord_str = ";".join(f"{lon},{lat}" for lat, lon in coords)
    url = f"{osrm_url.rstrip('/')}/route/v1/driving/{coord_str}?overview=full&geometries=polyline"
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        encoded = data["routes"][0]["geometry"]
        decoded = polyline.decode(encoded)  # [(lat, lon), …]
        return [
            {"shape_id": shape_id, "shape_pt_lat": lat, "shape_pt_lon": lon,
             "shape_pt_sequence": i, "shape_dist_traveled": ""}
            for i, (lat, lon) in enumerate(decoded)
        ]
    except Exception as e:
        log.warning("OSRM failed for shape %s (%s) — falling back to straight line", shape_id, e)
        return [
            {"shape_id": shape_id, "shape_pt_lat": lat, "shape_pt_lon": lon,
             "shape_pt_sequence": i, "shape_dist_traveled": ""}
            for i, (lat, lon) in enumerate(coords)
        ]


def _read(zf: zipfile.ZipFile, file_names: dict, name: str) -> Optional[pd.DataFrame]:
    if name not in file_names:
        return None
    with zf.open(file_names[name]) as f:
        return pd.read_csv(f, dtype=str, keep_default_na=False)


def _rewrite_zip(
    zip_path: Path,
    original_zf: zipfile.ZipFile,
    file_names: dict,
    trips: pd.DataFrame,
    shapes: pd.DataFrame,
) -> None:
    """Replace the zip in-place with updated trips.txt and new shapes.txt."""
    tmp_path = zip_path.with_suffix(".tmp.zip")
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as out_zf:
        for arc_name in original_zf.namelist():
            bname = Path(arc_name).name
            if bname == "trips.txt":
                out_zf.writestr(arc_name, trips.to_csv(index=False))
            else:
                out_zf.writestr(arc_name, original_zf.read(arc_name))
        out_zf.writestr("shapes.txt", shapes.to_csv(index=False))

    tmp_path.replace(zip_path)
