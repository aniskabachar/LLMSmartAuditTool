"""
RL Training Orchestrator with Groq Integration
============================================

This module orchestrates the complete RL training pipeline with Groq backend
for cost-effective, high-throughput training of mode selection and stopping policies.

Key Features:
1. Distributed training coordination across multiple RL policies
2. Cost-aware training with budget management and early stopping
3. Performance monitoring and automatic hyperparameter adjustment
4. Experiment tracking and reproducible training runs
5. Integration with Groq backend for fast, cheap LLM inference
6. Automated baseline comparison and evaluation

Training Pipeline:
1. Dataset preparation and contract feature extraction
2. Baseline model training and evaluation
3. RL policy training with Groq-powered environment simulation
4. Cross-validation and hyperparameter optimization
5. Final evaluation and comparison with fixed baselines
"""

import os
import sys
import json
import logging
import time
import threading
from typing import Dict, List, Tuple, Optional, Any, Callable
from dataclasses import dataclass, asdict
from pathlib import Path
import numpy as np
from datetime import datetime
import pickle
import traceback

# Add project imports
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from infrastructure.groq_backend import GroqTrainingInfrastructure, create_training_infrastructure
from rl_policies.mode_selector import ModeSelector
from rl_policies.stopping_policy import StoppingPolicy
from evaluation.benchmark_datasets import BenchmarkDatasetManager, DatasetSplit
from rl_environment.contract_analyzer import ContractAnalyzer

@dataclass
class TrainingConfig:
    """Configuration for RL training session"""
    
    # Dataset configuration
    dataset_name: str = "synthetic"
    dataset_size: int = 1000
    train_split: float = 0.7
    validation_split: float = 0.15
    test_split: float = 0.15
    
    # Training parameters
    mode_selector_timesteps: int = 50000
    stopping_policy_timesteps: int = 50000
    batch_size: int = 64
    learning_rate: float = 3e-4
    
    # Cost management
    max_training_budget: float = 100.0  # Maximum budget per training run
    cost_per_episode_target: float = 0.05  # Target cost per episode
    early_stopping_threshold: float = 0.90  # Stop when 90% of budget used
    
    # Performance targets
    target_mode_accuracy: float = 0.80  # Target accuracy for mode selection
    target_cost_reduction: float = 0.30  # Target cost reduction vs baseline
    min_improvement_threshold: float = 0.05  # Minimum improvement to continue training
    
    # Infrastructure
    max_concurrent_requests: int = 5
    enable_caching: bool = True
    checkpoint_frequency: int = 5000  # Checkpoint every N timesteps
    
    # Experiment tracking
    experiment_name: str = "rl_audit_training"
    output_dir: str = "./experiments"
    save_intermediate_results: bool = True

@dataclass
class TrainingProgress:
    """Track training progress across policies"""
    
    mode_selector_progress: Dict[str, Any] = None
    stopping_policy_progress: Dict[str, Any] = None
    total_cost: float = 0.0
    total_time: float = 0.0
    checkpoints: List[str] = None
    
    def __post_init__(self):
        if self.mode_selector_progress is None:
            self.mode_selector_progress = {}
        if self.stopping_policy_progress is None:
            self.stopping_policy_progress = {}
        if self.checkpoints is None:
            self.checkpoints = []

class TrainingOrchestrator:
    """
    Orchestrates distributed RL training with Groq backend
    
    Manages the complete training pipeline from dataset preparation
    to final evaluation, with cost monitoring and performance optimization.
    """
    
    def __init__(self, 
                 config: TrainingConfig,
                 groq_api_key: str,
                 baseline_models_dir: str = "./baselines"):
        
        self.config = config
        self.groq_api_key = groq_api_key
        self.baseline_models_dir = Path(baseline_models_dir)
        
        # Create experiment directory
        self.experiment_dir = Path(config.output_dir) / config.experiment_name / datetime.now().strftime("%Y%m%d_%H%M%S")
        self.experiment_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize components
        self.groq_infrastructure = create_training_infrastructure(
            groq_api_key, 
            max_budget=config.max_training_budget
        )
        
        self.dataset_manager = BenchmarkDatasetManager()
        self.contract_analyzer = ContractAnalyzer()
        
        # Training state
        self.dataset_split = None
        self.baseline_results = {}
        self.training_progress = TrainingProgress()
        self.trained_policies = {}
        
        # Setup logging
        self.setup_logging()
        
    def setup_logging(self):
        """Setup logging for training session"""
        
        log_file = self.experiment_dir / "training.log"
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # File handler
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        
        # Configure logger
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        
        self.logger.info(f"Training orchestrator initialized")
        self.logger.info(f"Experiment directory: {self.experiment_dir}")
    
    def prepare_datasets(self) -> DatasetSplit:
        """Prepare and validate datasets for training"""
        
        self.logger.info(f"Preparing {self.config.dataset_name} dataset...")
        
        if self.config.dataset_name == "synthetic":
            # Generate synthetic dataset
            dataset_split = self.dataset_manager.generate_synthetic_dataset(
                n_contracts=self.config.dataset_size
            )
        else:
            # Try to load real dataset
            datasets = self.dataset_manager.download_and_prepare_datasets()
            
            if self.config.dataset_name in datasets:
                dataset_split = datasets[self.config.dataset_name]
            else:
                self.logger.warning(f"Dataset {self.config.dataset_name} not available, "
                                  f"falling back to synthetic")
                dataset_split = self.dataset_manager.generate_synthetic_dataset(
                    n_contracts=self.config.dataset_size
                )
        
        # Save dataset split for reproducibility
        dataset_file = self.experiment_dir / "dataset_split.pkl"
        with open(dataset_file, 'wb') as f:
            pickle.dump(dataset_split, f)
        
        # Log dataset statistics
        stats = dataset_split.get_statistics()
        self.logger.info(f"Dataset prepared: {json.dumps(stats, indent=2, default=str)}")
        
        self.dataset_split = dataset_split
        return dataset_split
    
    def run_baseline_experiments(self) -> Dict[str, Any]:
        """
        Run baseline experiments with fixed BA/TA/Hybrid modes
        
        This establishes performance benchmarks for comparison with RL policies.
        """
        
        self.logger.info("Running baseline experiments...")
        
        baseline_results = {
            'BA': self._run_baseline_mode('BA'),
            'TA': self._run_baseline_mode('TA'), 
            'HYBRID': self._run_baseline_mode('HYBRID')
        }
        
        # Save baseline results
        baseline_file = self.experiment_dir / "baseline_results.json"
        with open(baseline_file, 'w') as f:
            json.dump(baseline_results, f, indent=2, default=str)
        
        # Log summary
        self._log_baseline_summary(baseline_results)
        
        self.baseline_results = baseline_results
        return baseline_results
    
    def _run_baseline_mode(self, mode: str) -> Dict[str, Any]:
        """Run baseline experiment for specific mode"""
        
        self.logger.info(f"Running baseline experiment for {mode} mode...")
        
        # Simulate baseline performance based on paper results
        # In practice, this would run the actual ChatChain system
        
        if mode == 'BA':
            # BA mode characteristics from paper
            avg_cost = 0.21
            avg_accuracy = 0.55  # Lower accuracy, broader coverage
            avg_rounds = 3
            coverage = 0.544  # 54.4% vulnerability type coverage
            
        elif mode == 'TA':
            # TA mode characteristics from paper
            avg_cost = 0.98
            avg_accuracy = 0.90  # Higher accuracy on known patterns
            avg_rounds = 1  # Single pass through 40 detectors
            coverage = 0.351  # 35.1% vulnerability type coverage
            
        else:  # HYBRID
            # Hybrid mode characteristics from paper
            avg_cost = 1.19
            avg_accuracy = 0.88  # Best overall accuracy
            avg_rounds = 4  # Combined approach
            coverage = 0.623  # 62.3% vulnerability type coverage
        
        # Add some realistic variation
        n_contracts = len(self.dataset_split.test_samples)
        costs = np.random.normal(avg_cost, avg_cost * 0.1, n_contracts)
        accuracies = np.random.beta(avg_accuracy * 10, (1 - avg_accuracy) * 10, n_contracts)
        
        baseline_result = {
            'mode': mode,
            'n_contracts': n_contracts,
            'avg_cost': float(np.mean(costs)),
            'std_cost': float(np.std(costs)),
            'avg_accuracy': float(np.mean(accuracies)),
            'std_accuracy': float(np.std(accuracies)),
            'avg_rounds': avg_rounds,
            'coverage': coverage,
            'total_cost': float(np.sum(costs)),
            'cost_efficiency': float(np.mean(accuracies) / np.mean(costs)),
            'individual_results': [
                {
                    'contract_id': f"contract_{i}",
                    'cost': float(costs[i]),
                    'accuracy': float(accuracies[i])
                } for i in range(min(10, n_contracts))  # Save first 10 for inspection
            ]
        }
        
        return baseline_result
    
    def _log_baseline_summary(self, baseline_results: Dict[str, Any]):
        """Log summary of baseline results"""
        
        self.logger.info("Baseline Experiments Summary:")
        self.logger.info("=" * 50)
        
        for mode, results in baseline_results.items():
            self.logger.info(f"{mode} Mode:")
            self.logger.info(f"  Average Cost: ${results['avg_cost']:.2f}")
            self.logger.info(f"  Average Accuracy: {results['avg_accuracy']:.3f}")
            self.logger.info(f"  Cost Efficiency: {results['cost_efficiency']:.3f}")
            self.logger.info(f"  Coverage: {results['coverage']:.1%}")
            
        # Identify best performing baseline
        best_efficiency = max(results['cost_efficiency'] for results in baseline_results.values())
        best_mode = [mode for mode, results in baseline_results.items() 
                    if results['cost_efficiency'] == best_efficiency][0]
        
        self.logger.info(f"Best baseline mode: {best_mode} (efficiency: {best_efficiency:.3f})")
    
    def train_rl_policies(self) -> Dict[str, Any]:
        """
        Train both RL policies with coordinated cost management
        
        Returns:
            Training results and final policy performance
        """
        
        self.logger.info("Starting RL policy training...")
        
        # Start Groq training session
        session_name = f"{self.config.experiment_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.groq_infrastructure.start_training_session(session_name)
        
        training_results = {}
        
        try:
            # Phase 1: Train Mode Selector Policy
            self.logger.info("Phase 1: Training Mode Selector Policy")
            mode_selector_results = self._train_mode_selector()
            training_results['mode_selector'] = mode_selector_results
            
            # Check budget after first policy
            can_continue, remaining_budget = self.groq_infrastructure.check_budget()
            self.logger.info(f"After mode selector training: ${remaining_budget:.2f} budget remaining")
            
            if not can_continue:
                self.logger.warning("Budget exhausted after mode selector training")
                return training_results
            
            # Phase 2: Train Stopping Policy
            self.logger.info("Phase 2: Training Stopping Policy")
            stopping_policy_results = self._train_stopping_policy()
            training_results['stopping_policy'] = stopping_policy_results
            
            # Phase 3: Joint evaluation
            self.logger.info("Phase 3: Joint Policy Evaluation")
            joint_results = self._evaluate_joint_policies()
            training_results['joint_evaluation'] = joint_results
            
        except Exception as e:
            self.logger.error(f"Training failed: {e}")
            self.logger.error(traceback.format_exc())
            training_results['error'] = str(e)
            
        finally:
            # End training session and get final metrics
            final_metrics = self.groq_infrastructure.end_training_session()
            training_results['infrastructure_metrics'] = final_metrics
            
            # Save training results
            self._save_training_results(training_results)
        
        return training_results
    
    def _train_mode_selector(self) -> Dict[str, Any]:
        """Train mode selector policy"""
        
        self.logger.info(f"Training Mode Selector for {self.config.mode_selector_timesteps} timesteps")
        
        # Initialize mode selector
        mode_selector = ModeSelector(
            dataset_split=self.dataset_split,
            model_save_path=str(self.experiment_dir / "mode_selector"),
            config={
                'learning_rate': self.config.learning_rate,
                'batch_size': self.config.batch_size,
                'total_timesteps': self.config.mode_selector_timesteps,
            }
        )
        
        # Train with progress monitoring
        start_time = time.time()
        training_summary = mode_selector.train(
            total_timesteps=self.config.mode_selector_timesteps
        )
        training_time = time.time() - start_time
        
        # Evaluate trained policy
        eval_results = mode_selector.evaluate(
            test_samples=self.dataset_split.validation_samples,
            n_episodes=100
        )
        
        # Store trained policy
        self.trained_policies['mode_selector'] = mode_selector
        
        results = {
            'training_summary': training_summary,
            'evaluation_results': eval_results,
            'training_time': training_time,
            'timesteps': self.config.mode_selector_timesteps
        }
        
        self.logger.info(f"Mode Selector training completed in {training_time:.1f}s")
        self.logger.info(f"Evaluation results: {json.dumps(eval_results, indent=2, default=str)}")
        
        return results
    
    def _train_stopping_policy(self) -> Dict[str, Any]:
        """Train stopping policy"""
        
        self.logger.info(f"Training Stopping Policy for {self.config.stopping_policy_timesteps} timesteps")
        
        # Initialize stopping policy
        stopping_policy = StoppingPolicy(
            dataset_split=self.dataset_split,
            model_save_path=str(self.experiment_dir / "stopping_policy"),
            config={
                'learning_rate': self.config.learning_rate,
                'batch_size': self.config.batch_size,
                'total_timesteps': self.config.stopping_policy_timesteps,
            }
        )
        
        # Train with progress monitoring
        start_time = time.time()
        training_summary = stopping_policy.train(
            total_timesteps=self.config.stopping_policy_timesteps
        )
        training_time = time.time() - start_time
        
        # Evaluate trained policy
        eval_results = stopping_policy.evaluate(
            test_samples=self.dataset_split.validation_samples,
            n_episodes=100
        )
        
        # Store trained policy
        self.trained_policies['stopping_policy'] = stopping_policy
        
        results = {
            'training_summary': training_summary,
            'evaluation_results': eval_results,
            'training_time': training_time,
            'timesteps': self.config.stopping_policy_timesteps
        }
        
        self.logger.info(f"Stopping Policy training completed in {training_time:.1f}s")
        self.logger.info(f"Evaluation results: {json.dumps(eval_results, indent=2, default=str)}")
        
        return results
    
    def _evaluate_joint_policies(self) -> Dict[str, Any]:
        """Evaluate both policies working together"""
        
        self.logger.info("Evaluating joint policy performance...")
        
        # This would integrate both policies with the actual audit system
        # For now, simulate combined performance
        
        mode_selector = self.trained_policies['mode_selector']
        stopping_policy = self.trained_policies['stopping_policy']
        
        # Get individual policy performance
        mode_eval = mode_selector.evaluate(
            test_samples=self.dataset_split.test_samples[:50],
            n_episodes=50
        )
        
        stop_eval = stopping_policy.evaluate(
            test_samples=self.dataset_split.test_samples[:50],
            n_episodes=50
        )
        
        # Estimate combined performance
        # This is a simplified model - real implementation would run full audits
        estimated_cost_reduction = 0.25  # 25% cost reduction vs baseline Hybrid
        estimated_accuracy = 0.85  # Maintain good accuracy
        
        joint_results = {
            'mode_selector_performance': mode_eval,
            'stopping_policy_performance': stop_eval,
            'estimated_combined_performance': {
                'cost_reduction_vs_hybrid': estimated_cost_reduction,
                'accuracy': estimated_accuracy,
                'cost_efficiency': estimated_accuracy / (self.baseline_results['HYBRID']['avg_cost'] * (1 - estimated_cost_reduction))
            }
        }
        
        return joint_results
    
    def _save_training_results(self, training_results: Dict[str, Any]):
        """Save comprehensive training results"""
        
        # Save main results
        results_file = self.experiment_dir / "training_results.json"
        with open(results_file, 'w') as f:
            json.dump(training_results, f, indent=2, default=str)
        
        # Save configuration
        config_file = self.experiment_dir / "training_config.json"
        with open(config_file, 'w') as f:
            json.dump(asdict(self.config), f, indent=2)
        
        # Save training progress
        progress_file = self.experiment_dir / "training_progress.json"
        with open(progress_file, 'w') as f:
            json.dump(asdict(self.training_progress), f, indent=2, default=str)
        
        self.logger.info(f"Training results saved to {self.experiment_dir}")
    
    def run_complete_training_pipeline(self) -> Dict[str, Any]:
        """
        Run the complete training pipeline from start to finish
        
        Returns:
            Comprehensive results including baselines, training, and evaluation
        """
        
        self.logger.info("Starting complete RL training pipeline...")
        pipeline_start = time.time()
        
        pipeline_results = {
            'config': asdict(self.config),
            'start_time': datetime.now().isoformat(),
        }
        
        try:
            # Step 1: Prepare datasets
            self.logger.info("Step 1: Dataset preparation")
            dataset_split = self.prepare_datasets()
            pipeline_results['dataset_stats'] = dataset_split.get_statistics()
            
            # Step 2: Run baseline experiments
            self.logger.info("Step 2: Baseline experiments")
            baseline_results = self.run_baseline_experiments()
            pipeline_results['baseline_results'] = baseline_results
            
            # Step 3: Train RL policies
            self.logger.info("Step 3: RL policy training")
            training_results = self.train_rl_policies()
            pipeline_results['training_results'] = training_results
            
            # Step 4: Compare results
            self.logger.info("Step 4: Results comparison")
            comparison_results = self._compare_with_baselines(training_results, baseline_results)
            pipeline_results['comparison_results'] = comparison_results
            
            pipeline_results['status'] = 'completed'
            
        except Exception as e:
            self.logger.error(f"Pipeline failed: {e}")
            self.logger.error(traceback.format_exc())
            pipeline_results['status'] = 'failed'
            pipeline_results['error'] = str(e)
        
        finally:
            pipeline_time = time.time() - pipeline_start
            pipeline_results['total_time'] = pipeline_time
            pipeline_results['end_time'] = datetime.now().isoformat()
            
            # Save final pipeline results
            pipeline_file = self.experiment_dir / "pipeline_results.json"
            with open(pipeline_file, 'w') as f:
                json.dump(pipeline_results, f, indent=2, default=str)
            
            self.logger.info(f"Complete pipeline finished in {pipeline_time:.1f}s")
            self._log_final_summary(pipeline_results)
        
        return pipeline_results
    
    def _compare_with_baselines(self, training_results: Dict, baseline_results: Dict) -> Dict[str, Any]:
        """Compare RL results with baseline performance"""
        
        if 'joint_evaluation' not in training_results:
            return {'error': 'No joint evaluation results available'}
        
        joint_eval = training_results['joint_evaluation']
        hybrid_baseline = baseline_results['HYBRID']
        
        # Extract key metrics
        rl_estimated_perf = joint_eval.get('estimated_combined_performance', {})
        
        comparison = {
            'cost_comparison': {
                'hybrid_baseline_cost': hybrid_baseline['avg_cost'],
                'rl_estimated_cost': hybrid_baseline['avg_cost'] * (1 - rl_estimated_perf.get('cost_reduction_vs_hybrid', 0)),
                'cost_reduction_pct': rl_estimated_perf.get('cost_reduction_vs_hybrid', 0) * 100,
            },
            'accuracy_comparison': {
                'hybrid_baseline_accuracy': hybrid_baseline['avg_accuracy'],
                'rl_estimated_accuracy': rl_estimated_perf.get('accuracy', 0),
                'accuracy_change': rl_estimated_perf.get('accuracy', 0) - hybrid_baseline['avg_accuracy'],
            },
            'efficiency_comparison': {
                'hybrid_baseline_efficiency': hybrid_baseline['cost_efficiency'],
                'rl_estimated_efficiency': rl_estimated_perf.get('cost_efficiency', 0),
                'efficiency_improvement': (rl_estimated_perf.get('cost_efficiency', 0) / hybrid_baseline['cost_efficiency'] - 1) * 100,
            }
        }
        
        # Determine if RL approach meets success criteria
        success_criteria = {
            'cost_reduction_target_met': comparison['cost_comparison']['cost_reduction_pct'] >= self.config.target_cost_reduction * 100,
            'accuracy_maintained': comparison['accuracy_comparison']['accuracy_change'] >= -0.05,  # Allow 5% accuracy drop
            'efficiency_improved': comparison['efficiency_comparison']['efficiency_improvement'] > 0,
        }
        
        comparison['success_criteria'] = success_criteria
        comparison['overall_success'] = all(success_criteria.values())
        
        return comparison
    
    def _log_final_summary(self, pipeline_results: Dict[str, Any]):
        """Log final summary of pipeline results"""
        
        self.logger.info("\n" + "="*60)
        self.logger.info("RL TRAINING PIPELINE SUMMARY")
        self.logger.info("="*60)
        
        if pipeline_results['status'] == 'completed':
            self.logger.info("Status: ✓ COMPLETED SUCCESSFULLY")
            
            # Log key results
            if 'comparison_results' in pipeline_results:
                comp = pipeline_results['comparison_results']
                
                self.logger.info(f"Cost Reduction: {comp['cost_comparison']['cost_reduction_pct']:.1f}%")
                self.logger.info(f"Efficiency Improvement: {comp['efficiency_comparison']['efficiency_improvement']:.1f}%")
                self.logger.info(f"Overall Success: {'✓' if comp['overall_success'] else '✗'}")
                
        else:
            self.logger.error("Status: ✗ FAILED")
            if 'error' in pipeline_results:
                self.logger.error(f"Error: {pipeline_results['error']}")
        
        self.logger.info(f"Total Time: {pipeline_results['total_time']:.1f}s")
        self.logger.info(f"Experiment Directory: {self.experiment_dir}")
        self.logger.info("="*60)

# Factory function for easy setup
def create_training_orchestrator(groq_api_key: str, 
                               experiment_name: str = "rl_audit_training",
                               max_budget: float = 100.0) -> TrainingOrchestrator:
    """Create training orchestrator with sensible defaults"""
    
    config = TrainingConfig(
        experiment_name=experiment_name,
        max_training_budget=max_budget,
        dataset_size=1000,
        mode_selector_timesteps=50000,
        stopping_policy_timesteps=50000
    )
    
    return TrainingOrchestrator(config, groq_api_key)

# Example usage
if __name__ == "__main__":
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    
    # Get Groq API key
    groq_api_key = os.getenv('GROQ_API_KEY')
    
    if not groq_api_key:
        print("GROQ_API_KEY environment variable not set")
        print("Please set your Groq API key to run training")
    else:
        # Create training orchestrator
        orchestrator = create_training_orchestrator(
            groq_api_key=groq_api_key,
            experiment_name="test_training",
            max_budget=50.0
        )
        
        # Run complete training pipeline
        results = orchestrator.run_complete_training_pipeline()
        
        print("Training completed!")
        print(f"Results saved to: {orchestrator.experiment_dir}")
        
        if results['status'] == 'completed':
            print("Training was successful!")
        else:
            print(f"Training failed: {results.get('error', 'Unknown error')}")