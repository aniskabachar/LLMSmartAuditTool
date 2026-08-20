"""
RL Policy Implementations for Smart Contract Auditing
====================================================

This module implements the two main RL policies:
1. Mode Selector Policy - Chooses BA/TA/Hybrid based on contract features
2. Stopping Policy - Decides when to stop consensus rounds

Uses stable-baselines3 for policy implementations with Groq backend support.
"""

import numpy as np
import torch
import torch.nn as nn
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass
from stable_baselines3 import PPO, DQN
from stable_baselines3.common.policies import ActorCriticPolicy
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from stable_baselines3.common.callbacks import BaseCallback
import gymnasium as gym
from rl_architecture import AuditMode, ContractFeatures, AuditState

class ContractFeatureExtractor(BaseFeaturesExtractor):
    """
    Custom feature extractor for contract characteristics
    
    Processes the contract feature vector and extracts relevant
    representations for policy learning.
    """
    
    def __init__(self, observation_space: gym.Space, features_dim: int = 128):
        super().__init__(observation_space, features_dim)
        
        # Input dimension from AuditState.to_vector()
        input_dim = observation_space.shape[0]  # 27 features
        
        # Feature extraction network
        self.feature_net = nn.Sequential(
            nn.Linear(input_dim, 64),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(64, 128),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(128, features_dim),
            nn.ReLU()
        )
        
    def forward(self, observations: torch.Tensor) -> torch.Tensor:
        return self.feature_net(observations)

class ModeSelectorPolicy(ActorCriticPolicy):
    """
    Custom policy for mode selection (BA/TA/Hybrid)
    
    Specializes in the initial decision of which audit mode to use
    based on contract characteristics.
    """
    
    def __init__(self, *args, **kwargs):
        # Use custom feature extractor
        kwargs['features_extractor_class'] = ContractFeatureExtractor
        kwargs['features_extractor_kwargs'] = {'features_dim': 128}
        
        super().__init__(*args, **kwargs)
        
        # Override action network for mode selection (3 discrete actions)
        self.action_net = nn.Sequential(
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Linear(64, 32),
            nn.ReLU(), 
            nn.Linear(32, 3)  # BA, TA, HYBRID
        )
        
        # Override value network
        self.value_net = nn.Sequential(
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Linear(32, 1)
        )

class StoppingPolicy(ActorCriticPolicy):
    """
    Custom policy for stopping decisions (Continue/Stop consensus rounds)
    
    Focuses on temporal decision making during the consensus process.
    """
    
    def __init__(self, *args, **kwargs):
        kwargs['features_extractor_class'] = ContractFeatureExtractor  
        kwargs['features_extractor_kwargs'] = {'features_dim': 128}
        
        super().__init__(*args, **kwargs)
        
        # Override action network for stopping decision (2 discrete actions)
        self.action_net = nn.Sequential(
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Linear(64, 32), 
            nn.ReLU(),
            nn.Linear(32, 2)  # CONTINUE, STOP
        )
        
        # Override value network  
        self.value_net = nn.Sequential(
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Linear(64, 32),
            nn.ReLU(), 
            nn.Linear(32, 1)
        )

@dataclass
class PolicyConfig:
    """Configuration for RL policies"""
    
    # Training hyperparameters
    learning_rate: float = 3e-4
    batch_size: int = 64
    n_steps: int = 2048
    n_epochs: int = 10
    gamma: float = 0.99
    gae_lambda: float = 0.95
    clip_range: float = 0.2
    ent_coef: float = 0.01
    vf_coef: float = 0.5
    max_grad_norm: float = 0.5
    
    # Environment parameters
    total_timesteps: int = 100000
    eval_freq: int = 5000
    save_freq: int = 10000
    
    # Cost-accuracy tradeoff weights
    accuracy_weight: float = 1.0
    cost_weight: float = 0.3
    efficiency_bonus: float = 0.2

class TrainingCallback(BaseCallback):
    """
    Custom callback for monitoring training progress
    
    Tracks key metrics like cost-accuracy tradeoffs and mode selection patterns.
    """
    
    def __init__(self, verbose: int = 1):
        super().__init__(verbose)
        self.episode_costs = []
        self.episode_accuracies = []
        self.mode_selections = []
        self.stopping_rounds = []
        
    def _on_step(self) -> bool:
        # Extract info from environment
        infos = self.locals.get('infos', [])
        
        for info in infos:
            if 'mode_selected' in info:
                self.mode_selections.append(info['mode_selected'])
            
            if 'final_cost' in info:
                self.episode_costs.append(info['final_cost'])
                
            if 'final_accuracy' in info:
                self.episode_accuracies.append(info['final_accuracy'])
                
            if 'stopping_round' in info:
                self.stopping_rounds.append(info['stopping_round'])
        
        # Log statistics every 1000 steps
        if self.num_timesteps % 1000 == 0:
            self._log_statistics()
            
        return True
    
    def _log_statistics(self):
        """Log training statistics"""
        if self.episode_costs:
            avg_cost = np.mean(self.episode_costs[-100:])  # Last 100 episodes
            self.logger.record("train/avg_cost", avg_cost)
            
        if self.episode_accuracies:
            avg_accuracy = np.mean(self.episode_accuracies[-100:])
            self.logger.record("train/avg_accuracy", avg_accuracy)
            
        if self.mode_selections:
            mode_dist = {}
            recent_modes = self.mode_selections[-100:]
            for mode in recent_modes:
                mode_dist[mode] = mode_dist.get(mode, 0) + 1
            
            for mode, count in mode_dist.items():
                self.logger.record(f"train/mode_{mode}_pct", count / len(recent_modes))
                
        if self.stopping_rounds:
            avg_rounds = np.mean(self.stopping_rounds[-100:])
            self.logger.record("train/avg_stopping_round", avg_rounds)

class RLAuditOrchestrator:
    """
    Main orchestrator for RL-based audit system
    
    Manages both mode selection and stopping policies, integrates with
    the base LLM-SmartAudit system.
    """
    
    def __init__(self, 
                 env: gym.Env,
                 config: PolicyConfig = None,
                 groq_api_key: str = None,
                 model_save_path: str = "./models/"):
        
        self.env = env
        self.config = config or PolicyConfig()
        self.groq_api_key = groq_api_key
        self.model_save_path = model_save_path
        
        # Initialize policies
        self.mode_selector = None
        self.stopping_policy = None
        
        # Training history
        self.training_history = {
            'mode_selector': {},
            'stopping_policy': {},
        }
        
    def initialize_policies(self):
        """Initialize both RL policies"""
        
        # Mode Selector Policy (PPO)
        self.mode_selector = PPO(
            policy=ModeSelectorPolicy,
            env=self.env,
            learning_rate=self.config.learning_rate,
            n_steps=self.config.n_steps,
            batch_size=self.config.batch_size,
            n_epochs=self.config.n_epochs,
            gamma=self.config.gamma,
            gae_lambda=self.config.gae_lambda,
            clip_range=self.config.clip_range,
            ent_coef=self.config.ent_coef,
            vf_coef=self.config.vf_coef,
            max_grad_norm=self.config.max_grad_norm,
            verbose=1,
        )
        
        # Stopping Policy (DQN for discrete temporal decisions)
        self.stopping_policy = DQN(
            policy="MlpPolicy",
            env=self.env,
            learning_rate=self.config.learning_rate,
            buffer_size=50000,
            learning_starts=1000,
            batch_size=self.config.batch_size,
            gamma=self.config.gamma,
            train_freq=4,
            gradient_steps=1,
            target_update_interval=1000,
            exploration_fraction=0.1,
            exploration_initial_eps=1.0,
            exploration_final_eps=0.05,
            verbose=1,
        )
        
    def train_mode_selector(self, total_timesteps: int = None):
        """Train the mode selection policy"""
        
        timesteps = total_timesteps or self.config.total_timesteps
        
        print(f"Training Mode Selector for {timesteps} timesteps...")
        
        callback = TrainingCallback()
        
        self.mode_selector.learn(
            total_timesteps=timesteps,
            callback=callback,
            progress_bar=True
        )
        
        # Save trained model
        model_path = f"{self.model_save_path}/mode_selector.zip"
        self.mode_selector.save(model_path)
        print(f"Mode Selector saved to {model_path}")
        
        # Store training history
        self.training_history['mode_selector'] = {
            'timesteps': timesteps,
            'final_reward': callback.episode_accuracies[-10:] if callback.episode_accuracies else [],
            'costs': callback.episode_costs[-10:] if callback.episode_costs else [],
        }
        
    def train_stopping_policy(self, total_timesteps: int = None):
        """Train the stopping decision policy"""
        
        timesteps = total_timesteps or self.config.total_timesteps
        
        print(f"Training Stopping Policy for {timesteps} timesteps...")
        
        callback = TrainingCallback()
        
        self.stopping_policy.learn(
            total_timesteps=timesteps,
            callback=callback,
            progress_bar=True
        )
        
        # Save trained model
        model_path = f"{self.model_save_path}/stopping_policy.zip"  
        self.stopping_policy.save(model_path)
        print(f"Stopping Policy saved to {model_path}")
        
        # Store training history
        self.training_history['stopping_policy'] = {
            'timesteps': timesteps,
            'stopping_rounds': callback.stopping_rounds[-10:] if callback.stopping_rounds else [],
        }
    
    def load_policies(self, mode_selector_path: str = None, stopping_policy_path: str = None):
        """Load pre-trained policies"""
        
        if mode_selector_path:
            self.mode_selector = PPO.load(mode_selector_path, env=self.env)
            print(f"Mode Selector loaded from {mode_selector_path}")
            
        if stopping_policy_path:
            self.stopping_policy = DQN.load(stopping_policy_path, env=self.env)
            print(f"Stopping Policy loaded from {stopping_policy_path}")
    
    def predict_mode(self, contract_features: ContractFeatures) -> AuditMode:
        """Predict optimal audit mode for given contract"""
        
        if self.mode_selector is None:
            raise ValueError("Mode selector not initialized. Call initialize_policies() first.")
        
        # Create temporary state for prediction
        temp_state = AuditState(contract_features=contract_features)
        obs = temp_state.to_vector().reshape(1, -1)
        
        action, _ = self.mode_selector.predict(obs, deterministic=True)
        
        mode_map = {0: AuditMode.BA, 1: AuditMode.TA, 2: AuditMode.HYBRID}
        return mode_map[int(action)]
    
    def predict_stopping(self, current_state: AuditState) -> bool:
        """Predict whether to stop consensus rounds"""
        
        if self.stopping_policy is None:
            raise ValueError("Stopping policy not initialized. Call initialize_policies() first.")
        
        obs = current_state.to_vector().reshape(1, -1)
        action, _ = self.stopping_policy.predict(obs, deterministic=True)
        
        return bool(action)  # True = stop, False = continue
    
    def evaluate_policies(self, n_episodes: int = 100) -> Dict[str, Any]:
        """Evaluate trained policies on test episodes"""
        
        total_rewards = []
        total_costs = []
        mode_selections = []
        stopping_rounds = []
        
        for episode in range(n_episodes):
            obs, _ = self.env.reset()
            episode_reward = 0
            episode_cost = 0
            done = False
            step_count = 0
            
            while not done and step_count < 20:  # Max 20 steps per episode
                if self.mode_selector and step_count == 0:
                    # Mode selection
                    action, _ = self.mode_selector.predict(obs, deterministic=True)
                elif self.stopping_policy and step_count > 0:
                    # Stopping decision  
                    action, _ = self.stopping_policy.predict(obs, deterministic=True)
                    action += 3  # Offset for stopping actions (3=continue, 4=stop)
                else:
                    # Random action if policies not loaded
                    action = self.env.action_space.sample()
                
                obs, reward, terminated, truncated, info = self.env.step(action)
                done = terminated or truncated
                
                episode_reward += reward
                episode_cost += info.get('step_cost', 0)
                
                if 'mode_selected' in info:
                    mode_selections.append(info['mode_selected'])
                if 'stopping_round' in info:
                    stopping_rounds.append(info['stopping_round'])
                
                step_count += 1
            
            total_rewards.append(episode_reward)
            total_costs.append(episode_cost)
        
        return {
            'mean_reward': np.mean(total_rewards),
            'std_reward': np.std(total_rewards),
            'mean_cost': np.mean(total_costs),
            'std_cost': np.std(total_costs),
            'mode_distribution': {mode: mode_selections.count(mode) / len(mode_selections) 
                               for mode in set(mode_selections)} if mode_selections else {},
            'mean_stopping_round': np.mean(stopping_rounds) if stopping_rounds else 0,
            'episodes_evaluated': n_episodes,
        }
    
    def get_training_summary(self) -> Dict[str, Any]:
        """Get summary of training progress"""
        return {
            'config': self.config.__dict__,
            'training_history': self.training_history,
            'models_saved': {
                'mode_selector': f"{self.model_save_path}/mode_selector.zip",
                'stopping_policy': f"{self.model_save_path}/stopping_policy.zip",
            }
        }