"""
rl_tools/collect_llm.py

Runs the LLM as one more "tool" for the tool-selection RL: a single Broad Analysis
call (BA round 1, the exact prompt / model / v2 settings of verify/mode_evaluator.py)
on each of the 69 contracts in datasets/tool_results/curated_tools.json.

Writes datasets/tool_results/llm_ba.json: per contract the raw reply, finish reason,
tokens, API latency in seconds (the tool's run-time cost) and provider. Resumable:
contracts already in the file are skipped. Parallel, one Groq key per worker.

Run:  py rl_tools/collect_llm.py [--workers 10]
Then: py rl_tools/build_dataset.py --src <smartbugs-results clone>   (adds tool "llm_ba")
"""

from __future__ import annotations
import argparse, json, sys, threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from verify import mode_evaluator as ME
from verify.real_evaluator import strip_comments, truncate   # truncate() also strips comments

DATA = ROOT / "datasets" / "tool_results" / "curated_tools.json"
OUT = ROOT / "datasets" / "tool_results" / "llm_ba.json"


def main():
    from rl_offline.common import utf8_console
    from rl_offline.llm import GroqBackend, KeyPool, load_keys, DailyLimitReached, CallFailed, AccessDenied
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    contracts = json.loads(DATA.read_text(encoding="utf-8"))["contracts"]
    done = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {
        "model": ME.MODEL, "temperature": ME.TEMPERATURE, "max_tokens": ME.MAX_TOKENS,
        "reasoning_effort": ME.REASONING_EFFORT, "prompt_hash": ME.config("ba")["prompt_hash"],
        "comments_stripped": True, "contracts": {}}
    if (done["max_tokens"], done["reasoning_effort"], done["prompt_hash"], done.get("comments_stripped")) != \
            (ME.MAX_TOKENS, ME.REASONING_EFFORT, ME.config("ba")["prompt_hash"], True):
        raise SystemExit(f"{OUT} was made with other settings; move it away first.")
    todo = [n for n in contracts if n not in done["contracts"]]
    print(f"{len(done['contracts'])} done, {len(todo)} to go")
    pool = KeyPool(load_keys())
    lock = threading.Lock()
    queue = list(todo)

    def save():
        tmp = OUT.with_suffix(".tmp")
        tmp.write_text(json.dumps(done, indent=1), encoding="utf-8")
        tmp.replace(OUT)

    def worker():
        b = GroqBackend(pool=pool)
        while True:
            with lock:
                if not queue:
                    return
                name = queue.pop(0)
            raw_code = (ROOT / contracts[name]["source"]).read_text(encoding="utf-8", errors="replace")
            code = truncate(strip_comments(raw_code), ME.MAX_CODE_CHARS)
            try:
                out = b.call(ME.BA_SYSTEM, f"Contract to audit:\n\n{code}")
            except (DailyLimitReached, AccessDenied, RuntimeError) as e:
                print(f"{name}: stopping worker ({type(e).__name__}: {str(e)[:120]})", flush=True)
                return
            except CallFailed as e:
                print(f"{name}: failed ({str(e)[:120]}) - rerun later", flush=True)
                continue
            out["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with lock:
                done["contracts"][name] = out
                save()
                print(f"[{len(done['contracts'])}/{len(contracts)}] {name}: {out['finish_reason']} "
                      f"{out['prompt_tokens'] + out['completion_tokens']} tok {out['seconds']}s", flush=True)

    ths = [threading.Thread(target=worker) for _ in range(min(args.workers, len(pool.free), len(todo) or 1))]
    [t.start() for t in ths]
    [t.join() for t in ths]
    print(f"{len(done['contracts'])}/{len(contracts)} contracts have an LLM reply -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
