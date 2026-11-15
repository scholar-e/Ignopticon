# FRONT_PMODE=weighted notes (2026-11-15)

- ELMFIRE adj: elmfire_io.f90 resets adj < 0 to 1 (silently); adj = 0 is kept, and the level set only tags /
  ignites cells with adj > 0 (elmfire_level_set.f90 ~1360, 2962, 3034) and spread velocity scales with adj
  (elmfire_spread_rate.f90 122-267). Decision: p = 0 cells are NOT ignited (phi +1, interior fuel 99, as binary
  non-selection) rather than adj = 0; positive p gets multiplier max(p, 0.01) (FRONT_PFLOOR), so no zero or
  near-zero speed is ever emitted. Empty selection -> whole edge at x1.
- Level-set limitation (found by the binary_pos diagnostic arm): ELMFIRE advances phi in an unburned cell with
  that cell's own velocity, so a multiplier on cells already burning at t=0 barely matters. Dev: weighted ==
  binary_pos (same ignition set, x1) to <= 0.0001 growth IoU. The weighted arm is effectively "activate every
  covered edge cell with p > 0". Downstream weighting would be needed for the multiplier to act (not done: the
  landscape-wide clipped multiplier was the earlier, worse experiment).
- Raw 2026 window: verify.json — VNP14A1/weather npz windows equal the tensors exactly for the 3 dev days.
  Dev probability rasters equal data/edge_causal/activation's bit for bit; control/binary reproduce that pilot.
- Held-out manifest frozen before any held-out run (manifest.json, 12 fires, 6 regions).

## Handoff state (held-out run in progress)
Done: weighted mode in scripts/elmfire_case.py; tests pass (`uv run tests/test_front_probability.py`, 3 tests OK);
dev runs + scores (dev/comparison.md, dev/scores.json); manifest.json frozen; scripts/edge_front_weighted.py,
generalized scripts/edge_pilot_score.py. Nothing committed yet; MODELLING_LOG/AI_README not yet updated.

Running: `uv run scripts/edge_front_weighted.py heldout` (PID in heldout.pid, log heldout.log / heldout.out).
Per fire: landscape_download.py (LANDFIRE, network) if landscape.tif missing, HRRR sub-hourly 12 h weather from
cached data/raw/hrrr, edge_probability.tif, then arms control/binary/binary_pos/weighted under heldout/FIRE/.
Marker: data/edge_causal/front_weighted/heldout/DONE (written when the PID exits). Status: heldout/status.json.

Next:
1. `cat data/edge_causal/front_weighted/heldout/status.json` (failures recorded, not dropped). Re-running
   `heldout` is safe: completed cases are reused only if their overrides match.
2. `uv run scripts/edge_pilot_score.py data/edge_causal/front_weighted/heldout --manifest data/edge_causal/front_weighted/manifest.json --arms control,binary,binary_pos,weighted`
   -> heldout/comparison.md, heldout/scores.json.
3. MODELLING_LOG.md entry 22 (weighted multiplier, adj floor, level-set limitation, dev table from
   dev/comparison.md marked development/in-sample, held-out table, coverage/failures, LF2025 fuels fine for 2026
   but anachronistic for dev fires; daily 0.1-degree probabilities are not calibrated fine-cell spread rates).
4. AI_README.md -> short done/next. 5. Commit explicit paths: scripts/elmfire_case.py scripts/edge_front_weighted.py
   scripts/edge_pilot_score.py tests/test_front_probability.py MODELLING_LOG.md AI_README.md
   data/edge_causal/front_weighted/{NOTES.md,manifest.json,verify.json,dev/comparison.md,dev/scores.json,dev/pilot.json,
   heldout/comparison.md,heldout/scores.json,heldout/status.json} (no tifs/case dirs); end message with
   Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>; git pull --rebase --autostash if push rejected.

## 2026-11-15 resume: held-out scored, band variant done
Held-out scored (heldout/scores.json, comparison.md); band arms band750/band2000 run on dev + held-out
(band_status.json, all 30 complete; marker BAND_DONE). Tables: heldout_table.md/json (scripts/edge_heldout_table.py),
figure growth_iou_vs_control.png. MODELLING_LOG entry 22, AI_README updated. Next: commit.
