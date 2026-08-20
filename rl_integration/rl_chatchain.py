"""
RL-Augmented ChatChain Integration
=================================

This module extends the existing ChatChain class to integrate RL policies
for adaptive mode selection and dynamic stopping criteria.

Key Integration Points:
1. Mode Selection: RL policy replaces hardcoded --config CLI argument
2. Stopping Decision: RL policy replaces fixed cycleNum in ComposedPhase
3. Cost Tracking: Monitor token usage and API costs for reward calculation
4. State Management: Extract contract features and track consensus progress
"""

import os
import sys
import json
import logging
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, asdict
import numpy as np

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from chatdev.chat_chain import ChatChain
from chatdev.chat_env import ChatEnv, ChatEnvConfig  
from camel.typing import ModelType
from rl_environment.rl_architecture import (
    SmartContractAuditEnv, 
    ContractFeatures, 
    AuditState, 
    AuditMode
)
from rl_environment.policies import RLAuditOrchestrator, PolicyConfig

@dataclass
class AuditResults:
    """Container for audit results and metrics"""
    vulnerabilities_detected: List[str]
    confidence_scores: List[float]
    total_cost: float
    token_usage: int
    execution_time: float
    mode_used: str
    consensus_rounds: int
    
class ContractFeatureExtractor:
    """
    Extracts features from Solidity smart contract code
    
    Implements static analysis to determine contract characteristics
    for RL state representation.
    """
    
    @staticmethod
    def extract_from_code(contract_code: str) -> ContractFeatures:
        """Extract contract features from source code"""
        
        lines = contract_code.split('\n')
        non_empty_lines = [line for line in lines if line.strip() and not line.strip().startswith('//')]
        
        # Basic metrics
        lines_of_code = len(non_empty_lines)
        function_count = len([line for line in lines if 'function ' in line])
        modifier_count = len([line for line in lines if 'modifier ' in line])
        event_count = len([line for line in lines if 'event ' in line])
        
        # Complexity indicators
        external_calls = contract_code.count('.call(') + contract_code.count('.send(') + contract_code.count('.transfer(')
        internal_calls = contract_code.count('this.')
        loop_count = contract_code.count('for(') + contract_code.count('for (') + contract_code.count('while(') + contract_code.count('while (')
        conditional_count = contract_code.count('if(') + contract_code.count('if (') + contract_code.count('require(')
        
        # Security-relevant patterns
        has_payable_functions = 'payable' in contract_code
        has_fallback_function = 'fallback(' in contract_code or 'fallback (' in contract_code
        has_receive_function = 'receive(' in contract_code or 'receive (' in contract_code
        uses_delegatecall = 'delegatecall' in contract_code
        uses_assembly = 'assembly' in contract_code
        
        # Contract type indicators
        is_token_contract = any(pattern in contract_code for pattern in ['ERC20', 'ERC721', 'transfer(', 'balanceOf('])
        is_proxy_contract = any(pattern in contract_code for pattern in ['Proxy', 'delegatecall', 'implementation'])
        is_multisig_contract = 'multisig' in contract_code.lower() or 'MultiSig' in contract_code
        has_upgradeable_pattern = any(pattern in contract_code for pattern in ['Upgradeable', 'Initializable', 'UUPSUpgradeable'])
        
        # Inheritance and interface complexity  
        inheritance_depth = contract_code.count(' is ')
        interface_count = contract_code.count('interface ')
        
        # Value handling patterns
        handles_ether = 'payable' in contract_code or 'msg.value' in contract_code or '.value' in contract_code
        has_withdrawal_pattern = any(pattern in contract_code.lower() for pattern in ['withdraw', 'claim', 'redeem'])
        has_access_control = any(pattern in contract_code for pattern in ['onlyOwner', 'AccessControl', 'Ownable', 'onlyAdmin'])
        
        return ContractFeatures(
            lines_of_code=lines_of_code,
            function_count=function_count,
            modifier_count=modifier_count,
            event_count=event_count,
            external_calls=external_calls,
            internal_calls=internal_calls,
            loop_count=loop_count,
            conditional_count=conditional_count,
            has_payable_functions=has_payable_functions,
            has_fallback_function=has_fallback_function,
            has_receive_function=has_receive_function,
            uses_delegatecall=uses_delegatecall,
            uses_assembly=uses_assembly,
            is_token_contract=is_token_contract,
            is_proxy_contract=is_proxy_contract,
            is_multisig_contract=is_multisig_contract,
            has_upgradeable_pattern=has_upgradeable_pattern,
            inheritance_depth=inheritance_depth,
            interface_count=interface_count,
            handles_ether=handles_ether,
            has_withdrawal_pattern=has_withdrawal_pattern,
            has_access_control=has_access_control,
        )

class RLChatChain(ChatChain):
    """
    RL-Augmented ChatChain that integrates reinforcement learning policies
    
    Extends the base ChatChain to support:
    1. Dynamic mode selection via RL policy
    2. Adaptive consensus rounds via RL stopping policy  
    3. Cost-accuracy optimization
    4. Performance tracking for reward calculation
    """
    
    def __init__(self, 
                 task_prompt: str,
                 project_name: str,
                 org_name: str = "RLAudit",
                 model_type: ModelType = ModelType.GPT_4_O_MINI,
                 rl_orchestrator: RLAuditOrchestrator = None,
                 enable_rl: bool = True,
                 groq_api_key: str = None,
                 **kwargs):
        
        # Initialize with default BA mode - will be overridden by RL
        config_path, config_phase_path, config_role_path = self.get_config("SmartContractBA")
        
        super().__init__(
            config_path=config_path,
            config_phase_path=config_phase_path, 
            config_role_path=config_role_path,
            task_prompt=task_prompt,
            project_name=project_name,
            org_name=org_name,
            model_type=model_type,
            **kwargs
        )
        
        self.enable_rl = enable_rl
        self.rl_orchestrator = rl_orchestrator
        self.groq_api_key = groq_api_key
        
        # Extract contract features from task prompt (assumed to be contract code)
        self.contract_features = ContractFeatureExtractor.extract_from_code(task_prompt)
        
        # Initialize RL state tracking
        self.audit_state = AuditState(
            contract_features=self.contract_features,
            current_round=0,
            max_rounds=10,
            cumulative_cost=0.0,
            vulnerabilities_found=0,
        )
        
        # Performance tracking
        self.performance_metrics = {
            'start_time': None,
            'end_time': None,
            'token_usage': 0,
            'api_calls': 0,
            'detected_vulnerabilities': [],
            'confidence_scores': [],
        }
        
        # RL-selected configuration
        self.selected_mode = None
        self.dynamic_config = None
        
    def pre_processing(self):
        """Enhanced pre-processing with RL mode selection"""
        
        # Record start time
        import time
        self.performance_metrics['start_time'] = time.time()
        
        if self.enable_rl and self.rl_orchestrator:
            # Use RL to select optimal mode
            self.selected_mode = self.rl_orchestrator.predict_mode(self.contract_features)
            print(f"RL Mode Selector chose: {self.selected_mode.value}")
            
            # Update configuration paths based on RL selection
            config_path, config_phase_path, config_role_path = self.get_config(self.selected_mode.value)
            
            # Reload configurations
            with open(config_path, 'r', encoding="utf8") as file:
                self.config = json.load(file)
            with open(config_phase_path, 'r', encoding="utf8") as file:
                self.config_phase = json.load(file)
            with open(config_role_path, 'r', encoding="utf8") as file:
                self.config_role = json.load(file)
            
            # Store selected configuration
            self.dynamic_config = {
                'config_path': config_path,
                'config_phase_path': config_phase_path,
                'config_role_path': config_role_path,
                'selected_mode': self.selected_mode.value
            }
            
        else:
            # Fallback to default mode selection
            self.selected_mode = AuditMode.BA
            print("Using default mode: BA (RL disabled)")
        
        # Continue with original pre-processing
        super().pre_processing()
        
    def execute_step(self, phase_item: dict):
        """Enhanced step execution with RL stopping policy"""
        
        phase = phase_item['phase']
        phase_type = phase_item['phaseType']
        
        if phase_type == "ComposedPhase" and self.enable_rl and self.rl_orchestrator:
            # Use RL stopping policy for adaptive consensus rounds
            self._execute_rl_composed_phase(phase_item)
        else:
            # Use original execution for SimplePhase
            super().execute_step(phase_item)
    
    def _execute_rl_composed_phase(self, phase_item: dict):
        """Execute ComposedPhase with RL-controlled stopping"""
        
        phase = phase_item['phase']
        composition = phase_item['Composition']
        
        print(f"Starting RL-controlled ComposedPhase: {phase}")
        
        # Import the composed phase class
        compose_phase_class = getattr(self.compose_phase_module, phase)
        
        # Initialize with unlimited cycles (RL will control stopping)
        compose_phase_instance = compose_phase_class(
            phase_name=phase,
            cycle_num=999,  # Large number - RL will decide when to stop
            composition=composition,
            config_phase=self.config_phase,
            config_role=self.config_role,
            model_type=self.model_type,
            log_filepath=self.log_filepath
        )
        
        # Execute with RL stopping control
        self.chat_env = self._execute_with_rl_stopping(compose_phase_instance)
    
    def _execute_with_rl_stopping(self, compose_phase_instance):
        """Execute phase with RL stopping policy"""
        
        chat_env = self.chat_env
        round_count = 0
        max_rounds = 10  # Safety limit
        
        while round_count < max_rounds:
            round_count += 1
            self.audit_state.current_round = round_count
            
            print(f"Executing consensus round {round_count}")
            
            # Execute one round of the composed phase
            # This is a simplified version - actual implementation would need
            # to integrate with the ComposedPhase execution logic
            chat_env = self._execute_single_consensus_round(compose_phase_instance, chat_env)
            
            # Update audit state based on round results
            self._update_audit_state_from_round()
            
            # Use RL policy to decide whether to continue
            should_stop = self.rl_orchestrator.predict_stopping(self.audit_state)
            
            print(f"RL Stopping Policy decision: {'STOP' if should_stop else 'CONTINUE'}")
            
            if should_stop:
                break
                
        print(f"Consensus completed after {round_count} rounds")
        return chat_env
    
    def _execute_single_consensus_round(self, compose_phase_instance, chat_env):
        """Execute a single consensus round"""
        
        # This would integrate with the actual ComposedPhase logic
        # For now, simulate round execution
        
        # Update performance metrics
        self.performance_metrics['api_calls'] += len(compose_phase_instance.composition)
        
        # Simulate token usage (would be extracted from actual API calls)
        estimated_tokens = 1000 * len(compose_phase_instance.composition)
        self.performance_metrics['token_usage'] += estimated_tokens
        
        # Simulate cost accumulation
        round_cost = 0.05  # Base round cost
        if self.selected_mode == AuditMode.TA:
            round_cost *= 4.7  # TA is more expensive
        elif self.selected_mode == AuditMode.HYBRID:
            round_cost *= 5.7  # Hybrid is most expensive
            
        self.audit_state.cumulative_cost += round_cost
        
        return chat_env
    
    def _update_audit_state_from_round(self):
        """Update audit state based on round execution"""
        
        # Simulate vulnerability detection (would extract from actual results)
        additional_vulns = np.random.randint(0, 3)
        self.audit_state.vulnerabilities_found += additional_vulns
        
        # Simulate confidence improvement
        confidence = min(1.0, 0.5 + (self.audit_state.current_round * 0.1) + np.random.uniform(0, 0.2))
        self.audit_state.confidence_scores.append(confidence)
        
        # Track performance metrics
        self.performance_metrics['detected_vulnerabilities'].extend([f"Vuln_{i}" for i in range(additional_vulns)])
        self.performance_metrics['confidence_scores'].append(confidence)
    
    def post_processing(self):
        """Enhanced post-processing with performance tracking"""
        
        # Record end time
        import time
        self.performance_metrics['end_time'] = time.time()
        
        # Calculate final metrics
        execution_time = self.performance_metrics['end_time'] - self.performance_metrics['start_time']
        
        # Generate audit results
        audit_results = AuditResults(
            vulnerabilities_detected=self.performance_metrics['detected_vulnerabilities'],
            confidence_scores=self.performance_metrics['confidence_scores'],
            total_cost=self.audit_state.cumulative_cost,
            token_usage=self.performance_metrics['token_usage'],
            execution_time=execution_time,
            mode_used=self.selected_mode.value if self.selected_mode else "Unknown",
            consensus_rounds=self.audit_state.current_round,
        )
        
        # Save RL-specific metrics
        self._save_rl_metrics(audit_results)
        
        # Continue with original post-processing
        super().post_processing()
        
        return audit_results
    
    def _save_rl_metrics(self, audit_results: AuditResults):
        """Save RL-specific metrics for training and evaluation"""
        
        metrics_file = os.path.join(self.chat_env.env_dict['directory'], "rl_metrics.json")
        
        rl_metrics = {
            'contract_features': asdict(self.contract_features),
            'audit_state': {
                'current_round': self.audit_state.current_round,
                'cumulative_cost': self.audit_state.cumulative_cost,
                'vulnerabilities_found': self.audit_state.vulnerabilities_found,
                'confidence_scores': self.audit_state.confidence_scores,
            },
            'audit_results': asdict(audit_results),
            'selected_mode': self.selected_mode.value if self.selected_mode else None,
            'rl_enabled': self.enable_rl,
            'performance_metrics': self.performance_metrics,
        }
        
        with open(metrics_file, 'w', encoding='utf-8') as f:
            json.dump(rl_metrics, f, indent=2, default=str)
            
        print(f"RL metrics saved to: {metrics_file}")
    
    @staticmethod
    def get_config(company: str) -> Tuple[str, str, str]:
        """Get configuration paths for given company/mode"""
        
        root = os.path.dirname(os.path.dirname(__file__))
        config_dir = os.path.join(root, "CompanyConfig", company)
        default_config_dir = os.path.join(root, "CompanyConfig", "Default")
        
        config_files = [
            "ChatChainConfig.json",
            "PhaseConfig.json", 
            "RoleConfig.json"
        ]
        
        config_paths = []
        
        for config_file in config_files:
            company_config_path = os.path.join(config_dir, config_file)
            default_config_path = os.path.join(default_config_dir, config_file)
            
            if os.path.exists(company_config_path):
                config_paths.append(company_config_path)
            else:
                config_paths.append(default_config_path)
        
        return tuple(config_paths)

def create_rl_audit_system(groq_api_key: str, 
                          model_save_path: str = "./models/",
                          enable_training: bool = False) -> Tuple[RLChatChain, RLAuditOrchestrator]:
    """
    Factory function to create complete RL audit system
    
    Args:
        groq_api_key: API key for Groq LLaMA-3.3-70B
        model_save_path: Path to save/load trained models
        enable_training: Whether to train policies or load pre-trained ones
        
    Returns:
        Tuple of (RLChatChain, RLAuditOrchestrator)
    """
    
    # Create RL environment
    env = SmartContractAuditEnv(
        base_audit_system_path=".",
        groq_api_key=groq_api_key
    )
    
    # Create RL orchestrator
    config = PolicyConfig()
    orchestrator = RLAuditOrchestrator(
        env=env,
        config=config,
        groq_api_key=groq_api_key,
        model_save_path=model_save_path
    )
    
    # Initialize policies
    orchestrator.initialize_policies()
    
    if enable_training:
        # Train policies
        print("Training RL policies...")
        orchestrator.train_mode_selector(total_timesteps=50000)
        orchestrator.train_stopping_policy(total_timesteps=50000)
    else:
        # Load pre-trained policies if they exist
        try:
            mode_selector_path = f"{model_save_path}/mode_selector.zip"
            stopping_policy_path = f"{model_save_path}/stopping_policy.zip"
            
            if os.path.exists(mode_selector_path) and os.path.exists(stopping_policy_path):
                orchestrator.load_policies(mode_selector_path, stopping_policy_path)
                print("Loaded pre-trained RL policies")
            else:
                print("No pre-trained policies found. Using random initialization.")
        except Exception as e:
            print(f"Error loading policies: {e}. Using random initialization.")
    
    return env, orchestrator

# Example usage
def run_rl_audit(contract_code: str, 
                 groq_api_key: str,
                 project_name: str = "RLAudit",
                 enable_rl: bool = True) -> AuditResults:
    """
    Run RL-augmented audit on a smart contract
    
    Args:
        contract_code: Solidity contract source code
        groq_api_key: API key for Groq
        project_name: Name for the audit project
        enable_rl: Whether to use RL policies
        
    Returns:
        AuditResults with comprehensive audit information
    """
    
    # Create RL system
    env, orchestrator = create_rl_audit_system(
        groq_api_key=groq_api_key,
        enable_training=False  # Use pre-trained policies
    )
    
    # Create RL-augmented ChatChain
    rl_chain = RLChatChain(
        task_prompt=contract_code,
        project_name=project_name,
        rl_orchestrator=orchestrator,
        enable_rl=enable_rl,
        groq_api_key=groq_api_key,
        model_type=ModelType.GPT_4_O_MINI  # Placeholder - would use Groq model
    )
    
    # Execute audit
    print("Starting RL-augmented smart contract audit...")
    
    rl_chain.pre_processing()
    rl_chain.make_recruitment()
    rl_chain.execute_chain()
    audit_results = rl_chain.post_processing()
    
    return audit_results