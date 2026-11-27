"""
rl_tools/audit.py

End-to-end smart-contract audit with the trained RL coordinator.

Give it a .sol file. The coordinator (a PPO policy) looks at the contract's features,
picks one analyser at a time, runs it live, looks at what it found, and decides what to
run next or when to STOP. The report is the union of the findings of the analysers it ran.

Analysers it can call (the same ones it was trained on, datasets/tool_results/agents_live_tools.json):
  slither            Slither 0.11.6, run locally (.venv-slither)
  llm_ba             generalist LLM auditor (base project's BA prompt, low reasoning)
  llm_ba_med         same prompt, medium reasoning
  llm_ba_dasp        same prompt + DASP category guide
  spec_0 .. spec_5   the base project's six TA specialist agents (targeted scenario batches)
  llm_qwen_ba, llm_qwen_think, llm_qwen_dasp   Qwen 3.8 27B agents (second model family;
                     only if the coordinator was trained with them)
LLM agents: openai/gpt-oss-120b on Groq (keys from .env, rotated as in the collectors).

Train the coordinator once (all 269 labelled contracts; no API calls):
  py rl_tools/audit.py --train [--data datasets/tool_results/agents_live_qwen_tools.json]
Audit a contract:
  py rl_tools/audit.py path/to/Contract.sol [--max-tools N]
Writes verify/results/audits/<name>-<time>.{md,json} and prints the report.
"""

from __future__ import annotations
import argparse, json, sys, time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

DATA = ROOT / "datasets" / "tool_results" / "agents_live_tools.json"
MODEL_DIR = ROOT / "models" / "coordinator"
OUT_DIR = ROOT / "verify" / "results" / "audits"
STEPS, LAM, COST, SEED = 40_000, 0.02, "both", 0


# ── training ──────────────────────────────────────────────────────────────────

def train(data: Path = DATA, model_dir: Path | None = None, exclude: set[str] = frozenset()):
    """exclude: contract names left out of training (e.g. those shared with a test set)."""
    import torch
    from stable_baselines3 import PPO
    from rl_tools.run import COMPACT, TYPES, ToolEnv, load
    MODEL_DIR = model_dir or globals()["MODEL_DIR"]
    torch.set_num_threads(1)
    meta_d, tools, items = load(data, COST)
    items = [it for it in items if it["name"] not in exclude]
    X = np.stack([it["x"] for it in items])
    mu, sd = X.mean(0), X.std(0) + 1e-6
    env = ToolEnv(items, len(tools), LAM, mu, sd, adaptive=True, seed=SEED, feat_idx=COMPACT)
    model = PPO("MlpPolicy", env, seed=SEED, verbose=0, n_steps=512, batch_size=128,
                ent_coef=0.02, gamma=1.0, policy_kwargs={"net_arch": [64, 64]})
    t0 = time.time()
    model.learn(STEPS)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model.save(MODEL_DIR / "model.zip")
    (MODEL_DIR / "meta.json").write_text(json.dumps({
        "tools": tools, "types": list(TYPES), "feat_idx": list(COMPACT),
        "mu": mu[list(COMPACT)].tolist(), "sd": sd[list(COMPACT)].tolist(),
        "lam": LAM, "cost": COST, "steps": STEPS, "seed": SEED, "policy": "MlpPolicy 64x64",
        "trained_on": str(data.relative_to(ROOT)), "n_contracts": len(items),
        "excluded": sorted(exclude),
        "data_built_at": meta_d.get("built_at"),
        "trained_at": datetime.now().isoformat(timespec="seconds")}, indent=1), encoding="utf-8")
    print(f"Trained on {len(items)} contracts x {len(tools)} analysers in {time.time() - t0:.0f}s "
          f"-> {MODEL_DIR.relative_to(ROOT)}")


# ── live analysers ────────────────────────────────────────────────────────────

class Analysers:
    def __init__(self, sol: Path):
        from verify import mode_evaluator as ME
        from verify.real_evaluator import truncate
        self.ME, self.sol = ME, sol
        self.raw_code = sol.read_text(encoding="utf-8", errors="replace")
        self.code = truncate(self.raw_code, ME.MAX_CODE_CHARS)   # strips comments too
        self._backends = {}

    def backend(self, model: str):
        """One backend per model (gpt-oss agents may fall back to other providers; Qwen only on Groq)."""
        if model not in self._backends:
            from rl_offline.llm import GroqBackend, KeyPool, load_keys
            keys = load_keys()
            if model != "openai/gpt-oss-120b":
                keys = [(p, k) for p, k in keys if p == "groq"]
            self._backends[model] = GroqBackend(pool=KeyPool(keys))
        return self._backends[model]

    def run(self, tool: str) -> dict:
        if tool == "slither":
            return self._slither()
        import os
        from rl_tools.collect_llm import use_variant
        from rl_offline.collect import prompt_for
        if tool.startswith("spec_"):                 # base TA specialists: gpt-oss, low reasoning
            model = "openai/gpt-oss-120b"
            self.ME.REASONING_EFFORT, self.ME.MODEL = "low", model
            os.environ["GROQ_MODEL"] = model
            system, user = prompt_for(f"ta_r1_b{tool.split('_')[1]}", self.code, {})
        else:                                        # sets model + reasoning, read at call time
            system, model = use_variant(tool.removeprefix("llm_"))
            user = f"Contract to audit:\n\n{self.code}"
        out = self.backend(model).call(system, user)
        return {"categories": self._categories(out["raw"]), "details": self._details(out["raw"]),
                "seconds": float(out["seconds"]),
                "tokens": int(out["prompt_tokens"] + out["completion_tokens"])}

    @staticmethod
    def _categories(raw: str) -> list[str]:
        from rl_offline.common import _parse
        from rl_tools.build_dataset import LLM_TYPE_MAP
        a, _ = _parse(raw)
        types = {v.type.value for v in a.vulnerabilities} if a else set()
        return sorted({LLM_TYPE_MAP.get(t, t) for t in types})

    @staticmethod
    def _details(raw: str) -> list[dict]:
        from rl_offline.common import _parse
        a, _ = _parse(raw)
        return [{"type": v.type.value, "location": v.location, "severity": v.severity.value,
                 "explanation": v.explanation} for v in (a.vulnerabilities if a else [])]

    def _slither(self) -> dict:
        from rl_tools.build_dataset import CATEGORY_MAP, load_mapping
        from rl_tools.run_slither import run_one
        from rl_tools.solc_version import pick_solc
        r = run_one(self.sol.resolve(), pick_solc(self.raw_code))
        mapping = load_mapping(ROOT / "datasets" / "smartbugs_meta")
        cats = {mapping.get(("slither", c.strip().lower())) for c in r["checks"]}
        return {"categories": sorted({CATEGORY_MAP[c] for c in cats if c in CATEGORY_MAP}),
                "details": [{"type": "slither", "location": "", "severity": "", "explanation": c}
                            for c in sorted(set(r["checks"]))],
                "seconds": float(r["seconds"]), "tokens": 0, "compiled": bool(r["ok"]),
                "solc": r["solc"]}


# ── audit ─────────────────────────────────────────────────────────────────────

def coordinate(meta: dict, model, feats, run, max_tools: int | None = None, say=lambda *a, **k: None):
    """The coordinator's loop: observation exactly as ToolEnv.obs, argmax action, no reruns.
    run(tool) -> {"categories", "seconds", "tokens", ...}: live analysers (audit) or replies
    collected earlier for the same request (rl_tools/paper_eval.py)."""
    import torch
    tools, types, fidx = meta["tools"], meta["types"], meta["feat_idx"]
    mu, sd, T = np.asarray(meta["mu"]), np.asarray(meta["sd"]), len(meta["tools"])
    used, found, cost, steps = [], set(), 0.0, []
    for _ in range(min(T, max_tools or T)):
        x = np.clip((feats[fidx] - mu) / sd, -10, 10)
        u = np.zeros(T, np.float32)
        u[used] = 1.0
        f = np.asarray([float(t in found) for t in types], np.float32)
        obs = np.concatenate([x, u, f, [min(cost / 10.0, 1.0)]]).astype(np.float32)
        with torch.no_grad():
            o, _ = model.policy.obs_to_tensor(obs)
            probs = model.policy.get_distribution(o).distribution.probs[0].cpu().numpy()
        probs[used] = -1.0                                      # never rerun an analyser
        a = int(np.argmax(probs))
        if a == T:
            say(f"  coordinator: STOP (p={probs[T]:.2f})")
            break
        tool = tools[a]
        say(f"  coordinator: run {tool} (p={probs[a]:.2f}) ...", end="", flush=True)
        try:
            r = run(tool)
        except Exception as e:                               # e.g. every Groq key at its limit
            say(f" failed: {type(e).__name__}: {str(e)[:120]}")
            r = {"categories": [], "details": [], "seconds": 0.0, "tokens": 0, "error": str(e)[:300]}
        used.append(a)
        new = sorted(set(r["categories"]) - found)
        found |= set(r["categories"])
        cost += r["seconds"] / 60.0 + r["tokens"] / 1000.0      # same cost unit as training ("both")
        steps.append({"tool": tool, "p": round(float(probs[a]), 3), **r, "new_categories": new})
        say(f" {r['categories'] or 'nothing'} ({r['tokens']:,} tokens, {r['seconds']:.1f}s)")
    else:
        say("  coordinator: used its tool budget")
    return found, steps


def audit(sol: Path, max_tools: int | None = None, quiet: bool = False):
    import torch
    from stable_baselines3 import PPO
    from rl_offline.common import _features
    if not (MODEL_DIR / "model.zip").exists():
        raise SystemExit("No trained coordinator; run: py rl_tools/audit.py --train")
    meta = json.loads((MODEL_DIR / "meta.json").read_text(encoding="utf-8"))
    tools, types, fidx = meta["tools"], meta["types"], meta["feat_idx"]
    mu, sd, T = np.asarray(meta["mu"]), np.asarray(meta["sd"]), len(meta["tools"])
    model = PPO.load(MODEL_DIR / "model.zip", device="cpu")
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):     # its import notice is about the feature
        import rl_environment.contract_analyzer          # extractor (regex only, by design), not
    feats = np.asarray(_features(sol), dtype=np.float32) # about the Slither analyser used below
    an = Analysers(sol)
    say = (lambda *a_, **k_: None) if quiet else print

    t_start = time.time()
    say(f"Auditing {sol.name} with the RL coordinator ({T} analysers available)")
    found, steps = coordinate(meta, model, feats, an.run, max_tools, say)

    rep = {"contract": str(sol), "audited_at": datetime.now().isoformat(timespec="seconds"),
           "vulnerability_categories": sorted(found), "analysers_run": [s["tool"] for s in steps],
           "llm_tokens": sum(s["tokens"] for s in steps),
           "analyser_seconds": round(sum(s["seconds"] for s in steps), 1),
           "wall_seconds": round(time.time() - t_start, 1), "steps": steps,
           "coordinator": {k: meta[k] for k in ("trained_on", "n_contracts", "lam", "cost", "policy",
                                                "trained_at")}}
    md = [f"# Audit report: {sol.name}", "",
          f"- Audited {rep['audited_at']} by the RL coordinator ({meta['policy']}, trained on "
          f"{meta['n_contracts']} labelled contracts, λ = {meta['lam']}).",
          f"- Analysers run, in order: {', '.join(rep['analysers_run']) or 'none'}",
          f"- LLM tokens: {rep['llm_tokens']:,}; analyser time {rep['analyser_seconds']}s "
          f"(wall {rep['wall_seconds']}s)", "",
          "## Vulnerability categories found", ""]
    md += [f"- **{c}**" for c in rep["vulnerability_categories"]] or ["- none"]
    md += ["", "## Findings by analyser", ""]
    for s in steps:
        md += [f"### {s['tool']} — {', '.join(s['categories']) or 'nothing'} "
               f"({s['tokens']:,} tokens, {s['seconds']:.1f}s)", ""]
        for d in s["details"]:
            loc = f" `{d['location']}`" if d["location"] else ""
            sev = f" ({d['severity']})" if d["severity"] else ""
            md.append(f"- {d['type']}{loc}{sev}: {d['explanation']}")
        md.append("")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = OUT_DIR / f"{sol.stem}-{datetime.now():%Y%m%d-%H%M%S}"
    stem.with_suffix(".json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    stem.with_suffix(".md").write_text("\n".join(md), encoding="utf-8")
    say(f"\nFound: {', '.join(rep['vulnerability_categories']) or 'nothing'} | "
          f"{rep['llm_tokens']:,} LLM tokens | {len(steps)} analysers")
    say(f"Report => {stem.with_suffix('.md').relative_to(ROOT)}")
    return rep


def main():
    from rl_offline.common import utf8_console
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("contract", nargs="?", help=".sol file to audit")
    ap.add_argument("--train", action="store_true", help="train and save the coordinator")
    ap.add_argument("--data", default=str(DATA.relative_to(ROOT)),
                    help="training dataset for --train (agents_live_qwen_tools.json adds the Qwen agents)")
    ap.add_argument("--max-tools", type=int, default=None, help="cap on analysers per contract")
    args = ap.parse_args()
    if args.train:
        train(ROOT / args.data)
    if args.contract:
        audit(Path(args.contract), args.max_tools)
    elif not args.train:
        ap.print_help()


if __name__ == "__main__":
    main()
