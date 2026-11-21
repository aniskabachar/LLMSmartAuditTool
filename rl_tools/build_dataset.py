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
# LLM "tools" from rl_tools/collect_llm.py (each optional): tool name -> reply file.
# llm_ba_high is left out: at reasoning_effort "high" 30 of 69 replies ran out of tokens.
LLM_TOOLS = {
    "llm_ba":      ROOT / "datasets" / "tool_results" / "llm_ba.json",       # BA prompt, effort low
    "llm_ba_med":  ROOT / "datasets" / "tool_results" / "llm_ba_med.json",   # BA prompt, effort medium
    "llm_ba_dasp": ROOT / "datasets" / "tool_results" / "llm_ba_dasp.json",  # BA + DASP definitions
}

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


# ── SolidiFI (optional second source) ─────────────────────────────────────────
# SolidiFI ran each tool only on the bug types that tool claims to detect, so which
# tools have output would reveal the label. Only the 4 bug types on which the same 4
# tools all ran are used, and only those 4 tools (for every contract, both sources).
SHARED_TOOLS = ("mythril", "oyente", "slither", "smartcheck")
SOLIDIFI_TYPES = {"Overflow-Underflow": "integer_overflow", "Re-entrancy": "reentrancy",
                  "Timestamp-Dependency": "timestamp_dependence",
                  "Unhandled-Exceptions": "unchecked_low_level_calls"}
SOLIDIFI_DIR = {"mythril": "Mythril", "oyente": "Oyente", "slither": "Slither", "smartcheck": "Smartcheck"}
SOLIDIFI_SRC_OUT = ROOT / "datasets" / "solidifi"
# Finding names that SolidiFI's (newer) tool versions use but the SmartBugs mapping
# CSV lists under an older name -> the SmartBugs category of that older name.
FINDING_ALIASES = {
    ("mythril", "unprotected ether withdrawal"): "access_control",      # = "Ether send"
    ("mythril", "external call to user-supplied address"): "reentrancy",  # = "Message call to external contract"
    ("mythril", "multiple calls in a single transaction"): "Ignore",      # = "Multiple Calls"
    ("mythril", "write to an arbitrary storage slot"): "access_control",
    ("mythril", "jump to an arbitrary instruction"): "Other",
    ("oyente", "transaction-ordering dependency."): "front_running",
    ("slither", "reentrancy-events"): "reentrancy",
    ("slither", "reentrancy-unlimited-gas"): "reentrancy",
    ("slither", "unchecked-lowlevel"): "unchecked_low_calls",
    ("slither", "unchecked-send"): "unchecked_low_calls",
    ("slither", "pragma"): "Ignore",
    ("slither", "too-many-digits"): "Ignore",
}


def load_mapping(src: Path) -> dict:
    """(tool, finding name lowercased) -> SmartBugs category, from the authors' CSV."""
    import csv
    rows = list(csv.reader((src / "metadata" / "vulnerabilities_mapping.csv").open(encoding="utf-8")))
    hdr, mp = rows[0], {}
    for r in rows[1:]:
        idx = r.index("TRUE") if "TRUE" in r else (r.index("MAYBE") if "MAYBE" in r else -1)
        if idx > 0:
            mp[(r[0].lower(), r[1].strip().lower())] = hdr[idx]
    mp.update(FINDING_ALIASES)
    return mp


def solidifi_findings(tool: str, files: list[Path]) -> list[str]:
    """Raw finding names in one tool's SolidiFI output files for one contract."""
    import re

    def flat(x):
        if isinstance(x, str):
            yield x
        elif isinstance(x, list):
            for y in x:
                yield from flat(y)
    names = []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        if tool == "mythril":
            names += re.findall(r"^==== (.+?) ====", text, re.M)
        elif tool == "smartcheck":
            names += re.findall(r"ruleId: (\S+)", text)
        else:
            try:
                d = json.loads(text)
            except Exception:
                continue
            if tool == "slither":
                names += [det["check"] for det in ((d.get("results") or {}).get("detectors") or [])]
            else:                                            # oyente
                for v in (d.get("vulnerabilities") or {}).values():
                    for m in flat(v):
                        mm = re.search(r"Warning: ([^\n]+)", m)
                        if mm:
                            names.append(mm.group(1).strip())
    return names


def main():
    from rl_offline.common import list_contracts, _features, utf8_console
    import shutil, statistics
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="Local clone of smartbugs-results")
    ap.add_argument("--solidifi", default=None, help="Local clone of SolidiFI-benchmark (adds its contracts)")
    ap.add_argument("--out", default=None, help="Output file (default curated_tools.json, "
                                                "or combined_tools.json with --solidifi)")
    args = ap.parse_args()
    src = Path(args.src)
    global OUT
    if args.out:
        OUT = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out
    elif args.solidifi:
        OUT = ROOT / "datasets" / "tool_results" / "combined_tools.json"
    TOOLS_USED = SHARED_TOOLS if args.solidifi else TOOLS

    results = json.loads((src / "metadata" / "results_curated.json").read_text(encoding="utf-8"))
    truth = {e["name"].removesuffix(".sol"): e
             for e in json.loads((src / "metadata" / "vulnerabilities.json").read_text(encoding="utf-8"))}
    sources = {}
    for part in ("test", "train"):
        for c in list_contracts(part):
            sources.setdefault(c.stem, c)

    llms = {t: json.loads(p.read_text(encoding="utf-8"))["contracts"]
            for t, p in LLM_TOOLS.items() if p.exists()}
    from rl_offline.common import _parse
    contracts = {}
    for name in sorted(results):
        if name not in truth or name not in sources:
            print(f"skip {name}: no ground truth or no source in this repo")
            continue
        labels = sorted({CATEGORY_MAP[v["category"]] for v in truth[name]["vulnerabilities"]})
        tools = {}
        for t in TOOLS_USED:
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
        c = sources[name]
        contracts[name] = {"labels": labels, "source": c.sol.relative_to(ROOT).as_posix(),
                           "origin": "smartbugs", "split_in_repo": c.part, "tier": c.tier,
                           "features": [float(x) for x in _features(c.sol)], "tools": tools}

    if args.solidifi:
        sf = Path(args.solidifi)
        mapping = load_mapping(src)
        # SolidiFI publishes no run times: use each tool's median on the SmartBugs contracts
        median_sec = {t: round(statistics.median(c["tools"][t]["seconds"] for c in contracts.values()), 2)
                      for t in SHARED_TOOLS}
        unmapped, missing = {}, {t: 0 for t in SHARED_TOOLS}
        for folder, label in SOLIDIFI_TYPES.items():
            sols = sorted((sf / "buggy_contracts" / folder).glob("buggy_*.sol"),
                          key=lambda p: int(p.stem.split("_")[1]))
            for sol in sols:
                n = sol.stem.split("_")[1]
                name = f"sf_{label}_{n}"
                dst = SOLIDIFI_SRC_OUT / folder / sol.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(sol, dst)
                tools = {}
                for t in SHARED_TOOLS:
                    rdir = sf / "results" / SOLIDIFI_DIR[t] / "analyzed_buggy_contracts" / folder / "results"
                    files = sorted(rdir.glob(f"buggy_{n}.sol*"))
                    if not files:
                        missing[t] += 1
                    found = set()
                    for raw in solidifi_findings(t, files):
                        cat = mapping.get((t, raw.strip().lower()))
                        if cat is None:
                            unmapped[(t, raw)] = unmapped.get((t, raw), 0) + 1
                        elif cat in CATEGORY_MAP:
                            found.add(CATEGORY_MAP[cat])
                    tools[t] = {"found": sorted(found), "seconds": median_sec[t], "seconds_estimated": True}
                contracts[name] = {"labels": [label], "source": dst.relative_to(ROOT).as_posix(),
                                   "origin": "solidifi", "split_in_repo": None, "tier": None,
                                   "features": [float(x) for x in _features(dst)], "tools": tools}
        print(f"SolidiFI: {sum(c['origin'] == 'solidifi' for c in contracts.values())} contracts; "
              f"no output file: {missing}; median seconds used: {median_sec}")
        if unmapped:
            print("unmapped finding names (ignored):", unmapped)

    # LLM tools: included only when they have a reply for every contract
    for t, llm in list(llms.items()):
        gap = [n for n in contracts if n not in llm]
        if gap:
            print(f"{t}: no reply for {len(gap)} contracts -> left out; run "
                  f"py rl_tools/collect_llm.py --data {OUT.relative_to(ROOT)} --variant "
                  f"{t.removeprefix('llm_')}")
            del llms[t]
    for name, c in contracts.items():
        for t, llm in llms.items():
            call = llm[name]
            action, _ = _parse(call["raw"])
            types = {v.type.value for v in action.vulnerabilities} if action else set()
            c["tools"][t] = {"found": sorted({LLM_TYPE_MAP.get(x, x) for x in types}),
                             "seconds": float(call["seconds"]),
                             "tokens": int(call["prompt_tokens"] + call["completion_tokens"])}
    tool_names = list(TOOLS_USED) + list(llms)

    try:
        commit = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
    except Exception:
        commit = None
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "https://github.com/smartbugs/smartbugs-results (Apache-2.0), Durieux et al. ICSE 2020"
                  + ("; https://github.com/smartbugs/SolidiFI-benchmark, Ghaleb & Pattabiraman ISSTA 2020"
                     if args.solidifi else ""),
        "source_commit": commit,
        "solidifi_commit": (subprocess.run(["git", "-C", args.solidifi, "rev-parse", "HEAD"],
                                           capture_output=True, text=True).stdout.strip()
                            if args.solidifi else None),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tools": tool_names, "llm": ({t: str(LLM_TOOLS[t].relative_to(ROOT)) for t in llms} or None),
        "llm_note": "openai/gpt-oss-120b via Groq, one BA call per contract, comments stripped "
                    "(rl_tools/collect_llm.py --variant ...)",
        "category_map": CATEGORY_MAP,
        "n_contracts": len(contracts), "contracts": contracts}, indent=1), encoding="utf-8")
    print(f"Wrote {len(contracts)} contracts x {len(tool_names)} tools -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
