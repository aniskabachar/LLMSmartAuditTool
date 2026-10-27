"""
verify/eval_common.py

Shared helpers for the real (ground-truth) evaluation harness:
  - load the held-out test split for a tier
  - deterministic, seeded contract selection
  - per-contract records that keep the raw LLM output and parsed findings
  - run metadata (git commit, prompt hash, seed) for reproducibility

Every evaluator stores the agent's parsed findings per contract, so
verify/regrade.py can recompute all metrics from a results file without
calling the LLM again.
"""

from __future__ import annotations
import hashlib, json, platform, random, subprocess
from datetime import datetime, timezone
from pathlib import Path

from auditor.models import Action
from auditor.grader import grade

ROOT     = Path(__file__).resolve().parent.parent
TIERS    = ("easy", "medium", "hard")
SPLIT_SEED  = 42   # seed used by verify/split_dataset.py
SAMPLE_SEED = 42   # seed for sub-sampling when n < test split size


def split_path(difficulty: str) -> Path:
    return ROOT / "verify" / "splits" / f"{difficulty}_split.json"


def rel(path: Path) -> str:
    """Repo-relative POSIX path when inside the repo, else the path as given."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def load_contracts(difficulty: str, use_test_split: bool = True) -> list[Path]:
    """
    Return contracts (with ground truth) for a tier, sorted by name.

    use_test_split=True  -> only the held-out test split (raises if missing)
    use_test_split=False -> every labelled contract in tasks/{difficulty}/
    """
    task_dir  = ROOT / "tasks" / difficulty
    sol_files = sorted(f for f in task_dir.glob("*.sol")
                       if f.with_suffix(".json").exists())
    if not sol_files:
        raise FileNotFoundError(
            f"No labelled .sol files in tasks/{difficulty}/. "
            f"Run: py scripts/fetch_smartbugs.py"
        )
    if not use_test_split:
        return sol_files

    sp = split_path(difficulty)
    if not sp.exists():
        raise FileNotFoundError(
            f"Missing {sp.relative_to(ROOT)}. Run: py verify/split_dataset.py"
        )
    test_stems = set(json.loads(sp.read_text(encoding="utf-8"))["test"])
    test_files = [f for f in sol_files if f.stem in test_stems]
    missing    = test_stems - {f.stem for f in test_files}
    if missing:
        raise FileNotFoundError(
            f"{len(missing)} test-split contracts missing from tasks/{difficulty}/: "
            f"{sorted(missing)[:3]}..."
        )
    return test_files


def select_contracts(paths: list[Path], n: int | None = None,
                     seed: int = SAMPLE_SEED) -> list[Path]:
    """All contracts when n is None (or >= len), else a seeded sample, sorted."""
    paths = sorted(paths)
    if n is None or n >= len(paths):
        return paths
    return sorted(random.Random(seed).sample(paths, n))


def contract_record(sol_path: Path, action: Action, raw: list[str],
                    error: str | None = None) -> dict:
    """Grade one contract and keep everything needed to re-grade it later."""
    gt     = json.loads(sol_path.with_suffix(".json").read_text(encoding="utf-8"))
    result = grade(action, gt)
    return {
        "contract":        sol_path.name,
        "score":           result["score"],
        "precision":       result["precision"],
        "recall":          result["recall"],
        "f1":              result["f1"],
        "true_positives":  result["true_positives"],
        "false_positives": result["false_positives"],
        "missed_bugs":     result["missed_bugs"],
        "found_types":     result["found_types"],
        "expected_types":  result["expected_types"],
        "action":          action.model_dump(mode="json"),
        "raw_responses":   raw,
        "error":           error,
    }


def summarize(difficulty: str, details: list[dict]) -> dict:
    """Mean metrics over per-contract records (errors count as score 0)."""
    if not details:
        return {"difficulty": difficulty, "n_contracts": 0, "error": "no contracts evaluated"}
    n = len(details)
    mean = lambda key: round(sum(d[key] for d in details) / n, 4)
    return {
        "difficulty":    difficulty,
        "n_contracts":   n,
        "n_errors":      sum(1 for d in details if d.get("error")),
        "avg_score":     mean("score"),
        "avg_precision": mean("precision"),
        "avg_recall":    mean("recall"),
        "avg_f1":        mean("f1"),
        "details":       details,
    }


def sha256(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
    return h.hexdigest()[:16]


def git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                               capture_output=True, text=True, timeout=10)
        if out.returncode != 0:
            return None
        return out.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")
    except Exception:
        return None


def run_metadata(**extra) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit":   git_commit(),
        "python":       platform.python_version(),
        "split_seed":   SPLIT_SEED,
        **extra,
    }
