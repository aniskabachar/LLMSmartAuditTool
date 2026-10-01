"""
Expanded TA Scenario Templates for Improved Coverage
==================================================

This module adds 20 new TA scenarios to the existing 40, focusing on
modern vulnerability patterns that are often missed by traditional analysis.

Target: 40 → 60 scenarios for better coverage of complex vulnerabilities
"""

from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

@dataclass
class TAScenario:
    """Technical Analysis scenario template"""
    scenario_id: str
    name: str
    description: str
    vulnerability_patterns: List[str]
    detection_prompts: List[str]
    confidence_threshold: float
    severity: str

class VulnerabilityCategory(Enum):
    REENTRANCY = "reentrancy"
    FLASH_LOAN = "flash_loan"
    TOKEN_ISSUES = "token_issues" 
    PROXY_ISSUES = "proxy_issues"
    DEFI_SPECIFIC = "defi_specific"
    ACCESS_CONTROL = "access_control"

# NEW: 20 Additional TA Scenarios (Task 3)
EXPANDED_TA_SCENARIOS = [
    
    # Flash Loan Attack Scenarios (4 scenarios)
    TAScenario(
        scenario_id="TA_041",
        name="Flash Loan Reentrancy",
        description="Flash loan callback reenters the original function",
        vulnerability_patterns=[
            r"function\s+receiveFlashLoan.*{.*call.*}",
            r"onFlashLoan.*{.*call.*}",
            r"flashLoan.*{.*_transfer.*call.*}"
        ],
        detection_prompts=[
            "Check if flash loan callback functions can reenter the original contract",
            "Verify state changes occur before external flash loan calls",
            "Look for missing reentrancy guards in flash loan handlers"
        ],
        confidence_threshold=0.8,
        severity="CRITICAL"
    ),
    
    TAScenario(
        scenario_id="TA_042", 
        name="Flash Loan Price Manipulation",
        description="Flash loans used to manipulate oracle prices",
        vulnerability_patterns=[
            r"flashLoan.*getPrice",
            r"onFlashLoan.*oracle.*price",
            r"receiveFlashLoan.*swap.*getAmountOut"
        ],
        detection_prompts=[
            "Check if price oracles can be manipulated via flash loans",
            "Verify use of TWAP or other manipulation-resistant price feeds",
            "Look for single-block price dependencies in flash loan callbacks"
        ],
        confidence_threshold=0.75,
        severity="HIGH"
    ),
    
    TAScenario(
        scenario_id="TA_043",
        name="Flash Loan Arbitrage Drain",
        description="Flash loans exploited to drain protocol funds via arbitrage",
        vulnerability_patterns=[
            r"flashLoan.*swap.*withdraw",
            r"onFlashLoan.*exchange.*liquidate",
            r"receiveFlashLoan.*removeLiquidity.*transfer"
        ],
        detection_prompts=[
            "Check if flash loans can be used to extract more value than deposited",
            "Verify arbitrage protection mechanisms",
            "Look for MEV vulnerabilities in flash loan interactions"
        ],
        confidence_threshold=0.7,
        severity="HIGH"
    ),
    
    TAScenario(
        scenario_id="TA_044",
        name="Flash Loan Governance Attack",
        description="Flash loans used to manipulate governance voting",
        vulnerability_patterns=[
            r"flashLoan.*vote.*propose",
            r"onFlashLoan.*delegate.*voting",
            r"receiveFlashLoan.*governance.*execute"
        ],
        detection_prompts=[
            "Check if governance tokens can be flash borrowed for voting",
            "Verify voting delay and timelock mechanisms",
            "Look for flash loan protection in governance contracts"
        ],
        confidence_threshold=0.8,
        severity="CRITICAL"
    ),
    
    # Advanced Reentrancy Scenarios (4 scenarios)
    TAScenario(
        scenario_id="TA_045",
        name="Cross-Function Reentrancy",
        description="Reentrancy across different functions sharing state",
        vulnerability_patterns=[
            r"function\s+\w+.*{.*call.*}.*function\s+\w+.*{.*\w+\s*=",
            r"modifier\s+nonReentrant.*function\s+(?!withdraw|deposit)",
            r"external.*call.*internal.*function.*shared.*state"
        ],
        detection_prompts=[
            "Check if external calls in one function can affect state used by another",
            "Verify reentrancy protection covers all state-modifying functions",
            "Look for shared state variables across multiple external functions"
        ],
        confidence_threshold=0.85,
        severity="HIGH"
    ),
    
    TAScenario(
        scenario_id="TA_046",
        name="Read-Only Reentrancy",
        description="Reentrancy through view functions affecting state reads",
        vulnerability_patterns=[
            r"view.*function.*{.*external.*call",
            r"pure.*function.*{.*staticcall",
            r"balanceOf.*{.*call.*address.*this"
        ],
        detection_prompts=[
            "Check if view functions make external calls that could be exploited",
            "Verify read-only reentrancy protection",
            "Look for state inconsistencies in view function calls"
        ],
        confidence_threshold=0.7,
        severity="MEDIUM"
    ),
    
    TAScenario(
        scenario_id="TA_047",
        name="Callback Reentrancy",
        description="Reentrancy through token transfer callbacks",
        vulnerability_patterns=[
            r"transfer.*{.*onReceived.*call",
            r"safeTransfer.*{.*hook.*external",
            r"_transfer.*{.*callback.*function"
        ],
        detection_prompts=[
            "Check if token transfer callbacks can cause reentrancy",
            "Verify state updates occur before token transfers",
            "Look for missing reentrancy protection in callback handlers"
        ],
        confidence_threshold=0.8,
        severity="HIGH"
    ),
    
    TAScenario(
        scenario_id="TA_048",
        name="Delegatecall Reentrancy",
        description="Reentrancy through delegatecall to untrusted contracts",
        vulnerability_patterns=[
            r"delegatecall.*{.*external.*contract",
            r"proxy.*delegatecall.*implementation",
            r"library.*delegatecall.*state.*modification"
        ],
        detection_prompts=[
            "Check if delegatecall targets can cause reentrancy",
            "Verify delegatecall destination validation",
            "Look for state corruption through delegatecall reentrancy"
        ],
        confidence_threshold=0.9,
        severity="CRITICAL"
    ),
    
    # DeFi-Specific Vulnerabilities (4 scenarios)
    TAScenario(
        scenario_id="TA_049", 
        name="ERC4626 Inflation Attack",
        description="First depositor inflation attack on vault shares",
        vulnerability_patterns=[
            r"totalSupply.*==.*0.*deposit",
            r"shares.*assets.*totalSupply.*0",
            r"convertToShares.*totalSupply.*small"
        ],
        detection_prompts=[
            "Check if first deposit can be manipulated to inflate share prices",
            "Verify minimum deposit amounts and share calculations",
            "Look for rounding issues in empty vault deposits"
        ],
        confidence_threshold=0.8,
        severity="HIGH"
    ),
    
    TAScenario(
        scenario_id="TA_050",
        name="MEV Sandwich Attack Vector",
        description="Functions vulnerable to MEV sandwich attacks",
        vulnerability_patterns=[
            r"swap.*public.*slippage.*unlimited",
            r"addLiquidity.*deadline.*block\.timestamp",
            r"removeLiquidity.*no.*slippage.*protection"
        ],
        detection_prompts=[
            "Check if swaps can be sandwiched by MEV bots",
            "Verify slippage protection and deadline parameters",
            "Look for front-running vulnerabilities in AMM interactions"
        ],
        confidence_threshold=0.7,
        severity="MEDIUM"
    ),
    
    TAScenario(
        scenario_id="TA_051",
        name="Liquidity Pool Manipulation",
        description="LP token or pool ratio manipulation attacks",
        vulnerability_patterns=[
            r"addLiquidity.*no.*checks.*ratio",
            r"removeLiquidity.*burn.*LP.*manipulation",
            r"swap.*reserves.*manipulation.*attack"
        ],
        detection_prompts=[
            "Check if liquidity pool ratios can be manipulated",
            "Verify LP token minting and burning logic",
            "Look for reserve manipulation vulnerabilities"
        ],
        confidence_threshold=0.75,
        severity="HIGH"
    ),
    
    TAScenario(
        scenario_id="TA_052",
        name="Yield Farming Exploit",
        description="Yield farming reward calculation manipulation",
        vulnerability_patterns=[
            r"rewardPerToken.*totalSupply.*manipulation",
            r"updateReward.*before.*balance.*change",
            r"earned.*reward.*calculation.*stale.*data"
        ],
        detection_prompts=[
            "Check if reward calculations can be manipulated",
            "Verify reward update timing and balance tracking",
            "Look for stale reward data or calculation errors"
        ],
        confidence_threshold=0.8,
        severity="HIGH"
    ),
    
    # Token-Specific Issues (4 scenarios)
    TAScenario(
        scenario_id="TA_053",
        name="Fee-On-Transfer Token Issues",
        description="Logic breaks with fee-on-transfer tokens",
        vulnerability_patterns=[
            r"transferFrom.*amount.*balance.*before.*after",
            r"transfer.*received.*amount.*different.*sent",
            r"deposit.*amount.*balance.*change.*mismatch"
        ],
        detection_prompts=[
            "Check if contract handles fee-on-transfer tokens correctly",
            "Verify balance changes match expected transfer amounts",
            "Look for assumptions about exact transfer amounts"
        ],
        confidence_threshold=0.7,
        severity="MEDIUM"
    ),
    
    TAScenario(
        scenario_id="TA_054",
        name="Permit Front-Running",
        description="EIP-2612 permit signatures can be front-run",
        vulnerability_patterns=[
            r"permit.*signature.*deadline.*public",
            r"permitAndCall.*frontrun.*signature.*replay",
            r"meta.*transaction.*permit.*nonce.*manipulation"
        ],
        detection_prompts=[
            "Check if permit signatures can be front-run or replayed",
            "Verify nonce handling and signature validation",
            "Look for permit signature griefing attacks"
        ],
        confidence_threshold=0.75,
        severity="MEDIUM"
    ),
    
    TAScenario(
        scenario_id="TA_055",
        name="Rebasing Token Complications",
        description="Logic breaks with rebasing token supply changes",
        vulnerability_patterns=[
            r"balanceOf.*stored.*balance.*rebase.*change",
            r"totalSupply.*assumption.*fixed.*rebasing",
            r"share.*calculation.*rebase.*token.*supply"
        ],
        detection_prompts=[
            "Check if contract handles rebasing tokens correctly",
            "Verify balance tracking during supply changes",
            "Look for fixed supply assumptions with rebasing tokens"
        ],
        confidence_threshold=0.8,
        severity="MEDIUM"
    ),
    
    TAScenario(
        scenario_id="TA_056",
        name="Token with Hooks Exploit",
        description="Tokens with transfer hooks causing unexpected behavior",
        vulnerability_patterns=[
            r"transfer.*hook.*external.*call.*unknown",
            r"ERC777.*tokensReceived.*reentrancy.*risk",
            r"beforeTokenTransfer.*afterTokenTransfer.*call"
        ],
        detection_prompts=[
            "Check if token hooks can cause reentrancy or state issues",
            "Verify handling of tokens with transfer callbacks",
            "Look for unexpected behavior from token hook calls"
        ],
        confidence_threshold=0.85,
        severity="HIGH"
    ),
    
    # Proxy and Upgrade Issues (4 scenarios)
    TAScenario(
        scenario_id="TA_057",
        name="Storage Collision Attack",
        description="Proxy storage slots collide between implementation versions",
        vulnerability_patterns=[
            r"storage.*slot.*collision.*proxy.*implementation",
            r"upgrade.*storage.*layout.*incompatible",
            r"variable.*declaration.*order.*proxy.*state"
        ],
        detection_prompts=[
            "Check for storage layout compatibility between proxy versions",
            "Verify storage slot assignments don't collide",
            "Look for unintended state variable overwrites"
        ],
        confidence_threshold=0.9,
        severity="CRITICAL"
    ),
    
    TAScenario(
        scenario_id="TA_058",
        name="Uninitialized Implementation",
        description="Implementation contract left uninitialized creating attack vector",
        vulnerability_patterns=[
            r"implementation.*constructor.*initialize.*missing",
            r"proxy.*implementation.*uninitialized.*exploit",
            r"logic.*contract.*initialize.*disable.*missing"
        ],
        detection_prompts=[
            "Check if implementation contracts are properly initialized",
            "Verify constructors or initializers disable further initialization",
            "Look for uninitialized implementation exploitation vectors"
        ],
        confidence_threshold=0.85,
        severity="HIGH"
    ),
    
    TAScenario(
        scenario_id="TA_059",
        name="Function Selector Collision",
        description="Proxy function selectors collide causing wrong function calls",
        vulnerability_patterns=[
            r"selector.*collision.*proxy.*function.*signature",
            r"function.*signature.*hash.*conflict.*4.*bytes",
            r"proxy.*call.*wrong.*function.*selector.*match"
        ],
        detection_prompts=[
            "Check for function selector collisions in proxy contracts",
            "Verify function signature uniqueness across proxy and implementation",
            "Look for unintended function calls due to selector conflicts"
        ],
        confidence_threshold=0.8,
        severity="HIGH"
    ),
    
    TAScenario(
        scenario_id="TA_060",
        name="Metamorphic Contract Attack",
        description="Contracts that can change implementation after deployment",
        vulnerability_patterns=[
            r"CREATE2.*deterministic.*address.*implementation.*change",
            r"selfdestruct.*recreate.*different.*bytecode.*same.*address",
            r"factory.*deploy.*destroy.*redeploy.*metamorphic"
        ],
        detection_prompts=[
            "Check if contracts can be redeployed with different code at same address",
            "Verify CREATE2 usage doesn't enable metamorphic contracts",
            "Look for selfdestruct enabling contract replacement attacks"
        ],
        confidence_threshold=0.9,
        severity="CRITICAL"
    )
]

class ExpandedTAScenarioManager:
    """Manages the expanded set of TA scenarios"""
    
    def __init__(self):
        self.scenarios = EXPANDED_TA_SCENARIOS
        self.scenario_by_id = {s.scenario_id: s for s in self.scenarios}
        self.scenarios_by_category = self._group_by_category()
    
    def _group_by_category(self) -> Dict[VulnerabilityCategory, List[TAScenario]]:
        """Group scenarios by vulnerability category"""
        categories = {
            VulnerabilityCategory.FLASH_LOAN: ["TA_041", "TA_042", "TA_043", "TA_044"],
            VulnerabilityCategory.REENTRANCY: ["TA_045", "TA_046", "TA_047", "TA_048"], 
            VulnerabilityCategory.DEFI_SPECIFIC: ["TA_049", "TA_050", "TA_051", "TA_052"],
            VulnerabilityCategory.TOKEN_ISSUES: ["TA_053", "TA_054", "TA_055", "TA_056"],
            VulnerabilityCategory.PROXY_ISSUES: ["TA_057", "TA_058", "TA_059", "TA_060"],
        }
        
        result = {}
        for category, scenario_ids in categories.items():
            result[category] = [self.scenario_by_id[sid] for sid in scenario_ids if sid in self.scenario_by_id]
        
        return result
    
    def get_scenarios_for_contract(self, contract_features) -> List[TAScenario]:
        """Select relevant scenarios based on contract features"""
        relevant_scenarios = []
        
        # Flash loan scenarios - check for flash loan patterns
        if (contract_features.has_flash_loan_callback or 
            contract_features.external_calls > 5):
            relevant_scenarios.extend(self.scenarios_by_category[VulnerabilityCategory.FLASH_LOAN])
        
        # Reentrancy scenarios - check for external calls without guards
        if (contract_features.external_calls > 0 and 
            not contract_features.reentrancy_guard_present):
            relevant_scenarios.extend(self.scenarios_by_category[VulnerabilityCategory.REENTRANCY])
        
        # DeFi scenarios - check for DeFi patterns
        if (contract_features.handles_ether or 
            contract_features.is_token_contract or
            contract_features.external_calls > 3):
            relevant_scenarios.extend(self.scenarios_by_category[VulnerabilityCategory.DEFI_SPECIFIC])
        
        # Token scenarios - check for token handling
        if contract_features.is_token_contract:
            relevant_scenarios.extend(self.scenarios_by_category[VulnerabilityCategory.TOKEN_ISSUES])
        
        # Proxy scenarios - check for proxy patterns
        if (contract_features.is_proxy_contract or 
            contract_features.has_upgradeable_pattern or
            contract_features.uses_delegatecall):
            relevant_scenarios.extend(self.scenarios_by_category[VulnerabilityCategory.PROXY_ISSUES])
        
        return relevant_scenarios
    
    def get_all_scenarios(self) -> List[TAScenario]:
        """Get all expanded TA scenarios"""
        return self.scenarios
    
    def get_scenario_by_id(self, scenario_id: str) -> Optional[TAScenario]:
        """Get specific scenario by ID"""
        return self.scenario_by_id.get(scenario_id)

# Integration function for the main TA system
def get_expanded_ta_scenarios() -> List[TAScenario]:
    """Get the list of 20 new TA scenarios to add to the existing 40"""
    return EXPANDED_TA_SCENARIOS

if __name__ == "__main__":
    # Test the expanded scenarios
    manager = ExpandedTAScenarioManager()
    
    print(f"Total expanded scenarios: {len(manager.get_all_scenarios())}")
    
    for category, scenarios in manager.scenarios_by_category.items():
        print(f"\n{category.value}: {len(scenarios)} scenarios")
        for scenario in scenarios:
            print(f"  - {scenario.scenario_id}: {scenario.name} ({scenario.severity})")