"""
rl_tools/build_dataset.py

Builds the static-analysis-tool dataset for the tool-selection RL (no LLM calls).

Source: smartbugs-results (https://github.com/smartbugs/smartbugs-results, Apache-2.0),
Durieux et al., "Empirical Review of Automated Analysis Tools on 47,587 Ethereum Smart
Contracts", ICSE 2020. For the 69 contracts of the SmartBugs curated set it holds the
real output of each tool. This script reads, from a local clone of that repo:
  metadata/results_curated.json      tool findings already mapped to DASP categories
                                     (by the authors' own script and mapping CSV)
  metadata/vulnerabilities.json      the curated ground truth (multi-label)
  results/<tool>/curated/<c>/result.json   run time ("duration", seconds)
and writes datasets/tool_results/curated_tools.json with, per contract:
  label types, the 28 ContractAnalyzer features of the source in this repo,
  and per tool the categories it reported and its run time.

Run:
  git clone --filter=blob:none --depth 1 https://github.com/smartbugs/smartbugs-results <dir>
  (cd <dir> && git sparse-checkout set --no-cone "/metadata/*" "/results/*/curated/*/result.json")
  py rl_tools/build_dataset.py --src <dir>
"""

from __future__ import annotations
import argparse, json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "datasets" / "tool_results" / "curated_tools.json"
LLM = ROOT / "datasets" / "tool_results" / "llm_ba.json"   # rl_tools/collect_llm.py (optional)

# LLM finding type (auditor/models.py) -> category set used here (SmartBugs/DASP
# puts tx.origin and unprotected selfdestruct under access control, uninitialised
# storage under "other").
LLM_TYPE_MAP = {"tx_origin": "access_control", "selfdestruct": "access_control",
                "uninitialized_storage": "other"}

# manticore is left out: it has results for only 37 of the 69 contracts.
TOOLS = ("honeybadger", "maian", "mythril", "osiris", "oyente", "securify", "slither", "smartcheck")

# SmartBugs / DASP category -> auditor/grader type
CATEGORY_MAP = {
    "access_control": "access_control",
    "arithmetic": "integer_overflow",
    "denial_service": "denial_of_service", "denial_of_service": "denial_of_service",
    "reentrancy": "reentrancy",
    "unchecked_low_calls": "unchecked_low_level_calls",
    "unchecked_low_level_calls": "unchecked_low_level_calls",
    "bad_randomness": "bad_randomness",
    "front_running": "front_running",
    "time_manipulation": "timestamp_dependence",
    "short_addresses": "other",
    "Other": "other", "other": "other",
}


def main():
    from rl_offline.common import list_contracts, _features, utf8_console
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="Local clone of smartbugs-results")
    args = ap.parse_args()
    src = Path(args.src)

    results = json.loads((src / "metadata" / "results_curated.json").read_text(encoding="utf-8"))
    truth = {e["name"].removesuffix(".sol"): e
             for e in json.loads((src / "metadata" / "vulnerabilities.json").read_text(encoding="utf-8"))}
    sources = {}
    for part in ("test", "train"):
        for c in list_contracts(part):
            sources.setdefault(c.stem, c)

    llm = json.loads(LLM.read_text(encoding="utf-8"))["contracts"] if LLM.exists() else None
    if llm is not None:
        from rl_offline.common import _parse
    tool_names = list(TOOLS) + (["llm_ba"] if llm is not None else [])
    contracts = {}
    for name in sorted(results):
        if name not in truth or name not in sources:
            print(f"skip {name}: no ground truth or no source in this repo")
            continue
        labels = sorted({CATEGORY_MAP[v["category"]] for v in truth[name]["vulnerabilities"]})
        tools = {}
        for t in TOOLS:
            rj = src / "results" / t / "curated" / name / "result.json"
            dur = None
            if rj.exists():
                try:
                    dur = float(json.loads(rj.read_text(encoding="utf-8"))["duration"])
                except Exception:
                    dur = None
            cats = (results[name]["tools"].get(t) or {}).get("categories", {})
            found = sorted({CATEGORY_MAP[k] for k in cats if k in CATEGORY_MAP})
            if dur is None:
                raise SystemExit(f"{name}/{t}: missing run time")
            tools[t] = {"found": found, "seconds": round(dur, 2)}
        if llm is not None:
            if name not in llm:
                raise SystemExit(f"{name}: no LLM reply in {LLM}; rerun rl_tools/collect_llm.py")
            call = llm[name]
            action, _ = _parse(call["raw"])
            types = {v.type.value for v in action.vulnerabilities} if action else set()
            tools["llm_ba"] = {"found": sorted({LLM_TYPE_MAP.get(t, t) for t in types}),
                               "seconds": float(call["seconds"]),
                               "tokens": int(call["prompt_tokens"] + call["completion_tokens"])}
        c = sources[name]
        contracts[name] = {"labels": labels, "source": c.sol.relative_to(ROOT).as_posix(),
                           "split_in_repo": c.part, "tier": c.tier,
                           "features": [float(x) for x in _features(c.sol)], "tools": tools}

    try:
        commit = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
    except Exception:
        commit = None
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "https://github.com/smartbugs/smartbugs-results (Apache-2.0), Durieux et al. ICSE 2020",
        "source_commit": commit,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tools": tool_names, "llm": (None if llm is None else
                 "openai/gpt-oss-120b via Groq, BA round 1, v2 settings (rl_tools/collect_llm.py)"),
        "category_map": CATEGORY_MAP,
        "n_contracts": len(contracts), "contracts": contracts}, indent=1), encoding="utf-8")
    print(f"Wrote {len(contracts)} contracts x {len(tool_names)} tools -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
