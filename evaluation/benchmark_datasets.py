"""
Benchmark Dataset Preparation and Management
===========================================

This module handles the preparation and management of benchmark datasets
for evaluating the RL-augmented smart contract auditing system.

Datasets:
1. Common-Vulnerability Set (110 contracts) - Training/validation
2. Real-World Set (6,454 contracts from 102 Code4rena projects) - Final evaluation  
3. SmartBugs Curated dataset - Additional validation

Key functionality:
- Dataset downloading and preprocessing
- Ground truth vulnerability annotation
- Cost baseline calculation
- Performance metrics computation
"""

import os
import json
import csv
import requests
import zipfile
import tarfile
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any, Set
from dataclasses import dataclass, asdict
import pandas as pd
import numpy as np
import logging
from urllib.parse import urljoin
import tempfile
import shutil

from rl_environment.contract_analyzer import ContractAnalyzer, analyze_contract_file
from rl_environment.rl_architecture import ContractFeatures

@dataclass
class VulnerabilityLabel:
    """Standard vulnerability label format"""
    vulnerability_type: str
    severity: str  # "High", "Medium", "Low", "Info"
    description: str
    line_numbers: List[int]
    confidence: float  # 0.0 to 1.0
    
@dataclass
class ContractSample:
    """Single contract sample with metadata"""
    contract_id: str
    contract_name: str
    source_code: str
    features: ContractFeatures
    vulnerabilities: List[VulnerabilityLabel]
    dataset_source: str
    project_name: Optional[str] = None
    file_path: Optional[str] = None
    complexity_metrics: Optional[Dict[str, Any]] = None

@dataclass
class DatasetSplit:
    """Dataset split for train/validation/test"""
    train_samples: List[ContractSample]
    validation_samples: List[ContractSample]
    test_samples: List[ContractSample]
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about the dataset split"""
        
        def get_split_stats(samples: List[ContractSample]) -> Dict[str, Any]:
            if not samples:
                return {"count": 0, "avg_vulnerabilities": 0, "vulnerability_types": {}}
                
            vuln_counts = [len(sample.vulnerabilities) for sample in samples]
            vuln_types = {}
            
            for sample in samples:
                for vuln in sample.vulnerabilities:
                    vuln_type = vuln.vulnerability_type
                    vuln_types[vuln_type] = vuln_types.get(vuln_type, 0) + 1
            
            return {
                "count": len(samples),
                "avg_vulnerabilities": np.mean(vuln_counts),
                "std_vulnerabilities": np.std(vuln_counts),
                "max_vulnerabilities": np.max(vuln_counts) if vuln_counts else 0,
                "vulnerability_types": vuln_types,
                "avg_lines_of_code": np.mean([s.features.lines_of_code for s in samples]),
            }
        
        return {
            "train": get_split_stats(self.train_samples),
            "validation": get_split_stats(self.validation_samples), 
            "test": get_split_stats(self.test_samples),
            "total": get_split_stats(self.train_samples + self.validation_samples + self.test_samples)
        }

class BenchmarkDatasetManager:
    """
    Manager for benchmark datasets used in LLM-SmartAudit evaluation
    
    Handles downloading, preprocessing, and serving datasets for RL training
    and evaluation.
    """
    
    def __init__(self, 
                 data_dir: str = "./datasets",
                 use_slither: bool = True,
                 cache_features: bool = True):
        
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.use_slither = use_slither
        self.cache_features = cache_features
        self.logger = logging.getLogger(__name__)
        
        # Dataset URLs and configurations
        self.dataset_configs = {
            "smartbugs": {
                "url": "https://github.com/smartbugs/smartbugs-curated/archive/main.zip",
                "local_path": self.data_dir / "smartbugs-curated",
                "description": "SmartBugs Curated dataset with known vulnerabilities"
            },
            "common_vulnerability": {
                "local_path": self.data_dir / "common_vulnerability_set",
                "description": "Common-Vulnerability Set (110 contracts) from LLM-SmartAudit paper"
            },
            "real_world": {
                "local_path": self.data_dir / "real_world_set", 
                "description": "Real-World Set (6,454 contracts from Code4rena)"
            }
        }
        
        # Vulnerability type mapping for standardization
        self.vulnerability_mapping = {
            # Arithmetic vulnerabilities
            "integer_overflow": "Arithmetic",
            "integer_underflow": "Arithmetic", 
            "arithmetic": "Arithmetic",
            
            # Reentrancy
            "reentrancy": "Reentrancy",
            "cross_function_reentrancy": "Reentrancy",
            
            # Access control
            "unprotected_function": "Access Control",
            "missing_access_control": "Access Control",
            "authorization": "Access Control",
            
            # External calls
            "unchecked_call": "External Calls",
            "unchecked_send": "External Calls", 
            "external_call": "External Calls",
            
            # Time manipulation
            "timestamp_dependence": "Time Manipulation",
            "block_info_dependency": "Time Manipulation",
            
            # Randomness
            "weak_randomness": "Randomness",
            "predictable_randomness": "Randomness",
            
            # Gas and DoS
            "gas_limit": "DoS",
            "denial_of_service": "DoS",
            
            # Token handling
            "erc20_issues": "Token Standard",
            "token_standard": "Token Standard",
            
            # Proxy and upgrades
            "proxy_issues": "Proxy Pattern",
            "upgradeable_issues": "Proxy Pattern",
        }
        
    def download_and_prepare_datasets(self) -> Dict[str, DatasetSplit]:
        """
        Download and prepare all benchmark datasets
        
        Returns:
            Dictionary mapping dataset names to DatasetSplit objects
        """
        
        datasets = {}
        
        # Download SmartBugs dataset
        try:
            smartbugs_split = self._prepare_smartbugs_dataset()
            datasets["smartbugs"] = smartbugs_split
            self.logger.info("SmartBugs dataset prepared successfully")
        except Exception as e:
            self.logger.error(f"Failed to prepare SmartBugs dataset: {e}")
        
        # Prepare Common-Vulnerability Set (if available)
        try:
            cv_split = self._prepare_common_vulnerability_set()
            if cv_split:
                datasets["common_vulnerability"] = cv_split
                self.logger.info("Common-Vulnerability Set prepared successfully")
        except Exception as e:
            self.logger.error(f"Failed to prepare Common-Vulnerability Set: {e}")
        
        # Prepare Real-World Set (if available)
        try:
            rw_split = self._prepare_real_world_set()
            if rw_split:
                datasets["real_world"] = rw_split
                self.logger.info("Real-World Set prepared successfully")
        except Exception as e:
            self.logger.error(f"Failed to prepare Real-World Set: {e}")
        
        return datasets
    
    def _download_file(self, url: str, local_path: Path) -> bool:
        """Download file from URL to local path"""
        
        try:
            self.logger.info(f"Downloading {url} to {local_path}")
            
            response = requests.get(url, stream=True)
            response.raise_for_status()
            
            local_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(local_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to download {url}: {e}")
            return False
    
    def _extract_archive(self, archive_path: Path, extract_to: Path) -> bool:
        """Extract zip or tar archive"""
        
        try:
            extract_to.mkdir(parents=True, exist_ok=True)
            
            if archive_path.suffix == '.zip':
                with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_to)
            elif archive_path.suffix in ['.tar', '.gz', '.tgz']:
                with tarfile.open(archive_path, 'r:*') as tar_ref:
                    tar_ref.extractall(extract_to)
            else:
                self.logger.error(f"Unsupported archive format: {archive_path}")
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to extract {archive_path}: {e}")
            return False
    
    def _prepare_smartbugs_dataset(self) -> DatasetSplit:
        """Prepare SmartBugs Curated dataset"""
        
        config = self.dataset_configs["smartbugs"]
        
        # Download if not exists
        if not config["local_path"].exists():
            archive_path = self.data_dir / "smartbugs.zip"
            
            if not archive_path.exists():
                success = self._download_file(config["url"], archive_path)
                if not success:
                    raise RuntimeError("Failed to download SmartBugs dataset")
            
            success = self._extract_archive(archive_path, config["local_path"].parent)
            if not success:
                raise RuntimeError("Failed to extract SmartBugs dataset")
        
        # Find the actual dataset directory (may be nested)
        dataset_dir = config["local_path"]
        if not dataset_dir.exists():
            # Look for extracted directory
            extracted_dirs = list(self.data_dir.glob("smartbugs-*"))
            if extracted_dirs:
                dataset_dir = extracted_dirs[0]
            else:
                raise RuntimeError("SmartBugs dataset directory not found after extraction")
        
        # Parse SmartBugs dataset
        samples = self._parse_smartbugs_contracts(dataset_dir)
        
        # Create train/validation/test split (70/15/15)
        np.random.seed(42)  # For reproducible splits
        np.random.shuffle(samples)
        
        n_total = len(samples)
        n_train = int(0.7 * n_total)
        n_val = int(0.15 * n_total)
        
        return DatasetSplit(
            train_samples=samples[:n_train],
            validation_samples=samples[n_train:n_train + n_val],
            test_samples=samples[n_train + n_val:]
        )
    
    def _parse_smartbugs_contracts(self, dataset_dir: Path) -> List[ContractSample]:
        """Parse SmartBugs contracts and vulnerabilities"""
        
        samples = []
        analyzer = ContractAnalyzer(use_slither=self.use_slither)
        
        # Look for Solidity files
        sol_files = list(dataset_dir.rglob("*.sol"))
        
        for sol_file in sol_files:
            try:
                # Read contract source
                with open(sol_file, 'r', encoding='utf-8', errors='ignore') as f:
                    source_code = f.read()
                
                if not source_code.strip():
                    continue
                
                # Analyze contract features
                features = analyzer.analyze_contract(source_code, sol_file.stem)
                
                # Parse vulnerabilities from filename/directory structure
                vulnerabilities = self._extract_smartbugs_vulnerabilities(sol_file)
                
                # Create contract sample
                sample = ContractSample(
                    contract_id=f"smartbugs_{sol_file.stem}",
                    contract_name=sol_file.stem,
                    source_code=source_code,
                    features=features,
                    vulnerabilities=vulnerabilities,
                    dataset_source="smartbugs",
                    file_path=str(sol_file)
                )
                
                samples.append(sample)
                
            except Exception as e:
                self.logger.warning(f"Failed to process {sol_file}: {e}")
                continue
        
        self.logger.info(f"Parsed {len(samples)} contracts from SmartBugs dataset")
        return samples
    
    def _extract_smartbugs_vulnerabilities(self, sol_file: Path) -> List[VulnerabilityLabel]:
        """Extract vulnerability labels from SmartBugs file structure"""
        
        vulnerabilities = []
        
        # SmartBugs organizes files by vulnerability type
        path_parts = sol_file.parts
        
        # Look for vulnerability indicators in path
        vuln_indicators = [
            "reentrancy", "overflow", "underflow", "unchecked",
            "timestamp", "randomness", "access", "dos"
        ]
        
        detected_vulns = []
        for part in path_parts:
            part_lower = part.lower()
            for indicator in vuln_indicators:
                if indicator in part_lower:
                    detected_vulns.append(indicator)
        
        # Create vulnerability labels
        for vuln_type in set(detected_vulns):
            mapped_type = self.vulnerability_mapping.get(vuln_type, vuln_type.title())
            
            vulnerability = VulnerabilityLabel(
                vulnerability_type=mapped_type,
                severity="Medium",  # Default severity
                description=f"Detected {vuln_type} vulnerability",
                line_numbers=[],  # Would need more sophisticated analysis
                confidence=0.8
            )
            vulnerabilities.append(vulnerability)
        
        return vulnerabilities
    
    def _prepare_common_vulnerability_set(self) -> Optional[DatasetSplit]:
        """Prepare Common-Vulnerability Set (110 contracts)"""
        
        # This would be implemented when the actual dataset is available
        # For now, return None to indicate dataset not available
        
        config = self.dataset_configs["common_vulnerability"]
        
        if not config["local_path"].exists():
            self.logger.warning("Common-Vulnerability Set not available locally")
            return None
        
        # Implementation would parse the 110 contracts with ground truth labels
        # Similar structure to SmartBugs parsing
        
        return None
    
    def _prepare_real_world_set(self) -> Optional[DatasetSplit]:
        """Prepare Real-World Set (6,454 contracts from Code4rena)"""
        
        # This would be implemented when the actual dataset is available
        # For now, return None to indicate dataset not available
        
        config = self.dataset_configs["real_world"]
        
        if not config["local_path"].exists():
            self.logger.warning("Real-World Set not available locally")
            return None
        
        # Implementation would parse Code4rena project contracts
        # May need to scrape from Code4rena API or use provided dataset
        
        return None
    
    def generate_synthetic_dataset(self, n_contracts: int = 1000) -> DatasetSplit:
        """
        Generate synthetic dataset for testing and initial training
        
        Creates contracts with known vulnerability patterns for RL training.
        """
        
        samples = []
        analyzer = ContractAnalyzer(use_slither=False)  # Use regex for speed
        
        # Contract templates with different complexity levels
        templates = {
            "simple_token": self._get_simple_token_template(),
            "complex_defi": self._get_complex_defi_template(),
            "proxy_contract": self._get_proxy_template(),
            "multisig": self._get_multisig_template(),
        }
        
        # Vulnerability injection patterns
        vuln_patterns = {
            "reentrancy": "msg.sender.call{value: amount}(\"\");",
            "overflow": "balance = balance + amount;",  # No SafeMath
            "access_control": "// Missing onlyOwner modifier",
            "timestamp": "require(block.timestamp > deadline);",
        }
        
        for i in range(n_contracts):
            # Randomly select template and modifications
            template_name = np.random.choice(list(templates.keys()))
            base_contract = templates[template_name]
            
            # Inject random vulnerabilities
            modified_contract = base_contract
            injected_vulns = []
            
            if np.random.random() < 0.3:  # 30% chance of vulnerability
                vuln_type = np.random.choice(list(vuln_patterns.keys()))
                vuln_code = vuln_patterns[vuln_type]
                
                # Simple injection (would be more sophisticated in practice)
                modified_contract = base_contract.replace("// INJECT_VULN", vuln_code)
                
                injected_vulns.append(VulnerabilityLabel(
                    vulnerability_type=vuln_type.replace("_", " ").title(),
                    severity=np.random.choice(["High", "Medium", "Low"]),
                    description=f"Synthetic {vuln_type} vulnerability",
                    line_numbers=[],
                    confidence=1.0  # Known synthetic vulnerability
                ))
            
            # Analyze features
            features = analyzer.analyze_contract(modified_contract, f"synthetic_{i}")
            
            # Create sample
            sample = ContractSample(
                contract_id=f"synthetic_{i}_{template_name}",
                contract_name=f"Synthetic_{i}",
                source_code=modified_contract,
                features=features,
                vulnerabilities=injected_vulns,
                dataset_source="synthetic"
            )
            
            samples.append(sample)
        
        # Create split
        np.random.shuffle(samples)
        n_train = int(0.7 * len(samples))
        n_val = int(0.15 * len(samples))
        
        return DatasetSplit(
            train_samples=samples[:n_train],
            validation_samples=samples[n_train:n_train + n_val],
            test_samples=samples[n_train + n_val:]
        )
    
    def _get_simple_token_template(self) -> str:
        """Simple ERC20-like token template"""
        return """
        pragma solidity ^0.8.0;
        
        contract SimpleToken {
            mapping(address => uint256) public balances;
            uint256 public totalSupply;
            address public owner;
            
            event Transfer(address indexed from, address indexed to, uint256 value);
            
            modifier onlyOwner() {
                require(msg.sender == owner);
                _;
            }
            
            constructor(uint256 _totalSupply) {
                totalSupply = _totalSupply;
                owner = msg.sender;
                balances[msg.sender] = _totalSupply;
            }
            
            function transfer(address to, uint256 amount) public returns (bool) {
                require(balances[msg.sender] >= amount);
                // INJECT_VULN
                balances[msg.sender] -= amount;
                balances[to] += amount;
                emit Transfer(msg.sender, to, amount);
                return true;
            }
            
            function withdraw() public onlyOwner {
                payable(owner).transfer(address(this).balance);
            }
        }
        """
    
    def _get_complex_defi_template(self) -> str:
        """Complex DeFi protocol template"""
        return """
        pragma solidity ^0.8.0;
        
        contract DeFiProtocol {
            mapping(address => uint256) public deposits;
            mapping(address => uint256) public rewards;
            uint256 public totalDeposited;
            address public governance;
            uint256 public rewardRate = 100;
            
            event Deposit(address user, uint256 amount);
            event Withdraw(address user, uint256 amount);
            event RewardClaimed(address user, uint256 amount);
            
            modifier onlyGovernance() {
                require(msg.sender == governance);
                _;
            }
            
            constructor() {
                governance = msg.sender;
            }
            
            function deposit() public payable {
                require(msg.value > 0);
                // INJECT_VULN
                deposits[msg.sender] += msg.value;
                totalDeposited += msg.value;
                emit Deposit(msg.sender, msg.value);
            }
            
            function withdraw(uint256 amount) public {
                require(deposits[msg.sender] >= amount);
                // INJECT_VULN
                deposits[msg.sender] -= amount;
                totalDeposited -= amount;
                payable(msg.sender).transfer(amount);
                emit Withdraw(msg.sender, amount);
            }
            
            function claimRewards() public {
                uint256 reward = calculateReward(msg.sender);
                rewards[msg.sender] = 0;
                // INJECT_VULN
                payable(msg.sender).transfer(reward);
                emit RewardClaimed(msg.sender, reward);
            }
            
            function calculateReward(address user) public view returns (uint256) {
                return deposits[user] * rewardRate / 10000;
            }
        }
        """
    
    def _get_proxy_template(self) -> str:
        """Proxy contract template"""
        return """
        pragma solidity ^0.8.0;
        
        contract Proxy {
            address public implementation;
            address public admin;
            
            event Upgraded(address indexed implementation);
            
            modifier onlyAdmin() {
                require(msg.sender == admin);
                _;
            }
            
            constructor(address _implementation) {
                implementation = _implementation;
                admin = msg.sender;
            }
            
            function upgrade(address newImplementation) public onlyAdmin {
                // INJECT_VULN
                implementation = newImplementation;
                emit Upgraded(newImplementation);
            }
            
            fallback() external payable {
                address impl = implementation;
                assembly {
                    calldatacopy(0, 0, calldatasize())
                    let result := delegatecall(gas(), impl, 0, calldatasize(), 0, 0)
                    returndatacopy(0, 0, returndatasize())
                    switch result
                    case 0 { revert(0, returndatasize()) }
                    default { return(0, returndatasize()) }
                }
            }
        }
        """
    
    def _get_multisig_template(self) -> str:
        """MultiSig wallet template"""
        return """
        pragma solidity ^0.8.0;
        
        contract MultiSigWallet {
            address[] public owners;
            uint256 public required;
            uint256 public transactionCount;
            
            struct Transaction {
                address destination;
                uint256 value;
                bytes data;
                bool executed;
            }
            
            mapping(uint256 => Transaction) public transactions;
            mapping(uint256 => mapping(address => bool)) public confirmations;
            
            event Confirmation(address indexed sender, uint256 indexed transactionId);
            event Execution(uint256 indexed transactionId);
            
            modifier onlyWallet() {
                require(msg.sender == address(this));
                _;
            }
            
            modifier ownerExists(address owner) {
                require(isOwner(owner));
                _;
            }
            
            constructor(address[] memory _owners, uint256 _required) {
                require(_owners.length > 0 && _required > 0 && _required <= _owners.length);
                owners = _owners;
                required = _required;
            }
            
            function isOwner(address owner) public view returns (bool) {
                for (uint256 i = 0; i < owners.length; i++) {
                    if (owners[i] == owner) return true;
                }
                return false;
            }
            
            function submitTransaction(address destination, uint256 value, bytes memory data) public returns (uint256) {
                // INJECT_VULN
                uint256 transactionId = transactionCount++;
                transactions[transactionId] = Transaction({
                    destination: destination,
                    value: value,
                    data: data,
                    executed: false
                });
                return transactionId;
            }
        }
        """
    
    def save_dataset_split(self, dataset_split: DatasetSplit, dataset_name: str):
        """Save dataset split to disk for later use"""
        
        output_dir = self.data_dir / "processed" / dataset_name
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Save each split
        splits = {
            "train": dataset_split.train_samples,
            "validation": dataset_split.validation_samples,
            "test": dataset_split.test_samples
        }
        
        for split_name, samples in splits.items():
            split_file = output_dir / f"{split_name}.json"
            
            # Convert samples to JSON-serializable format
            serializable_samples = []
            for sample in samples:
                sample_dict = asdict(sample)
                sample_dict['features'] = asdict(sample.features)
                serializable_samples.append(sample_dict)
            
            with open(split_file, 'w', encoding='utf-8') as f:
                json.dump(serializable_samples, f, indent=2)
        
        # Save statistics
        stats_file = output_dir / "statistics.json"
        with open(stats_file, 'w', encoding='utf-8') as f:
            json.dump(dataset_split.get_statistics(), f, indent=2)
        
        self.logger.info(f"Dataset split saved to {output_dir}")

# Example usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Create dataset manager
    manager = BenchmarkDatasetManager(data_dir="./datasets")
    
    # Generate synthetic dataset for testing
    print("Generating synthetic dataset...")
    synthetic_split = manager.generate_synthetic_dataset(n_contracts=100)
    
    # Print statistics
    stats = synthetic_split.get_statistics()
    print("Dataset Statistics:")
    print(json.dumps(stats, indent=2))
    
    # Save dataset
    manager.save_dataset_split(synthetic_split, "synthetic_v1")
    print("Synthetic dataset saved!")