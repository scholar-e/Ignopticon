# Compare experiment notes (GBM agent)
- 17:22 strict harmonization fit (base, 2012-2020) + apply 2003-2011 -> data/harmonize/out-base-strict (log data/harmonize/logs/strict-fit-apply.log)
- 17:28 tensor format fixed, README.md written; viirs 2012-2024 building (3 procs)
- harm conventions: days-since fire = 3x3 sum p >= 0.5; GBM near-fire sampling uses p >= 0.05
- 17:29 strict fit done: held-out 2013/16/19 (train 2012/14/15/17/18/20) AUC 0.9618, logloss 0.0038, Brier 0.00069, exp 135413 vs obs 135440
- 17:50 GBM: full-grid predict too slow (17us/row); now lr 0.1, <=600 trees, predict on mask | fire crops only. running viirs-only raw-pre (log gbm-a.log)
- 17:55 viirs-only done (val CSI .114), raw-pre val thr .109 CSI .110; harm 2003-08 built; waiting apply 2010-11
