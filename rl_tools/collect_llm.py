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

# DASP-10 category definitions (the taxonomy the SmartBugs labels use), written from the
# taxonomy itself, not from individual contracts.
DASP_GUIDE = """Vulnerability categories (use the closest one):
- reentrancy: an external call is made before the contract's state is updated, so the callee can re-enter and repeat an action.
- access_control: a sensitive function lacks or misuses authorization (missing onlyOwner, public initializer or misnamed constructor, tx.origin used for auth, unprotected selfdestruct or ownership change).
- integer_overflow: arithmetic that can overflow or underflow (Solidity < 0.8 without SafeMath).
- unchecked_low_level_calls: the return value of call / send / delegatecall is not checked.
- denial_of_service: an attacker or a failing call can block the contract: unbounded loops over growing arrays, a revert in an external call inside a loop or refund, gas-limit exhaustion, reliance on one party to proceed.
- bad_randomness: randomness derived from block variables (blockhash, block.number, timestamp, difficulty) or from on-chain "secret" values.
- front_running: the outcome depends on transaction order, e.g. a revealed answer or price can be copied by a higher-gas transaction (transaction-order dependence).
- timestamp_dependence: logic depends on block.timestamp / now in a way a miner can manipulate (not for randomness).
- other: e.g. short address attack, uninitialised storage pointer, other flaws not listed above.

"""

VARIANTS = {
    #           system prompt                                                       reasoning_effort
    "ba":      (lambda: ME.BA_SYSTEM,                                                "low"),
    "ba_med":  (lambda: ME.BA_SYSTEM,                                                "medium"),
    "ba_high": (lambda: ME.BA_SYSTEM,                                                "high"),
    "ba_dasp": (lambda: ME.BA_SYSTEM.replace("Respond ONLY", DASP_GUIDE + "Respond ONLY", 1), "low"),
}


def main():
    from rl_offline.common import utf8_console
    from rl_offline.llm import GroqBackend, KeyPool, load_keys, DailyLimitReached, CallFailed, AccessDenied
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--variant", choices=list(VARIANTS), default="ba")
    ap.add_argument("--data", default=str(DATA.relative_to(ROOT)),
                    help="Dataset whose contracts get an LLM reply (replies already present are kept)")
    args = ap.parse_args()
    system = VARIANTS[args.variant][0]()
    ME.REASONING_EFFORT = VARIANTS[args.variant][1]          # read by rl_offline.llm at call time
    OUT = ROOT / "datasets" / "tool_results" / (
        "llm_ba.json" if args.variant == "ba" else f"llm_{args.variant}.json")
    import hashlib
    sys_hash = hashlib.sha256(system.encode()).hexdigest()[:16]

    data = Path(args.data) if Path(args.data).is_absolute() else ROOT / args.data
    contracts = json.loads(data.read_text(encoding="utf-8"))["contracts"]
    done = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {
        "model": ME.MODEL, "temperature": ME.TEMPERATURE, "max_tokens": ME.MAX_TOKENS,
        "reasoning_effort": ME.REASONING_EFFORT, "prompt_hash": ME.config("ba")["prompt_hash"],
        "comments_stripped": True, "variant": args.variant, "system_hash": sys_hash, "contracts": {}}
    if (done["max_tokens"], done["reasoning_effort"], done["prompt_hash"], done.get("comments_stripped"),
            done.get("system_hash", sys_hash)) != \
            (ME.MAX_TOKENS, ME.REASONING_EFFORT, ME.config("ba")["prompt_hash"], True, sys_hash):
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
                out = b.call(system, f"Contract to audit:\n\n{code}")
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
