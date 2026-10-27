"""
training/train_policies.py

Trains the PPO mode selector and the DQN stopping policy and saves them where
verify/mode_evaluator.py (--evaluator rl) and run_rl_audit.py load them:

  models/mode_selector/mode_selector.zip
  models/stopping_policy/stopping_policy.zip

Same procedure as `run_rl_audit.py --train` (1000 synthetic contracts, 50,000
timesteps per policy, rl_policies default hyperparameters) without importing
the ChatDev pipeline. Seeds are fixed, and models/training_info.json records
the settings, package versions, model hashes and a post-training check.

The policies learn from the simulation in rl_policies (no LLM calls).
`py verify/run_real_evaluation.py --evaluator rl` then applies them to real
contracts.

Run:
    py training/train_policies.py
    py training/train_policies.py --timesteps 20000 --seed 1
"""

from __future__ import annotations
import argparse, collections, hashlib, json, logging, platform, random, sys, tempfile, time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import torch

from evaluation.benchmark_datasets import BenchmarkDatasetManager
from rl_policies.mode_selector import ModeSelector
from rl_policies.stopping_policy import StoppingPolicy


def seed_everything(seed: int):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def real_contract_modes(mode_selector: ModeSelector) -> dict:
    """Modes the trained PPO picks for the real held-out test contracts (no LLM calls)."""
    from rl_environment.contract_analyzer import ContractAnalyzer
    from verify.eval_common import TIERS, load_contracts
    analyzer = ContractAnalyzer(use_slither=False)
    counts = {}
    for tier in TIERS:
        modes = collections.Counter(
            mode_selector.predict(analyzer.analyze_contract(
                p.read_text(encoding="utf-8", errors="replace")))[0].name
            for p in load_contracts(tier))
        counts[tier] = dict(sorted(modes.items()))
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timesteps", type=int, default=50_000, help="Timesteps per policy")
    parser.add_argument("--contracts", type=int, default=1000, help="Synthetic contracts")
    parser.add_argument("--seed",      type=int, default=42)
    parser.add_argument("--models-dir", default=str(ROOT / "models"))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    models_dir = Path(args.models_dir)
    started = time.time()

    seed_everything(args.seed)
    with tempfile.TemporaryDirectory() as data_dir:
        split = BenchmarkDatasetManager(data_dir=data_dir).generate_synthetic_dataset(
            n_contracts=args.contracts)
    print(f"Synthetic dataset: train={len(split.train_samples)} "
          f"val={len(split.validation_samples)} test={len(split.test_samples)}")

    print(f"\n=== PPO mode selector ({args.timesteps} timesteps) ===")
    seed_everything(args.seed)
    mode_selector = ModeSelector(split, model_save_path=str(models_dir / "mode_selector"))
    mode_summary = mode_selector.train(total_timesteps=args.timesteps)
    t_mode = time.time() - started

    print(f"\n=== DQN stopping policy ({args.timesteps} timesteps) ===")
    seed_everything(args.seed)
    stopping = StoppingPolicy(split, model_save_path=str(models_dir / "stopping_policy"))
    stop_summary = stopping.train(total_timesteps=args.timesteps)
    t_total = time.time() - started

    mode_zip = models_dir / "mode_selector" / "mode_selector.zip"
    stop_zip = models_dir / "stopping_policy" / "stopping_policy.zip"

    # Post-training check: reload from disk and evaluate on the synthetic test split
    seed_everything(args.seed)
    reloaded_mode = ModeSelector(split, model_save_path=str(models_dir / "mode_selector"))
    reloaded_mode.load_policy()
    reloaded_stop = StoppingPolicy(split, model_save_path=str(models_dir / "stopping_policy"))
    reloaded_stop.load_policy()
    synthetic_modes = collections.Counter(
        reloaded_mode.predict(s.features)[0].name for s in split.test_samples)
    real_modes = real_contract_modes(reloaded_mode)

    info = {
        "trained_at":     datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "procedure":      "rl_policies ModeSelector.train / StoppingPolicy.train "
                          "(same as run_rl_audit.py --train)",
        "training_data":  "synthetic simulation (evaluation/benchmark_datasets.py "
                          "generate_synthetic_dataset); no LLM calls",
        "seed":           args.seed,
        "synthetic_contracts": args.contracts,
        "timesteps_per_policy": args.timesteps,
        "train_seconds":  {"mode_selector": round(t_mode, 1), "total": round(t_total, 1)},
        "mode_selector":  {"path": mode_zip.relative_to(ROOT).as_posix() if mode_zip.is_relative_to(ROOT) else str(mode_zip),
                           "sha256": file_hash(mode_zip), "config": mode_selector.config,
                           "train_summary": mode_summary},
        "stopping_policy": {"path": stop_zip.relative_to(ROOT).as_posix() if stop_zip.is_relative_to(ROOT) else str(stop_zip),
                            "sha256": file_hash(stop_zip), "config": stopping.config,
                            "train_summary": stop_summary},
        "check": {
            "synthetic_test_mode_counts": dict(sorted(synthetic_modes.items())),
            "real_test_split_mode_counts": real_modes,
        },
        "versions": {"python": platform.python_version(),
                     **{p: version(p) for p in ("torch", "stable-baselines3", "gymnasium", "numpy")}},
    }
    (models_dir / "training_info.json").write_text(
        json.dumps(info, indent=2, default=str), encoding="utf-8")

    print(f"\nSaved {mode_zip}\n      {stop_zip}\n      {models_dir / 'training_info.json'}")
    print(f"PPO modes on synthetic test split: {dict(synthetic_modes)}")
    print(f"PPO modes on real test contracts:  {real_modes}")
    print(f"Training time: {t_total / 60:.1f} min")


if __name__ == "__main__":
    main()
