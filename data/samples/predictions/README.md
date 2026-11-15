# August 2, 2026 daily fire predictions

Start with [index.json](index.json). The basic baseline is
[gbm_viirs-only/2026-08-02.png](gbm_viirs-only/2026-08-02.png).
The other four layers compare raw and harmonized training data.

Each PNG follows the prediction encoding in `docs/COMPARISON.md`:

| Channel | Decode |
|---|---|
| R | Probability = R / 255 |
| G | 255 = observed S-NPP fire on the target date; 0 otherwise |
| B | 255 = clear S-NPP observation on the target date; 0 otherwise |
| A | 255 = land; 0 = outside the domain |

A zero G value is only an observed negative where B is 255. G/B are
retrospective evaluation layers, not prediction inputs.

Images are 590×260, north up, covering longitude −125 to −66 and latitude
24 to 50. Cell (x,y) has centre longitude −125 + 0.1×(x+0.5), latitude
50 − 0.1×(y+0.5). Use image pixels directly, without resampling, to decode.
These channels differ from the hotspot-count encoding in `docs/DATASETS.md`.

All five LightGBM models predict August 2 using satellite history through
August 1. Training ends in 2019; thresholds were chosen on 2020.
The index records the exact model hashes and thresholds.

This is a retrospective next-day activity prediction conditional on a clear
satellite look. It uses observed gridMET weather on August 1 and August 2
as a perfect-forecast stand-in. It is not an operational forecast or a fire
perimeter. The 0.1° cells are much coarser than individual fire fronts.

| Arm | Training history |
|---|---|
| viirs-only | VIIRS, 2012–2019 |
| raw-pre | Raw MODIS, 2003–2011 |
| harm-pre | Harmonized MODIS, 2003–2011 |
| raw-all | Raw MODIS 2003–2011 plus VIIRS 2012–2019 |
| harm-all | Harmonized MODIS 2003–2011 plus VIIRS 2012–2019 |
