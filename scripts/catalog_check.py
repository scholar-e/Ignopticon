#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Check data/catalog.json against what is on disk, and print a table of datasets, tiers, sizes and status.

For each dataset it expands `check_paths` (globs, files or directories, relative to the repo root), sums the file
sizes and counts the files, and compares them with the catalogued `size_bytes`. A dataset whose status is not
"planned" must have files on disk. A size more than TOLERANCE away from the catalogued size is a warning (datasets
that are in progress grow), not an error. Then it prints totals per serving tier.

Usage: catalog_check.py [CATALOG] [TOLERANCE]      defaults: data/catalog.json 0.25
Exit status: 0 if every non-planned dataset exists and every entry has the required fields, 1 otherwise.
"""
import glob
import json
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = ["id", "title", "description", "source", "produced_by", "path", "format", "schema", "grid", "temporal",
            "check_paths", "size_bytes", "status", "in_git", "serving"]
TIERS = {"public-static", "backend-only", "raw-archive"}


def disk_usage(globs):
    files = set()
    for g in globs:
        for p in glob.glob(os.path.join(ROOT, g)):
            if os.path.isfile(p):
                files.add(p)
            elif os.path.isdir(p):
                for d, _, names in os.walk(p):
                    files.update(os.path.join(d, n) for n in names)
    return sum(os.path.getsize(f) for f in files if os.path.exists(f)), len(files)


def human(n):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1000 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1000


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data/catalog.json")
    tol = float(sys.argv[2]) if len(sys.argv) > 2 else 0.25
    cat = json.load(open(path))
    errors, warnings = 0, 0
    totals = {}
    ids = set()
    rows = []
    for e in cat["datasets"]:
        missing = [k for k in REQUIRED if k not in e]
        if missing:
            log.error("%s: missing fields %s", e.get("id"), missing)
            errors += 1
            continue
        if e["id"] in ids:
            log.error("%s: duplicate id", e["id"])
            errors += 1
        ids.add(e["id"])
        tier = e["serving"].get("tier")
        if tier not in TIERS:
            log.error("%s: unknown tier %r", e["id"], tier)
            errors += 1
        if tier == "public-static" and not e["serving"].get("url"):
            log.error("%s: public-static without a serving url", e["id"])
            errors += 1
        size, n = disk_usage(e["check_paths"])
        flag = "ok"
        if n == 0 and e["status"] != "planned":
            log.error("%s: nothing on disk at %s", e["id"], e["check_paths"])
            flag, errors = "MISSING", errors + 1
        elif n and e["status"] == "planned":
            log.warning("%s: marked planned but %d files exist; update the catalog", e["id"], n)
            flag, warnings = "exists", warnings + 1
        elif e["size_bytes"] and abs(size - e["size_bytes"]) > tol * e["size_bytes"]:
            log.warning("%s: %s on disk vs %s catalogued", e["id"], human(size), human(e["size_bytes"]))
            flag, warnings = "size", warnings + 1
        t = totals.setdefault(tier, [0, 0, 0])
        t[0] += size
        t[1] += n
        t[2] += 1
        rows.append((e["id"], tier, human(size), n, e["status"], e["in_git"], flag))

    w = max(len(r[0]) for r in rows)
    lines = [f"{'id':{w}}  {'tier':13}  {'size':>9}  {'files':>6}  {'status':13}  {'git':7}  check",
             "-" * (w + 66)]
    lines += [f"{r[0]:{w}}  {r[1]:13}  {r[2]:>9}  {r[3]:>6}  {r[4]:13}  {r[5]:7}  {r[6]}" for r in rows]
    lines.append("-" * (w + 66))
    for tier in ["public-static", "backend-only", "raw-archive"]:
        if tier in totals:
            s, n, k = totals[tier]
            lines.append(f"{tier + ' total':{w}}  {k:>3} datasets  {human(s):>9}  {n:>6}")
    print("\n".join(lines), flush=True)
    log.info("%d datasets, %d errors, %d warnings", len(rows), errors, warnings)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
