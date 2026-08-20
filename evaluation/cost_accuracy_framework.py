"""
Cost-Accuracy Evaluation Framework for RL-Augmented Smart Contract Auditing

This module provides comprehensive evaluation capabilities to measure and compare
the cost-accuracy tradeoffs of different audit modes and RL policies.
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, asdict
import json
import logging
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import pickle

from rl_environment.reward_function import (
    RewardFunction, AuditResult, VulnerabilityDetection, 
    VulnerabilityType, AccuracyMetrics, CostModel
)

logger = logging.getLogger(__name__)

@dataclass
class EvaluationConfig:
    """Configuration for evaluation experiments"""
    dataset_path: str
    output_dir: str
    modes_to_evaluate: List[str] = None
    num_parallel_jobs: int = 4
    timeout_per_audit: int = 300  # seconds
    save_detailed_results: bool = True
    generate_plots: bool = True
    
    def __post_init__(self):
        if self.modes_to_evaluate is None:
            self.modes_to_evaluate = ['BA', 'TA', 'Hybrid', 'RL-Adaptive']

@dataclass 
class AuditExperiment:
    """Single audit experiment result"""
    contract_path: str
    mode_used: str
    audit_result: AuditResult
    ground_truth: List[VulnerabilityDetection]
    reward_breakdown: Dict[str, float]
    execution_metadata: Dict[str, Any]

@dataclass
class ComparisonMetrics:
    """Aggregated comparison metrics across experiments"""
    mode: str
    total_experiments: int
    
    # Accuracy metrics
    avg_accuracy: float
    avg_precision: float
    avg_recall: float
    avg_f1_score: float
    
    # Cost metrics  
    avg_cost: float
    avg_execution_time: float
    
    # Efficiency metrics
    accuracy_per_cost: float
    pareto_efficiency_score: float
    
    # Reliability metrics
    std_accuracy: float
    std_cost: float
    success_rate: float

class CostAccuracyEvaluator:
    """Main evaluation framework for cost-accuracy analysis"""
    
    def __init__(self, config: EvaluationConfig):
        self.config = config
        self.reward_function = RewardFunction()
        self.accuracy_metrics = AccuracyMetrics()
        self.cost_model = CostModel()
        
        # Create output directory
        self.output_path = Path(config.output_dir)
        self.output_path.mkdir(parents=True, exist_ok=True)
        
        # Results storage
        self.experiments: List[AuditExperiment] = []
        self.comparison_results: Dict[str, ComparisonMetrics] = {}
        
    def run_comprehensive_evaluation(
        self, 
        contracts: List[str],
        ground_truth_data: Dict[str, List[VulnerabilityDetection]]
    ) -> Dict[str, Any]:
        """
        Run comprehensive evaluation across all modes and contracts
        
        Args:
            contracts: List of contract file paths
            ground_truth_data: Ground truth vulnerabilities for each contract
            
        Returns:
            Complete evaluation results
        """
        logger.info(f"Starting comprehensive evaluation on {len(contracts)} contracts")
        
        start_time = time.time()
        
        # Run experiments for each mode
        all_results = {}
        
        for mode in self.config.modes_to_evaluate:
            logger.info(f"Evaluating mode: {mode}")
            
            mode_results = self._evaluate_mode(mode, contracts, ground_truth_data)
            all_results[mode] = mode_results
            
            # Calculate comparison metrics
            self.comparison_results[mode] = self._calculate_comparison_metrics(
                mode, mode_results
            )
        
        # Generate cross-mode analysis
        cross_analysis = self._perform_cross_mode_analysis()
        
        # Create evaluation report
        evaluation_report = {
            'config': asdict(self.config),
            'execution_time': time.time() - start_time,
            'total_contracts': len(contracts),
            'modes_evaluated': self.config.modes_to_evaluate,
            'individual_results': all_results,
            'comparison_metrics': {k: asdict(v) for k, v in self.comparison_results.items()},
            'cross_analysis': cross_analysis,
            'pareto_frontier': self._calculate_pareto_frontier(),
            'statistical_significance': self._calculate_statistical_significance()
        }
        
        # Save results
        self._save_evaluation_results(evaluation_report)
        
        # Generate visualizations
        if self.config.generate_plots:
            self._generate_evaluation_plots()
        
        logger.info(f"Evaluation completed in {evaluation_report['execution_time']:.2f} seconds")
        
        return evaluation_report
    
    def _evaluate_mode(
        self,
        mode: str,
        contracts: List[str], 
        ground_truth_data: Dict[str, List[VulnerabilityDetection]]
    ) -> List[AuditExperiment]:
        """Evaluate single mode across all contracts"""
        
        mode_experiments = []
        
        # Parallel execution for efficiency
        with ThreadPoolExecutor(max_workers=self.config.num_parallel_jobs) as executor:
            
            futures = []
            for contract_path in contracts:
                if contract_path in ground_truth_data:
                    future = executor.submit(
                        self._run_single_audit,
                        contract_path,
                        mode,
                        ground_truth_data[contract_path]
                    )
                    futures.append((future, contract_path))
            
            # Collect results
            for future, contract_path in futures:
                try:
                    experiment = future.result(timeout=self.config.timeout_per_audit)
                    if experiment:
                        mode_experiments.append(experiment)
                        self.experiments.append(experiment)
                except Exception as e:
                    logger.error(f"Failed to evaluate {contract_path} with {mode}: {e}")
        
        return mode_experiments
    
    def _run_single_audit(
        self,
        contract_path: str,
        mode: str,
        ground_truth: List[VulnerabilityDetection]
    ) -> Optional[AuditExperiment]:
        """Run single audit experiment"""
        
        try:
            start_time = time.time()
            
            # This would integrate with the actual audit system
            # For now, simulate audit results based on mode characteristics
            audit_result = self._simulate_audit_result(contract_path, mode)
            
            execution_time = time.time() - start_time
            
            # Calculate rewards
            reward_breakdown = self.reward_function.calculate_reward(
                audit_result, ground_truth
            )
            
            # Create experiment record
            experiment = AuditExperiment(
                contract_path=contract_path,
                mode_used=mode,
                audit_result=audit_result,
                ground_truth=ground_truth,
                reward_breakdown=reward_breakdown,
                execution_metadata={
                    'execution_time': execution_time,
                    'timestamp': time.time(),
                    'success': True
                }
            )
            
            return experiment
            
        except Exception as e:
            logger.error(f"Audit failed for {contract_path} with {mode}: {e}")
            return None
    
    def _simulate_audit_result(self, contract_path: str, mode: str) -> AuditResult:
        """
        Simulate audit results based on mode characteristics
        (In production, this would call the actual audit system)
        """
        
        # Mode-specific simulation parameters
        mode_params = {
            'BA': {
                'base_accuracy': 0.85,
                'cost_multiplier': 1.0,
                'consensus_rounds': 3,
                'agent_iterations': 12
            },
            'TA': {
                'base_accuracy': 0.75,
                'cost_multiplier': 0.7,
                'consensus_rounds': 1,
                'agent_iterations': 40  # Many detectors
            },
            'Hybrid': {
                'base_accuracy': 0.90,
                'cost_multiplier': 1.3,
                'consensus_rounds': 2,
                'agent_iterations': 20
            },
            'RL-Adaptive': {
                'base_accuracy': 0.88,
                'cost_multiplier': 0.9,  # Should be more efficient
                'consensus_rounds': 2,   # Adaptive stopping
                'agent_iterations': 15
            }
        }
        
        params = mode_params.get(mode, mode_params['BA'])
        
        # Simulate vulnerabilities found (simplified)
        vulnerabilities = []
        
        # Add some randomness to simulation
        np.random.seed(hash(contract_path + mode) % (2**32))
        
        num_vulns = np.random.poisson(2)  # Average 2 vulnerabilities
        
        for i in range(num_vulns):
            vuln_type = np.random.choice(list(VulnerabilityType))
            confidence = min(1.0, np.random.normal(params['base_accuracy'], 0.1))
            
            vulnerabilities.append(VulnerabilityDetection(
                vuln_type=vuln_type,
                confidence=max(0.1, confidence),
                location=f"{contract_path}:{np.random.randint(10, 100)}",
                description=f"Simulated {vuln_type.value} vulnerability"
            ))
        
        # Calculate execution time and cost
        execution_time = np.random.normal(60, 15) * params['cost_multiplier']
        total_cost = 50.0 * params['cost_multiplier']  # Base cost
        
        return AuditResult(
            vulnerabilities=vulnerabilities,
            total_cost=total_cost,
            execution_time=max(10.0, execution_time),
            mode_used=mode,
            consensus_rounds=params['consensus_rounds'],
            agent_iterations=params['agent_iterations']
        )
    
    def _calculate_comparison_metrics(
        self, 
        mode: str, 
        experiments: List[AuditExperiment]
    ) -> ComparisonMetrics:
        """Calculate aggregated metrics for a mode"""
        
        if not experiments:
            return ComparisonMetrics(
                mode=mode, total_experiments=0, avg_accuracy=0.0,
                avg_precision=0.0, avg_recall=0.0, avg_f1_score=0.0,
                avg_cost=0.0, avg_execution_time=0.0, accuracy_per_cost=0.0,
                pareto_efficiency_score=0.0, std_accuracy=0.0, std_cost=0.0,
                success_rate=0.0
            )
        
        # Extract metrics
        accuracies = [exp.reward_breakdown['accuracy_score'] for exp in experiments]
        precisions = [exp.reward_breakdown['precision'] for exp in experiments]
        recalls = [exp.reward_breakdown['recall'] for exp in experiments]
        f1_scores = [exp.reward_breakdown['f1_score'] for exp in experiments]
        costs = [exp.audit_result.total_cost for exp in experiments]
        execution_times = [exp.audit_result.execution_time for exp in experiments]
        
        # Calculate aggregated metrics
        avg_accuracy = np.mean(accuracies)
        avg_cost = np.mean(costs)
        
        return ComparisonMetrics(
            mode=mode,
            total_experiments=len(experiments),
            avg_accuracy=avg_accuracy,
            avg_precision=np.mean(precisions),
            avg_recall=np.mean(recalls),
            avg_f1_score=np.mean(f1_scores),
            avg_cost=avg_cost,
            avg_execution_time=np.mean(execution_times),
            accuracy_per_cost=avg_accuracy / avg_cost if avg_cost > 0 else 0.0,
            pareto_efficiency_score=self._calculate_pareto_score(avg_accuracy, avg_cost),
            std_accuracy=np.std(accuracies),
            std_cost=np.std(costs),
            success_rate=1.0  # All simulated experiments succeed
        )
    
    def _perform_cross_mode_analysis(self) -> Dict[str, Any]:
        """Perform statistical analysis across modes"""
        
        analysis = {
            'best_accuracy_mode': '',
            'most_efficient_mode': '',
            'best_balanced_mode': '',
            'cost_accuracy_correlation': {},
            'mode_rankings': {},
            'statistical_tests': {}
        }
        
        if not self.comparison_results:
            return analysis
        
        # Find best modes by different criteria
        best_accuracy = max(self.comparison_results.values(), 
                           key=lambda x: x.avg_accuracy)
        analysis['best_accuracy_mode'] = best_accuracy.mode
        
        best_efficiency = max(self.comparison_results.values(),
                             key=lambda x: x.accuracy_per_cost)
        analysis['most_efficient_mode'] = best_efficiency.mode
        
        best_balanced = max(self.comparison_results.values(),
                           key=lambda x: x.pareto_efficiency_score)
        analysis['best_balanced_mode'] = best_balanced.mode
        
        # Calculate mode rankings
        modes_data = []
        for metrics in self.comparison_results.values():
            modes_data.append({
                'mode': metrics.mode,
                'accuracy': metrics.avg_accuracy,
                'cost': metrics.avg_cost,
                'efficiency': metrics.accuracy_per_cost,
                'pareto_score': metrics.pareto_efficiency_score
            })
        
        analysis['mode_rankings'] = sorted(
            modes_data, 
            key=lambda x: x['pareto_score'], 
            reverse=True
        )
        
        return analysis
    
    def _calculate_pareto_frontier(self) -> List[Dict[str, Any]]:
        """Calculate Pareto frontier for cost-accuracy tradeoffs"""
        
        pareto_points = []
        
        for mode, metrics in self.comparison_results.items():
            pareto_points.append({
                'mode': mode,
                'accuracy': metrics.avg_accuracy,
                'cost': metrics.avg_cost,
                'is_pareto_optimal': False
            })
        
        # Determine Pareto optimality (higher accuracy, lower cost is better)
        for i, point in enumerate(pareto_points):
            is_optimal = True
            for j, other in enumerate(pareto_points):
                if i != j:
                    # Other point dominates if it has higher accuracy AND lower cost
                    if (other['accuracy'] >= point['accuracy'] and 
                        other['cost'] <= point['cost'] and
                        (other['accuracy'] > point['accuracy'] or other['cost'] < point['cost'])):
                        is_optimal = False
                        break
            
            pareto_points[i]['is_pareto_optimal'] = is_optimal
        
        return pareto_points
    
    def _calculate_statistical_significance(self) -> Dict[str, Any]:
        """Calculate statistical significance of mode differences"""
        
        # This would use proper statistical tests (t-tests, ANOVA, etc.)
        # For now, provide basic comparison
        
        significance_results = {}
        
        modes = list(self.comparison_results.keys())
        
        for i, mode1 in enumerate(modes):
            for mode2 in modes[i+1:]:
                metrics1 = self.comparison_results[mode1]
                metrics2 = self.comparison_results[mode2]
                
                # Simple effect size calculation
                accuracy_diff = abs(metrics1.avg_accuracy - metrics2.avg_accuracy)
                cost_diff = abs(metrics1.avg_cost - metrics2.avg_cost)
                
                comparison_key = f"{mode1}_vs_{mode2}"
                significance_results[comparison_key] = {
                    'accuracy_difference': accuracy_diff,
                    'cost_difference': cost_diff,
                    'effect_size': accuracy_diff / max(metrics1.std_accuracy, metrics2.std_accuracy, 0.01),
                    'practical_significance': accuracy_diff > 0.05 or cost_diff > 5.0
                }
        
        return significance_results
    
    def _calculate_pareto_score(self, accuracy: float, cost: float) -> float:
        """Calculate Pareto efficiency score (higher is better)"""
        
        # Normalize cost (lower is better, so invert)
        max_cost = max([m.avg_cost for m in self.comparison_results.values()] + [100.0])
        normalized_cost_efficiency = (max_cost - cost) / max_cost
        
        # Combine accuracy and cost efficiency
        return 0.7 * accuracy + 0.3 * normalized_cost_efficiency
    
    def _save_evaluation_results(self, results: Dict[str, Any]):
        """Save evaluation results to files"""
        
        # Save JSON report
        results_file = self.output_path / "evaluation_results.json"
        with open(results_file, 'w') as f:
            # Convert numpy types for JSON serialization
            json_results = self._convert_for_json(results)
            json.dump(json_results, f, indent=2)
        
        # Save pickle for detailed data
        if self.config.save_detailed_results:
            pickle_file = self.output_path / "detailed_results.pkl"
            with open(pickle_file, 'wb') as f:
                pickle.dump({
                    'experiments': self.experiments,
                    'comparison_results': self.comparison_results,
                    'full_results': results
                }, f)
        
        # Save CSV summary
        self._save_csv_summary()
        
        logger.info(f"Results saved to {self.output_path}")
    
    def _save_csv_summary(self):
        """Save summary results as CSV"""
        
        summary_data = []
        for mode, metrics in self.comparison_results.items():
            summary_data.append(asdict(metrics))
        
        if summary_data:
            df = pd.DataFrame(summary_data)
            csv_file = self.output_path / "comparison_summary.csv"
            df.to_csv(csv_file, index=False)
    
    def _convert_for_json(self, obj):
        """Convert numpy types and other non-JSON serializable objects"""
        
        if isinstance(obj, dict):
            return {k: self._convert_for_json(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_for_json(item) for item in obj]
        elif isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        else:
            return obj
    
    def _generate_evaluation_plots(self):
        """Generate visualization plots for evaluation results"""
        
        plt.style.use('default')
        fig_size = (15, 12)
        
        # Create subplots
        fig, axes = plt.subplots(2, 3, figsize=fig_size)
        fig.suptitle('Cost-Accuracy Evaluation Results', fontsize=16)
        
        # Prepare data for plotting
        modes = list(self.comparison_results.keys())
        accuracies = [self.comparison_results[mode].avg_accuracy for mode in modes]
        costs = [self.comparison_results[mode].avg_cost for mode in modes]
        efficiencies = [self.comparison_results[mode].accuracy_per_cost for mode in modes]
        f1_scores = [self.comparison_results[mode].avg_f1_score for mode in modes]
        
        # Plot 1: Cost vs Accuracy Scatter
        axes[0, 0].scatter(costs, accuracies, s=100, alpha=0.7)
        for i, mode in enumerate(modes):
            axes[0, 0].annotate(mode, (costs[i], accuracies[i]), 
                              xytext=(5, 5), textcoords='offset points')
        axes[0, 0].set_xlabel('Average Cost')
        axes[0, 0].set_ylabel('Average Accuracy')
        axes[0, 0].set_title('Cost vs Accuracy Tradeoff')
        axes[0, 0].grid(True, alpha=0.3)
        
        # Plot 2: Efficiency Comparison
        axes[0, 1].bar(modes, efficiencies, alpha=0.7)
        axes[0, 1].set_ylabel('Accuracy per Cost Unit')
        axes[0, 1].set_title('Efficiency Comparison')
        axes[0, 1].tick_params(axis='x', rotation=45)
        
        # Plot 3: Accuracy Comparison
        axes[0, 2].bar(modes, accuracies, alpha=0.7, color='green')
        axes[0, 2].set_ylabel('Average Accuracy')
        axes[0, 2].set_title('Accuracy Comparison')
        axes[0, 2].tick_params(axis='x', rotation=45)
        
        # Plot 4: Cost Comparison  
        axes[1, 0].bar(modes, costs, alpha=0.7, color='red')
        axes[1, 0].set_ylabel('Average Cost')
        axes[1, 0].set_title('Cost Comparison')
        axes[1, 0].tick_params(axis='x', rotation=45)
        
        # Plot 5: F1 Score Comparison
        axes[1, 1].bar(modes, f1_scores, alpha=0.7, color='blue')
        axes[1, 1].set_ylabel('Average F1 Score')
        axes[1, 1].set_title('F1 Score Comparison')
        axes[1, 1].tick_params(axis='x', rotation=45)
        
        # Plot 6: Pareto Frontier
        pareto_data = self._calculate_pareto_frontier()
        pareto_optimal = [p for p in pareto_data if p['is_pareto_optimal']]
        non_pareto = [p for p in pareto_data if not p['is_pareto_optimal']]
        
        if pareto_optimal:
            axes[1, 2].scatter([p['cost'] for p in pareto_optimal], 
                              [p['accuracy'] for p in pareto_optimal],
                              c='red', s=100, label='Pareto Optimal', alpha=0.8)
        
        if non_pareto:
            axes[1, 2].scatter([p['cost'] for p in non_pareto], 
                              [p['accuracy'] for p in non_pareto],
                              c='blue', s=100, label='Non-Pareto', alpha=0.6)
        
        axes[1, 2].set_xlabel('Cost')
        axes[1, 2].set_ylabel('Accuracy')
        axes[1, 2].set_title('Pareto Frontier Analysis')
        axes[1, 2].legend()
        axes[1, 2].grid(True, alpha=0.3)
        
        plt.tight_layout()
        
        # Save plot
        plot_file = self.output_path / "evaluation_plots.png"
        plt.savefig(plot_file, dpi=300, bbox_inches='tight')
        plt.close()
        
        logger.info(f"Evaluation plots saved to {plot_file}")

def create_sample_evaluation():
    """Create sample evaluation for testing"""
    
    # Sample configuration
    config = EvaluationConfig(
        dataset_path="./datasets/sample_contracts",
        output_dir="./evaluation/results",
        modes_to_evaluate=['BA', 'TA', 'Hybrid', 'RL-Adaptive']
    )
    
    evaluator = CostAccuracyEvaluator(config)
    
    # Sample contracts and ground truth
    contracts = [
        "contract1.sol",
        "contract2.sol", 
        "contract3.sol",
        "contract4.sol",
        "contract5.sol"
    ]
    
    ground_truth_data = {}
    for contract in contracts:
        # Create mock ground truth
        ground_truth_data[contract] = [
            VulnerabilityDetection(
                vuln_type=VulnerabilityType.HIGH,
                confidence=1.0,
                location=f"{contract}:25",
                description="Reentrancy vulnerability",
                is_true_positive=True
            ),
            VulnerabilityDetection(
                vuln_type=VulnerabilityType.MEDIUM,
                confidence=1.0,
                location=f"{contract}:45",
                description="Integer overflow",
                is_true_positive=True
            )
        ]
    
    # Run evaluation
    results = evaluator.run_comprehensive_evaluation(contracts, ground_truth_data)
    
    return results

if __name__ == "__main__":
    # Run sample evaluation
    results = create_sample_evaluation()
    
    print("Evaluation completed!")
    print(f"Results saved to: {results['config']['output_dir']}")
    print("\nComparison Summary:")
    
    for mode, metrics in results['comparison_metrics'].items():
        print(f"\n{mode}:")
        print(f"  Accuracy: {metrics['avg_accuracy']:.3f}")
        print(f"  Cost: {metrics['avg_cost']:.1f}")
        print(f"  Efficiency: {metrics['accuracy_per_cost']:.4f}")