"""
api/server.py

FastAPI server exposing real evaluation endpoints.
Adds /evaluate/real and /evaluate/random endpoints backed by
the real SmartBugs + Groq evaluation harness.
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
from pathlib import Path
import json

app = FastAPI(
    title="LLM SmartAudit — Real Evaluation API",
    version="2.0",
    description="Real precision/recall evaluation on SmartBugs Curated contracts."
)


# ── Health check ─────────────────────────────────────────────────────────────

@app.get("/")
def root():
    """Health check + available endpoints."""
    contracts = {}
    for t in ["easy", "medium", "hard"]:
        p = Path(f"tasks/{t}")
        contracts[t] = len(list(p.glob("*.sol"))) if p.exists() else 0
    return {
        "status": "ok",
        "contracts_available": contracts,
        "total": sum(contracts.values()),
        "endpoints": [
            "GET /evaluate/real/{difficulty}?n=10",
            "GET /evaluate/random/{difficulty}?n=20",
            "GET /evaluate/split/{difficulty}",
            "GET /results",
        ]
    }


# ── Dataset info ─────────────────────────────────────────────────────────────

@app.get("/dataset/info")
def dataset_info():
    """Returns counts of available SmartBugs contracts per tier."""
    info = {}
    for t in ["easy", "medium", "hard"]:
        p = Path(f"tasks/{t}")
        sols  = list(p.glob("*.sol"))  if p.exists() else []
        jsons = list(p.glob("*.json")) if p.exists() else []
        info[t] = {"sol_files": len(sols), "ground_truth": len(jsons)}
    return info


# ── Create splits ─────────────────────────────────────────────────────────────

@app.post("/evaluate/split/{difficulty}")
def create_split(difficulty: str, test_ratio: float = 0.2):
    """Creates 80/20 train/test split for given difficulty tier."""
    if difficulty not in ("easy", "medium", "hard"):
        raise HTTPException(400, "difficulty must be easy|medium|hard")
    from verify.split_dataset import create_split as _split
    result = _split(difficulty, test_ratio=test_ratio)
    return result


# ── Random baseline ───────────────────────────────────────────────────────────

@app.get("/evaluate/random/{difficulty}")
def random_baseline(difficulty: str, n: int = 20):
    """
    Runs a completely random agent on real SmartBugs contracts.
    Proves discriminative floor is genuinely low (< 0.20).
    """
    if difficulty not in ("easy", "medium", "hard"):
        raise HTTPException(400, "difficulty must be easy|medium|hard")
    try:
        from verify.random_baseline_real import run_random_baseline
        split_file = f"verify/splits/{difficulty}_split.json"
        result = run_random_baseline(
            difficulty, n=n,
            split_file=split_file if Path(split_file).exists() else None,
        )
        return result
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


# ── Real LLM evaluation ───────────────────────────────────────────────────────

@app.get("/evaluate/real/{difficulty}")
def real_evaluation(difficulty: str, n: int = 10):
    """
    Runs real Groq LLM evaluation on actual SmartBugs contracts.
    Returns true precision/recall/F1 scores.

    NOTE: This makes real Groq API calls (costs tokens).
    """
    if difficulty not in ("easy", "medium", "hard"):
        raise HTTPException(400, "difficulty must be easy|medium|hard")

    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        raise HTTPException(500, "GROQ_API_KEY not set. Add to .env file.")

    try:
        from verify.real_evaluator import evaluate_difficulty
        split_file = f"verify/splits/{difficulty}_split.json"
        result = evaluate_difficulty(
            difficulty, n=n,
            split_file=split_file if Path(split_file).exists() else None,
            use_test_split=True,
        )
        # Don't return full per-contract details in API response (too large)
        return {
            "difficulty":    result["difficulty"],
            "n_contracts":   result["n_contracts"],
            "avg_score":     result["avg_score"],
            "avg_precision": result["avg_precision"],
            "avg_recall":    result["avg_recall"],
            "avg_f1":        result.get("avg_f1", 0.0),
        }
    except FileNotFoundError as e:
        raise HTTPException(404, f"{e}\nRun: py scripts/fetch_smartbugs.py")
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Load saved results ────────────────────────────────────────────────────────

@app.get("/results")
def get_results():
    """Returns last saved real evaluation results."""
    result_path = Path("verify/results/real_evaluation.json")
    if not result_path.exists():
        raise HTTPException(404, "No results yet. Run /evaluate/real/{difficulty} first.")
    return json.loads(result_path.read_text())


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
