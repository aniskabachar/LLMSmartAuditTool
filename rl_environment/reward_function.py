"""
Reward Function for RL-Augmented Smart Contract Auditing

This module implements sophisticated reward functions that balance audit accuracy 
against computational costs, enabling the RL system to learn optimal cost-accuracy tradeoffs.
"""

import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import logging
from enum import Enum

logger = logging.getLogger(__name__)

class VulnerabilityType(Enum):
    """Classification of vulnerability types by severity"""
    CRITICAL = "critical"      # Direct fund loss
    HIGH = "high"             # Significant security impact
    MEDIUM = "medium"         # Moderate security concern
    LOW = "low"              # Minor issues
    INFO = "informational"    # Best practice violations

@dataclass
class VulnerabilityDetection:
    """Represents a detected vulnerability"""
    vuln_type: VulnerabilityType
    confidence: float  # 0.0 to 1.0
    location: str
    description: str
    is_true_positive: Optional[bool] = None  # Ground truth when available

@dataclass
class AuditResult:
    """Complete audit result with detections and metadata"""
    vulnerabilities: List[VulnerabilityDetection]
    total_cost: float
    execution_time: float
    mode_used: str  # "BA", "TA", or "Hybrid"
    consensus_rounds: int
    agent_iterations: int
    
class CostModel:
    """Models computational costs for different audit operations"""
    
    def __init__(self):
        # Base costs per operation (in normalized units)
        self.costs = {
            'llm_call': 1.0,           # Base LLM inference cost
            'slither_analysis': 0.5,    # Static analysis cost
            'consensus_round': 2.0,     # Additional consensus overhead
            'ba_agent_iteration': 1.5,  # Behavior Analysis agent cost
            'ta_detector': 0.3,         # Technical Analysis detector cost
        }
        
        # Mode-specific multipliers
        self.mode_multipliers = {
            'BA': 1.0,      # Baseline
            'TA': 0.7,      # More efficient but less thorough
            'Hybrid': 1.3,  # Higher cost but better coverage
        }
    
    def calculate_audit_cost(self, audit_result: AuditResult) -> float:
        """Calculate total cost for an audit"""
        base_cost = (
            audit_result.agent_iterations * self.costs['llm_call'] +
            audit_result.consensus_rounds * self.costs['consensus_round'] +
            1.0 * self.costs['slither_analysis']  # Always run once
        )
        
        # Apply mode-specific multiplier
        mode_multiplier = self.mode_multipliers.get(audit_result.mode_used, 1.0)
        
        return base_cost * mode_multiplier

class AccuracyMetrics:
    """Calculates accuracy metrics for audit results"""
    
    def __init__(self):
        # Severity weights for different vulnerability types
        self.severity_weights = {
            VulnerabilityType.CRITICAL: 10.0,
            VulnerabilityType.HIGH: 5.0,
            VulnerabilityType.MEDIUM: 2.0,
            VulnerabilityType.LOW: 1.0,
            VulnerabilityType.INFO: 0.5,
        }
    
    def calculate_precision_recall(
        self, 
        predicted: List[VulnerabilityDetection],
        ground_truth: List[VulnerabilityDetection]
    ) -> Tuple[float, float, float]:
        """Calculate precision, recall, and F1 score"""
        
        if not predicted and not ground_truth:
            return 1.0, 1.0, 1.0  # Perfect when nothing to find
        
        if not predicted:
            return 0.0, 0.0, 0.0  # No predictions made
        
        if not ground_truth:
            return 0.0, 1.0, 0.0  # No vulnerabilities to find
        
        # Match predictions to ground truth (simplified matching by type and location)
        true_positives = 0
        false_positives = 0
        false_negatives = len(ground_truth)
        
        for pred in predicted:
            matched = False
            for gt in ground_truth:
                if (pred.vuln_type == gt.vuln_type and 
                    self._locations_match(pred.location, gt.location)):
                    true_positives += 1
                    false_negatives -= 1
                    matched = True
                    break
            
            if not matched:
                false_positives += 1
        
        # Calculate metrics
        precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) > 0 else 0.0
        recall = true_positives / (true_positives + false_negatives) if (true_positives + false_negatives) > 0 else 0.0
        f1_score = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
        
        return precision, recall, f1_score
    
    def calculate_weighted_accuracy(
        self,
        predicted: List[VulnerabilityDetection],
        ground_truth: List[VulnerabilityDetection]
    ) -> float:
        """Calculate severity-weighted accuracy score"""
        
        total_weight = 0.0
        matched_weight = 0.0
        penalty_weight = 0.0
        
        # Calculate matched vulnerabilities (true positives)
        for gt in ground_truth:
            weight = self.severity_weights[gt.vuln_type]
            total_weight += weight
            
            for pred in predicted:
                if (pred.vuln_type == gt.vuln_type and 
                    self._locations_match(pred.location, gt.location)):
                    # Weight by confidence for partial credit
                    matched_weight += weight * pred.confidence
                    break
        
        # Penalize false positives
        for pred in predicted:
            is_false_positive = True
            for gt in ground_truth:
                if (pred.vuln_type == gt.vuln_type and 
                    self._locations_match(pred.location, gt.location)):
                    is_false_positive = False
                    break
            
            if is_false_positive:
                weight = self.severity_weights[pred.vuln_type]
                penalty_weight += weight * pred.confidence * 0.5  # 50% penalty for FP
        
        # Calculate final accuracy
        if total_weight == 0:
            return 1.0 if penalty_weight == 0 else 0.5  # No vulnerabilities case
        
        accuracy = max(0.0, (matched_weight - penalty_weight) / total_weight)
        return min(1.0, accuracy)
    
    def _locations_match(self, loc1: str, loc2: str, tolerance: int = 5) -> bool:
        """Check if two vulnerability locations match within tolerance"""
        try:
            # Extract line numbers from location strings
            line1 = int(loc1.split(':')[1]) if ':' in loc1 else 0
            line2 = int(loc2.split(':')[1]) if ':' in loc2 else 0
            return abs(line1 - line2) <= tolerance
        except (ValueError, IndexError):
            # Fallback to string matching
            return loc1.lower() == loc2.lower()

class RewardFunction:
    """Main reward function balancing accuracy and cost"""
    
    def __init__(self, 
                 cost_weight: float = 0.3,
                 accuracy_weight: float = 0.7,
                 efficiency_bonus: float = 0.1):
        """
        Initialize reward function with configurable weights
        
        Args:
            cost_weight: Weight for cost penalty (0.0 to 1.0)
            accuracy_weight: Weight for accuracy reward (0.0 to 1.0)  
            efficiency_bonus: Bonus for achieving high accuracy at low cost
        """
        self.cost_weight = cost_weight
        self.accuracy_weight = accuracy_weight
        self.efficiency_bonus = efficiency_bonus
        
        self.cost_model = CostModel()
        self.accuracy_metrics = AccuracyMetrics()
        
        # Normalization factors (learned from training data)
        self.max_expected_cost = 100.0
        self.cost_baseline = 50.0  # Average cost for reference
    
    def calculate_reward(
        self,
        audit_result: AuditResult,
        ground_truth: List[VulnerabilityDetection],
        baseline_cost: Optional[float] = None
    ) -> Dict[str, float]:
        """
        Calculate comprehensive reward for audit result
        
        Args:
            audit_result: The audit result to evaluate
            ground_truth: Ground truth vulnerabilities
            baseline_cost: Optional baseline cost for comparison
            
        Returns:
            Dictionary with reward components and total reward
        """
        
        # Calculate accuracy component
        accuracy_score = self.accuracy_metrics.calculate_weighted_accuracy(
            audit_result.vulnerabilities, ground_truth
        )
        
        precision, recall, f1_score = self.accuracy_metrics.calculate_precision_recall(
            audit_result.vulnerabilities, ground_truth
        )
        
        # Calculate cost component
        total_cost = self.cost_model.calculate_audit_cost(audit_result)
        normalized_cost = min(1.0, total_cost / self.max_expected_cost)
        
        # Cost penalty (higher cost = lower reward)
        cost_penalty = normalized_cost
        
        # Efficiency bonus for achieving good results efficiently
        efficiency_ratio = accuracy_score / (normalized_cost + 0.1)  # Avoid division by zero
        efficiency_bonus = self.efficiency_bonus * min(1.0, efficiency_ratio / 2.0)
        
        # Baseline comparison bonus
        baseline_bonus = 0.0
        if baseline_cost is not None:
            cost_improvement = max(0.0, (baseline_cost - total_cost) / baseline_cost)
            baseline_bonus = 0.2 * cost_improvement
        
        # Calculate final reward
        accuracy_reward = self.accuracy_weight * accuracy_score
        cost_reward = self.cost_weight * (1.0 - cost_penalty)
        
        total_reward = (
            accuracy_reward + 
            cost_reward + 
            efficiency_bonus + 
            baseline_bonus
        )
        
        # Ensure reward is in reasonable range [-1, 2]
        total_reward = max(-1.0, min(2.0, total_reward))
        
        return {
            'total_reward': total_reward,
            'accuracy_score': accuracy_score,
            'accuracy_reward': accuracy_reward,
            'cost_penalty': cost_penalty,
            'cost_reward': cost_reward,
            'efficiency_bonus': efficiency_bonus,
            'baseline_bonus': baseline_bonus,
            'precision': precision,
            'recall': recall,
            'f1_score': f1_score,
            'total_cost': total_cost,
            'normalized_cost': normalized_cost,
        }
    
    def calculate_mode_selection_reward(
        self,
        selected_mode: str,
        contract_features: Dict[str, float],
        audit_result: AuditResult,
        ground_truth: List[VulnerabilityDetection]
    ) -> float:
        """Calculate reward for mode selection decision"""
        
        base_reward = self.calculate_reward(audit_result, ground_truth)
        
        # Mode-specific bonuses based on contract characteristics
        mode_bonus = 0.0
        
        complexity = contract_features.get('complexity_score', 0.5)
        risk_level = contract_features.get('risk_score', 0.5)
        
        if selected_mode == 'BA':
            # BA is good for complex, high-risk contracts needing thorough analysis
            if complexity > 0.7 and risk_level > 0.7:
                mode_bonus = 0.2
            elif complexity < 0.3 or risk_level < 0.3:
                mode_bonus = -0.1  # Overkill for simple contracts
        
        elif selected_mode == 'TA':
            # TA is good for simple contracts or when speed is priority
            if complexity < 0.4 and risk_level < 0.4:
                mode_bonus = 0.15
            elif complexity > 0.8 or risk_level > 0.8:
                mode_bonus = -0.15  # May miss complex vulnerabilities
        
        elif selected_mode == 'Hybrid':
            # Hybrid is good for medium complexity or uncertain cases
            if 0.4 <= complexity <= 0.7 or 0.4 <= risk_level <= 0.7:
                mode_bonus = 0.1
        
        return base_reward['total_reward'] + mode_bonus
    
    def calculate_stopping_reward(
        self,
        should_stop: bool,
        current_consensus: float,
        marginal_improvement: float,
        rounds_completed: int,
        cost_per_round: float
    ) -> float:
        """Calculate reward for stopping decision"""
        
        # Base reward for correct stopping decision
        if should_stop:
            # Reward for stopping when consensus is high or improvement is low
            consensus_reward = min(0.5, current_consensus)
            improvement_penalty = max(-0.3, -marginal_improvement * 2.0)
            efficiency_reward = max(0.0, 0.3 - rounds_completed * 0.05)
            
            return consensus_reward + improvement_penalty + efficiency_reward
        else:
            # Reward for continuing when there's potential for improvement  
            improvement_reward = min(0.3, marginal_improvement * 2.0)
            consensus_penalty = -max(0.0, current_consensus - 0.8) * 0.5
            cost_penalty = -cost_per_round * 0.01
            
            return improvement_reward + consensus_penalty + cost_penalty

# Utility functions for reward calculation
def create_mock_ground_truth(
    contract_path: str,
    vulnerability_types: List[VulnerabilityType] = None
) -> List[VulnerabilityDetection]:
    """Create mock ground truth for testing (replace with real data in production)"""
    
    if vulnerability_types is None:
        # Default mix of vulnerabilities
        vulnerability_types = [
            VulnerabilityType.HIGH,
            VulnerabilityType.MEDIUM,
            VulnerabilityType.LOW
        ]
    
    mock_vulnerabilities = []
    for i, vuln_type in enumerate(vulnerability_types):
        mock_vulnerabilities.append(VulnerabilityDetection(
            vuln_type=vuln_type,
            confidence=0.9,
            location=f"{contract_path}:{10 + i * 5}",
            description=f"Mock {vuln_type.value} vulnerability",
            is_true_positive=True
        ))
    
    return mock_vulnerabilities

def load_ground_truth_from_dataset(contract_path: str) -> List[VulnerabilityDetection]:
    """Load ground truth vulnerabilities from dataset annotations"""
    # This would integrate with the dataset validation system
    # For now, return mock data
    return create_mock_ground_truth(contract_path)

if __name__ == "__main__":
    # Example usage and testing
    reward_fn = RewardFunction()
    
    # Create example audit result
    audit_result = AuditResult(
        vulnerabilities=[
            VulnerabilityDetection(
                vuln_type=VulnerabilityType.HIGH,
                confidence=0.85,
                location="contract.sol:15",
                description="Reentrancy vulnerability"
            ),
            VulnerabilityDetection(
                vuln_type=VulnerabilityType.MEDIUM,
                confidence=0.75,
                location="contract.sol:42", 
                description="Integer overflow"
            )
        ],
        total_cost=75.0,
        execution_time=120.0,
        mode_used="Hybrid",
        consensus_rounds=2,
        agent_iterations=8
    )
    
    # Create ground truth
    ground_truth = create_mock_ground_truth("contract.sol")
    
    # Calculate reward
    reward_breakdown = reward_fn.calculate_reward(audit_result, ground_truth)
    
    print("Reward Breakdown:")
    for key, value in reward_breakdown.items():
        print(f"  {key}: {value:.4f}")