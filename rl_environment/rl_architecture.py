"""
RL Environment Architecture for Smart Contract Auditing
======================================================

This module defines the reinforcement learning environment architecture
that wraps around the existing LLM-SmartAudit system to enable adaptive
mode selection and dynamic stopping criteria.

Key Components:
1. State Representation: Contract feature extraction
2. Action Space: Mode selection (BA/TA/Hybrid) + Stopping decisions
3. Reward Function: Accuracy - Cost penalty
4. Environment Interface: Gym-compatible wrapper
"""

import gymnasium as gym
import numpy as np
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass
from enum import Enum
import json
import os

class AuditMode(Enum):
    """Audit mode enumeration"""
    BA = "SmartContractBA"          # Broad Analysis (Thought-Reasoning)
    TA = "SmartContractTA"          # Targeted Analysis (Buffer-Reasoning) 
    HYBRID = "SmartContractHybrid"  # Both BA + TA

class StoppingDecision(Enum):
    """Stopping decision enumeration"""
    CONTINUE = 0
    STOP = 1

@dataclass
class ContractFeatures:
    """
    Contract feature representation for RL state
    Based on static analysis of smart contract code
    """
    # Basic metrics
    lines_of_code: int
    function_count: int
    modifier_count: int
    event_count: int
    
    # Complexity indicators
    external_calls: int
    internal_calls: int
    loop_count: int
    conditional_count: int
    
    # Security-relevant patterns
    has_payable_functions: bool
    has_fallback_function: bool
    has_receive_function: bool
    uses_delegatecall: bool
    uses_assembly: bool
    
    # Contract type indicators
    is_token_contract: bool
    is_proxy_contract: bool
    is_multisig_contract: bool
    has_upgradeable_pattern: bool
    
    # Inheritance and interface complexity
    inheritance_depth: int
    interface_count: int
    
    # Value handling patterns
    handles_ether: bool
    has_withdrawal_pattern: bool
    has_access_control: bool
    
    def to_vector(self) -> np.ndarray:
        """Convert features to numerical vector for RL state"""
        return np.array([
            # Normalize numerical features
            min(self.lines_of_code / 1000.0, 1.0),  # Cap at 1000 LOC
            min(self.function_count / 50.0, 1.0),   # Cap at 50 functions
            min(self.modifier_count / 20.0, 1.0),   # Cap at 20 modifiers
            min(self.event_count / 30.0, 1.0),      # Cap at 30 events
            min(self.external_calls / 20.0, 1.0),   # Cap at 20 ext calls
            min(self.internal_calls / 100.0, 1.0),  # Cap at 100 int calls
            min(self.loop_count / 10.0, 1.0),       # Cap at 10 loops
            min(self.conditional_count / 50.0, 1.0), # Cap at 50 conditionals
            min(self.inheritance_depth / 10.0, 1.0), # Cap at depth 10
            min(self.interface_count / 10.0, 1.0),   # Cap at 10 interfaces
            
            # Binary features (0 or 1)
            float(self.has_payable_functions),
            float(self.has_fallback_function),
            float(self.has_receive_function),
            float(self.uses_delegatecall),
            float(self.uses_assembly),
            float(self.is_token_contract),
            float(self.is_proxy_contract),
            float(self.is_multisig_contract),
            float(self.has_upgradeable_pattern),
            float(self.handles_ether),
            float(self.has_withdrawal_pattern),
            float(self.has_access_control),
        ], dtype=np.float32)

@dataclass
class AuditState:
    """
    Complete RL state representation
    """
    contract_features: ContractFeatures
    current_round: int = 0
    max_rounds: int = 10
    cumulative_cost: float = 0.0
    vulnerabilities_found: int = 0
    confidence_scores: List[float] = None
    
    def __post_init__(self):
        if self.confidence_scores is None:
            self.confidence_scores = []
    
    def to_vector(self) -> np.ndarray:
        """Convert full state to vector representation"""
        contract_vec = self.contract_features.to_vector()
        
        # Add temporal and cost information
        temporal_features = np.array([
            self.current_round / self.max_rounds,           # Progress through rounds
            min(self.cumulative_cost / 2.0, 1.0),         # Normalized cost (cap at $2)
            min(self.vulnerabilities_found / 20.0, 1.0),   # Normalized vuln count
            np.mean(self.confidence_scores) if self.confidence_scores else 0.0,  # Avg confidence
            len(self.confidence_scores) / 10.0 if self.confidence_scores else 0.0, # Consensus history
        ], dtype=np.float32)
        
        return np.concatenate([contract_vec, temporal_features])

@dataclass
class AuditAction:
    """
    RL Action representation
    """
    mode_selection: Optional[AuditMode] = None  # Only set at start
    stopping_decision: StoppingDecision = StoppingDecision.CONTINUE
    
    def to_discrete(self) -> int:
        """Convert to discrete action for simple RL algorithms"""
        if self.mode_selection is not None:
            # Initial mode selection: 0=BA, 1=TA, 2=HYBRID
            return self.mode_selection.value if isinstance(self.mode_selection.value, int) else hash(self.mode_selection.value) % 3
        else:
            # Stopping decision: 3=CONTINUE, 4=STOP
            return 3 + self.stopping_decision.value

class SmartContractAuditEnv(gym.Env):
    """
    Gymnasium-compatible RL environment for smart contract auditing
    
    This environment wraps the existing LLM-SmartAudit system and provides
    RL interfaces for adaptive mode selection and dynamic stopping.
    """
    
    def __init__(self, 
                 base_audit_system_path: str,
                 groq_api_key: str,
                 cost_weights: Dict[str, float] = None):
        super().__init__()
        
        self.base_audit_system_path = base_audit_system_path
        self.groq_api_key = groq_api_key
        
        # Cost parameters (from base paper)
        self.cost_weights = cost_weights or {
            "BA": 0.21,    # $0.21 per contract
            "TA": 0.98,    # $0.98 per contract  
            "HYBRID": 1.19, # $1.19 per contract
            "round_penalty": 0.05,  # Additional cost per consensus round
        }
        
        # Define observation and action spaces
        state_dim = 22 + 5  # 22 contract features + 5 temporal features
        self.observation_space = gym.spaces.Box(
            low=0.0, high=1.0, shape=(state_dim,), dtype=np.float32
        )
        
        # Action space: 5 discrete actions
        # 0-2: Mode selection (BA, TA, HYBRID) - only at start
        # 3: Continue consensus round
        # 4: Stop consensus rounds
        self.action_space = gym.spaces.Discrete(5)
        
        # Environment state
        self.current_state = None
        self.selected_mode = None
        self.audit_complete = False
        self.ground_truth_vulnerabilities = None
        
    def reset(self, seed=None, options=None) -> Tuple[np.ndarray, Dict]:
        """Reset environment for new contract"""
        super().reset(seed=seed)
        
        if options and "contract_code" in options:
            contract_features = self._extract_features(options["contract_code"])
            self.ground_truth_vulnerabilities = options.get("ground_truth", [])
        else:
            # Generate random contract for testing
            contract_features = self._generate_random_features()
            self.ground_truth_vulnerabilities = []
        
        self.current_state = AuditState(
            contract_features=contract_features,
            current_round=0,
            max_rounds=10,
            cumulative_cost=0.0,
            vulnerabilities_found=0,
        )
        
        self.selected_mode = None
        self.audit_complete = False
        
        return self.current_state.to_vector(), {}
    
    def step(self, action: int) -> Tuple[np.ndarray, float, bool, bool, Dict]:
        """Execute one step in the environment"""
        
        if self.audit_complete:
            return self.current_state.to_vector(), 0.0, True, False, {}
        
        reward = 0.0
        terminated = False
        info = {}
        
        # Handle mode selection (first step only)
        if self.selected_mode is None:
            if action in [0, 1, 2]:  # Mode selection actions
                mode_map = {0: AuditMode.BA, 1: AuditMode.TA, 2: AuditMode.HYBRID}
                self.selected_mode = mode_map[action]
                
                # Execute audit with selected mode
                audit_results = self._execute_audit(self.selected_mode)
                
                # Update state based on audit results
                self._update_state_from_audit(audit_results)
                
                # Calculate immediate reward from mode selection
                reward = self._calculate_mode_reward(audit_results)
                
                info["mode_selected"] = self.selected_mode.value
                info["audit_results"] = audit_results
                
            else:
                # Invalid action - mode must be selected first
                reward = -1.0
                info["error"] = "Must select mode first"
        
        # Handle stopping decision
        else:
            if action in [3, 4]:  # Continue or stop
                stopping_decision = StoppingDecision.CONTINUE if action == 3 else StoppingDecision.STOP
                
                if stopping_decision == StoppingDecision.STOP or self.current_state.current_round >= self.current_state.max_rounds:
                    # Finalize audit
                    terminated = True
                    self.audit_complete = True
                    reward = self._calculate_final_reward()
                    info["audit_finalized"] = True
                
                elif stopping_decision == StoppingDecision.CONTINUE:
                    # Execute additional consensus round
                    consensus_results = self._execute_consensus_round()
                    self._update_state_from_consensus(consensus_results)
                    reward = self._calculate_round_reward(consensus_results)
                    info["consensus_round"] = self.current_state.current_round
                    
            else:
                # Invalid stopping action
                reward = -0.5
                info["error"] = "Invalid stopping action"
        
        return self.current_state.to_vector(), reward, terminated, False, info
    
    def _extract_features(self, contract_code: str) -> ContractFeatures:
        """Extract features from contract code"""
        # This would implement actual Solidity parsing
        # For now, return dummy features based on code length
        lines = contract_code.split('\n')
        loc = len([line for line in lines if line.strip()])
        
        return ContractFeatures(
            lines_of_code=loc,
            function_count=contract_code.count('function'),
            modifier_count=contract_code.count('modifier'),
            event_count=contract_code.count('event'),
            external_calls=contract_code.count('.call(') + contract_code.count('.delegatecall('),
            internal_calls=contract_code.count('this.'),
            loop_count=contract_code.count('for(') + contract_code.count('while('),
            conditional_count=contract_code.count('if(') + contract_code.count('require('),
            has_payable_functions='payable' in contract_code,
            has_fallback_function='fallback(' in contract_code,
            has_receive_function='receive(' in contract_code,
            uses_delegatecall='delegatecall' in contract_code,
            uses_assembly='assembly' in contract_code,
            is_token_contract='ERC20' in contract_code or 'transfer(' in contract_code,
            is_proxy_contract='Proxy' in contract_code or 'delegatecall' in contract_code,
            is_multisig_contract='multisig' in contract_code.lower(),
            has_upgradeable_pattern='Upgradeable' in contract_code,
            inheritance_depth=contract_code.count('is '),
            interface_count=contract_code.count('interface '),
            handles_ether='payable' in contract_code or 'msg.value' in contract_code,
            has_withdrawal_pattern='withdraw' in contract_code.lower(),
            has_access_control='onlyOwner' in contract_code or 'AccessControl' in contract_code,
        )
    
    def _generate_random_features(self) -> ContractFeatures:
        """Generate random contract features for testing"""
        return ContractFeatures(
            lines_of_code=np.random.randint(50, 1000),
            function_count=np.random.randint(5, 50),
            modifier_count=np.random.randint(0, 10),
            event_count=np.random.randint(0, 20),
            external_calls=np.random.randint(0, 15),
            internal_calls=np.random.randint(5, 80),
            loop_count=np.random.randint(0, 8),
            conditional_count=np.random.randint(5, 40),
            has_payable_functions=np.random.choice([True, False]),
            has_fallback_function=np.random.choice([True, False]),
            has_receive_function=np.random.choice([True, False]),
            uses_delegatecall=np.random.choice([True, False]),
            uses_assembly=np.random.choice([True, False]),
            is_token_contract=np.random.choice([True, False]),
            is_proxy_contract=np.random.choice([True, False]),
            is_multisig_contract=np.random.choice([True, False]),
            has_upgradeable_pattern=np.random.choice([True, False]),
            inheritance_depth=np.random.randint(0, 8),
            interface_count=np.random.randint(0, 5),
            handles_ether=np.random.choice([True, False]),
            has_withdrawal_pattern=np.random.choice([True, False]),
            has_access_control=np.random.choice([True, False]),
        )
    
    def _execute_audit(self, mode: AuditMode) -> Dict[str, Any]:
        """Execute audit using base LLM-SmartAudit system"""
        # This would integrate with the actual ChatChain system
        # For now, simulate audit results
        
        base_cost = self.cost_weights[mode.name if mode.name in self.cost_weights else "BA"]
        
        # Simulate vulnerability detection based on mode
        if mode == AuditMode.BA:
            # BA mode: broader but potentially less accurate
            detected_vulns = np.random.randint(0, len(self.ground_truth_vulnerabilities) + 3)
            accuracy = np.random.uniform(0.4, 0.7)  # Lower accuracy, broader coverage
        elif mode == AuditMode.TA:
            # TA mode: more accurate but may miss novel vulnerabilities
            detected_vulns = np.random.randint(0, len(self.ground_truth_vulnerabilities) + 1)
            accuracy = np.random.uniform(0.8, 0.98)  # Higher accuracy on known patterns
        else:  # HYBRID
            # Hybrid: best of both but more expensive
            detected_vulns = np.random.randint(0, len(self.ground_truth_vulnerabilities) + 2)
            accuracy = np.random.uniform(0.6, 0.9)
        
        return {
            "mode": mode.value,
            "detected_vulnerabilities": detected_vulns,
            "accuracy": accuracy,
            "cost": base_cost,
            "confidence_score": accuracy,
        }
    
    def _execute_consensus_round(self) -> Dict[str, Any]:
        """Execute additional consensus round"""
        round_cost = self.cost_weights["round_penalty"]
        
        # Simulate incremental improvement from additional consensus
        confidence_improvement = np.random.uniform(0.02, 0.1)
        additional_vulns = np.random.randint(0, 2)  # May find 0-1 additional vulnerabilities
        
        return {
            "round": self.current_state.current_round + 1,
            "cost": round_cost,
            "confidence_improvement": confidence_improvement,
            "additional_vulnerabilities": additional_vulns,
        }
    
    def _update_state_from_audit(self, audit_results: Dict[str, Any]):
        """Update state based on audit results"""
        self.current_state.cumulative_cost += audit_results["cost"]
        self.current_state.vulnerabilities_found = audit_results["detected_vulnerabilities"]
        self.current_state.confidence_scores.append(audit_results["confidence_score"])
        self.current_state.current_round = 1
    
    def _update_state_from_consensus(self, consensus_results: Dict[str, Any]):
        """Update state based on consensus round results"""
        self.current_state.cumulative_cost += consensus_results["cost"]
        self.current_state.vulnerabilities_found += consensus_results["additional_vulnerabilities"]
        self.current_state.current_round = consensus_results["round"]
        
        # Update confidence with improvement
        if self.current_state.confidence_scores:
            improved_confidence = min(1.0, self.current_state.confidence_scores[-1] + consensus_results["confidence_improvement"])
            self.current_state.confidence_scores.append(improved_confidence)
    
    def _calculate_mode_reward(self, audit_results: Dict[str, Any]) -> float:
        """Calculate reward for mode selection"""
        # Reward based on accuracy vs cost tradeoff
        accuracy_reward = audit_results["accuracy"] * 10  # Scale accuracy reward
        cost_penalty = audit_results["cost"] * 5         # Penalize high costs
        
        return accuracy_reward - cost_penalty
    
    def _calculate_round_reward(self, consensus_results: Dict[str, Any]) -> float:
        """Calculate reward for consensus round"""
        # Small reward for confidence improvement, penalty for cost
        confidence_reward = consensus_results["confidence_improvement"] * 5
        cost_penalty = consensus_results["cost"] * 3
        vuln_reward = consensus_results["additional_vulnerabilities"] * 2
        
        return confidence_reward + vuln_reward - cost_penalty
    
    def _calculate_final_reward(self) -> float:
        """Calculate final reward based on overall performance"""
        if not self.ground_truth_vulnerabilities:
            # If no ground truth, reward based on confidence and cost efficiency
            confidence_reward = np.mean(self.current_state.confidence_scores) * 15 if self.current_state.confidence_scores else 0
            cost_efficiency = max(0, 2.0 - self.current_state.cumulative_cost) * 5
            return confidence_reward + cost_efficiency
        
        # Calculate precision and recall if ground truth available
        true_positives = min(self.current_state.vulnerabilities_found, len(self.ground_truth_vulnerabilities))
        precision = true_positives / max(1, self.current_state.vulnerabilities_found)
        recall = true_positives / max(1, len(self.ground_truth_vulnerabilities))
        f1_score = 2 * (precision * recall) / max(0.01, precision + recall)
        
        # Final reward: F1 score weighted against cost
        accuracy_reward = f1_score * 20
        cost_penalty = self.current_state.cumulative_cost * 8
        
        return accuracy_reward - cost_penalty

    def render(self, mode="human"):
        """Render current environment state"""
        if mode == "human":
            print(f"Current State:")
            print(f"  Mode: {self.selected_mode.value if self.selected_mode else 'Not selected'}")
            print(f"  Round: {self.current_state.current_round}/{self.current_state.max_rounds}")
            print(f"  Cost: ${self.current_state.cumulative_cost:.2f}")
            print(f"  Vulnerabilities Found: {self.current_state.vulnerabilities_found}")
            if self.current_state.confidence_scores:
                print(f"  Confidence: {np.mean(self.current_state.confidence_scores):.3f}")