"""
rl_tools/finish_pipeline.py

Called after each daily collection run (run_ta_collection.bat). Does each remaining step
exactly once, as soon as its data is complete; does nothing otherwise.

  A. TA mode complete (datasets/tool_results/llm_ta.json covers the 269 contracts):
     build datasets/tool_results/combined_ta_tools.json (269 contracts, + llm_ta1, llm_ta3)
     and run PPO on it with token-aware cost (--cost both).
  B. LLM calls complete for the 179 real SmartBugs contracts:
     rebuild datasets/tool_results/real_tools.json (slither + 3 LLM calls) and run PPO on it.

Markers in verify/results/tools/_pipeline/ record finished steps (delete one to redo it).
Each PPO run is resumable through rl_tools/run.py's own checkpoints.

Run:  py rl_tools/finish_pipeline.py [--dry-run]
"""

from __future__ import annotations
import argparse, json, subprocess, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TR = ROOT / "datasets" / "tool_results"
MARK = ROOT / "verify" / "results" / "tools" / "_pipeline"
SB_META = "datasets/smartbugs_meta"                                   # mapping CSV (Apache-2.0 copy)


def py(*args) -> int:
    cmd = [sys.executable, "-u", *args]
    print(f"[finish_pipeline] $ {' '.join(args)}", flush=True)
    return subprocess.run(cmd, cwd=ROOT).returncode


def ta_complete() -> bool:
    f = TR / "llm_ta.json"
    if not f.exists():
        return False
    ta = json.loads(f.read_text(encoding="utf-8"))
    names = json.loads((TR / "combined_tools.json").read_text(encoding="utf-8"))["contracts"]
    return all(all(k in ta["contracts"].get(n, {}).get("calls", {}) for k in ta["keys"]) for n in names)


def real_llm_complete() -> bool:
    f = TR / "real_tools.json"
    if not f.exists():
        return False
    names = json.loads(f.read_text(encoding="utf-8"))["contracts"]
    for v in ("llm_ba", "llm_ba_med", "llm_ba_dasp"):
        p = TR / f"{v}.json"
        if not p.exists() or any(n not in json.loads(p.read_text(encoding="utf-8"))["contracts"] for n in names):
            return False
    return True


def add_ta_tools(src: Path, dst: Path):
    """combined_tools.json + the two TA tools (same logic as build_dataset.py's TA step)."""
    from rl_offline.common import trajectory
    from rl_tools.build_dataset import LLM_TYPE_MAP
    d = json.loads(src.read_text(encoding="utf-8"))
    ta = json.loads((TR / "llm_ta.json").read_text(encoding="utf-8"))
    rounds = ta["rounds"]
    for name, c in d["contracts"].items():
        rec = ta["contracts"][name]
        traj = trajectory(rec, "TA", max_rounds=rounds)
        for tname, k in (("llm_ta1", 1), (f"llm_ta{rounds}", rounds)):
            r = traj[k - 1]
            calls = [v for key, v in rec["calls"].items()
                     if key.startswith("ta_r1_b") or (not key.startswith("ta_r1") and int(key[4:]) <= k)]
            c["tools"][tname] = {
                "found": sorted({LLM_TYPE_MAP.get(v.type.value, v.type.value) for v in r.action.vulnerabilities}),
                "seconds": round(sum(float(v["seconds"]) for v in calls), 2), "tokens": int(r.tokens)}
    d["tools"] = d["tools"] + ["llm_ta1", f"llm_ta{rounds}"]
    d["built_at"] = datetime.now().isoformat(timespec="seconds")
    d["ta_note"] = "llm_ta1 / llm_ta3: base project TA mode (rl_tools/collect_ta.py), round 1 and n=3"
    dst.write_text(json.dumps(d, indent=1), encoding="utf-8")


def main():
    from rl_offline.common import utf8_console
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    MARK.mkdir(parents=True, exist_ok=True)

    a_done, b_done = (MARK / "A_ta_ppo.done").exists(), (MARK / "B_real_ppo.done").exists()
    ta_ok, real_ok = ta_complete(), real_llm_complete()
    print(f"[finish_pipeline] A (TA as tools + PPO): data {'complete' if ta_ok else 'incomplete'}, "
          f"{'done' if a_done else 'not done'} | B (real-only PPO): data "
          f"{'complete' if real_ok else 'incomplete'}, {'done' if b_done else 'not done'}")
    if args.dry_run:
        return

    if ta_ok and not a_done:
        add_ta_tools(TR / "combined_tools.json", TR / "combined_ta_tools.json")
        if py("rl_tools/run.py", "--data", "datasets/tool_results/combined_ta_tools.json",
              "--cost", "both", "--steps", "40000") == 0:
            (MARK / "A_ta_ppo.done").write_text(datetime.now().isoformat(), encoding="utf-8")

    if real_ok and not b_done:
        if py("rl_tools/build_real_dataset.py", "--src", SB_META) == 0 and \
                py("rl_tools/run.py", "--data", "datasets/tool_results/real_tools.json", "--steps", "40000") == 0:
            (MARK / "B_real_ppo.done").write_text(datetime.now().isoformat(), encoding="utf-8")


if __name__ == "__main__":
    main()
