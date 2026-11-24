# SmartBugs finding-name mapping

`metadata/vulnerabilities_mapping.csv` is copied from [smartbugs-results](https://github.com/smartbugs/smartbugs-results) (Apache-2.0, Durieux et al., ICSE 2020).
It maps each analysis tool's finding names to the DASP categories; `rl_tools/build_dataset.py` and `rl_tools/build_real_dataset.py` read it via `--src datasets/smartbugs_meta`.
