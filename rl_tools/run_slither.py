"""
rl_tools/run_slither.py

Runs Slither locally on the real labelled contracts in tasks/ (SmartBugs curated), so the
real-bug part of the tool-selection dataset can grow beyond the 69 contracts that have
published tool results. Uses the separate venv .venv-slither (slither-analyzer + solc-select);
the solc version per contract comes from its pragma (rl_tools/solc_version.py).

Writes datasets/tool_results/slither_local.json: per contract the Slither detector names it
reported, the solc version, the measured run time and whether compilation succeeded.
Resumable (contracts already in the file are skipped).

Setup (once):
  py -m venv .venv-slither
  .venv-slither/Scripts/python -m pip install slither-analyzer solc-select
  .venv-slither/Scripts/solc-select install 0.4.26 0.5.17 ...   (versions the pragmas need)
Run:
  py rl_tools/run_slither.py [--limit N]
"""

from __future__ import annotations
import argparse, json, os, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VENV = ROOT / ".venv-slither" / "Scripts"
OUT = ROOT / "datasets" / "tool_results" / "slither_local.json"


def run_one(sol: Path, version: str) -> dict:
    env = dict(os.environ, SOLC_VERSION=version, PATH=str(VENV) + os.pathsep + os.environ.get("PATH", ""))
    t0 = time.time()
    p = subprocess.run([str(VENV / "slither.exe"), str(sol), "--json", "-", "--disable-color"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env, cwd=str(sol.parent), timeout=600)
    secs = round(time.time() - t0, 2)
    try:
        d = json.loads(p.stdout)
    except Exception:
        return {"ok": False, "seconds": secs, "solc": version, "checks": [],
                "error": (p.stderr or p.stdout)[-400:]}
    dets = ((d.get("results") or {}).get("detectors") or [])
    return {"ok": bool(d.get("success")), "seconds": secs, "solc": version,
            "checks": [x["check"] for x in dets], "error": (d.get("error") or "")[:400]}


def main():
    from rl_offline.common import list_contracts, utf8_console
    from rl_tools.solc_version import pick_solc
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--data", default=None,
                    help="run on the contracts of this tool dataset (name -> source) instead of tasks/")
    args = ap.parse_args()
    if not (VENV / "slither.exe").exists():
        raise SystemExit("Slither venv missing; see the setup lines at the top of this file.")

    real = {}
    if args.data:
        from types import SimpleNamespace
        d = json.loads((ROOT / args.data).read_text(encoding="utf-8"))
        for n, c in d["contracts"].items():
            sol = ROOT / c["source"]
            real[n] = SimpleNamespace(sol=sol, code=lambda s=sol: s.read_text(encoding="utf-8", errors="replace"))
    else:
        for part in ("test", "train"):
            for c in list_contracts(part):
                if c.origin == "tasks":
                    real.setdefault(c.stem, c)
    names = sorted(real)[: args.limit] if args.limit else sorted(real)
    done = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"slither": None, "contracts": {}}
    done["slither"] = subprocess.run([str(VENV / "slither.exe"), "--version"], capture_output=True,
                                     text=True).stdout.strip()
    todo = [n for n in names if n not in done["contracts"]]
    print(f"Slither {done['slither']}: {len(names) - len(todo)}/{len(names)} done, {len(todo)} to go")
    for i, n in enumerate(todo, 1):
        c = real[n]
        r = run_one(c.sol, pick_solc(c.code()))
        r["source"] = c.sol.relative_to(ROOT).as_posix()
        done["contracts"][n] = r
        tmp = OUT.with_suffix(".tmp")
        tmp.write_text(json.dumps(done, indent=1), encoding="utf-8")
        tmp.replace(OUT)
        print(f"[{i}/{len(todo)}] {n}: {'ok' if r['ok'] else 'FAILED'} solc {r['solc']} "
              f"{len(r['checks'])} findings {r['seconds']}s" + ("" if r["ok"] else f"  {r['error'][-120:]!r}"),
              flush=True)
    ok = sum(v["ok"] for v in done["contracts"].values())
    print(f"done: {ok}/{len(done['contracts'])} compiled and analysed -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
