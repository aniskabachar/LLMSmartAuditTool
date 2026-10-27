"""
verify/build_ground_truth_from_existing.py

Converts the existing evaluation/benchmark/Labeled dataset into
tasks/ ground truth JSON files that the grader can use.

The Labeled/ folder already contains categorised .sol files:
  proc_reentrancy/        -> reentrancy
  proc_arithmetic/        -> integer_overflow
  proc_access_control/    -> access_control (mapped)
  proc_tx_origin/         -> tx_origin
  proc_time_manipulation/ -> timestamp_dependence
  proc_unsafe_suicide/    -> selfdestruct
  proc_unchecked_send/    -> unchecked_low_level_calls
  proc_gasless_send/      -> unchecked_low_level_calls
  proc_bad_randomness/    -> bad_randomness
  proc_TOD/               -> front_running
  proc_unsafe_delegatecall/ -> access_control (delegatecall variant)
  proc_safecontract/      -> (no vuln — clean contracts)
  proc_code3rena/         -> mixed (skip for now)

Also maps traditional_tools_results.csv findings per contract
for multi-label ground truth where available.

Run:
    py verify/build_ground_truth_from_existing.py
"""

from __future__ import annotations
import csv, json, shutil
from pathlib import Path

csv.field_size_limit(10_000_000)

LABELED_DIR = Path("evaluation/benchmark/Labeled")
TOOLS_CSV   = Path("evaluation/LabeledResult/traditional_tools_results.csv")
TASKS_DIR   = Path("tasks")

# Category folder -> (difficulty, vuln_type, severity)
CATEGORY_MAP = {
    "proc_reentrancy":           ("easy",   "reentrancy",                "high"),
    "proc_arithmetic":           ("easy",   "integer_overflow",          "medium"),
    "proc_tx_origin":            ("easy",   "tx_origin",                 "medium"),
    "proc_gasless_send":         ("medium", "unchecked_low_level_calls", "medium"),
    "proc_unchecked_send":       ("medium", "unchecked_low_level_calls", "medium"),
    "proc_time_manipulation":    ("medium", "timestamp_dependence",      "medium"),
    "proc_TOD":                  ("medium", "front_running",             "medium"),
    "proc_bad_randomness":       ("hard",   "bad_randomness",            "high"),
    "proc_unsafe_delegatecall":  ("hard",   "access_control",            "high"),
    "proc_unsafe_suicide":       ("hard",   "selfdestruct",              "high"),
    # safecontract = clean, no vuln (use as negative examples later)
    # code3rena = mixed, skip
}

# Tools findings normalisation
TOOL_VULN_MAP = {
    "Integer_Overflow":        "integer_overflow",
    "Integer_Underflow":       "integer_overflow",
    "Reentrancy":              "reentrancy",
    "Reentrancy_no_eth":       "reentrancy",
    "Delegatecall":            "access_control",
    "Unchecked_Send":          "unchecked_low_level_calls",
    "Gasless_Send":            "unchecked_low_level_calls",
    "Unhandled_Exception":     "unchecked_low_level_calls",
    "Transaction_order_Dependence": "front_running",
    "Time_Manipulation":       "timestamp_dependence",
    "block_timestamp":         "timestamp_dependence",
    "Suicidal":                "selfdestruct",
    "Bad_Randomness":          "bad_randomness",
    "tx_origin":               "tx_origin",
    "Access_Control":          "access_control",
}


def load_tool_findings() -> dict[str, set[str]]:
    """Load per-basename set of confirmed vulnerability types from tool CSV."""
    findings: dict[str, set[str]] = {}
    if not TOOLS_CSV.exists():
        print(f"  [WARN] {TOOLS_CSV} not found — using category-only GT")
        return findings

    with open(TOOLS_CSV, encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            basename = Path(row.get("basename", "")).name
            raw_findings = row.get("findings", "{}")
            # Parse set-like string: {Integer_Overflow, Reentrancy}
            raw_findings = raw_findings.strip("{} ").strip()
            if not raw_findings:
                continue
            types = {t.strip() for t in raw_findings.split(",") if t.strip()}
            normalised = {TOOL_VULN_MAP.get(t, "other") for t in types
                         if t not in ("", "other")}
            if normalised:
                findings.setdefault(basename, set()).update(normalised)

    print(f"  Loaded tool findings for {len(findings)} unique contracts")
    return findings


def build():
    print("Building ground truth from existing evaluation/benchmark/Labeled...\n")

    # Ensure tasks dirs exist
    for d in ["easy", "medium", "hard"]:
        (TASKS_DIR / d).mkdir(parents=True, exist_ok=True)

    tool_findings = load_tool_findings()
    stats = {"easy": 0, "medium": 0, "hard": 0, "skipped": 0}

    for cat_dir in sorted(LABELED_DIR.iterdir()):
        if not cat_dir.is_dir():
            continue
        cat_name = cat_dir.name

        if cat_name not in CATEGORY_MAP:
            print(f"  SKIP {cat_name} (not in category map)")
            stats["skipped"] += 1
            continue

        difficulty, vuln_type, severity = CATEGORY_MAP[cat_name]
        sol_files = sorted(cat_dir.glob("*.sol"))
        print(f"  [{cat_name}] -> {difficulty} ({len(sol_files)} files)")

        for sol_path in sol_files:
            dest_sol  = TASKS_DIR / difficulty / sol_path.name
            dest_json = dest_sol.with_suffix(".json")

            # Copy .sol
            shutil.copy2(sol_path, dest_sol)

            # Build ground truth — start with category label
            vulns = [{
                "type":        vuln_type,
                "severity":    severity,
                "location":    "unknown",
                "description": f"Known {vuln_type} from SmartBugs Labeled ({cat_name})"
            }]

            # Enrich with confirmed tool findings if available
            extra = tool_findings.get(sol_path.name, set())
            for extra_type in extra:
                if extra_type != vuln_type and extra_type != "other":
                    vulns.append({
                        "type":        extra_type,
                        "severity":    "medium",
                        "location":    "unknown",
                        "description": f"Confirmed by static analysis tools"
                    })

            gt = {
                "contract":        sol_path.name,
                "source":          f"SmartBugs Labeled / {cat_name}",
                "category":        cat_name,
                "vulnerabilities": vulns,
            }
            dest_json.write_text(json.dumps(gt, indent=2), encoding="utf-8")
            stats[difficulty] += 1

    print(f"\n{'='*50}")
    print("GROUND TRUTH BUILD COMPLETE")
    print(f"{'='*50}")
    for diff in ["easy", "medium", "hard"]:
        n = len(list((TASKS_DIR / diff).glob("*.sol")))
        print(f"  {diff:<8}: {n} contracts")
    print(f"  skipped : {stats['skipped']} unknown categories")
    total = sum(stats[d] for d in ["easy","medium","hard"])
    print(f"  Total   : {total} contracts with ground truth")


if __name__ == "__main__":
    build()
