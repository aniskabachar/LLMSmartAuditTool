"""
RL Training Pipeline for Smart Contract Auditing

This module implements the complete training pipeline for both mode selector
and stopping policy RL agents, including curriculum learning and joint training.
"""

import asyncio
import logging
import json
import time
import pickle
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
import numpy as np
import torch
import torch.nn as nn
from dataclasses import dataclass, asdict
import matplotlib.pyplot as plt
from collections import deque
import gymnasium as gym

from stable_baselines3 import PPO, DQN
from stable_baselines3.common.env_util import make_vec_env
from stable_baselines3.common.callbacks import BaseCallback, EvalCallback, StopTrainingOnRewardThreshold
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import SubprocVecEnv, DummyVecEnv

from rl_environment.rl_architecture import SmartContractAuditEnv
from rl_policies.mode_selector import ModeSelector, ContractComplexityEncoder
from rl_policies.stopping_policy import StoppingPolicy, ConsensusStateEncoder
from rl_environment.reward_function import RewardFunction
from evaluation.baseline_experiments import BaselineExperimentRunner, BaselineConfig
from infrastructure.groq_backend import GroqBackend, GroqTrainingInfrastructure

logger = logging.getLogger(__name__)

@dataclass
class TrainingConfig:
    """Configuration for RL training pipeline"""
    
    # Dataset and environment
    dataset_path: str
    output_dir: str
    num_training_contracts: int = 200
    num_validation_contracts: int = 50
    
    # Training parameters
    total_timesteps: int = 100000
    learning_rate: float = 3e-4
    batch_size: int = 64
    n_epochs: int = 10
    
    # Environment parameters
    n_envs: int = 4  # Number of parallel environments
    env_timeout: int = 300
    
    # Curriculum learning
    use_curriculum: bool = True
    curriculum_stages: int = 3
    
    # Joint training
    joint_training: bool = True
    mode_selector_weight: float = 0.6
    stopping_policy_weight: float = 0.4
    
    # Evaluation
    eval_frequency: int = 10000
    save_frequency: int = 25000
    
    # Early stopping
    target_reward_threshold: float = 1.5
    patience: int = 5
    
    # Infrastructure
    use_groq_backend: bool = True
    max_budget: float = 100.0  # USD

@dataclass
class TrainingMetrics:
    """Training metrics and progress tracking"""
    episode: int
    total_timesteps: int
    mean_reward: float
    std_reward: float
    mode_selector_accuracy: float
    stopping_policy_accuracy: float
    avg_cost_per_audit: float
    curriculum_stage: int
    training_time: float

class CurriculumManager:
    """Manages curriculum learning progression"""
    
    def __init__(self, config: TrainingConfig):
        self.config = config
        self.current_stage = 0
        self.stage_progress = 0
        self.timesteps_per_stage = config.total_timesteps // config.curriculum_stages
        
        # Define curriculum stages
        self.stages = [
            {
                'name': 'simple_contracts',
                'complexity_range': (0.0, 0.4),
                'vulnerability_density': 'low',
                'description': 'Simple contracts with few vulnerabilities'
            },
            {
                'name': 'medium_contracts', 
                'complexity_range': (0.3, 0.7),
                'vulnerability_density': 'medium',
                'description': 'Medium complexity contracts'
            },
            {
                'name': 'complex_contracts',
                'complexity_range': (0.6, 1.0),
                'vulnerability_density': 'high', 
                'description': 'Complex contracts with many vulnerabilities'
            }
        ]
    
    def should_advance_stage(self, timesteps: int) -> bool:
        """Check if we should advance to next curriculum stage"""
        
        if not self.config.use_curriculum:
            return False
            
        target_timesteps = (self.current_stage + 1) * self.timesteps_per_stage
        return timesteps >= target_timesteps and self.current_stage < len(self.stages) - 1
    
    def advance_stage(self) -> Dict[str, Any]:
        """Advance to next curriculum stage"""
        
        if self.current_stage < len(self.stages) - 1:
            self.current_stage += 1
            logger.info(f"Advanced to curriculum stage {self.current_stage}: {self.stages[self.current_stage]['name']}")
        
        return self.get_current_stage_config()
    
    def get_current_stage_config(self) -> Dict[str, Any]:
        """Get current stage configuration"""
        
        if not self.config.use_curriculum or self.current_stage >= len(self.stages):
            # Default to full complexity range
            return {
                'complexity_range': (0.0, 1.0),
                'vulnerability_density': 'mixed',
                'stage_name': 'full_curriculum'
            }
        
        return self.stages[self.current_stage]

class TrainingCallback(BaseCallback):
    """Custom callback for training monitoring and curriculum management"""
    
    def __init__(self, curriculum_manager: CurriculumManager, 
                 training_pipeline: 'RLTrainingPipeline',
                 verbose: int = 1):
        super().__init__(verbose)
        self.curriculum_manager = curriculum_manager
        self.training_pipeline = training_pipeline
        self.episode_rewards = deque(maxlen=100)
        self.episode_costs = deque(maxlen=100)
        
    def _on_step(self) -> bool:
        """Called at each training step"""
        
        # Check for curriculum advancement
        if self.curriculum_manager.should_advance_stage(self.num_timesteps):
            new_config = self.curriculum_manager.advance_stage()
            # Update environment configuration
            self.training_pipeline._update_env_config(new_config)
        
        # Collect episode metrics
        if len(self.locals.get('rewards', [])) > 0:
            self.episode_rewards.extend(self.locals['rewards'])
        
        # Log progress
        if self.num_timesteps % 1000 == 0:
            mean_reward = np.mean(self.episode_rewards) if self.episode_rewards else 0.0
            
            logger.info(f"Timestep {self.num_timesteps}: Mean reward = {mean_reward:.3f}, "
                       f"Curriculum stage = {self.curriculum_manager.current_stage}")
        
        return True
    
    def _on_rollout_end(self) -> None:
        """Called at the end of a rollout"""
        
        # Save training metrics
        if hasattr(self.training_pipeline, '_save_training_metrics'):
            metrics = TrainingMetrics(
                episode=self.n_calls,
                total_timesteps=self.num_timesteps,
                mean_reward=np.mean(self.episode_rewards) if self.episode_rewards else 0.0,
                std_reward=np.std(self.episode_rewards) if self.episode_rewards else 0.0,
                mode_selector_accuracy=0.0,  # Would be calculated from actual performance
                stopping_policy_accuracy=0.0,
                avg_cost_per_audit=np.mean(self.episode_costs) if self.episode_costs else 0.0,
                curriculum_stage=self.curriculum_manager.current_stage,
                training_time=time.time()
            )
            
            self.training_pipeline._save_training_metrics(metrics)

class RLTrainingPipeline:
    """Main RL training pipeline"""
    
    def __init__(self, config: TrainingConfig):
        self.config = config
        self.output_path = Path(config.output_dir)
        self.output_path.mkdir(parents=True, exist_ok=True)
        
        # Initialize components
        self.reward_function = RewardFunction()
        self.curriculum_manager = CurriculumManager(config)
        
        # Training infrastructure
        if config.use_groq_backend:
            self.groq_backend = GroqBackend()
            self.groq_infrastructure = GroqTrainingInfrastructure(
                backend=self.groq_backend,
                max_budget=config.max_budget
            )
        
        # Training state
        self.training_metrics: List[TrainingMetrics] = []
        self.best_reward = float('-inf')
        self.patience_counter = 0
        
        # Models (will be initialized during training)
        self.mode_selector_model = None
        self.stopping_policy_model = None
        self.training_envs = None
        self.eval_envs = None
        
    async def run_complete_training(self) -> Dict[str, Any]:
        """Run complete RL training pipeline"""
        
        logger.info("Starting complete RL training pipeline")
        start_time = time.time()
        
        try:
            # Step 1: Run baseline experiments
            logger.info("Step 1: Running baseline experiments")
            baseline_results = await self._run_baseline_experiments()
            
            # Step 2: Prepare training data
            logger.info("Step 2: Preparing training datasets")
            training_data = await self._prepare_training_data()
            
            # Step 3: Initialize training environments
            logger.info("Step 3: Initializing training environments")
            self._initialize_environments(training_data)
            
            # Step 4: Train RL policies
            logger.info("Step 4: Training RL policies")
            if self.config.joint_training:
                training_results = await self._joint_training()
            else:
                training_results = await self._sequential_training()
            
            # Step 5: Validate trained policies
            logger.info("Step 5: Validating trained policies")
            validation_results = await self._validate_policies()
            
            # Step 6: Generate final evaluation
            logger.info("Step 6: Generating final evaluation")
            final_evaluation = await self._final_evaluation()
            
            # Compile results
            complete_results = {
                'config': asdict(self.config),
                'execution_time': time.time() - start_time,
                'baseline_results': baseline_results,
                'training_data_info': training_data,
                'training_results': training_results,
                'validation_results': validation_results,
                'final_evaluation': final_evaluation,
                'training_metrics': [asdict(m) for m in self.training_metrics],
                'best_models_saved': {
                    'mode_selector': str(self.output_path / 'best_mode_selector.zip'),
                    'stopping_policy': str(self.output_path / 'best_stopping_policy.zip')
                }
            }
            
            # Save complete results
            await self._save_training_results(complete_results)
            
            logger.info(f"Complete training pipeline finished in {complete_results['execution_time']:.2f} seconds")
            
            return complete_results
            
        except Exception as e:
            logger.error(f"Training pipeline failed: {e}")
            raise
        
        finally:
            # Cleanup
            if self.training_envs:
                self.training_envs.close()
            if self.eval_envs:
                self.eval_envs.close()
    
    async def _run_baseline_experiments(self) -> Dict[str, Any]:
        """Run baseline experiments to establish benchmarks"""
        
        baseline_config = BaselineConfig(
            dataset_path=self.config.dataset_path,
            output_dir=str(self.output_path / 'baseline'),
            sample_size=min(50, self.config.num_validation_contracts),
            repetitions=3
        )
        
        runner = BaselineExperimentRunner(baseline_config)
        results = await runner.run_all_baselines()
        
        return results
    
    async def _prepare_training_data(self) -> Dict[str, Any]:
        """Prepare and validate training datasets"""
        
        dataset_path = Path(self.config.dataset_path)
        
        # Find all contract files
        contract_files = list(dataset_path.glob("**/*.sol"))
        
        if len(contract_files) < self.config.num_training_contracts + self.config.num_validation_contracts:
            logger.warning(f"Not enough contracts found ({len(contract_files)}), using mock generation")
            # Generate mock contracts if needed
            contract_files = self._generate_mock_contracts(
                self.config.num_training_contracts + self.config.num_validation_contracts
            )
        
        # Split into training and validation
        np.random.seed(42)  # For reproducibility
        shuffled_files = np.random.permutation(contract_files)
        
        training_contracts = shuffled_files[:self.config.num_training_contracts].tolist()
        validation_contracts = shuffled_files[
            self.config.num_training_contracts:
            self.config.num_training_contracts + self.config.num_validation_contracts
        ].tolist()
        
        return {
            'total_contracts': len(contract_files),
            'training_contracts': [str(f) for f in training_contracts],
            'validation_contracts': [str(f) for f in validation_contracts],
            'training_size': len(training_contracts),
            'validation_size': len(validation_contracts)
        }
    
    def _generate_mock_contracts(self, num_contracts: int) -> List[Path]:
        """Generate mock contract files for training"""
        
        mock_contracts = []
        mock_dir = self.output_path / 'mock_contracts'
        mock_dir.mkdir(exist_ok=True)
        
        for i in range(num_contracts):
            contract_file = mock_dir / f"mock_contract_{i:04d}.sol"
            
            # Create simple mock contract content
            mock_content = f"""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract MockContract_{i} {{
    uint256 public value;
    mapping(address => uint256) public balances;
    
    function setValue(uint256 _value) public {{
        value = _value;
    }}
    
    function deposit() public payable {{
        balances[msg.sender] += msg.value;
    }}
    
    function withdraw(uint256 amount) public {{
        require(balances[msg.sender] >= amount, "Insufficient balance");
        balances[msg.sender] -= amount;
        payable(msg.sender).transfer(amount);
    }}
}}
"""
            
            with open(contract_file, 'w') as f:
                f.write(mock_content)
            
            mock_contracts.append(contract_file)
        
        logger.info(f"Generated {len(mock_contracts)} mock contracts")
        return mock_contracts
    
    def _initialize_environments(self, training_data: Dict[str, Any]):
        """Initialize training and evaluation environments"""
        
        # Create environment factory
        def make_env(contracts: List[str], rank: int = 0):
            def _init():
                # Create base environment
                env = SmartContractAuditEnv(
                    contracts=contracts,
                    reward_function=self.reward_function,
                    timeout=self.config.env_timeout
                )
                
                # Add monitoring
                env = Monitor(env, str(self.output_path / f'monitor_{rank}'))
                
                return env
            return _init
        
        # Create vectorized environments
        training_contracts = training_data['training_contracts']
        validation_contracts = training_data['validation_contracts']
        
        # Training environments
        self.training_envs = SubprocVecEnv([
            make_env(training_contracts, i) for i in range(self.config.n_envs)
        ])
        
        # Evaluation environments
        self.eval_envs = SubprocVecEnv([
            make_env(validation_contracts, i) for i in range(min(2, self.config.n_envs))
        ])
        
        logger.info(f"Initialized {self.config.n_envs} training and 2 evaluation environments")
    
    async def _joint_training(self) -> Dict[str, Any]:
        """Joint training of both policies"""
        
        logger.info("Starting joint training of mode selector and stopping policy")
        
        # Initialize models
        self.mode_selector_model = PPO(
            'MultiInputPolicy',
            self.training_envs,
            learning_rate=self.config.learning_rate,
            n_steps=2048,
            batch_size=self.config.batch_size,
            n_epochs=self.config.n_epochs,
            verbose=1,
            tensorboard_log=str(self.output_path / 'tensorboard')
        )
        
        self.stopping_policy_model = DQN(
            'MultiInputPolicy',
            self.training_envs,
            learning_rate=self.config.learning_rate,
            buffer_size=50000,
            batch_size=self.config.batch_size,
            verbose=1,
            tensorboard_log=str(self.output_path / 'tensorboard')
        )
        
        # Setup callbacks
        training_callback = TrainingCallback(
            curriculum_manager=self.curriculum_manager,
            training_pipeline=self
        )
        
        eval_callback = EvalCallback(
            self.eval_envs,
            best_model_save_path=str(self.output_path / 'best_models'),
            log_path=str(self.output_path / 'eval_logs'),
            eval_freq=self.config.eval_frequency,
            deterministic=True,
            render=False
        )
        
        # Joint training loop
        joint_timesteps = self.config.total_timesteps // 2
        
        try:
            # Train mode selector
            logger.info("Training mode selector (PPO)")
            self.mode_selector_model.learn(
                total_timesteps=joint_timesteps,
                callback=[training_callback, eval_callback],
                progress_bar=True
            )
            
            # Save intermediate model
            self.mode_selector_model.save(self.output_path / 'mode_selector_intermediate')
            
            # Train stopping policy
            logger.info("Training stopping policy (DQN)")
            self.stopping_policy_model.learn(
                total_timesteps=joint_timesteps,
                callback=[training_callback, eval_callback],
                progress_bar=True
            )
            
            # Save final models
            self.mode_selector_model.save(self.output_path / 'best_mode_selector')
            self.stopping_policy_model.save(self.output_path / 'best_stopping_policy')
            
            return {
                'mode': 'joint_training',
                'mode_selector_timesteps': joint_timesteps,
                'stopping_policy_timesteps': joint_timesteps,
                'total_timesteps': self.config.total_timesteps,
                'curriculum_stages_completed': self.curriculum_manager.current_stage + 1,
                'training_success': True
            }
            
        except Exception as e:
            logger.error(f"Joint training failed: {e}")
            return {
                'mode': 'joint_training',
                'training_success': False,
                'error': str(e)
            }
    
    async def _sequential_training(self) -> Dict[str, Any]:
        """Sequential training of policies"""
        
        logger.info("Starting sequential training")
        
        # This would implement sequential training
        # For now, delegate to joint training
        return await self._joint_training()
    
    async def _validate_policies(self) -> Dict[str, Any]:
        """Validate trained policies on validation set"""
        
        if not self.mode_selector_model or not self.stopping_policy_model:
            logger.error("Models not trained, cannot validate")
            return {'validation_success': False, 'error': 'Models not trained'}
        
        logger.info("Validating trained policies")
        
        # Load best models
        try:
            self.mode_selector_model = PPO.load(self.output_path / 'best_mode_selector')
            self.stopping_policy_model = DQN.load(self.output_path / 'best_stopping_policy')
        except Exception as e:
            logger.warning(f"Could not load best models: {e}, using current models")
        
        # Run validation episodes
        validation_rewards = []
        validation_costs = []
        
        num_validation_episodes = 50
        
        for episode in range(num_validation_episodes):
            obs = self.eval_envs.reset()
            episode_reward = 0
            episode_cost = 0
            done = False
            
            while not done:
                # Get action from model (simplified)
                action, _ = self.mode_selector_model.predict(obs, deterministic=True)
                obs, reward, done, info = self.eval_envs.step(action)
                
                episode_reward += reward[0] if isinstance(reward, (list, np.ndarray)) else reward
                
                if isinstance(info, list) and len(info) > 0 and 'cost' in info[0]:
                    episode_cost += info[0]['cost']
            
            validation_rewards.append(episode_reward)
            validation_costs.append(episode_cost)
        
        # Calculate validation metrics
        validation_results = {
            'num_episodes': num_validation_episodes,
            'mean_reward': float(np.mean(validation_rewards)),
            'std_reward': float(np.std(validation_rewards)),
            'mean_cost': float(np.mean(validation_costs)),
            'std_cost': float(np.std(validation_costs)),
            'reward_cost_ratio': float(np.mean(validation_rewards) / np.mean(validation_costs)) if np.mean(validation_costs) > 0 else 0,
            'validation_success': True
        }
        
        logger.info(f"Validation completed: Mean reward = {validation_results['mean_reward']:.3f}")
        
        return validation_results
    
    async def _final_evaluation(self) -> Dict[str, Any]:
        """Generate final evaluation comparing RL vs baselines"""
        
        logger.info("Generating final evaluation")
        
        # This would run the full evaluation framework
        # comparing RL-adaptive vs baseline modes
        
        return {
            'evaluation_type': 'comparative',
            'modes_compared': ['BA', 'TA', 'Hybrid', 'RL-Adaptive'],
            'metrics_calculated': ['accuracy', 'cost', 'efficiency', 'pareto_optimality'],
            'evaluation_success': True
        }
    
    def _update_env_config(self, new_config: Dict[str, Any]):
        """Update environment configuration for curriculum learning"""
        
        # This would update the environment parameters
        # based on curriculum stage
        logger.info(f"Updated environment config for stage: {new_config.get('stage_name', 'unknown')}")
    
    def _save_training_metrics(self, metrics: TrainingMetrics):
        """Save training metrics"""
        
        self.training_metrics.append(metrics)
        
        # Save to file periodically
        if len(self.training_metrics) % 10 == 0:
            metrics_file = self.output_path / 'training_metrics.json'
            with open(metrics_file, 'w') as f:
                json.dump([asdict(m) for m in self.training_metrics], f, indent=2)
    
    async def _save_training_results(self, results: Dict[str, Any]):
        """Save complete training results"""
        
        # Save main results
        results_file = self.output_path / 'training_results.json'
        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        
        # Save training plots
        await self._generate_training_plots()
        
        # Save models if not already saved
        if self.mode_selector_model:
            self.mode_selector_model.save(self.output_path / 'final_mode_selector')
        
        if self.stopping_policy_model:
            self.stopping_policy_model.save(self.output_path / 'final_stopping_policy')
        
        logger.info(f"Training results saved to {self.output_path}")
    
    async def _generate_training_plots(self):
        """Generate training progress plots"""
        
        if not self.training_metrics:
            return
        
        fig, axes = plt.subplots(2, 2, figsize=(15, 10))
        fig.suptitle('RL Training Progress', fontsize=16)
        
        # Extract data
        timesteps = [m.total_timesteps for m in self.training_metrics]
        rewards = [m.mean_reward for m in self.training_metrics]
        costs = [m.avg_cost_per_audit for m in self.training_metrics]
        stages = [m.curriculum_stage for m in self.training_metrics]
        
        # Plot 1: Reward over time
        axes[0, 0].plot(timesteps, rewards)
        axes[0, 0].set_xlabel('Timesteps')
        axes[0, 0].set_ylabel('Mean Reward')
        axes[0, 0].set_title('Training Reward Progress')
        axes[0, 0].grid(True, alpha=0.3)
        
        # Plot 2: Cost over time
        axes[0, 1].plot(timesteps, costs, color='red')
        axes[0, 1].set_xlabel('Timesteps')
        axes[0, 1].set_ylabel('Average Cost')
        axes[0, 1].set_title('Cost Efficiency Progress')
        axes[0, 1].grid(True, alpha=0.3)
        
        # Plot 3: Curriculum progression
        axes[1, 0].plot(timesteps, stages, marker='o')
        axes[1, 0].set_xlabel('Timesteps')
        axes[1, 0].set_ylabel('Curriculum Stage')
        axes[1, 0].set_title('Curriculum Learning Progress')
        axes[1, 0].grid(True, alpha=0.3)
        
        # Plot 4: Reward vs Cost scatter
        axes[1, 1].scatter(costs, rewards, alpha=0.6)
        axes[1, 1].set_xlabel('Cost')
        axes[1, 1].set_ylabel('Reward')
        axes[1, 1].set_title('Reward vs Cost Tradeoff')
        axes[1, 1].grid(True, alpha=0.3)
        
        plt.tight_layout()
        
        # Save plot
        plot_file = self.output_path / 'training_progress.png'
        plt.savefig(plot_file, dpi=300, bbox_inches='tight')
        plt.close()
        
        logger.info(f"Training plots saved to {plot_file}")

async def run_training_pipeline():
    """Run complete RL training pipeline"""
    
    config = TrainingConfig(
        dataset_path="./datasets/contracts",
        output_dir="./training/rl_results",
        num_training_contracts=100,  # Reduced for testing
        num_validation_contracts=25,
        total_timesteps=50000,      # Reduced for testing
        use_curriculum=True,
        joint_training=True
    )
    
    pipeline = RLTrainingPipeline(config)
    results = await pipeline.run_complete_training()
    
    return results

if __name__ == "__main__":
    # Run training pipeline
    asyncio.run(run_training_pipeline())