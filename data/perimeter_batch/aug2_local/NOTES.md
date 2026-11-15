# Aug 2 2026 12Z ops forecasts, run locally

Driver: `scripts/aug2_forecasts.py` (docstring has the details). It runs the physics3 `adj1` member exactly as
`scripts/perimeter_batch.py` ran it on Hyak: INIT=feds, HRRR 12Z wrfsubhf 15-min wind (`hrrr_weather.py --subhourly`),
EMC m1, lagged m10 and previous-day gridMET fm100, ADJ=1, LH 90, 12 h. It covers the fires in `data/perimeter_batch/fires.txt`
that have no 2026-08-02T12Z entry in `earth/public/data/forecasts/index.json` yet (50 of the 76; the other 26 were already
there). It runs 6 fires at a time, each ELMFIRE run on one core. It does no scoring.

The driver downloads the HRRR GRIB once and the fires share it: hourly f00-f24 goes to `data/raw/hrrr_aug2_12z` and is
hard-linked into each build, and sub-hourly f00-f12 goes to `data/raw/hrrr_subh`.
Each fire keeps its inputs in `data/training/fires/FIRE/`. Per-fire logs and the run are in `aug2_local/FIRE/`
(`build.log`, `wx-subh.log`, `elmfire.log`, `convert.log`, `adj1/`).

Started with:
    nohup scripts/aug2_forecasts.py > data/perimeter_batch/aug2_local/run.log 2>&1 &

Check progress:
    tail data/perimeter_batch/aug2_local/driver.log
    python3 -c "import json,collections;s=json.load(open('data/perimeter_batch/aug2_local/status.json'));print(collections.Counter(v['status'] for v in s.values()))"
    ls data/perimeter_batch/aug2_local/DONE   # appears at the end and holds the status counts

Each fire's status is one of:
- `ok`: forecast written.
- `no growth`: forecast written, but ELMFIRE grew nothing. The file is still written, and the ops page shows "no growth predicted".
- `no FEDS start`: no FEDS perimeter for this fire at issue, or no associated VIIRS hot cells in the 6 h before.
  These fires get no run. Hyak raised an error in the same cases.
- `build failed`: `build_fire_case.py` failed, or the case is not ready or is truncated.
- `elmfire failed`.
- `failed`: an unexpected error (see `detail`).
- `existing forecast (skipped)`.

Re-running the driver resumes. It skips anything that is already built, has weather, has been run, or has a forecast.

The forecasts land in `earth/public/data/forecasts/FEDS_ID/2026-08-02T1200.json` plus `index.json`. The run does not
commit them; the main session commits `earth/public/data/forecasts` afterwards.
