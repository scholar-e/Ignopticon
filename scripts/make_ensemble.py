#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Write one directory per ensemble member, for scripts/run_member.sh to run locally or as a Slurm array task.

Every combination of the comma-separated values becomes a member (m0000, m0001, ...), whose member.json holds its
engine and settings; OUTDIR/members.txt lists the member directories, one per line, for xargs or the array.

Engines:
  ellipse  scripts/ellipse_spread.py, the stand-in until ELMFIRE runs. Settings: state, issue, wind, head_ros,
           hours, hpa (as ellipse_spread.py takes them).
  elmfire  ELMFIRE. Settings: template, a directory holding elmfire.data and its inputs (copied into the member,
           inputs symlinked), and any &namelist variable to set in elmfire.data, e.g. WIND_SPEED_MULTIPLIER or
           SIMULATION_TSTOP, written as KEY = VALUE in place of the template's line for KEY.

Usage: make_ensemble.py OUTDIR ENGINE KEY=VALUE[,VALUE...] ...
       e.g. make_ensemble.py data/ensembles/widemouth ellipse state=data/fires/widemouth-2026-08-02-state.tif \
                issue=2026-08-02T12:00 wind=earth/public/data/weather/2026/08/02/1800-wind-surface-level-gfs-1.0.json \
                head_ros=0,0.01,0.02,0.03,0.05,0.1 hours=24
"""
import itertools
import json
import logging
import os
import re
import shutil
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

ENGINES = ("ellipse", "elmfire")


def set_namelist(text, key, value):
    """elmfire.data with KEY = VALUE in place of the KEY line, or added before the end of its first namelist
    group if the template doesn't set KEY."""
    pattern = re.compile(r"^(\s*)%s\s*=.*$" % re.escape(key), re.MULTILINE | re.IGNORECASE)
    if pattern.search(text):
        return pattern.sub(lambda m: "%s%s = %s" % (m.group(1), key, value), text, count=1)
    raise KeyError("%s is not in the template's elmfire.data; add it there first so its namelist group is "
                   "known" % key)


def main(outdir, engine, *settings):
    if engine not in ENGINES:
        raise SystemExit("engine must be one of %s" % ", ".join(ENGINES))
    keys, choices = [], []
    for setting in settings:
        key, values = setting.split("=", 1)
        keys.append(key)
        choices.append(values.split(","))
    os.makedirs(outdir, exist_ok=True)
    members = []
    for i, combo in enumerate(itertools.product(*choices)):
        member = os.path.join(outdir, "m%04d" % i)
        os.makedirs(member, exist_ok=True)
        values = dict(zip(keys, combo))
        if engine == "elmfire":
            template = values.pop("template")
            text = open(os.path.join(template, "elmfire.data")).read()
            for key, value in values.items():
                text = set_namelist(text, key, value)
            open(os.path.join(member, "elmfire.data"), "w").write(text)
            for name in os.listdir(template):  # inputs, shared between members
                if name != "elmfire.data" and not os.path.exists(os.path.join(member, name)):
                    os.symlink(os.path.abspath(os.path.join(template, name)), os.path.join(member, name))
            values["template"] = template
        json.dump({"engine": engine, "settings": values}, open(os.path.join(member, "member.json"), "w"), indent=1)
        members.append(member)
    with open(os.path.join(outdir, "members.txt"), "w") as out:
        out.write("\n".join(members) + "\n")
    log.info("wrote %d %s members to %s", len(members), engine, outdir)


if __name__ == "__main__":
    main(*sys.argv[1:])
