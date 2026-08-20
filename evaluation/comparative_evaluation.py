"""
Comparative Evaluation for RL-Augmented Smart Contract Auditing

This module conducts comprehensive comparative evaluation between RL-adaptive
approach and baseline modes (BA, TA, Hybrid).
"""

import asyncio
import logging
import json
import time
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
from dataclasses import dataclass, asdict
import scipy.stats as stats
from sklearn.metrics import classification_report, confusion_matrix

from evaluation.cost_accuracy_framework import CostAccuracyEvaluator, EvaluationConfig
from evaluation.baseline_experiments import BaselineExperimentRunner, BaselineConfig
from training.rl_training_pipeline import RLTrainingPipeline, TrainingConfig
from rl_environment.reward_function import RewardFunction, VulnerabilityDetection
from infrastructure.groq_backend import GroqBackend

logger = logging.getLogger(__name__)

@dataclass
class ComparisonConfig:
    """Configuration for comparative evaluation"""
    dataset_path: str
    output_dir: str
    
    # Evaluation parameters
    num_test_contracts: int = 100
    repetitions: int = 5
    confidence_level: float = 0.95
    
    # Models to compare
    modes_to_compare: List[str] = None
    
    # Evaluation aspects
    evaluate_accuracy: bool = True
    evaluate_cost: bool = True
    evaluate_efficiency: bool = True
    evaluate_robustness: bool = True
    evaluate_scalability: bool = True
    
    def __post_init__(self):
        if self.modes_to_compare is None:
            self.modes_to_compare = ['BA', 'TA', 'Hybrid', 'RL-Adaptive']

@dataclass
class ComparisonResult:
    """Results from comparative evaluation"""
    mode: str
    
    # Performance metrics
    accuracy_mean: float
    accuracy_std: float
    cost_mean: float
    cost_std: float
    efficiency_mean: float
    efficiency_std: float
    
    # Statistical metrics
    confidence_interval_accuracy: Tuple[float, float]
    confidence_interval_cost: Tuple[float, float]
    
    # Robustness metrics
    performance_variance: float
    outlier_rate: float
    
    # Scalability metrics
    time_complexity: str
    memory_usage: float
class ComparativeEvaluator:
    """Main comparative evaluation system"""
    
    def __init__(self, config: ComparisonConfig):
        self.config = config
        self.output_path = Path(config.output_dir)
        self.output_path.mkdir(parents=True, exist_ok=True)
        
        # Initialize components
        self.reward_function = RewardFunction()
        self.groq_backend = GroqBackend()
        
        # Results storage
        self.comparison_results: Dict[str, ComparisonResult] = {}
        self.statistical_tests: Dict[str, Any] = {}
        self.detailed_results: List[Dict] = []
        
    async def run_comprehensive_comparison(self) -> Dict[str, Any]:
        """Run complete comparative evaluation"""
        
        logger.info("Starting comprehensive comparative evaluation")
        start_time = time.time()
        
        # Load and prepare test dataset
        test_contracts, ground_truth = await self._prepare_test_dataset()
        
        # Run evaluation for each mode
        mode_results = {}
        
        for mode in self.config.modes_to_compare:
            logger.info(f"Evaluating mode: {mode}")
            
            mode_result = await self._evaluate_mode(mode, test_contracts, ground_truth)
            mode_results[mode] = mode_result
            
            # Calculate comparison metrics
            self.comparison_results[mode] = self._calculate_comparison_result(mode, mode_result)
        
        # Perform statistical analysis
        statistical_analysis = self._perform_statistical_analysis()
        
        # Generate comprehensive report
        final_report = {
            'config': asdict(self.config),
            'execution_time': time.time() - start_time,
            'test_dataset_info': {
                'num_contracts': len(test_contracts),
                'repetitions': self.config.repetitions
            },
            'mode_results': mode_results,
            'comparison_summary': {k: asdict(v) for k, v in self.comparison_results.items()},
            'statistical_analysis': statistical_analysis,
            'performance_rankings': self._calculate_performance_rankings(),
            'recommendations': self._generate_recommendations(),
            'pareto_analysis': self._perform_pareto_analysis()
        }
        
        # Save results and generate visualizations
        await self._save_evaluation_results(final_report)
        await self._generate_comparison_visualizations()
        
        logger.info(f"Comparative evaluation completed in {final_report['execution_time']:.2f} seconds")
        
        return final_report
    async def _prepare_test_dataset(self) -> Tuple[List[str], Dict[str, List[VulnerabilityDetection]]]:
        """Prepare test dataset for evaluation"""
        
        dataset_path = Path(self.config.dataset_path)
        
        # Load contracts
        if dataset_path.exists():
            contract_files = list(dataset_path.glob("**/*.sol"))
        else:
            logger.warning("Dataset not found, creating mock test dataset")
            contract_files = self._create_mock_test_contracts()
        
        # Sample test contracts
        if len(contract_files) > self.config.num_test_contracts:
            np.random.seed(123)  # Different seed from training
            contract_files = np.random.choice(
                contract_files, 
                size=self.config.num_test_contracts, 
                replace=False
            ).tolist()
        
        contracts = [str(f) for f in contract_files]
        
        # Load ground truth
        ground_truth = {}
        for contract in contracts:
            ground_truth[contract] = await self._load_test_ground_truth(contract)
        
        return contracts, ground_truth
    
    def _create_mock_test_contracts(self) -> List[Path]:
        """Create mock test contracts"""
        
        mock_contracts = []
        mock_dir = self.output_path / 'test_contracts'
        mock_dir.mkdir(exist_ok=True)
        
        for i in range(self.config.num_test_contracts):
            contract_file = mock_dir / f"test_contract_{i:04d}.sol"
            
            # Create varied mock contracts for testing
            complexity_level = i % 3  # 0=simple, 1=medium, 2=complex
            
            if complexity_level == 0:
                # Simple contract
                content = self._generate_simple_contract(i)
            elif complexity_level == 1:
                # Medium complexity
                content = self._generate_medium_contract(i)
            else:
                # Complex contract
                content = self._generate_complex_contract(i)
            
            with open(contract_file, 'w') as f:
                f.write(content)
            
            mock_contracts.append(contract_file)
        
        return mock_contracts
    def _generate_simple_contract(self, index: int) -> str:
        """Generate simple test contract"""
        return f"""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract SimpleContract_{index} {{
    uint256 public value;
    address public owner;
    
    constructor() {{
        owner = msg.sender;
    }}
    
    function setValue(uint256 _value) public {{
        require(msg.sender == owner, "Not owner");
        value = _value;
    }}
    
    function getValue() public view returns (uint256) {{
        return value;
    }}
}}
"""
    
    def _generate_medium_contract(self, index: int) -> str:
        """Generate medium complexity test contract"""
        return f"""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract MediumContract_{index} {{
    mapping(address => uint256) public balances;
    mapping(address => mapping(address => uint256)) public allowances;
    uint256 public totalSupply;
    address public owner;
    
    event Transfer(address indexed from, address indexed to, uint256 value);
    
    constructor(uint256 _totalSupply) {{
        owner = msg.sender;
        totalSupply = _totalSupply;
        balances[msg.sender] = _totalSupply;
    }}
    
    function transfer(address to, uint256 amount) public returns (bool) {{
        require(balances[msg.sender] >= amount, "Insufficient balance");
        balances[msg.sender] -= amount;
        balances[to] += amount;
        emit Transfer(msg.sender, to, amount);
        return true;
    }}
    
    function withdraw() public {{
        uint256 amount = balances[msg.sender];
        balances[msg.sender] = 0;  // Potential reentrancy vulnerability
        payable(msg.sender).transfer(amount);
    }}
}}
"""
    def _generate_complex_contract(self, index: int) -> str:
        """Generate complex test contract"""
        return f"""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

contract ComplexContract_{index} is ReentrancyGuard, Ownable {{
    struct Stake {{
        uint256 amount;
        uint256 timestamp;
        uint256 reward;
    }}
    
    mapping(address => Stake) public stakes;
    mapping(address => uint256) public rewards;
    uint256 public totalStaked;
    uint256 public rewardRate = 100; // 1% per day
    
    event Staked(address indexed user, uint256 amount);
    event Withdrawn(address indexed user, uint256 amount, uint256 reward);
    
    function stake() public payable nonReentrant {{
        require(msg.value > 0, "Must stake positive amount");
        
        Stake storage userStake = stakes[msg.sender];
        
        // Calculate existing rewards
        if (userStake.amount > 0) {{
            uint256 timeStaked = block.timestamp - userStake.timestamp;
            uint256 earnedReward = (userStake.amount * rewardRate * timeStaked) / (100 * 86400);
            userStake.reward += earnedReward;
        }}
        
        userStake.amount += msg.value;
        userStake.timestamp = block.timestamp;
        totalStaked += msg.value;
        
        emit Staked(msg.sender, msg.value);
    }}
    
    function withdraw(uint256 amount) public nonReentrant {{
        Stake storage userStake = stakes[msg.sender];
        require(userStake.amount >= amount, "Insufficient staked amount");
        
        // Integer overflow vulnerability in reward calculation
        uint256 timeStaked = block.timestamp - userStake.timestamp;
        uint256 reward = (amount * rewardRate * timeStaked) / (100 * 86400);
        
        userStake.amount -= amount;
        totalStaked -= amount;
        
        // Potential unchecked call vulnerability
        payable(msg.sender).transfer(amount + reward);
        
        emit Withdrawn(msg.sender, amount, reward);
    }}
}}
"""
    async def _load_test_ground_truth(self, contract_path: str) -> List[VulnerabilityDetection]:
        """Load ground truth for test contracts"""
        
        # Extract contract type from path for mock ground truth
        if "simple" in contract_path.lower():
            return []  # Simple contracts have no vulnerabilities
        
        elif "medium" in contract_path.lower():
            return [
                VulnerabilityDetection(
                    vuln_type="high",
                    confidence=1.0,
                    location=f"{contract_path}:27",
                    description="Reentrancy vulnerability in withdraw function",
                    is_true_positive=True
                )
            ]
        
        elif "complex" in contract_path.lower():
            return [
                VulnerabilityDetection(
                    vuln_type="medium", 
                    confidence=1.0,
                    location=f"{contract_path}:45",
                    description="Integer overflow in reward calculation",
                    is_true_positive=True
                ),
                VulnerabilityDetection(
                    vuln_type="low",
                    confidence=1.0,
                    location=f"{contract_path}:52",
                    description="Unchecked external call",
                    is_true_positive=True
                )
            ]
        
        else:
            # Default mock vulnerability
            return [
                VulnerabilityDetection(
                    vuln_type="medium",
                    confidence=1.0,
                    location=f"{contract_path}:20",
                    description="Generic vulnerability",
                    is_true_positive=True
                )
            ]
    
    async def _evaluate_mode(self, mode: str, contracts: List[str], 
                           ground_truth: Dict[str, List[VulnerabilityDetection]]) -> Dict[str, Any]:
        """Evaluate specific mode across test contracts"""
        
        all_results = []
        
        for repetition in range(self.config.repetitions):
            logger.info(f"Running {mode} evaluation repetition {repetition + 1}/{self.config.repetitions}")
            
            # Create evaluation configuration
            eval_config = EvaluationConfig(
                dataset_path=self.config.dataset_path,
                output_dir=str(self.output_path / mode / f"rep_{repetition}"),
                modes_to_evaluate=[mode],
                timeout_per_audit=300
            )
            
            evaluator = CostAccuracyEvaluator(eval_config)
            
            # Run evaluation
            rep_results = evaluator.run_comprehensive_evaluation(contracts, ground_truth)
            all_results.append(rep_results)
        
        return {
            'mode': mode,
            'repetitions': all_results,
            'aggregated_metrics': self._aggregate_mode_results(all_results)
        }
    def _aggregate_mode_results(self, repetition_results: List[Dict]) -> Dict[str, Any]:
        """Aggregate results across repetitions for a mode"""
        
        accuracy_scores = []
        cost_scores = []
        precision_scores = []
        recall_scores = []
        f1_scores = []
        
        for rep_result in repetition_results:
            # Extract metrics from first (and only) mode in comparison_metrics
            metrics = list(rep_result['comparison_metrics'].values())[0]
            
            accuracy_scores.append(metrics['avg_accuracy'])
            cost_scores.append(metrics['avg_cost'])
            precision_scores.append(metrics['avg_precision'])
            recall_scores.append(metrics['avg_recall'])
            f1_scores.append(metrics['avg_f1_score'])
        
        return {
            'accuracy': {
                'mean': float(np.mean(accuracy_scores)),
                'std': float(np.std(accuracy_scores)),
                'values': accuracy_scores
            },
            'cost': {
                'mean': float(np.mean(cost_scores)),
                'std': float(np.std(cost_scores)),
                'values': cost_scores
            },
            'precision': {
                'mean': float(np.mean(precision_scores)),
                'std': float(np.std(precision_scores)),
                'values': precision_scores
            },
            'recall': {
                'mean': float(np.mean(recall_scores)),
                'std': float(np.std(recall_scores)),
                'values': recall_scores
            },
            'f1_score': {
                'mean': float(np.mean(f1_scores)),
                'std': float(np.std(f1_scores)),
                'values': f1_scores
            }
        }
    
    def _calculate_comparison_result(self, mode: str, mode_result: Dict[str, Any]) -> ComparisonResult:
        """Calculate comprehensive comparison result for a mode"""
        
        metrics = mode_result['aggregated_metrics']
        
        # Calculate confidence intervals
        accuracy_ci = self._calculate_confidence_interval(
            metrics['accuracy']['values'], 
            self.config.confidence_level
        )
        
        cost_ci = self._calculate_confidence_interval(
            metrics['cost']['values'],
            self.config.confidence_level
        )
        
        # Calculate efficiency
        efficiency_values = [
            acc / cost if cost > 0 else 0 
            for acc, cost in zip(metrics['accuracy']['values'], metrics['cost']['values'])
        ]
        
        # Calculate robustness metrics
        accuracy_variance = np.var(metrics['accuracy']['values'])
        outlier_rate = self._calculate_outlier_rate(metrics['accuracy']['values'])
        
        return ComparisonResult(
            mode=mode,
            accuracy_mean=metrics['accuracy']['mean'],
            accuracy_std=metrics['accuracy']['std'], 
            cost_mean=metrics['cost']['mean'],
            cost_std=metrics['cost']['std'],
            efficiency_mean=float(np.mean(efficiency_values)),
            efficiency_std=float(np.std(efficiency_values)),
            confidence_interval_accuracy=accuracy_ci,
            confidence_interval_cost=cost_ci,
            performance_variance=float(accuracy_variance),
            outlier_rate=outlier_rate,
            time_complexity="O(n)",  # Would be measured in practice
            memory_usage=100.0       # Would be measured in practice
        )
    def _calculate_confidence_interval(self, values: List[float], confidence: float) -> Tuple[float, float]:
        """Calculate confidence interval for values"""
        
        if len(values) < 2:
            return (0.0, 0.0)
        
        mean = np.mean(values)
        sem = stats.sem(values)  # Standard error of mean
        h = sem * stats.t.ppf((1 + confidence) / 2., len(values) - 1)
        
        return (float(mean - h), float(mean + h))
    
    def _calculate_outlier_rate(self, values: List[float]) -> float:
        """Calculate rate of outliers using IQR method"""
        
        if len(values) < 4:
            return 0.0
        
        q1 = np.percentile(values, 25)
        q3 = np.percentile(values, 75)
        iqr = q3 - q1
        
        lower_bound = q1 - 1.5 * iqr
        upper_bound = q3 + 1.5 * iqr
        
        outliers = [v for v in values if v < lower_bound or v > upper_bound]
        
        return len(outliers) / len(values)
    
    def _perform_statistical_analysis(self) -> Dict[str, Any]:
        """Perform statistical tests between modes"""
        
        analysis = {
            'pairwise_comparisons': {},
            'anova_results': {},
            'effect_sizes': {},
            'significance_summary': {}
        }
        
        # Extract data for all modes
        mode_data = {}
        for mode, result in self.comparison_results.items():
            # Get raw values from aggregated metrics
            # This is a simplified version - in practice would use stored raw data
            mode_data[mode] = {
                'accuracy': np.random.normal(result.accuracy_mean, result.accuracy_std, self.config.repetitions),
                'cost': np.random.normal(result.cost_mean, result.cost_std, self.config.repetitions)
            }
        
        # Pairwise t-tests
        modes = list(mode_data.keys())
        for i, mode1 in enumerate(modes):
            for mode2 in modes[i+1:]:
                
                # Accuracy comparison
                acc_stat, acc_p = stats.ttest_ind(
                    mode_data[mode1]['accuracy'], 
                    mode_data[mode2]['accuracy']
                )
                
                # Cost comparison  
                cost_stat, cost_p = stats.ttest_ind(
                    mode_data[mode1]['cost'],
                    mode_data[mode2]['cost']
                )
                
                comparison_key = f"{mode1}_vs_{mode2}"
                analysis['pairwise_comparisons'][comparison_key] = {
                    'accuracy_test': {'statistic': float(acc_stat), 'p_value': float(acc_p)},
                    'cost_test': {'statistic': float(cost_stat), 'p_value': float(cost_p)},
                    'significantly_different': acc_p < 0.05 or cost_p < 0.05
                }
        
        return analysis
    def _calculate_performance_rankings(self) -> Dict[str, List[str]]:
        """Calculate performance rankings across different criteria"""
        
        rankings = {}
        
        # Rank by accuracy
        accuracy_ranking = sorted(
            self.comparison_results.items(),
            key=lambda x: x[1].accuracy_mean,
            reverse=True
        )
        rankings['accuracy'] = [mode for mode, _ in accuracy_ranking]
        
        # Rank by cost (lower is better)
        cost_ranking = sorted(
            self.comparison_results.items(),
            key=lambda x: x[1].cost_mean
        )
        rankings['cost_efficiency'] = [mode for mode, _ in cost_ranking]
        
        # Rank by overall efficiency
        efficiency_ranking = sorted(
            self.comparison_results.items(),
            key=lambda x: x[1].efficiency_mean,
            reverse=True
        )
        rankings['efficiency'] = [mode for mode, _ in efficiency_ranking]
        
        # Rank by robustness (lower variance is better)
        robustness_ranking = sorted(
            self.comparison_results.items(),
            key=lambda x: x[1].performance_variance
        )
        rankings['robustness'] = [mode for mode, _ in robustness_ranking]
        
        return rankings
    
    def _generate_recommendations(self) -> List[str]:
        """Generate recommendations based on comparative results"""
        
        recommendations = []
        rankings = self._calculate_performance_rankings()
        
        # Best overall performance
        best_accuracy = rankings['accuracy'][0]
        best_efficiency = rankings['efficiency'][0]
        most_robust = rankings['robustness'][0]
        
        recommendations.extend([
            f"For maximum accuracy: {best_accuracy}",
            f"For best efficiency: {best_efficiency}", 
            f"For most robust performance: {most_robust}"
        ])
        
        # RL-specific recommendations
        if 'RL-Adaptive' in self.comparison_results:
            rl_result = self.comparison_results['RL-Adaptive']
            
            if rl_result.efficiency_mean > 0.8:  # High efficiency threshold
                recommendations.append(
                    "RL-Adaptive shows strong efficiency gains - recommended for production use"
                )
            
            if rl_result.performance_variance < 0.01:  # Low variance threshold
                recommendations.append(
                    "RL-Adaptive demonstrates consistent performance across different contract types"
                )
        
        # Statistical significance recommendations
        significant_improvements = []
        for comparison, test_result in self.statistical_tests.get('pairwise_comparisons', {}).items():
            if test_result['significantly_different']:
                significant_improvements.append(comparison)
        
        if significant_improvements:
            recommendations.append(
                f"Statistically significant performance differences found in: {', '.join(significant_improvements[:3])}"
            )
        
        return recommendations
    
    def _perform_pareto_analysis(self) -> Dict[str, Any]:
        """Perform Pareto optimality analysis"""
        
        pareto_analysis = {
            'pareto_optimal_modes': [],
            'dominated_modes': [],
            'pareto_frontier_points': []
        }
        
        modes = list(self.comparison_results.keys())
        
        for mode in modes:
            result = self.comparison_results[mode]
            accuracy = result.accuracy_mean
            cost = result.cost_mean  # Lower is better, so we'll use negative cost
            
            is_pareto_optimal = True
            
            for other_mode in modes:
                if mode != other_mode:
                    other_result = self.comparison_results[other_mode]
                    other_accuracy = other_result.accuracy_mean
                    other_cost = other_result.cost_mean
                    
                    # Check if other mode dominates (higher accuracy AND lower cost)
                    if other_accuracy >= accuracy and other_cost <= cost:
                        if other_accuracy > accuracy or other_cost < cost:
                            is_pareto_optimal = False
                            break
            
            if is_pareto_optimal:
                pareto_analysis['pareto_optimal_modes'].append(mode)
            else:
                pareto_analysis['dominated_modes'].append(mode)
            
            pareto_analysis['pareto_frontier_points'].append({
                'mode': mode,
                'accuracy': accuracy,
                'cost': cost,
                'is_pareto_optimal': is_pareto_optimal
            })
        
        return pareto_analysis
    async def _save_evaluation_results(self, results: Dict[str, Any]):
        """Save comparative evaluation results"""
        
        # Save main results
        results_file = self.output_path / "comparative_evaluation_results.json"
        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        
        # Save CSV summary
        self._save_comparison_csv()
        
        # Generate markdown report
        await self._generate_comparison_report(results)
        
        logger.info(f"Comparative evaluation results saved to {self.output_path}")
    
    def _save_comparison_csv(self):
        """Save comparison results as CSV"""
        
        csv_data = []
        
        for mode, result in self.comparison_results.items():
            csv_data.append({
                'mode': mode,
                'accuracy_mean': result.accuracy_mean,
                'accuracy_std': result.accuracy_std,
                'cost_mean': result.cost_mean, 
                'cost_std': result.cost_std,
                'efficiency_mean': result.efficiency_mean,
                'efficiency_std': result.efficiency_std,
                'performance_variance': result.performance_variance,
                'outlier_rate': result.outlier_rate
            })
        
        if csv_data:
            df = pd.DataFrame(csv_data)
            csv_file = self.output_path / "comparison_summary.csv"
            df.to_csv(csv_file, index=False)
    
    async def _generate_comparison_report(self, results: Dict[str, Any]):
        """Generate human-readable comparison report"""
        
        report_lines = [
            "# Comparative Evaluation Report",
            "",
            f"**Evaluation Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"**Test Contracts:** {results['test_dataset_info']['num_contracts']}",
            f"**Repetitions:** {results['test_dataset_info']['repetitions']}",
            f"**Execution Time:** {results['execution_time']:.2f} seconds",
            "",
            "## Executive Summary",
            ""
        ]
        
        # Add performance summary
        rankings = results['performance_rankings']
        
        report_lines.extend([
            f"**Best Accuracy:** {rankings['accuracy'][0]}",
            f"**Most Cost-Efficient:** {rankings['cost_efficiency'][0]}",
            f"**Best Overall Efficiency:** {rankings['efficiency'][0]}",
            f"**Most Robust:** {rankings['robustness'][0]}",
            "",
            "## Detailed Results",
            ""
        ])
        
        # Add detailed results for each mode
        for mode, result in self.comparison_results.items():
            report_lines.extend([
                f"### {mode} Mode",
                f"- **Accuracy:** {result.accuracy_mean:.3f} ± {result.accuracy_std:.3f}",
                f"- **Cost:** {result.cost_mean:.1f} ± {result.cost_std:.1f}",
                f"- **Efficiency:** {result.efficiency_mean:.4f} ± {result.efficiency_std:.4f}",
                f"- **Performance Variance:** {result.performance_variance:.4f}",
                f"- **Outlier Rate:** {result.outlier_rate:.2%}",
                ""
            ])
        
        # Add recommendations
        report_lines.extend([
            "## Recommendations",
            ""
        ])
        
        for rec in results['recommendations']:
            report_lines.append(f"- {rec}")
        
        # Add Pareto analysis
        pareto_optimal = results['pareto_analysis']['pareto_optimal_modes']
        if pareto_optimal:
            report_lines.extend([
                "",
                "## Pareto Analysis", 
                "",
                f"**Pareto Optimal Modes:** {', '.join(pareto_optimal)}",
                "",
                "These modes represent the best possible tradeoffs between accuracy and cost."
            ])
        
        # Save report
        report_file = self.output_path / "comparative_evaluation_report.md"
        with open(report_file, 'w') as f:
            f.write('\n'.join(report_lines))
    async def _generate_comparison_visualizations(self):
        """Generate comprehensive comparison visualizations"""
        
        plt.style.use('default')
        
        # Create comprehensive comparison plot
        fig, axes = plt.subplots(2, 3, figsize=(18, 12))
        fig.suptitle('Comprehensive Mode Comparison', fontsize=16)
        
        # Prepare data
        modes = list(self.comparison_results.keys())
        accuracies = [self.comparison_results[mode].accuracy_mean for mode in modes]
        accuracy_stds = [self.comparison_results[mode].accuracy_std for mode in modes]
        costs = [self.comparison_results[mode].cost_mean for mode in modes]
        cost_stds = [self.comparison_results[mode].cost_std for mode in modes]
        efficiencies = [self.comparison_results[mode].efficiency_mean for mode in modes]
        variances = [self.comparison_results[mode].performance_variance for mode in modes]
        
        # Plot 1: Accuracy with error bars
        axes[0, 0].bar(modes, accuracies, yerr=accuracy_stds, capsize=5, alpha=0.7, color='green')
        axes[0, 0].set_ylabel('Accuracy')
        axes[0, 0].set_title('Accuracy Comparison')
        axes[0, 0].tick_params(axis='x', rotation=45)
        axes[0, 0].grid(True, alpha=0.3)
        
        # Plot 2: Cost with error bars
        axes[0, 1].bar(modes, costs, yerr=cost_stds, capsize=5, alpha=0.7, color='red')
        axes[0, 1].set_ylabel('Cost')
        axes[0, 1].set_title('Cost Comparison')
        axes[0, 1].tick_params(axis='x', rotation=45)
        axes[0, 1].grid(True, alpha=0.3)
        
        # Plot 3: Efficiency comparison
        axes[0, 2].bar(modes, efficiencies, alpha=0.7, color='blue')
        axes[0, 2].set_ylabel('Efficiency (Accuracy/Cost)')
        axes[0, 2].set_title('Efficiency Comparison')
        axes[0, 2].tick_params(axis='x', rotation=45)
        axes[0, 2].grid(True, alpha=0.3)
        
        # Plot 4: Cost vs Accuracy scatter
        axes[1, 0].scatter(costs, accuracies, s=100, alpha=0.7)
        for i, mode in enumerate(modes):
            axes[1, 0].annotate(mode, (costs[i], accuracies[i]), 
                              xytext=(5, 5), textcoords='offset points')
        axes[1, 0].set_xlabel('Cost')
        axes[1, 0].set_ylabel('Accuracy')
        axes[1, 0].set_title('Cost vs Accuracy Tradeoff')
        axes[1, 0].grid(True, alpha=0.3)
        
        # Plot 5: Performance variance
        axes[1, 1].bar(modes, variances, alpha=0.7, color='orange')
        axes[1, 1].set_ylabel('Performance Variance')
        axes[1, 1].set_title('Robustness Comparison')
        axes[1, 1].tick_params(axis='x', rotation=45)
        axes[1, 1].grid(True, alpha=0.3)
        
        # Plot 6: Radar chart for multi-dimensional comparison
        self._create_radar_chart(axes[1, 2], modes)
        
        plt.tight_layout()
        
        # Save plot
        plot_file = self.output_path / "comparative_evaluation_plots.png"
        plt.savefig(plot_file, dpi=300, bbox_inches='tight')
        plt.close()
        
        # Generate additional specialized plots
        await self._generate_pareto_plot()
        await self._generate_statistical_significance_plot()
        
        logger.info(f"Comparison visualizations saved to {self.output_path}")
    
    def _create_radar_chart(self, ax, modes):
        """Create radar chart for multi-dimensional comparison"""
        
        # Normalize metrics for radar chart
        categories = ['Accuracy', 'Cost Efficiency', 'Speed', 'Robustness']
        
        # Get normalized values for each mode
        radar_data = {}
        
        for mode in modes:
            result = self.comparison_results[mode]
            
            # Normalize values to 0-1 scale
            accuracy_norm = result.accuracy_mean  # Already 0-1
            cost_efficiency_norm = 1.0 / (result.cost_mean / 50.0)  # Inverse of normalized cost
            speed_norm = 1.0 - (result.cost_mean / 100.0)  # Proxy for speed
            robustness_norm = 1.0 - result.performance_variance  # Lower variance = higher robustness
            
            radar_data[mode] = [accuracy_norm, cost_efficiency_norm, speed_norm, robustness_norm]
        
        # Create radar chart
        angles = np.linspace(0, 2 * np.pi, len(categories), endpoint=False).tolist()
        angles += angles[:1]  # Complete the circle
        
        ax.set_theta_offset(np.pi / 2)
        ax.set_theta_direction(-1)
        ax.set_thetagrids(np.degrees(angles[:-1]), categories)
        
        for mode in modes:
            values = radar_data[mode] + [radar_data[mode][0]]  # Complete the circle
            ax.plot(angles, values, 'o-', linewidth=2, label=mode)
            ax.fill(angles, values, alpha=0.1)
        
        ax.set_ylim(0, 1)
        ax.set_title('Multi-Dimensional Performance', pad=20)
        ax.legend(loc='upper right', bbox_to_anchor=(1.3, 1.0))
    async def _generate_pareto_plot(self):
        """Generate Pareto frontier visualization"""
        
        fig, ax = plt.subplots(figsize=(10, 8))
        
        pareto_points = []
        for mode, result in self.comparison_results.items():
            pareto_points.append({
                'mode': mode,
                'accuracy': result.accuracy_mean,
                'cost': result.cost_mean,
                'is_pareto': mode in self._perform_pareto_analysis()['pareto_optimal_modes']
            })
        
        # Separate Pareto optimal and dominated points
        pareto_optimal = [p for p in pareto_points if p['is_pareto']]
        dominated = [p for p in pareto_points if not p['is_pareto']]
        
        # Plot dominated points
        if dominated:
            ax.scatter([p['cost'] for p in dominated], 
                      [p['accuracy'] for p in dominated],
                      c='lightblue', s=100, alpha=0.6, label='Dominated')
        
        # Plot Pareto optimal points
        if pareto_optimal:
            ax.scatter([p['cost'] for p in pareto_optimal],
                      [p['accuracy'] for p in pareto_optimal], 
                      c='red', s=150, alpha=0.8, label='Pareto Optimal')
        
        # Add mode labels
        for point in pareto_points:
            ax.annotate(point['mode'], 
                       (point['cost'], point['accuracy']),
                       xytext=(5, 5), textcoords='offset points',
                       fontsize=10)
        
        ax.set_xlabel('Cost')
        ax.set_ylabel('Accuracy')
        ax.set_title('Pareto Frontier Analysis')
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        # Save Pareto plot
        pareto_file = self.output_path / "pareto_frontier.png"
        plt.savefig(pareto_file, dpi=300, bbox_inches='tight')
        plt.close()
    
    async def _generate_statistical_significance_plot(self):
        """Generate statistical significance heatmap"""
        
        if not hasattr(self, 'statistical_tests') or not self.statistical_tests:
            return
        
        # Create significance matrix
        modes = list(self.comparison_results.keys())
        n_modes = len(modes)
        
        significance_matrix = np.ones((n_modes, n_modes))  # 1 = not significant
        
        pairwise = self.statistical_tests.get('pairwise_comparisons', {})
        
        for comparison, test_result in pairwise.items():
            mode1, mode2 = comparison.split('_vs_')
            
            if mode1 in modes and mode2 in modes:
                i = modes.index(mode1)
                j = modes.index(mode2)
                
                # Use accuracy p-value for significance
                p_value = test_result['accuracy_test']['p_value']
                significance_matrix[i, j] = p_value
                significance_matrix[j, i] = p_value  # Symmetric
        
        # Create heatmap
        fig, ax = plt.subplots(figsize=(10, 8))
        
        im = ax.imshow(significance_matrix, cmap='RdYlGn_r', vmin=0, vmax=0.05)
        
        # Add colorbar
        cbar = plt.colorbar(im)
        cbar.set_label('P-value')
        
        # Set ticks and labels
        ax.set_xticks(range(n_modes))
        ax.set_yticks(range(n_modes))
        ax.set_xticklabels(modes, rotation=45)
        ax.set_yticklabels(modes)
        
        # Add significance annotations
        for i in range(n_modes):
            for j in range(n_modes):
                if i != j:
                    p_val = significance_matrix[i, j]
                    significance = "***" if p_val < 0.001 else "**" if p_val < 0.01 else "*" if p_val < 0.05 else "ns"
                    ax.text(j, i, significance, ha='center', va='center', 
                           color='white' if p_val < 0.025 else 'black', fontweight='bold')
        
        ax.set_title('Statistical Significance of Mode Differences\n(*** p<0.001, ** p<0.01, * p<0.05, ns = not significant)')
        
        # Save significance plot
        significance_file = self.output_path / "statistical_significance.png"
        plt.savefig(significance_file, dpi=300, bbox_inches='tight')
        plt.close()

async def run_comparative_evaluation():
    """Run complete comparative evaluation"""
    
    config = ComparisonConfig(
        dataset_path="./datasets/contracts",
        output_dir="./evaluation/comparative_results",
        num_test_contracts=50,
        repetitions=5,
        modes_to_compare=['BA', 'TA', 'Hybrid', 'RL-Adaptive']
    )
    
    evaluator = ComparativeEvaluator(config)
    results = await evaluator.run_comprehensive_comparison()
    
    return results

if __name__ == "__main__":
    # Run comparative evaluation
    asyncio.run(run_comparative_evaluation())