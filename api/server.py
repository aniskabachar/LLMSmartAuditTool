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

from auditor.models import Action

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
            "POST /reset",
            "POST /step",
            "GET /state",
            "GET /evaluate/real/{difficulty}?n=&seed=42&labels=curated",
            "GET /evaluate/random/{difficulty}?n=&seed=7777&labels=curated",
            "POST /evaluate/split/{difficulty}",
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


# ── Audit environment: reset / step / state ──────────────────────────────────

class ResetRequest(BaseModel):
    difficulty:    str           = "easy"
    split:         str           = "test"      # train | test | all
    labels:        str           = "curated"   # curated | all
    seed:          Optional[int] = None
    contract:      Optional[str] = None        # serve a specific contract
    with_features: bool          = False       # include RL contract features


_env = None   # one shared environment session per server process


@app.post("/reset")
def env_reset(req: Optional[ResetRequest] = None):
    """Start an episode: returns the next contract's source (labels hidden)."""
    global _env
    from auditor.environment import AuditEnvironment
    req = req or ResetRequest()
    try:
        if _env is None:
            _env = AuditEnvironment(req.difficulty, req.split, req.labels,
                                    req.seed or 0, req.with_features)
        _env.with_features = req.with_features
        obs, info = _env.reset(seed=req.seed, options={
            "difficulty": req.difficulty, "split": req.split,
            "labels": req.labels, "contract": req.contract,
        })
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return {"observation": obs, "info": info}


@app.post("/step")
def env_step(action: Action):
    """Submit findings for the current contract; reward = grader score."""
    if _env is None:
        raise HTTPException(409, "Call POST /reset first")
    try:
        obs, reward, terminated, truncated, info = _env.step(action)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    obs.pop("source_code", None)   # already sent by /reset
    return {"observation": obs, "reward": reward, "terminated": terminated,
            "truncated": truncated, "info": info}


@app.get("/state")
def env_state():
    """Current episode and running mean reward."""
    if _env is None:
        raise HTTPException(409, "Call POST /reset first")
    return _env.state()


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
def random_baseline(difficulty: str, n: Optional[int] = None, seed: int = 7777,
                    labels: str = "curated"):
    """
    Runs a completely random agent on the held-out test split.
    n omitted = whole test split. Proves discriminative floor is genuinely low (< 0.20).
    labels: curated (dataset category labels) | all (+ static-tool findings)
    """
    if difficulty not in ("easy", "medium", "hard"):
        raise HTTPException(400, "difficulty must be easy|medium|hard")
    if labels not in ("curated", "all"):
        raise HTTPException(400, "labels must be curated|all")
    try:
        from verify.random_baseline_real import run_random_baseline
        return run_random_baseline(difficulty, n=n, seed=seed, use_test_split=True,
                                   labels=labels)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


# ── Real LLM evaluation ───────────────────────────────────────────────────────

@app.get("/evaluate/real/{difficulty}")
def real_evaluation(difficulty: str, n: Optional[int] = None, seed: int = 42,
                    labels: str = "curated"):
    """
    Runs real Groq LLM evaluation on the held-out test split.
    n omitted = whole test split. Returns true precision/recall/F1 scores.
    labels: curated (dataset category labels) | all (+ static-tool findings)

    NOTE: This makes real Groq API calls (costs tokens).
    """
    if difficulty not in ("easy", "medium", "hard"):
        raise HTTPException(400, "difficulty must be easy|medium|hard")
    if labels not in ("curated", "all"):
        raise HTTPException(400, "labels must be curated|all")

    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        raise HTTPException(500, "GROQ_API_KEY not set. Add to .env file.")

    try:
        from verify.real_evaluator import evaluate_difficulty
        result = evaluate_difficulty(difficulty, n=n, seed=seed, use_test_split=True,
                                     labels=labels)
        # Don't return full per-contract details in API response (too large)
        return {
            "difficulty":    result["difficulty"],
            "labels":        labels,
            "n_contracts":   result["n_contracts"],
            "n_errors":      result.get("n_errors", 0),
            "avg_score":     result["avg_score"],
            "avg_precision": result["avg_precision"],
            "avg_recall":    result["avg_recall"],
            "avg_f1":        result.get("avg_f1", 0.0),
        }
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
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
