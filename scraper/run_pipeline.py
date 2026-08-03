"""Orchestrates the full pipeline: fetch listings -> filter -> description
check -> write output/candidates.csv for review.
"""
import argparse
import csv
import sys
from pathlib import Path

from describe_check import check_listings
from fetch_listings import get_listings
from filter_listings import filter_and_sort

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "output" / "candidates.csv"

CSV_FIELDS = [
    "company",
    "role",
    "location",
    "url",
    "date_posted",
    "ats",
    "grad_flag",
    "hardware_flag",
    "status",
]


def load_existing_status(path: Path) -> dict[str, str]:
    """Preserve status (skip/applied/etc.) for URLs already reviewed in a prior run."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return {row["url"]: row.get("status", "") for row in reader}


def run(limit: int) -> None:
    print("Fetching listings...")
    listings = get_listings()
    print(f"  {len(listings)} total listings parsed")

    filtered = filter_and_sort(listings)
    print(f"  {len(filtered)} remain after keyword/location/closed filtering")

    print(f"Checking descriptions for grad-degree/hardware-management flags (limit={limit})...")
    checked = check_listings(filtered, limit=limit)

    existing_status = load_existing_status(OUTPUT_PATH)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in checked:
            writer.writerow(
                {
                    "company": row["company"],
                    "role": row["role"],
                    "location": row["location"],
                    "url": row["url"],
                    "date_posted": row["date_posted"],
                    "ats": row["ats"],
                    "grad_flag": row["grad_flag"],
                    "hardware_flag": row["hardware_flag"],
                    "status": existing_status.get(row["url"], ""),
                }
            )

    print(f"Wrote {len(checked)} rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=40,
        help="Max number of postings to open for description-level checking per run (default: 40)",
    )
    args = parser.parse_args()
    run(args.limit)
