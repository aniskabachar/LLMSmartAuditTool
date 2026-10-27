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

The curated category label goes in `vulnerabilities` (what the grader
scores by default). Types reported by the tools in
traditional_tools_results.csv go in `tool_findings`: they are unverified
and only scored with labels="all". Tool results are matched by category
folder AND file name, since the same file name (e.g. BitCash.sol) occurs
in several folders with different code.

Run:
    py verify/build_ground_truth_from_existing.py
"""

from __future__ import annotations
import csv, json, shutil
from pathlib import Path

csv.field_size_limit(10_000_000)

ROOT        = Path(__file__).resolve().parent.parent
LABELED_DIR = ROOT / "evaluation" / "benchmark" / "Labeled"
TOOLS_CSV   = ROOT / "evaluation" / "LabeledResult" / "traditional_tools_results.csv"
TASKS_DIR   = ROOT / "tasks"

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


ToolIndex = dict[tuple[str, str], dict[str, set[str]]]


def load_tool_findings() -> ToolIndex:
    """
    Load tool findings from the CSV, keyed by (category folder, file name).
    Value maps normalised vulnerability type -> set of tool ids reporting it.
    """
    findings: ToolIndex = {}
    if not TOOLS_CSV.exists():
        print(f"  [WARN] {TOOLS_CSV} not found — no tool findings")
        return findings

    with open(TOOLS_CSV, encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            path     = Path(row.get("filename", ""))
            key      = (path.parent.name, path.name)
            raw_findings = row.get("findings", "{}")
            # Parse set-like string: {Integer_Overflow, Reentrancy}
            raw_findings = raw_findings.strip("{} ").strip()
            if not raw_findings:
                continue
            for t in (t.strip() for t in raw_findings.split(",")):
                vtype = TOOL_VULN_MAP.get(t, "other")
                if t and vtype != "other":
                    findings.setdefault(key, {}).setdefault(vtype, set()).add(row["toolid"])

    print(f"  Loaded tool findings for {len(findings)} contracts")
    return findings


def tool_findings_for(category: str, filename: str, curated_type: str,
                      index: ToolIndex) -> list[dict]:
    """Tool-reported types for one Labeled contract, excluding its curated type."""
    folder = category.removeprefix("proc_")   # proc_TOD -> TOD (CSV folder name)
    return [
        {
            "type":        vtype,
            "severity":    "medium",
            "location":    "unknown",
            "description": "Reported by static analysis tools (unverified)",
            "tools":       sorted(tools),
        }
        for vtype, tools in sorted(index.get((folder, filename), {}).items())
        if vtype != curated_type
    ]


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

            # Curated ground truth = category label only
            vulns = [{
                "type":        vuln_type,
                "severity":    severity,
                "location":    "unknown",
                "description": f"Known {vuln_type} from SmartBugs Labeled ({cat_name})"
            }]

            gt = {
                "contract":        sol_path.name,
                "source":          f"SmartBugs Labeled / {cat_name}",
                "category":        cat_name,
                "vulnerabilities": vulns,
                "tool_findings":   tool_findings_for(cat_name, sol_path.name,
                                                     vuln_type, tool_findings),
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
