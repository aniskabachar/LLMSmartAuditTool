"""
verify/real_evaluator.py

Replaces test_system_simple.py accuracy computation.
Real accuracy = precision/recall against SmartBugs ground truth labels.
Uses Groq LLaMA to actually read and analyse Solidity contracts.
"""

from __future__ import annotations
import sys, os, json, random
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from groq import Groq
from auditor.models import Action, DetectedVulnerability, VulnerabilityType, Severity
from auditor.grader import grade

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

SYSTEM_PROMPT = """You are a precise Solidity smart contract security auditor.
Find ONLY vulnerabilities CLEARLY present in the code.
Do NOT guess. Prefer fewer accurate findings over many false positives.
Do NOT default to reentrancy unless you see an external call BEFORE a state update.
Check pragma version before deciding integer overflow risk.

Respond ONLY in this exact JSON format with no extra text:
{
  "vulnerabilities": [
    {
      "type": "reentrancy|integer_overflow|access_control|tx_origin|timestamp_dependence|selfdestruct|uninitialized_storage|unchecked_low_level_calls|bad_randomness|front_running|denial_of_service|other",
      "location": "function name or line description",
      "severity": "low|medium|high|critical",
      "explanation": "exact reason this is a vulnerability"
    }
  ]
}"""

VALID_TYPES = {t.value for t in VulnerabilityType}

HINTS = {
    "easy":   "This contract has ONE main vulnerability. Find only that one.",
    "medium": "This contract has 1-2 vulnerabilities. Find them precisely.",
    "hard":   "Multiple complex vulnerabilities may be present. Be thorough but precise."
}

MODELS = {
    "easy":   "openai/gpt-oss-120b",
    "medium": "openai/gpt-oss-120b",
    "hard":   "openai/gpt-oss-120b",   # 20b doesn't follow JSON format well
}


def truncate(code: str, max_chars: int = 6000) -> str:
    if len(code) <= max_chars:
        return code
    half = max_chars // 2
    return code[:half] + "\n\n...[truncated for length]...\n\n" + code[-half:]


def _parse_llm_response(raw: str) -> dict:
    """
    Extract JSON from LLM response with multiple fallback strategies.
    Handles: markdown fences, plain JSON, JSON embedded in prose, truncated JSON.
    """
    import re as _re

    raw = raw.strip()

    # Strategy 1: strip markdown code fences
    if "```" in raw:
        # grab content between first ``` pair
        parts = raw.split("```")
        for part in parts[1:]:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            try:
                return json.loads(part)
            except Exception:
                pass

    # Strategy 2: direct parse
    try:
        return json.loads(raw)
    except Exception:
        pass

    # Strategy 3: find first { ... } block
    match = _re.search(r'\{.*\}', raw, _re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass

    # Strategy 4: manually extract vulnerability types from prose
    # If model returned a description instead of JSON, parse keywords
    vuln_keywords = {
        "reentrancy": "reentrancy",
        "integer overflow": "integer_overflow",
        "overflow": "integer_overflow",
        "underflow": "integer_overflow",
        "access control": "access_control",
        "tx.origin": "tx_origin",
        "timestamp": "timestamp_dependence",
        "block.timestamp": "timestamp_dependence",
        "selfdestruct": "selfdestruct",
        "suicide": "selfdestruct",
        "unchecked": "unchecked_low_level_calls",
        "low-level call": "unchecked_low_level_calls",
        "randomness": "bad_randomness",
        "front.run": "front_running",
        "front run": "front_running",
        "denial of service": "denial_of_service",
        "delegatecall": "access_control",
    }
    raw_lower = raw.lower()
    found_types = []
    for keyword, vuln_type in vuln_keywords.items():
        if keyword in raw_lower and vuln_type not in found_types:
            found_types.append(vuln_type)

    if found_types:
        return {
            "vulnerabilities": [
                {"type": t, "location": "inferred from prose",
                 "severity": "medium", "explanation": "extracted from model response"}
                for t in found_types
            ]
        }

    # Strategy 5: empty — model found nothing or returned garbage
    return {"vulnerabilities": []}


def _normalize_action(parsed: dict) -> Action:
    """Coerce parsed dict into validated Action, normalising types."""
    vulns = []
    for v in parsed.get("vulnerabilities", []):
        t = v.get("type", "other").lower().strip()
        if t not in VALID_TYPES:
            t = "other"
        s = v.get("severity", "medium").lower().strip()
        if s not in {"low", "medium", "high", "critical"}:
            s = "medium"
        vulns.append(DetectedVulnerability(
            type        = VulnerabilityType(t),
            location    = v.get("location", "unknown")[:200],
            severity    = Severity(s),
            explanation = v.get("explanation", "")[:500],
        ))
    return Action(vulnerabilities=vulns)


def audit_contract(contract_code: str, difficulty: str) -> Action:
    """
    Runs real Groq LLM agent on one contract.
    Returns Action with detected vulnerabilities.
    Includes exponential backoff retry on rate limit (429).
    """
    import time as _time

    max_retries = 4
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model    = MODELS[difficulty],
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",
                     "content": f"{HINTS[difficulty]}\n\nContract to audit:\n\n{truncate(contract_code)}"},
                ],
                temperature = 0.1,
                max_tokens  = 600,   # reduced to stay within TPM limit
            )
            raw    = response.choices[0].message.content
            parsed = _parse_llm_response(raw)
            # Polite inter-call delay to stay within 8000 TPM free tier
            _time.sleep(8)
            return _normalize_action(parsed)

        except json.JSONDecodeError as e:
            print(f"    JSON parse error: {e}")
            _time.sleep(8)
            return Action(vulnerabilities=[])

        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "rate_limit" in err_str:
                # Extract wait time from error message if available
                import re as _re
                m = _re.search(r'try again in (\d+\.?\d*)s', err_str)
                wait = float(m.group(1)) + 2.0 if m else (15.0 * (attempt + 1))
                print(f"    Rate limit — waiting {wait:.0f}s (attempt {attempt+1}/{max_retries})")
                _time.sleep(wait)
                continue
            print(f"    LLM error: {e}")
            _time.sleep(5)
            return Action(vulnerabilities=[])

    print(f"    Max retries exceeded")
    return Action(vulnerabilities=[])


def evaluate_difficulty(
    difficulty: str,
    n: int = 30,
    split_file: str | None = None,
    use_test_split: bool = True,
) -> dict:
    """
    Evaluates real LLM agent on n contracts from tasks/{difficulty}/

    Parameters
    ----------
    difficulty      : "easy" | "medium" | "hard"
    n               : number of contracts to evaluate
    split_file      : path to split JSON (if None, uses all available)
    use_test_split  : if True and split_file given, use test split only

    Returns
    -------
    dict with avg_score, avg_precision, avg_recall, per-contract details
    """
    task_dir  = Path(f"tasks/{difficulty}")
    sol_files = sorted(task_dir.glob("*.sol"))

    if not sol_files:
        raise FileNotFoundError(
            f"No .sol files found in tasks/{difficulty}/\n"
            f"Run:  py scripts/fetch_smartbugs.py  first."
        )

    # Honour train/test split if provided
    if split_file and use_test_split:
        try:
            split_data = json.loads(Path(split_file).read_text())
            test_stems = set(split_data.get("test", []))
            sol_files  = [f for f in sol_files if f.stem in test_stems]
            print(f"  Using test split: {len(sol_files)} contracts")
        except Exception as e:
            print(f"  [WARN] Could not load split {split_file}: {e}. Using all.")

    sample = random.sample(sol_files, min(n, len(sol_files)))
    scores, details = [], []

    for i, sol_path in enumerate(sample):
        gt_path = sol_path.with_suffix(".json")
        if not gt_path.exists():
            print(f"  [{i+1}/{len(sample)}] {sol_path.name} — SKIP (no GT)")
            continue

        ground_truth  = json.loads(gt_path.read_text(encoding="utf-8"))
        contract_code = sol_path.read_text(encoding="utf-8", errors="replace")

        print(f"  [{i+1}/{len(sample)}] {sol_path.name} ... ", end="", flush=True)

        action       = audit_contract(contract_code, difficulty)
        grade_result = grade(action, ground_truth)

        scores.append(grade_result["score"])
        details.append({
            "contract":        sol_path.name,
            "score":           grade_result["score"],
            "precision":       grade_result["precision"],
            "recall":          grade_result["recall"],
            "f1":              grade_result["f1"],
            "true_positives":  grade_result["true_positives"],
            "false_positives": grade_result["false_positives"],
            "missed_bugs":     grade_result["missed_bugs"],
            "found_types":     grade_result["found_types"],
            "expected_types":  grade_result["expected_types"],
        })

        print(f"score={grade_result['score']:.3f}  "
              f"TP={grade_result['true_positives']}  "
              f"FP={grade_result['false_positives']}  "
              f"miss={grade_result['missed_bugs']}")

    if not scores:
        return {"difficulty": difficulty, "error": "no contracts evaluated"}

    return {
        "difficulty":    difficulty,
        "n_contracts":   len(details),
        "avg_score":     round(sum(scores) / len(scores), 4),
        "avg_precision": round(sum(d["precision"] for d in details) / len(details), 4),
        "avg_recall":    round(sum(d["recall"]    for d in details) / len(details), 4),
        "avg_f1":        round(sum(d["f1"]        for d in details) / len(details), 4),
        "details":       details,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--difficulty", default="easy",
                        choices=["easy","medium","hard"])
    parser.add_argument("--n", type=int, default=10)
    args = parser.parse_args()

    print(f"\nReal LLM evaluation: {args.difficulty} tier, {args.n} contracts")
    result = evaluate_difficulty(args.difficulty, n=args.n)
    print(f"\nResult: score={result['avg_score']}  "
          f"P={result['avg_precision']}  R={result['avg_recall']}  "
          f"F1={result['avg_f1']}")
