# Ignopticon

Wildfire spread forecasting (ELMFIRE + VIIRS updates) shown on a fork of cambecc's earth, for the NASA Space Apps 2026 MODIS/VIIRS challenge.

| Need | Start here |
| --- | --- |
| Goals, decisions, schedule, next action | [PLAN.md](PLAN.md) |
| Background research (from an earlier ChatGPT thread) | [research/](research/) |
| What changed in the modelling science, and the evidence | [MODELLING_LOG.md](MODELLING_LOG.md) |
| Putting the web app online | [docs/DEPLOY.md](docs/DEPLOY.md) |

- Modelling matters more than display.
- US only for now. Hyak: Tillicum (GPU cluster, billed per GPU-hour) is available ($250 budget, enforced); the 48-hour plan must still run on this machine (RTX 5090, 16 cores). See hyak/README.md.
- ELMFIRE is CPU-only (Fortran/MPI). The GPU is for post-event ML.
- Research files are extracted from a ChatGPT share and contain leftover UI markup (`<box>`, `<Cite>` tags); read past it.
- Every change to the modelling science (assumptions, inputs, initial state, parameters, calibration, scores) gets a dated entry in MODELLING_LOG.md as it happens: what changed, why, the numbers, and caveats. Mark in-sample results as such.
