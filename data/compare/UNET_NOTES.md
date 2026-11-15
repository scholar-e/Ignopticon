# U-Net agent notes (docs/COMPARISON.md model 2)

- 17:25 torch 2.15+cu132 via uv works on the 5090. scripts/unet_train.py written; synthetic smoke passes (learns persistence).
- 17:35 loader rewritten to tensors/README.md (27 dynamic + 17 static + land + static_finite = 46 channels).
  Full-grid inference on the zero-padded 272x592 grid (fully convolutional = tiling with every overlap).
  Platt (a*logit+b) on all of 2020 undoes the oversampling (POS_FRAC 0.5) and pos_weight 5; threshold = max CSI on 2020.
- 17:37 local real smoke (viirs-only, 2018-19, 300 steps x64): train loss 0.20 -> 0.09, val AP 0.119 -> 0.139, AUC 0.88.
- Tillicum: hyakusage before = $0.03. hyak/torch.sif = docker://pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime (torch 2.7.1, numpy 2.2.6, H200 ok); pull job 471642 (<=$0.45).
- 17:5x submitted job 471744 array 0,2,4 (raw-pre, raw-all, viirs-only), est $5.40 at the 2 h limit. Defaults BATCH 128, STEPS 500, EPOCHS 40, PATIENCE 8.
- 17:50 round 1 (single seed, EPOCHS 40 OneCycle, early stop at peak LR): ~7-9 min per arm on H200 (load 2 min, 15 s/epoch).
  val AP (2020, calibrated): raw-pre 0.1714 (best ep 5), raw-all 0.1808 (ep 12), viirs-only 0.1730 (ep 8). Logs/json in data/compare/unet_round1/.
  Test AP 2021/22/23/24: raw-pre .183/.108/.084/.121, raw-all ?/.120/.094/.129, viirs-only .182/.114/.089/.124.
  Arm gaps are about seed-noise size -> round 2: 3 seeds per arm, EPOCHS 16 (LR anneals), no early stop beyond best-val checkpoint;
  scripts/unet_ensemble.py averages calibrated probs, threshold = max CSI on averaged 2020. Job 471814 (raw/viirs arms, est $6.08). hyakusage $0.43 at 17:55.
- Codex resumed 2026-11-14: nine seeds completed, $1.45 usage, no active jobs.
  Downloaded existing seeds; uploading all nine harm years. Ensemble now
  checks completeness and dates and handles threshold ties at float16.
- Submitted missing harm-pre/harm-all seeds as 472421 tasks 1,3,6,8,11,13,
  same round-two settings, 45-minute cap each ($4.05 combined scheduled max).
  data/compare/logs/finish-resume.sh waits for successful completion, downloads
  seeds/models/logs, averages all five arms, and runs U-Net/GBM evaluation,
  figures and UI export. Monitor finish-resume.log; resume-complete is success.
