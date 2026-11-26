"""
rl_tools/collect_ta.py

Runs the base project's TA (Targeted Analysis) mode on every contract of a tool
dataset, so it can be compared with the tool-selection RL on the same contracts.

TA mode exactly as in verify/mode_evaluator.py / rl_offline: round 1 = 6 calls, one per
batch of detector scenarios from CompanyConfig/SmartContractTA/PhaseConfig.json (union of
findings); rounds 2..3 = consensus review calls on the previous round's findings
(the base system's fixed n = 3). Same model and settings as the rest (gpt-oss-120b,
max_tokens 4000, reasoning "low"), code passed through truncate() (comments stripped,
SolidiFI bug names neutralised).

Writes datasets/tool_results/llm_ta.json: per contract every call (raw reply, tokens,
latency, finish reason). Resumable call by call; stops cleanly when every Groq key has hit
its daily limit, so it can simply be rerun (run_ta_collection.bat, daily task).

Run:  py rl_tools/collect_ta.py [--data datasets/tool_results/combined_tools.json] [--workers N]
                                [--max-minutes 100]
      py rl_tools/collect_ta.py --status
"""

from __future__ import annotations
import argparse, json, sys, threading, time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from verify import mode_evaluator as ME
from verify.real_evaluator import truncate

OUT = ROOT / "datasets" / "tool_results" / "llm_ta.json"
ROUNDS = ME.FIXED_ROUNDS                      # 3, the base system's fixed consensus cap
KEYS = [f"ta_r1_b{i}" for i in range(len(ME.TA_BATCH_TEXTS))] + [f"ta_r{k}" for k in range(2, ROUNDS + 1)]


def main():
    from rl_offline.common import utf8_console
    from rl_offline.collect import prompt_for
    from rl_offline.llm import GroqBackend, KeyPool, load_keys, DailyLimitReached, CallFailed, AccessDenied
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="datasets/tool_results/combined_tools.json")
    ap.add_argument("--workers", type=int, default=None, help="default: one per API key")
    ap.add_argument("--max-minutes", type=float, default=None,
                    help="stop taking new contracts after this long (scheduled runs: the next run "
                         "starts again with every key instead of a few slow leftover workers)")
    ap.add_argument("--limit", type=int, default=None, help="only the first N contracts (pilot)")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    data = Path(args.data) if Path(args.data).is_absolute() else ROOT / args.data
    contracts = json.loads(data.read_text(encoding="utf-8"))["contracts"]
    names = list(contracts)[: args.limit] if args.limit else list(contracts)
    settings = {"model": ME.MODEL, "temperature": ME.TEMPERATURE, "max_tokens": ME.MAX_TOKENS,
                "reasoning_effort": ME.REASONING_EFFORT, "prompt_hash": ME.config("ta")["prompt_hash"],
                "comments_stripped": True, "rounds": ROUNDS, "keys": KEYS}
    done = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {**settings, "contracts": {}}
    if any(done.get(k) != v for k, v in settings.items()):
        raise SystemExit(f"{OUT} was made with other settings; move it away first.")

    def complete(n):
        return all(k in done["contracts"].get(n, {}).get("calls", {}) for k in KEYS)
    n_done = sum(complete(n) for n in names)
    toks = [sum(c["prompt_tokens"] + c["completion_tokens"] for c in done["contracts"][n]["calls"].values())
            for n in names if complete(n)]
    per = sum(toks) / len(toks) if toks else 15000
    print(f"TA mode: {n_done}/{len(names)} contracts complete; "
          f"~{per:,.0f} tokens per contract; ~{(len(names) - n_done) * per / 1e6:.2f}M tokens to go")
    if args.status:
        return

    pool = KeyPool(load_keys())
    lock = threading.Lock()
    queue = [n for n in names if not complete(n)]

    def save():
        tmp = OUT.with_suffix(".tmp")
        tmp.write_text(json.dumps(done, indent=1), encoding="utf-8")
        tmp.replace(OUT)

    deadline = time.time() + args.max_minutes * 60 if args.max_minutes else None

    def worker():
        b = GroqBackend(pool=pool)
        while True:
            with lock:
                if not queue:
                    return
                if deadline and time.time() > deadline:   # next scheduled run restarts with every key
                    return
                name = queue.pop(0)
                rec = done["contracts"].setdefault(name, {"n_ta_batches": len(ME.TA_BATCH_TEXTS), "calls": {}})
            code = truncate((ROOT / contracts[name]["source"]).read_text(encoding="utf-8", errors="replace"),
                            ME.MAX_CODE_CHARS)
            try:
                for key in KEYS:                       # review rounds come after round 1
                    if key in rec["calls"]:
                        continue
                    system, user = prompt_for(key, code, rec)
                    out = b.call(system, user)
                    out["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    with lock:
                        rec["calls"][key] = out
                        save()
            except (DailyLimitReached, AccessDenied, RuntimeError) as e:
                print(f"{name}: stopping worker ({type(e).__name__}: {str(e)[:120]})", flush=True)
                return
            except CallFailed as e:
                print(f"{name}: incomplete ({str(e)[:120]}) - retried on the next run", flush=True)
                continue
            with lock:
                n_ok = sum(complete(n) for n in names)
                t = sum(c["prompt_tokens"] + c["completion_tokens"] for c in rec["calls"].values())
            print(f"[{n_ok}/{len(names)}] {name}: {t:,} tokens", flush=True)

    n_workers = args.workers or len(pool.free)                # default: one worker per key
    ths = [threading.Thread(target=worker) for _ in range(min(n_workers, len(pool.free), len(queue) or 1))]
    [t.start() for t in ths]
    [t.join() for t in ths]
    print(f"TA mode: {sum(complete(n) for n in names)}/{len(names)} contracts complete -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
