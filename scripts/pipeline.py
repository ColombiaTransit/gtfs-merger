#!/usr/bin/env python3
"""
GTFS Pipeline CLI
Usage: python scripts/pipeline.py <command> [options]
Commands: download, validate, shapes, normalize, merge
"""

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)


def cmd_download(args):
    from downloader import download_feeds
    download_feeds(config=args.config, out_dir=args.out)


def cmd_validate(args):
    from validator import validate_all
    ok = validate_all(feeds_dir=args.input, report_dir=args.report)
    if not ok:
        log.warning("Some feeds failed validation — check the report. Continuing.")
        sys.exit(0)  # non-fatal: upstream uses continue-on-error


def cmd_shapes(args):
    from shape_generator import add_shapes_to_all
    add_shapes_to_all(feeds_dir=args.input, method=args.method, osrm_url=args.osrm_url)


def cmd_normalize(args):
    from normalizer import normalize_all
    normalize_all(feeds_dir=args.input)


def cmd_merge(args):
    from merger import merge_feeds
    merge_feeds(feeds_dir=args.input, out_path=args.out)


def main():
    parser = argparse.ArgumentParser(description="GTFS processing pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    # download
    p = sub.add_parser("download", help="Download feed zips listed in feeds.yml")
    p.add_argument("--config", default="feeds.yml", help="Path to feeds.yml")
    p.add_argument("--out", default="tmp/feeds", help="Output directory for zips")

    # validate
    p = sub.add_parser("validate", help="Validate all feeds")
    p.add_argument("--input", default="tmp/feeds", help="Directory of feed zips")
    p.add_argument("--report", default="tmp/validation", help="Directory for reports")

    # shapes
    p = sub.add_parser("shapes", help="Generate missing shapes")
    p.add_argument("--input", default="tmp/feeds", help="Directory of feed zips")
    p.add_argument("--method", choices=["straight", "osrm"], default="straight")
    p.add_argument("--osrm-url", default="http://localhost:5000", help="OSRM base URL")

    # normalize
    p = sub.add_parser("normalize", help="Prefix & deduplicate IDs across feeds")
    p.add_argument("--input", default="tmp/feeds", help="Directory of feed zips")

    # merge
    p = sub.add_parser("merge", help="Merge all feeds into one zip")
    p.add_argument("--input", default="tmp/feeds", help="Directory of feed zips")
    p.add_argument("--out", default="tmp/merged.zip", help="Output merged zip path")

    args = parser.parse_args()
    dispatch = {
        "download": cmd_download,
        "validate": cmd_validate,
        "shapes": cmd_shapes,
        "normalize": cmd_normalize,
        "merge": cmd_merge,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
