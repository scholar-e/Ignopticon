# Controlled summer pilot: validation 2020

Same VIIRS targets and sampled rows; causal harmonization trained on separate 2012–2014 years. Day-t weather only. July–August, common clear looks. Exploratory validation, not a test result.

| Input arm | AP | AUC | Log loss | Trees |
|---|---:|---:|---:|---:|
| raw | 0.1401 | 0.8224 | 0.009747 | 131 |
| harm | 0.1459 | 0.8271 | 0.010314 | 122 |
| harm_given_viirs | 0.1520 | 0.8099 | 0.010621 | — |
| viirs | 0.1617 | 0.8545 | 0.009377 | 165 |
