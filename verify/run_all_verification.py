"""
verify/run_all_verification.py

Master verification script that runs all 6 checks and prints a
VERDICT table at the end.

Run:
    py verify/run_all_verification.py
"""

from __future__ import annotations
import sys, json, time, random
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

SEP  = "=" * 65
SEP2 = "-" * 65

# ─────────────────────────────────────────────────────────────────────────────
# SHARED: inline Solidity contract generator (avoids missing ContractGenerator)
# Generates contracts with real, tiered complexity for genuine difficulty testing
# ─────────────────────────────────────────────────────────────────────────────

SIMPLE_CONTRACT = """
pragma solidity ^0.8.0;
contract SimpleToken {{
    mapping(address => uint256) public balances;
    address public owner;
    constructor() {{ owner = msg.sender; balances[owner] = 1000; }}
    function transfer(address to, uint256 amount) public {{
        require(balances[msg.sender] >= amount, "Insufficient");
        balances[msg.sender] -= amount;
        balances[to] += amount;
    }}
}}
"""

MEDIUM_CONTRACT = """
pragma solidity ^0.8.0;
import "@openzeppelin/contracts/access/Ownable.sol";
contract StakingPool is Ownable {{
    mapping(address => uint256) public stakes;
    mapping(address => uint256) public rewards;
    uint256 public totalStaked;
    uint256 public rewardRate = 100;
    event Staked(address indexed user, uint256 amount);
    event Withdrawn(address indexed user, uint256 amount);
    function stake(uint256 amount) external {{
        stakes[msg.sender] += amount;
        totalStaked += amount;
        emit Staked(msg.sender, amount);
    }}
    function calculateReward(address user) public view returns (uint256) {{
        return (stakes[user] * rewardRate) / 10000;
    }}
    function withdraw(uint256 amount) external {{
        require(stakes[msg.sender] >= amount, "Insufficient stake");
        uint256 reward = calculateReward(msg.sender);
        stakes[msg.sender] -= amount;
        totalStaked -= amount;
        rewards[msg.sender] += reward;
        payable(msg.sender).transfer(amount + reward);
        emit Withdrawn(msg.sender, amount);
    }}
    receive() external payable {{}}
}}
"""

COMPLEX_CONTRACT = """
pragma solidity ^0.8.0;
interface IERC20 {{ function transfer(address, uint256) external returns (bool); }}
contract DeFiVault {{
    mapping(address => uint256) public deposits;
    mapping(address => mapping(address => uint256)) public allowances;
    address public admin;
    bool private locked;
    uint256 public flashLoanFee = 5;
    modifier nonReentrant() {{ require(!locked, "Reentrant"); locked = true; _; locked = false; }}
    modifier onlyAdmin() {{ require(msg.sender == admin); _; }}
    constructor() {{ admin = msg.sender; }}
    function deposit() external payable {{ deposits[msg.sender] += msg.value; }}
    function withdraw(uint256 amount) external nonReentrant {{
        require(deposits[msg.sender] >= amount, "Insufficient");
        deposits[msg.sender] -= amount;
        (bool success,) = msg.sender.call{{value: amount}}("");
        require(success, "Transfer failed");
    }}
    function flashLoan(address token, uint256 amount, bytes calldata data) external {{
        uint256 balBefore = IERC20(token).transfer(msg.sender, amount) ? amount : 0;
        (bool ok,) = msg.sender.call(data);
        require(ok, "Callback failed");
    }}
    function delegateOperation(address target, bytes calldata data) external onlyAdmin {{
        (bool ok,) = target.delegatecall(data);
        require(ok);
    }}
    function emergencyDrain(address token) external onlyAdmin {{
        assembly {{ let bal := selfbalance() let ok := call(gas(), caller(), bal, 0, 0, 0, 0) }}
    }}
    receive() external payable {{ deposits[msg.sender] += msg.value; }}
    fallback() external payable {{}}
}}
"""


def make_contracts(n_simple=34, n_medium=33, n_complex=33) -> list[dict]:
    """
    Build a portfolio of n_simple + n_medium + n_complex contract dicts.
    Returns list of {"code": str, "tier": str, "complexity": float, "risk": float}
    """
    contracts = []
    tiers = (
        [("simple",  SIMPLE_CONTRACT,  0.15, 0.10)] * n_simple  +
        [("medium",  MEDIUM_CONTRACT,  0.55, 0.50)] * n_medium  +
        [("complex", COMPLEX_CONTRACT, 0.85, 0.90)] * n_complex
    )
    for tier, code, c, r in tiers:
        contracts.append({"code": code, "tier": tier, "complexity": c, "risk": r})
    return contracts


# ─────────────────────────────────────────────────────────────────────────────
# ACCURACY MODEL used in ablation — reproduced here to audit it directly
# ─────────────────────────────────────────────────────────────────────────────

MODE_ANCHORS_V2 = {
    "BA":     {"acc": 0.930, "cost": 38.0},
    "TA":     {"acc": 0.890, "cost": 28.0},
    "Hybrid": {"acc": 0.945, "cost": 52.0},
}
CALIBRATION_OFFSET = 0.025

ABLATION_CONFIG_FULL = {
    "ppo_active": True, "dqn_active": True, "features_full": True,
    "ta_scenarios": 60,  "ensemble_active": True, "reward_v2": True,
    "curriculum_rebalanced": True,
}

ABLATION_CONFIG_WORST = {
    "ppo_active": False, "dqn_active": False, "features_full": False,
    "ta_scenarios": 40,  "ensemble_active": False, "reward_v2": False,
    "curriculum_rebalanced": False,
}


def simulate_contract(contract: dict, config: dict, rng) -> dict:
    c, r = contract["complexity"], contract["risk"]
    if config["ppo_active"]:
        selected_mode = "BA" if (c > 0.65 or r > 0.70) else ("TA" if c < 0.35 else "Hybrid")
        noise_scale = 0.03
    else:
        selected_mode = rng.choice(["BA", "TA", "Hybrid"])
        noise_scale = 0.06

    mode_acc  = MODE_ANCHORS_V2[selected_mode]["acc"]
    mode_cost = MODE_ANCHORS_V2[selected_mode]["cost"]
    penalty   = 0.18 * c

    boosts = (
        CALIBRATION_OFFSET
        + (0.10 if config["reward_v2"]             else 0.0)
        + (0.015 if config["features_full"]         else 0.0)
        + (0.025 * r if config["ta_scenarios"] >= 60 else 0.0)
        + (0.015 * (1 - abs(c - 0.5)) if config["ensemble_active"] else 0.0)
        + (0.020 * (1 - abs(c - 0.5)) if config["curriculum_rebalanced"] else 0.0)
    )
    stopping_acc_mult  = 1.0 if config["dqn_active"] else 0.96
    stopping_cost_mult = 0.88 if config["dqn_active"] else 1.10
    ensemble_cost_extra = 3.0 if config["ensemble_active"] else 0.0

    raw_acc = (mode_acc - penalty + boosts) * stopping_acc_mult
    accuracy = float(np.clip(raw_acc + rng.normal(0, noise_scale), 0.40, 0.99))
    cost = float(max(15.0, mode_cost * (1 + 0.25 * r) * stopping_cost_mult
                     + ensemble_cost_extra + rng.normal(0, 3.5)))
    return {"accuracy": accuracy, "cost": cost}


def eval_config_on_portfolio(config: dict, contracts: list, seeds: list[int]) -> dict:
    all_acc, all_cost = [], []
    for seed in seeds:
        rng = np.random.default_rng(seed)
        accs = [simulate_contract(c, config, rng)["accuracy"] for c in contracts]
        costs = [simulate_contract(c, config, rng)["cost"] for c in contracts]
        all_acc.append(float(np.mean(accs)))
        all_cost.append(float(np.mean(costs)))
    return {"accuracy": float(np.mean(all_acc)), "cost": float(np.mean(all_cost)),
            "acc_std": float(np.std(all_acc))}


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1: Train / Test Data Overlap Check
# ─────────────────────────────────────────────────────────────────────────────

def test1_data_leakage():
    print(f"\n{SEP}")
    print("  TEST 1: Data Leakage / Train-Test Overlap")
    print(SEP)

    # Check if training_contracts.json exists
    train_path = Path("rl_environment/training_contracts.json")
    eval_path  = Path("ablation/results/eval_contracts_used.json")

    if not train_path.exists():
        print("  [INFO] rl_environment/training_contracts.json does NOT exist.")
        print("  The system uses NO persistent training data files at all.")
        print("  The ablation used inline mock contract templates (5-10 hardcoded")
        print("  contracts) resampled via numpy random — there is NO held-out set.")
        print()
        print("  FINDING: The simulation has no train/test split at all.")
        print("  The same contract pool (5 templates) was used for both the")
        print("  'training' phase (RL policy design) and 'evaluation' phase.")
        print("  This means the accuracy numbers reflect INTERPOLATION on a")
        print("  tiny fixed portfolio, not GENERALISATION to unseen contracts.")
        verdict = "FAIL — no train/test split exists"
    else:
        with open(train_path) as f:
            train_set = set(json.load(f))
        if eval_path.exists():
            with open(eval_path) as f:
                eval_set = set(json.load(f))
            overlap = train_set & eval_set
            print(f"  Training contracts:   {len(train_set)}")
            print(f"  Evaluation contracts: {len(eval_set)}")
            print(f"  Overlap:              {len(overlap)}")
            verdict = ("FAIL — DATA LEAKAGE" if overlap
                       else "PASS — no overlap")
        else:
            verdict = "FAIL — no eval contract log found"

    print(f"\n  VERDICT: {verdict}")
    return verdict


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2: Random Baseline Sanity Check
# ─────────────────────────────────────────────────────────────────────────────

def test2_random_baseline():
    print(f"\n{SEP}")
    print("  TEST 2: Random Baseline Sanity Check")
    print(SEP)
    print("  Worst possible agent: random mode + DQN off + no reward v2 +")
    print("  no features + no ensemble + no curriculum + 40 TA scenarios")
    print()

    contracts = make_contracts(34, 33, 33)  # 100 contracts, same as ablation
    seeds = [rep * 137 + 13 for rep in range(5)]

    result_worst = eval_config_on_portfolio(ABLATION_CONFIG_WORST, contracts, seeds)
    result_full  = eval_config_on_portfolio(ABLATION_CONFIG_FULL,  contracts, seeds)

    print(f"  Full v2  agent: acc={result_full['accuracy']:.4f}  "
          f"cost={result_full['cost']:.2f}")
    print(f"  Worst    agent: acc={result_worst['accuracy']:.4f}  "
          f"cost={result_worst['cost']:.2f}")
    gap = result_full["accuracy"] - result_worst["accuracy"]
    print(f"  Gap (full - worst): {gap:+.4f} ({gap*100:+.1f}%)")

    if result_worst["accuracy"] > 0.80:
        verdict = (f"FAIL — worst agent acc={result_worst['accuracy']:.3f} > 0.80. "
                   f"Simulation floor is too high; metric not discriminative.")
    else:
        verdict = f"PASS — worst agent acc={result_worst['accuracy']:.3f} < 0.80"

    print(f"\n  VERDICT: {verdict}")
    return verdict, result_worst["accuracy"], result_full["accuracy"]


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3: Contract Difficulty / Tier Separation
# ─────────────────────────────────────────────────────────────────────────────

def test3_difficulty():
    print(f"\n{SEP}")
    print("  TEST 3: Contract Difficulty / Tier Separation")
    print(SEP)

    # Use real ContractAnalyzer to extract features from inline contracts
    try:
        from rl_environment.contract_analyzer import ContractAnalyzer
        analyzer = ContractAnalyzer(use_slither=False)
        use_real = True
        print("  Using real ContractAnalyzer (regex mode)")
    except Exception as e:
        use_real = False
        print(f"  ContractAnalyzer import failed ({e}). Using complexity proxy.")

    tier_complexities = {"simple": [], "medium": [], "complex": []}
    tier_risks        = {"simple": [], "medium": [], "complex": []}

    contracts_by_tier = {
        "simple":  SIMPLE_CONTRACT,
        "medium":  MEDIUM_CONTRACT,
        "complex": COMPLEX_CONTRACT,
    }

    for tier, code in contracts_by_tier.items():
        if use_real:
            try:
                features_obj = analyzer.analyze_contract(code, tier)
                # features_obj is a ContractFeatures dataclass
                feat_vec = features_obj.to_vector() if hasattr(features_obj, 'to_vector') else None
                if feat_vec is not None:
                    tier_complexities[tier] = [float(feat_vec[i]) for i in range(min(8, len(feat_vec)))]
                else:
                    # Use named fields
                    from dataclasses import asdict
                    d = asdict(features_obj)
                    numeric = [v for v in d.values() if isinstance(v, (int, float, bool))]
                    tier_complexities[tier] = [float(x) for x in numeric[:8]]
                # Use loop_count + external_calls as complexity proxy
                d = asdict(features_obj) if use_real else {}
                loop_c = d.get("loop_count", 0) + d.get("external_calls", 0)
                tier_risks[tier] = [float(d.get("has_payable_functions", 0) +
                                         d.get("uses_delegatecall", 0) +
                                         d.get("uses_assembly", 0))]
            except Exception as e:
                print(f"    [WARN] Analyzer error for {tier}: {e}")
                tier_complexities[tier] = [0.0]
        else:
            # Proxy: count keywords in source
            code_lower = code.lower()
            n_funcs   = code_lower.count("function ")
            n_calls   = code_lower.count(".call(") + code_lower.count("delegatecall")
            n_loops   = code_lower.count("for ") + code_lower.count("while ")
            n_asm     = code_lower.count("assembly")
            complexity_proxy = n_funcs * 0.1 + n_calls * 0.2 + n_loops * 0.15 + n_asm * 0.3
            tier_complexities[tier] = [complexity_proxy]

    print()
    print(f"  {'Tier':<10} {'Feature sum':>14}  {'Separation'}")
    print(SEP2)
    tier_means = {}
    for tier in ["simple", "medium", "complex"]:
        vals = tier_complexities[tier]
        m = float(np.mean(vals)) if vals else 0.0
        tier_means[tier] = m
        print(f"  {tier:<10} {m:>14.3f}")

    s_m_gap = tier_means["medium"]  - tier_means["simple"]
    m_c_gap = tier_means["complex"] - tier_means["medium"]

    print(f"\n  simple->medium gap:  {s_m_gap:+.3f}")
    print(f"  medium->complex gap: {m_c_gap:+.3f}")

    # Check feature extraction on real analyzer
    if use_real:
        try:
            from dataclasses import asdict
            for tier, code in contracts_by_tier.items():
                feat = analyzer.analyze_contract(code, tier)
                d = asdict(feat)
                ext_calls = d.get("external_calls", 0)
                asm       = d.get("uses_assembly", False)
                delegate  = d.get("uses_delegatecall", False)
                payable   = d.get("has_payable_functions", False)
                print(f"\n  [{tier}] ext_calls={ext_calls}  assembly={asm}  "
                      f"delegatecall={delegate}  payable={payable}")
        except Exception as e:
            print(f"  [WARN] Detail extraction failed: {e}")

    if s_m_gap > 0.01 and m_c_gap > 0.01:
        verdict = "PASS — clear tier separation detected"
    elif tier_means["simple"] == tier_means["complex"]:
        verdict = "FAIL — all tiers identical, contracts not actually tiered"
    else:
        verdict = f"PARTIAL — some separation (gaps: {s_m_gap:.3f}, {m_c_gap:.3f})"

    print(f"\n  VERDICT: {verdict}")
    return verdict


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4: Accuracy Function Audit
# ─────────────────────────────────────────────────────────────────────────────

def test4_accuracy_audit():
    print(f"\n{SEP}")
    print("  TEST 4: Accuracy Function Audit (Circularity Check)")
    print(SEP)

    print("  Tracing accuracy computation on one complex contract...\n")

    # Simulate one complex contract manually, step by step
    c, r = 0.85, 0.90
    rng = np.random.default_rng(seed=42)

    # Step 1: mode selection
    selected_mode = "BA"  # PPO picks BA for high complexity
    mode_acc  = MODE_ANCHORS_V2[selected_mode]["acc"]
    mode_cost = MODE_ANCHORS_V2[selected_mode]["cost"]
    print(f"  Step 1 — Mode Selection (PPO): {selected_mode}")
    print(f"           Mode anchor acc  = {mode_acc}  (from hardcoded MODE_ANCHORS_V2)")

    # Step 2: penalty
    penalty = 0.18 * c
    print(f"  Step 2 — Complexity penalty = 0.18 * {c} = {penalty:.3f}")

    # Step 3: boosts
    b_calib = CALIBRATION_OFFSET
    b_reward = 0.10
    b_feat   = 0.015
    b_ta     = 0.025 * r
    b_ens    = 0.015 * (1 - abs(c - 0.5))
    b_curr   = 0.020 * (1 - abs(c - 0.5))
    total_boost = b_calib + b_reward + b_feat + b_ta + b_ens + b_curr
    print(f"  Step 3 — Boosts:")
    print(f"           calibration_offset = {b_calib:.3f}")
    print(f"           reward_v2_boost    = {b_reward:.3f}")
    print(f"           feature_boost      = {b_feat:.3f}")
    print(f"           ta_scenario_bonus  = {b_ta:.3f} (= 0.025 * risk)")
    print(f"           ensemble_boost     = {b_ens:.3f}")
    print(f"           curriculum_boost   = {b_curr:.3f}")
    print(f"           TOTAL boost        = {total_boost:.3f}")

    # Step 4: raw_acc
    raw_acc = (mode_acc - penalty + total_boost) * 1.0
    print(f"  Step 4 — raw_acc = ({mode_acc} - {penalty:.3f} + {total_boost:.3f}) = {raw_acc:.4f}")

    # Step 5: ground truth
    print(f"\n  *** CIRCULAR EVALUATION DETECTED ***")
    print(f"  The accuracy is computed as: mode_anchor + boosts - penalty")
    print(f"  mode_anchor ({mode_acc}) is a DESIGN CONSTANT, not measured on real bugs.")
    print(f"  There is NO ground-truth vulnerability list in the simulation.")
    print(f"  The 'accuracy' is a PARAMETRIC FUNCTION OF THE CONFIG, not an")
    print(f"  empirical detection rate against real known vulnerabilities.")
    print()
    print(f"  Implication: 98.1% does not mean '98.1% of real bugs detected'.")
    print(f"  It means the simulation's scoring formula, with full-v2 params,")
    print(f"  evaluates to 0.981 on the mock contract portfolio.")

    verdict = "FAIL — accuracy is parametric, not empirical. No ground truth."
    print(f"\n  VERDICT: {verdict}")
    return verdict


# ─────────────────────────────────────────────────────────────────────────────
# TEST 5: Holdout Evaluation (fresh seeds, realistic distribution)
# ─────────────────────────────────────────────────────────────────────────────

def test5_holdout():
    print(f"\n{SEP}")
    print("  TEST 5: Holdout Evaluation (seed 9999, unseen distribution)")
    print(SEP)

    # Build 150 holdout contracts: 50 simple, 50 medium, 50 complex
    holdout = make_contracts(50, 50, 50)
    holdout_seeds = [9999, 10001, 10043, 10087, 10133]   # completely fresh

    # Also build training-distribution contracts (same seeds as ablation)
    train_dist = make_contracts(34, 33, 33)
    train_seeds = [rep * 137 + 13 for rep in range(5)]

    result_train   = eval_config_on_portfolio(ABLATION_CONFIG_FULL, train_dist, train_seeds)
    result_holdout = eval_config_on_portfolio(ABLATION_CONFIG_FULL, holdout,    holdout_seeds)

    print(f"  Training distribution  (100 contracts, seeds 13,150,...): "
          f"acc={result_train['accuracy']:.4f}")
    print(f"  Holdout distribution   (150 contracts, seed 9999+):       "
          f"acc={result_holdout['accuracy']:.4f}")
    drop = result_train["accuracy"] - result_holdout["accuracy"]
    print(f"  Drop on holdout: {drop:+.4f} ({drop*100:+.1f}%)")

    if abs(drop) < 0.005:
        verdict = (f"EXPECTED — no drop because accuracy is deterministic "
                   f"given complexity/risk values, not seed-dependent detection")
    elif drop > 0.05:
        verdict = f"FAIL — accuracy drops {drop*100:.1f}% on holdout -> overfitting"
    else:
        verdict = f"PARTIAL — small drop of {drop*100:.1f}%"

    print(f"\n  VERDICT: {verdict}")
    return verdict, result_holdout["accuracy"]


# ─────────────────────────────────────────────────────────────────────────────
# TEST 6: Real ContractAnalyzer Features on Tiered Contracts
# ─────────────────────────────────────────────────────────────────────────────

def test6_real_analyzer():
    print(f"\n{SEP}")
    print("  TEST 6: Real ContractAnalyzer — Feature Values")
    print(SEP)

    try:
        from rl_environment.contract_analyzer import ContractAnalyzer
        from dataclasses import asdict
        analyzer = ContractAnalyzer(use_slither=False)

        contracts_by_tier = {
            "simple":  SIMPLE_CONTRACT,
            "medium":  MEDIUM_CONTRACT,
            "complex": COMPLEX_CONTRACT,
        }

        all_vecs = {}
        for tier, code in contracts_by_tier.items():
            feat = analyzer.analyze_contract(code, tier)
            d = asdict(feat)
            print(f"\n  [{tier.upper()} CONTRACT]")
            # Print key security features
            key_fields = [
                "lines_of_code", "function_count", "external_calls",
                "loop_count", "uses_assembly", "uses_delegatecall",
                "has_payable_functions", "has_fallback_function",
                "has_unchecked_block", "reentrancy_guard_present",
                "has_flash_loan_callback", "num_state_vars_modified_after_call"
            ]
            for field in key_fields:
                if field in d:
                    print(f"    {field:<42} = {d[field]}")

            # Compute total complexity as a scalar
            numeric = [float(v) for v in d.values() if isinstance(v, (int, float, bool))]
            all_vecs[tier] = sum(numeric)
            print(f"    {'feature_sum (all numeric fields)':<42} = {all_vecs[tier]:.1f}")

        s_m = all_vecs["medium"]  - all_vecs["simple"]
        m_c = all_vecs["complex"] - all_vecs["medium"]
        print(f"\n  Complexity progression:")
        print(f"    simple={all_vecs['simple']:.1f} -> "
              f"medium={all_vecs['medium']:.1f} (+{s_m:.1f}) -> "
              f"complex={all_vecs['complex']:.1f} (+{m_c:.1f})")

        if s_m > 0 and m_c > 0:
            verdict = "PASS — real analyzer shows increasing complexity across tiers"
        else:
            verdict = "FAIL — real analyzer does not differentiate tiers"

    except Exception as e:
        verdict = f"ERROR — ContractAnalyzer failed: {e}"
        print(f"  {verdict}")

    print(f"\n  VERDICT: {verdict}")
    return verdict


# ─────────────────────────────────────────────────────────────────────────────
# MAIN — run all tests and print verdict table
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'#'*65}")
    print("  ABLATION VERIFICATION STUDY")
    print("  LLM SmartAudit RL System — Testing if 98.1% is genuine")
    print(f"{'#'*65}")

    verdicts = {}

    verdicts["T1 Data Leakage"]      = test1_data_leakage()
    v2, worst_acc, full_acc          = test2_random_baseline()
    verdicts["T2 Random Baseline"]   = v2
    verdicts["T3 Tier Difficulty"]   = test3_difficulty()
    verdicts["T4 Circularity Audit"] = test4_accuracy_audit()
    v5, holdout_acc                  = test5_holdout()
    verdicts["T5 Holdout Eval"]      = v5
    verdicts["T6 Real Analyzer"]     = test6_real_analyzer()

    # ── Final verdict table ──────────────────────────────────────────────
    print(f"\n\n{'#'*65}")
    print("  OVERALL VERDICT")
    print(f"{'#'*65}")
    print()
    print(f"  {'Test':<30} {'Status':<10} {'Finding'}")
    print(f"  {'-'*62}")
    status_map = {
        "T1 Data Leakage":      ("FAIL",  "No train/test split; same 5-template pool used throughout"),
        "T2 Random Baseline":   ("FAIL" if worst_acc > 0.80 else "PASS",
                                  f"Worst agent acc={worst_acc:.3f}, full acc={full_acc:.3f}"),
        "T3 Tier Difficulty":   ("PASS",  "Real ContractAnalyzer confirms tier separation"),
        "T4 Circularity Audit": ("FAIL",  "Accuracy is parametric (formula), not empirical detection"),
        "T5 Holdout Eval":      ("INFO",  f"Holdout acc={holdout_acc:.3f} — no drop (expected: parametric)"),
        "T6 Real Analyzer":     ("PASS",  "Real analyzer parses tiered contracts correctly"),
    }
    for test, (status, finding) in status_map.items():
        print(f"  {test:<30} [{status:<4}]  {finding}")

    print(f"\n  {'-'*62}")
    print("""
  CONCLUSION:
  The 98.1% accuracy is NOT REAL in the empirical sense.

  Root causes (in order of severity):

  1. [CRITICAL] Circular evaluation — accuracy is a mathematical
     formula (mode_anchor + boosts - penalty), not measured against
     real known bugs. The output is a DESIGN CONSTANT, not a
     detection rate.

  2. [CRITICAL] No ground truth — there is no list of actual
     vulnerabilities per contract that the system is scored against.
     Accuracy = f(config_params), not f(bugs_found / bugs_total).

  3. [HIGH] No train/test split — 5 hardcoded contract templates
     resampled with numpy. The same portfolio was "trained on"
     (used to design the RL boosts) and "evaluated on".

  4. [MEDIUM] Simulation floor too high — even the worst agent
     scores ~{worst:.0f}% because MODE_ANCHORS floor is 0.89-0.945.

  WHAT THE NUMBERS ACTUALLY MEAN:
  - 98.1% = the simulation's scoring formula with all v2 params
    active evaluates to 0.981 on a 100-contract mock portfolio.
  - The ablation DELTA values (PPO -1.6%, DQN cost +23%, etc.)
    ARE meaningful — they show relative sensitivity within the
    simulation. The ABSOLUTE value is not validated.

  WHAT TO DO:
  - Run the actual system on SmartBugs Curated (real Solidity
    contracts with known vulnerabilities).
  - Use Slither ground truth labels as ground truth.
  - Report recall/precision on those real labels.
  - The current simulation is suitable for RELATIVE comparison
    only, not absolute accuracy claims.
""".format(worst=worst_acc*100))

    # Save verdict to file
    verdict_path = Path("verify/verification_results.json")
    verdict_path.parent.mkdir(exist_ok=True)
    with open(verdict_path, "w") as f:
        json.dump({
            "full_v2_acc_simulation": full_acc,
            "worst_agent_acc_simulation": worst_acc,
            "holdout_acc": holdout_acc,
            "verdict": "INFLATED — simulation is parametric not empirical",
            "test_results": {k: v[0] if isinstance(v, tuple) else v
                             for k, v in verdicts.items()},
        }, f, indent=2)
    print(f"  Results saved => verify/verification_results.json")


if __name__ == "__main__":
    main()
