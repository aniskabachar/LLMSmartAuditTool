# ablation/__init__.py
"""
Ablation study package for LLM SmartAudit RL-Adaptive system.

Entry points
------------
    ablation_runner.py   — run all 8 ablation conditions
    analyze_results.py   — print results table and key findings

Degraded components
-------------------
    degraded_components/random_mode_selector.py
    degraded_components/fixed_stopping_policy.py
    degraded_components/masked_feature_extractor.py

Configs
-------
    configs/full_v2.yaml
    configs/no_ppo.yaml
    configs/no_dqn.yaml
    configs/no_new_features.yaml
    configs/no_ta_expansion.yaml
    configs/no_ensemble.yaml
    configs/no_reward_v2.yaml
    configs/no_curriculum.yaml
"""
