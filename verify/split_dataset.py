"""
verify/split_dataset.py

Creates reproducible 80/20 train/test splits from SmartBugs contracts.
Train set: used for RL policy design/tuning
Test set:  held out, never seen during training
"""

from __future__ import annotations
import json, random
from pathlib import Path


def create_split(
    difficulty: str,
    test_ratio: float = 0.2,
    seed: int = 42,
) -> dict:
    """
    Create and save a train/test split for one difficulty tier.

    Parameters
    ----------
    difficulty : "easy" | "medium" | "hard"
    test_ratio : fraction of contracts for test set (default 0.2 = 20%)
    seed       : random seed for reproducibility

    Returns
    -------
    dict with train / test lists and sizes
    """
    task_dir  = Path(f"tasks/{difficulty}")
    sol_files = sorted(task_dir.glob("*.sol"))

    # Only include contracts that have matching ground truth
    valid_stems = [
        f.stem for f in sol_files
        if f.with_suffix(".json").exists()
    ]

    if not valid_stems:
        print(f"  [WARN] No valid contracts in tasks/{difficulty}/ — skipping")
        return {"difficulty": difficulty, "train": [], "test": [], "error": "no contracts"}

    rng = random.Random(seed)
    shuffled = valid_stems[:]
    rng.shuffle(shuffled)

    split_idx  = max(1, int(len(shuffled) * (1 - test_ratio)))
    train_set  = shuffled[:split_idx]
    test_set   = shuffled[split_idx:]

    split = {
        "difficulty":  difficulty,
        "seed":        seed,
        "test_ratio":  test_ratio,
        "total":       len(valid_stems),
        "train_size":  len(train_set),
        "test_size":   len(test_set),
        "train":       train_set,
        "test":        test_set,
    }

    out_path = Path(f"verify/splits/{difficulty}_split.json")
    out_path.parent.mkdir(exist_ok=True, parents=True)
    out_path.write_text(json.dumps(split, indent=2))

    print(f"  {difficulty:<8}: total={len(valid_stems)}  "
          f"train={len(train_set)}  test={len(test_set)}  "
          f"=> verify/splits/{difficulty}_split.json")
    return split


if __name__ == "__main__":
    print("Creating 80/20 train/test splits for all difficulty tiers...\n")
    for level in ["easy", "medium", "hard"]:
        create_split(level, test_ratio=0.2, seed=42)
    print("\nDone. Splits saved to verify/splits/")
