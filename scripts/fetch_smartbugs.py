"""
scripts/fetch_smartbugs.py

Downloads real Solidity contracts from SmartBugs Curated GitHub repo
and creates tasks/easy / tasks/medium / tasks/hard with:
  - <name>.sol      — raw Solidity source
  - <name>.json     — ground truth vulnerability labels

Vulnerability → Difficulty mapping:
  easy   : reentrancy (single, classic), arithmetic (simple overflow)
  medium : access_control, unchecked_low_level_calls, time_manipulation
  hard   : bad_randomness, front_running, denial_of_service, reentrancy (complex)

Run:
    py scripts/fetch_smartbugs.py
"""

from __future__ import annotations
import json, time, re
from pathlib import Path
import requests

BASE_API  = "https://api.github.com/repos/smartbugs/smartbugs-curated/contents/dataset"
BASE_RAW  = "https://raw.githubusercontent.com/smartbugs/smartbugs-curated/master/dataset"
TASKS_DIR = Path("tasks")

# Category → (difficulty, vuln_type_label, severity)
CATEGORY_MAP = {
    "reentrancy":              ("easy",   "reentrancy",              "high"),
    "arithmetic":              ("easy",   "integer_overflow",        "medium"),
    "access_control":          ("medium", "access_control",          "high"),
    "unchecked_low_level_calls":("medium","unchecked_low_level_calls","medium"),
    "time_manipulation":       ("medium", "timestamp_dependence",    "medium"),
    "bad_randomness":          ("hard",   "bad_randomness",          "high"),
    "front_running":           ("hard",   "front_running",           "medium"),
    "denial_of_service":       ("hard",   "denial_of_service",       "medium"),
    "short_addresses":         ("hard",   "other",                   "low"),
    "other":                   ("hard",   "other",                   "low"),
}

# Target: ~50 per difficulty tier
TARGET = {"easy": 55, "medium": 55, "hard": 55}
counts = {"easy": 0, "medium": 0, "hard": 0}

HEADERS = {"User-Agent": "LLMSmartAudit-Evaluator/1.0"}

def github_ls(path: str) -> list[dict]:
    """List directory contents from GitHub API."""
    url = f"https://api.github.com/repos/smartbugs/smartbugs-curated/contents/{path}"
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 403:
                print(f"  Rate limited — sleeping 30s")
                time.sleep(30)
        except Exception as e:
            print(f"  Retry {attempt+1}: {e}")
            time.sleep(2)
    return []

def fetch_raw(cat: str, filename: str) -> str | None:
    """Fetch raw .sol file content."""
    url = f"{BASE_RAW}/{cat}/{filename}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        print(f"    fetch error: {e}")
    return None

def make_ground_truth(vuln_type: str, severity: str, category: str,
                      sol_name: str) -> dict:
    """
    Create a ground-truth JSON matching the grader schema.
    SmartBugs contracts are labelled with one known vulnerability type.
    """
    return {
        "contract": sol_name,
        "source":   "SmartBugs Curated",
        "category": category,
        "vulnerabilities": [
            {
                "type":        vuln_type,
                "severity":    severity,
                "location":    "unknown",
                "description": f"Known {vuln_type} vulnerability from SmartBugs Curated dataset"
            }
        ]
    }

def slug(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", name)

def run():
    print("Fetching SmartBugs Curated contracts from GitHub...")
    print(f"Target: {TARGET} contracts per difficulty tier\n")

    for category, (difficulty, vuln_type, severity) in CATEGORY_MAP.items():
        if counts[difficulty] >= TARGET[difficulty]:
            continue

        print(f"\n[{category}] -> {difficulty} (have {counts[difficulty]}/{TARGET[difficulty]})")
        files = github_ls(f"dataset/{category}")
        sol_files = [f for f in files if isinstance(f, dict) and f.get("name", "").endswith(".sol")]
        print(f"  Found {len(sol_files)} .sol files")

        for f_info in sol_files:
            if counts[difficulty] >= TARGET[difficulty]:
                break

            fname = f_info["name"]
            print(f"  Downloading {fname}...", end=" ")
            code = fetch_raw(category, fname)
            if not code:
                print("SKIP (fetch failed)")
                continue

            # Write .sol
            base  = Path(fname).stem
            safe  = slug(base)
            out_sol  = TASKS_DIR / difficulty / f"{safe}.sol"
            out_json = TASKS_DIR / difficulty / f"{safe}.json"

            out_sol.write_text(code, encoding="utf-8")

            # Write ground truth
            gt = make_ground_truth(vuln_type, severity, category, fname)
            out_json.write_text(json.dumps(gt, indent=2), encoding="utf-8")

            counts[difficulty] += 1
            print(f"OK ({counts[difficulty]}/{TARGET[difficulty]})")
            time.sleep(0.15)  # gentle rate limiting

    print("\n" + "=" * 50)
    print("DOWNLOAD SUMMARY")
    print("=" * 50)
    for diff in ["easy", "medium", "hard"]:
        n_sol  = len(list((TASKS_DIR / diff).glob("*.sol")))
        n_json = len(list((TASKS_DIR / diff).glob("*.json")))
        print(f"  {diff:<8}: {n_sol} contracts, {n_json} ground truth files")

    total = sum(len(list((TASKS_DIR / d).glob("*.sol"))) for d in ["easy","medium","hard"])
    print(f"\n  Total: {total} contracts across 3 difficulty tiers")

if __name__ == "__main__":
    run()
