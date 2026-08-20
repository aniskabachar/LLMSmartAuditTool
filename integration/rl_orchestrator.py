"""
RL Orchestrator Integration
==========================

This module provides the main orchestration layer that integrates RL policies
with the existing LLM-SmartAudit ChatChain system.

Key Integration Points:
1. Mode Selection: RL policy intercepts CLI --config parameter
2. Stopping Decision: RL policy replaces fixed cycleNum in ComposedPhase  
3. Cost Tracking: Monitor API costs and token usage for reward computation
4. Performance Metrics: Extract accuracy and vulnerability detection results
5. State Management: Maintain RL state across multi-agent conversations

Architecture:
- RLOrchestrator: Main coordination class
- ChatChainWrapper: Extended ChatChain with RL integration hooks
- PhaseInterceptor: Intercepts phase execution for RL control
- MetricsCollector: Tracks performance for reward calculation
"""

import os
import sys
import json
import time
import logging
from typing import Dict, List, Tuple, Optional, Any, Union
from dataclasses import dataclass, asdict
from pathlib import Path
import numpy as np

# Add paths for imports
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from chatdev.chat_chain import ChatChain
from chatdev.chat_env import ChatEnv
from camel.typing import ModelType

from rl_environment.contract_analyzer import ContractAnalyzer, ContractFeatures
from rl_environment.rl_architecture import AuditState, AuditMode
from rl_policies.mode_selector import ModeSelector
from rl_policies.stopping_policy import StoppingPolicy
from evaluation.benchmark_datasets import ContractSample, VulnerabilityLabel

@dataclass
class AuditSession:
    """Container for a complete audit session"""
    session_id: str
    contract_code: str
    contract_features: ContractFeatures
    selected_mode: AuditMode
    rl_decisions: List[Dict[str, Any]]
    consensus_rounds: List[Dict[str, Any]]
    final_results: Dict[str, Any]
    performance_metrics: Dict[str, Any]
    
@dataclass 
class RLDecision:
    """Individual RL decision record"""
    decision_type: str  # "mode_selection" or "stopping"
    timestamp: float
    state_features: np.ndarray
    action_taken: Union[int, str]
    confidence: float
    reward_components: Dict[str, float]
    
class MetricsCollector:
    """
    Collects performance metrics during audit execution
    
    Tracks costs, timing, accuracy, and other metrics needed for
    RL reward computation and evaluation.
    """
    
    def __init__(self):
        self.reset()
        
    def reset(self):
        """Reset metrics for new audit session"""
        self.start_time = time.time()
        self.end_time = None
        
        # Cost tracking
        self.api_calls = 0
        self.token_usage = 0
        self.estimated_cost = 0.0
        
        # Performance tracking  
        self.vulnerabilities_detected = []
        self.confidence_scores = []
        self.false_positives = []
        self.execution_phases = []
        
        # Timing tracking
        self.phase_timings = {}
        self.consensus_round_times = []
        
    def record_api_call(self, tokens_used: int, estimated_cost: float):
        """Record API call metrics"""
        self.api_calls += 1
        self.token_usage += tokens_used
        self.estimated_cost += estimated_cost
        
    def record_vulnerability(self, vuln_type: str, confidence: float, line_numbers: List[int] = None):
        """Record detected vulnerability"""
        self.vulnerabilities_detected.append({
            'type': vuln_type,
            'confidence': confidence,
            'line_numbers': line_numbers or [],
            'timestamp': time.time()
        })
        self.confidence_scores.append(confidence)
        
    def record_phase_start(self, phase_name: str):
        """Record phase start time"""
        self.phase_timings[phase_name] = {'start': time.time()}
        
    def record_phase_end(self, phase_name: str):
        """Record phase end time"""
        if phase_name in self.phase_timings:
            self.phase_timings[phase_name]['end'] = time.time()
            self.phase_timings[phase_name]['duration'] = (
                self.phase_timings[phase_name]['end'] - 
                self.phase_timings[phase_name]['start']
            )
    
    def record_consensus_round(self, round_number: int, confidence: float):
        """Record consensus round completion"""
        self.consensus_round_times.append({
            'round': round_number,
            'timestamp': time.time(),
            'confidence': confidence
        })
        
    def finalize(self):
        """Finalize metrics collection"""
        self.end_time = time.time()
        
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of collected metrics"""
        total_time = (self.end_time or time.time()) - self.start_time
        
        return {
            'execution_time': total_time,
            'api_calls': self.api_calls,
            'token_usage': self.token_usage,
            'estimated_cost': self.estimated_cost,
            'vulnerabilities_count': len(self.vulnerabilities_detected),
            'avg_confidence': np.mean(self.confidence_scores) if self.confidence_scores else 0.0,
            'consensus_rounds': len(self.consensus_round_times),
            'phase_count': len([p for p in self.phase_timings.values() if 'duration' in p]),
            'vulnerabilities_detected': self.vulnerabilities_detected,
            'phase_timings': self.phase_timings,
        }

class PhaseInterceptor:
    """
    Intercepts phase execution to integrate RL stopping policy
    
    Monitors ComposedPhase execution and uses RL policy to decide
    when to stop consensus rounds.
    """
    
    def __init__(self, stopping_policy: StoppingPolicy, metrics_collector: MetricsCollector):
        self.stopping_policy = stopping_policy
        self.metrics_collector = metrics_collector
        self.logger = logging.getLogger(__name__)
        
    def should_continue_consensus(self, 
                                audit_state: AuditState,
                                current_round: int,
                                max_rounds: int = 10) -> Tuple[bool, float]:
        """
        Use RL stopping policy to decide whether to continue consensus
        
        Args:
            audit_state: Current audit state
            current_round: Current consensus round number
            max_rounds: Maximum allowed rounds
            
        Returns:
            Tuple of (should_continue, confidence)
        """
        
        if current_round >= max_rounds:
            return False, 1.0  # Forced stop
            
        # Use RL policy to make stopping decision
        should_stop, confidence = self.stopping_policy.predict(
            audit_state, deterministic=True
        )
        
        self.logger.info(f"RL Stopping Policy - Round {current_round}: "
                        f"{'STOP' if should_stop else 'CONTINUE'} (confidence: {confidence:.3f})")
        
        return not should_stop, confidence
    
    def update_audit_state(self,
                          contract_features: ContractFeatures,
                          current_round: int,
                          vulnerabilities_found: int,
                          confidence_scores: List[float]) -> AuditState:
        """Update audit state with latest information"""
        
        return AuditState(
            contract_features=contract_features,
            current_round=current_round,
            max_rounds=10,
            cumulative_cost=self.metrics_collector.estimated_cost,
            vulnerabilities_found=vulnerabilities_found,
            confidence_scores=confidence_scores
        )

class ChatChainWrapper:
    """
    Wrapper around ChatChain that integrates RL policies
    
    Extends the original ChatChain functionality with RL decision points
    while maintaining compatibility with existing code.
    """
    
    def __init__(self,
                 mode_selector: ModeSelector,
                 stopping_policy: StoppingPolicy,
                 contract_analyzer: ContractAnalyzer,
                 metrics_collector: MetricsCollector,
                 groq_api_key: str = None):
        
        self.mode_selector = mode_selector
        self.stopping_policy = stopping_policy
        self.contract_analyzer = contract_analyzer
        self.metrics_collector = metrics_collector
        self.groq_api_key = groq_api_key
        
        # RL state management
        self.current_audit_state = None
        self.contract_features = None
        self.rl_decisions = []
        
        # Phase interception
        self.phase_interceptor = PhaseInterceptor(stopping_policy, metrics_collector)
        
        self.logger = logging.getLogger(__name__)
        
    def create_chatchain(self, 
                        contract_code: str,
                        project_name: str = "RLAudit",
                        org_name: str = "RLSystem") -> ChatChain:
        """
        Create ChatChain instance with RL-selected mode
        
        Args:
            contract_code: Solidity contract source code
            project_name: Name for audit project
            org_name: Organization name
            
        Returns:
            Configured ChatChain instance
        """
        
        # Extract contract features
        self.contract_features = self.contract_analyzer.analyze_contract(
            contract_code, project_name
        )
        
        self.logger.info(f"Contract analysis complete - "
                        f"LOC: {self.contract_features.lines_of_code}, "
                        f"Functions: {self.contract_features.function_count}")
        
        # Use RL mode selector to choose audit mode
        selected_mode, mode_confidence = self.mode_selector.predict(
            self.contract_features, deterministic=True
        )
        
        self.logger.info(f"RL Mode Selector chose: {selected_mode.value} "
                        f"(confidence: {mode_confidence:.3f})")
        
        # Record RL decision
        mode_decision = RLDecision(
            decision_type="mode_selection",
            timestamp=time.time(),
            state_features=AuditState(contract_features=self.contract_features).to_vector(),
            action_taken=selected_mode.value,
            confidence=mode_confidence,
            reward_components={}  # Will be computed later
        )
        self.rl_decisions.append(mode_decision)
        
        # Get configuration paths for selected mode
        config_path, config_phase_path, config_role_path = self._get_config_paths(selected_mode)
        
        # Create ChatChain with selected configuration
        chat_chain = ChatChain(
            config_path=config_path,
            config_phase_path=config_phase_path,
            config_role_path=config_role_path,
            task_prompt=contract_code,
            project_name=project_name,
            org_name=org_name,
            model_type=ModelType.GPT_4_O_MINI  # Would be configured for Groq
        )
        
        # Patch ChatChain methods for RL integration
        self._patch_chatchain_methods(chat_chain)
        
        return chat_chain
    
    def _get_config_paths(self, mode: AuditMode) -> Tuple[str, str, str]:
        """Get configuration paths for audit mode"""
        
        config_map = {
            AuditMode.BA: "SmartContractBA",
            AuditMode.TA: "SmartContractTA",
            AuditMode.HYBRID: "SmartContractHybrid"  # Would need to create this config
        }
        
        config_name = config_map[mode]
        root = os.path.dirname(os.path.dirname(__file__))
        
        config_dir = os.path.join(root, "CompanyConfig", config_name)
        default_config_dir = os.path.join(root, "CompanyConfig", "Default")
        
        config_files = ["ChatChainConfig.json", "PhaseConfig.json", "RoleConfig.json"]
        config_paths = []
        
        for config_file in config_files:
            company_config_path = os.path.join(config_dir, config_file)
            default_config_path = os.path.join(default_config_dir, config_file)
            
            if os.path.exists(company_config_path):
                config_paths.append(company_config_path)
            else:
                config_paths.append(default_config_path)
        
        return tuple(config_paths)
    
    def _patch_chatchain_methods(self, chat_chain: ChatChain):
        """Patch ChatChain methods to integrate RL stopping policy"""
        
        original_execute_step = chat_chain.execute_step
        
        def rl_execute_step(phase_item: dict):
            """Enhanced execute_step with RL stopping control"""
            
            phase_name = phase_item['phase']
            phase_type = phase_item['phaseType']
            
            self.metrics_collector.record_phase_start(phase_name)
            
            if phase_type == "ComposedPhase":
                # Use RL stopping policy for ComposedPhase
                self._execute_rl_composed_phase(chat_chain, phase_item)
            else:
                # Normal execution for SimplePhase
                original_execute_step(phase_item)
                
            self.metrics_collector.record_phase_end(phase_name)
        
        # Patch the method
        chat_chain.execute_step = rl_execute_step
        
        # Also patch other methods for metrics collection
        self._patch_metrics_collection(chat_chain)
    
    def _execute_rl_composed_phase(self, chat_chain: ChatChain, phase_item: dict):
        """Execute ComposedPhase with RL stopping control"""
        
        phase = phase_item['phase']
        composition = phase_item['Composition']
        
        self.logger.info(f"Starting RL-controlled ComposedPhase: {phase}")
        
        # Initialize consensus tracking
        consensus_round = 0
        max_rounds = 10
        
        while consensus_round < max_rounds:
            consensus_round += 1
            
            self.logger.info(f"Executing consensus round {consensus_round}")
            
            # Execute one round of consensus (simplified)
            round_results = self._execute_consensus_round(
                chat_chain, composition, consensus_round
            )
            
            # Update audit state
            self.current_audit_state = self.phase_interceptor.update_audit_state(
                contract_features=self.contract_features,
                current_round=consensus_round,
                vulnerabilities_found=len(self.metrics_collector.vulnerabilities_detected),
                confidence_scores=self.metrics_collector.confidence_scores
            )
            
            # Record consensus round
            self.metrics_collector.record_consensus_round(
                consensus_round, 
                np.mean(self.metrics_collector.confidence_scores) if self.metrics_collector.confidence_scores else 0.5
            )
            
            # Use RL policy to decide whether to continue
            should_continue, stop_confidence = self.phase_interceptor.should_continue_consensus(
                self.current_audit_state, consensus_round, max_rounds
            )
            
            # Record RL stopping decision
            stopping_decision = RLDecision(
                decision_type="stopping",
                timestamp=time.time(),
                state_features=self.current_audit_state.to_vector(),
                action_taken="CONTINUE" if should_continue else "STOP",
                confidence=stop_confidence,
                reward_components={}  # Will be computed later
            )
            self.rl_decisions.append(stopping_decision)
            
            if not should_continue:
                self.logger.info(f"RL Stopping Policy decided to stop after {consensus_round} rounds")
                break
        
        self.logger.info(f"ComposedPhase completed after {consensus_round} rounds")
    
    def _execute_consensus_round(self, 
                               chat_chain: ChatChain, 
                               composition: List[Dict], 
                               round_num: int) -> Dict[str, Any]:
        """Execute a single consensus round"""
        
        round_start = time.time()
        
        # Execute each phase in the composition
        for phase_config in composition:
            phase_name = phase_config['phase']
            
            # Simulate phase execution (would integrate with actual ChatChain phases)
            self.logger.debug(f"Executing phase: {phase_name}")
            
            # Simulate vulnerability detection
            if "Detector" in phase_name:
                # Simulate finding vulnerabilities with some probability
                if np.random.random() < 0.3:  # 30% chance per detector
                    vuln_type = phase_name.replace("Detector", "")
                    confidence = np.random.uniform(0.6, 0.95)
                    
                    self.metrics_collector.record_vulnerability(
                        vuln_type=vuln_type,
                        confidence=confidence
                    )
            
            # Simulate API costs
            estimated_tokens = np.random.randint(500, 2000)
            estimated_cost = estimated_tokens * 0.00001  # Rough cost estimate
            
            self.metrics_collector.record_api_call(estimated_tokens, estimated_cost)
        
        round_time = time.time() - round_start
        
        return {
            'round_number': round_num,
            'execution_time': round_time,
            'phases_executed': len(composition),
            'vulnerabilities_found': len(self.metrics_collector.vulnerabilities_detected)
        }
    
    def _patch_metrics_collection(self, chat_chain: ChatChain):
        """Patch ChatChain for metrics collection"""
        
        # This would patch various ChatChain methods to collect:
        # - API call costs and token usage
        # - Vulnerability detection results  
        # - Timing information
        # - Confidence scores from agent interactions
        
        # For now, we'll implement basic patching
        original_post_processing = chat_chain.post_processing
        
        def enhanced_post_processing():
            """Enhanced post-processing with metrics finalization"""
            result = original_post_processing()
            self.metrics_collector.finalize()
            return result
        
        chat_chain.post_processing = enhanced_post_processing

class RLOrchestrator:
    """
    Main orchestrator for RL-augmented smart contract auditing
    
    Coordinates all RL components and provides high-level interface
    for conducting audits with adaptive policies.
    """
    
    def __init__(self,
                 mode_selector: ModeSelector,
                 stopping_policy: StoppingPolicy,
                 contract_analyzer: ContractAnalyzer = None,
                 groq_api_key: str = None,
                 audit_config: Dict[str, Any] = None):
        
        self.mode_selector = mode_selector
        self.stopping_policy = stopping_policy
        self.contract_analyzer = contract_analyzer or ContractAnalyzer()
        self.groq_api_key = groq_api_key
        
        self.audit_config = audit_config or {
            'max_consensus_rounds': 10,
            'cost_threshold': 2.0,  # Maximum cost per audit
            'confidence_threshold': 0.85,  # Target confidence level
        }
        
        # Session management
        self.current_session = None
        self.audit_history = []
        
        self.logger = logging.getLogger(__name__)
    
    def audit_contract(self,
                      contract_code: str,
                      project_name: str = "RLAudit",
                      ground_truth_vulnerabilities: List[VulnerabilityLabel] = None) -> AuditSession:
        """
        Conduct complete audit of smart contract using RL policies
        
        Args:
            contract_code: Solidity contract source code
            project_name: Name for audit project
            ground_truth_vulnerabilities: Known vulnerabilities for evaluation
            
        Returns:
            Complete audit session with results and metrics
        """
        
        session_id = f"{project_name}_{int(time.time())}"
        self.logger.info(f"Starting RL audit session: {session_id}")
        
        # Initialize metrics collection
        metrics_collector = MetricsCollector()
        
        # Create ChatChain wrapper with RL integration
        wrapper = ChatChainWrapper(
            mode_selector=self.mode_selector,
            stopping_policy=self.stopping_policy,
            contract_analyzer=self.contract_analyzer,
            metrics_collector=metrics_collector,
            groq_api_key=self.groq_api_key
        )
        
        # Create and execute ChatChain
        chat_chain = wrapper.create_chatchain(contract_code, project_name)
        
        try:
            # Execute full audit pipeline
            chat_chain.pre_processing()
            chat_chain.make_recruitment()
            chat_chain.execute_chain()
            chat_chain.post_processing()
            
            # Collect final results
            final_metrics = metrics_collector.get_summary()
            
            # Calculate performance metrics
            performance_metrics = self._calculate_performance_metrics(
                final_metrics, ground_truth_vulnerabilities
            )
            
            # Create audit session
            audit_session = AuditSession(
                session_id=session_id,
                contract_code=contract_code,
                contract_features=wrapper.contract_features,
                selected_mode=AuditMode.BA,  # Would get from wrapper
                rl_decisions=wrapper.rl_decisions,
                consensus_rounds=metrics_collector.consensus_round_times,
                final_results=final_metrics,
                performance_metrics=performance_metrics
            )
            
            self.current_session = audit_session
            self.audit_history.append(audit_session)
            
            self.logger.info(f"Audit session completed: {session_id}")
            self.logger.info(f"Results - Cost: ${final_metrics['estimated_cost']:.2f}, "
                           f"Vulnerabilities: {final_metrics['vulnerabilities_count']}, "
                           f"Confidence: {final_metrics['avg_confidence']:.3f}")
            
            return audit_session
            
        except Exception as e:
            self.logger.error(f"Audit session failed: {e}")
            raise
    
    def _calculate_performance_metrics(self,
                                     final_metrics: Dict[str, Any],
                                     ground_truth: List[VulnerabilityLabel] = None) -> Dict[str, Any]:
        """Calculate performance metrics for audit session"""
        
        performance = {
            'cost_efficiency': final_metrics['avg_confidence'] / max(0.01, final_metrics['estimated_cost']),
            'time_efficiency': final_metrics['vulnerabilities_count'] / max(0.01, final_metrics['execution_time']),
            'consensus_efficiency': final_metrics['avg_confidence'] / max(1, final_metrics['consensus_rounds']),
        }
        
        if ground_truth:
            # Calculate accuracy metrics if ground truth available
            detected_types = set(v['type'] for v in final_metrics['vulnerabilities_detected'])
            ground_truth_types = set(v.vulnerability_type for v in ground_truth)
            
            true_positives = len(detected_types.intersection(ground_truth_types))
            false_positives = len(detected_types - ground_truth_types)
            false_negatives = len(ground_truth_types - detected_types)
            
            precision = true_positives / max(1, true_positives + false_positives)
            recall = true_positives / max(1, true_positives + false_negatives)
            f1_score = 2 * (precision * recall) / max(0.01, precision + recall)
            
            performance.update({
                'precision': precision,
                'recall': recall,
                'f1_score': f1_score,
                'true_positives': true_positives,
                'false_positives': false_positives,
                'false_negatives': false_negatives,
            })
        
        return performance
    
    def batch_audit(self,
                   contracts: List[ContractSample],
                   max_contracts: int = None) -> List[AuditSession]:
        """
        Conduct batch audit of multiple contracts
        
        Args:
            contracts: List of contract samples to audit
            max_contracts: Maximum number of contracts to process
            
        Returns:
            List of audit sessions
        """
        
        max_contracts = max_contracts or len(contracts)
        contracts_to_process = contracts[:max_contracts]
        
        self.logger.info(f"Starting batch audit of {len(contracts_to_process)} contracts")
        
        audit_sessions = []
        
        for i, contract_sample in enumerate(contracts_to_process):
            self.logger.info(f"Processing contract {i+1}/{len(contracts_to_process)}: {contract_sample.contract_name}")
            
            try:
                session = self.audit_contract(
                    contract_code=contract_sample.source_code,
                    project_name=f"Batch_{contract_sample.contract_name}",
                    ground_truth_vulnerabilities=contract_sample.vulnerabilities
                )
                
                audit_sessions.append(session)
                
            except Exception as e:
                self.logger.error(f"Failed to audit {contract_sample.contract_name}: {e}")
                continue
        
        self.logger.info(f"Batch audit completed: {len(audit_sessions)} successful audits")
        
        return audit_sessions
    
    def get_aggregate_metrics(self, sessions: List[AuditSession] = None) -> Dict[str, Any]:
        """Get aggregate metrics across audit sessions"""
        
        sessions = sessions or self.audit_history
        
        if not sessions:
            return {}
        
        # Aggregate cost metrics
        total_costs = [s.final_results['estimated_cost'] for s in sessions]
        avg_consensus_rounds = [s.final_results['consensus_rounds'] for s in sessions]
        avg_confidences = [s.final_results['avg_confidence'] for s in sessions]
        
        # Aggregate performance metrics
        cost_efficiencies = [s.performance_metrics['cost_efficiency'] for s in sessions if 'cost_efficiency' in s.performance_metrics]
        
        # Mode selection distribution
        mode_distribution = {}
        for session in sessions:
            mode = session.selected_mode.value if session.selected_mode else "Unknown"
            mode_distribution[mode] = mode_distribution.get(mode, 0) + 1
        
        return {
            'total_sessions': len(sessions),
            'avg_cost_per_audit': np.mean(total_costs),
            'std_cost_per_audit': np.std(total_costs),
            'avg_consensus_rounds': np.mean(avg_consensus_rounds),
            'avg_confidence': np.mean(avg_confidences),
            'avg_cost_efficiency': np.mean(cost_efficiencies) if cost_efficiencies else 0,
            'mode_distribution': mode_distribution,
            'total_cost': sum(total_costs),
        }

# Example usage and testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # This would be implemented with actual trained policies
    print("RLOrchestrator integration example")
    print("Note: This requires trained RL policies to function properly")
    
    # Example of creating orchestrator (would need trained policies)
    # orchestrator = RLOrchestrator(
    #     mode_selector=trained_mode_selector,
    #     stopping_policy=trained_stopping_policy,
    #     groq_api_key="your_groq_key"
    # )
    
    # Example audit
    # session = orchestrator.audit_contract(sample_contract_code)
    # print(f"Audit completed with cost: ${session.final_results['estimated_cost']:.2f}")