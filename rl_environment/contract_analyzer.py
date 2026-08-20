"""
Smart Contract Feature Extraction and Analysis
=============================================

This module implements comprehensive static analysis for Solidity smart contracts
to extract features for RL state representation. It uses multiple parsing approaches
to ensure robust feature extraction across different contract patterns.

Key Features:
1. Solidity AST parsing using slither/solc
2. Regex-based pattern matching for fallback analysis
3. Security pattern detection
4. Complexity metrics calculation
5. Contract type classification
"""

import re
import os
import json
import subprocess
import tempfile
from typing import Dict, List, Tuple, Optional, Set, Any
from dataclasses import dataclass, asdict
import logging
from pathlib import Path

# Try to import slither for advanced AST analysis
try:
    from slither import Slither
    from slither.core.declarations import Contract, Function
    from slither.core.variables import Variable
    SLITHER_AVAILABLE = True
except ImportError:
    SLITHER_AVAILABLE = False
    print("Slither not available. Using regex-based analysis only.")

from .rl_architecture import ContractFeatures

class SolidityPatterns:
    """
    Regex patterns for Solidity code analysis
    
    These patterns are designed to be robust against various formatting styles
    and handle edge cases in Solidity syntax.
    """
    
    # Function declarations
    FUNCTION_PATTERN = r'\bfunction\s+\w+\s*\([^)]*\)\s*(?:public|private|internal|external)?\s*(?:view|pure|payable)?\s*(?:virtual|override)?\s*(?:returns\s*\([^)]*\))?\s*[{;]'
    CONSTRUCTOR_PATTERN = r'\bconstructor\s*\([^)]*\)\s*(?:public|internal)?\s*(?:payable)?\s*[{;]'
    FALLBACK_PATTERN = r'\bfallback\s*\(\s*\)\s*external\s*(?:payable)?\s*[{;]'
    RECEIVE_PATTERN = r'\breceive\s*\(\s*\)\s*external\s*payable\s*[{;]'
    
    # Modifiers and events
    MODIFIER_PATTERN = r'\bmodifier\s+\w+\s*\([^)]*\)\s*[{;]'
    EVENT_PATTERN = r'\bevent\s+\w+\s*\([^)]*\)\s*;'
    
    # Control structures
    LOOP_PATTERNS = [
        r'\bfor\s*\([^)]*\)\s*{',
        r'\bwhile\s*\([^)]*\)\s*{',
        r'\bdo\s*{[^}]*}\s*while\s*\([^)]*\)'
    ]
    IF_PATTERN = r'\bif\s*\([^)]*\)\s*[{;]'
    REQUIRE_PATTERN = r'\brequire\s*\([^)]*\)'
    
    # Security-relevant patterns  
    EXTERNAL_CALL_PATTERNS = [
        r'\.call\s*\(',
        r'\.delegatecall\s*\(',
        r'\.staticcall\s*\(',
        r'\.send\s*\(',
        r'\.transfer\s*\('
    ]
    
    ASSEMBLY_PATTERN = r'\bassembly\s*{'
    SELFDESTRUCT_PATTERN = r'\bselfdestruct\s*\('
    
    # Value handling
    MSG_VALUE_PATTERN = r'\bmsg\.value\b'
    PAYABLE_PATTERN = r'\bpayable\b'
    
    # Access control patterns
    ACCESS_CONTROL_PATTERNS = [
        r'\bonlyOwner\b',
        r'\bonlyAdmin\b', 
        r'\bAccessControl\b',
        r'\bOwnable\b',
        r'\brequire\s*\(\s*msg\.sender\s*==',
        r'\bmodifier\s+only\w+'
    ]
    
    # Token patterns
    TOKEN_PATTERNS = [
        r'\bERC20\b',
        r'\bERC721\b',
        r'\bERC1155\b',
        r'\btransfer\s*\(',
        r'\btransferFrom\s*\(',
        r'\bbalanceOf\s*\(',
        r'\bapprove\s*\(',
        r'\ballowance\s*\('
    ]
    
    # Proxy patterns
    PROXY_PATTERNS = [
        r'\bProxy\b',
        r'\bUpgradeableProxy\b',
        r'\bTransparentUpgradeableProxy\b',
        r'\bUUPS\b',
        r'\bBeacon\b',
        r'\bdelegatecall\s*\(',
        r'\bimplementation\s*\(\)'
    ]
    
    # Upgradeable patterns
    UPGRADEABLE_PATTERNS = [
        r'\bUpgradeable\b',
        r'\bInitializable\b',
        r'\bUUPSUpgradeable\b',
        r'\b_initialize\b',
        r'\binitialize\s*\('
    ]

class ContractAnalyzer:
    """
    Comprehensive smart contract analyzer using multiple analysis techniques
    
    Combines AST-based analysis (when available) with regex pattern matching
    to provide robust feature extraction for any Solidity contract.
    """
    
    def __init__(self, use_slither: bool = True, solc_version: str = None):
        self.use_slither = use_slither and SLITHER_AVAILABLE
        self.solc_version = solc_version or "0.8.19"
        self.logger = logging.getLogger(__name__)
        
    def analyze_contract(self, contract_code: str, contract_name: str = "Contract") -> ContractFeatures:
        """
        Main analysis function that combines multiple analysis techniques
        
        Args:
            contract_code: Solidity source code
            contract_name: Optional contract name for identification
            
        Returns:
            ContractFeatures object with extracted metrics
        """
        
        # Clean and preprocess code
        cleaned_code = self._preprocess_code(contract_code)
        
        # Try Slither analysis first if available
        if self.use_slither:
            try:
                slither_features = self._analyze_with_slither(cleaned_code)
                if slither_features is not None:
                    self.logger.info("Successfully analyzed contract with Slither")
                    return slither_features
            except Exception as e:
                self.logger.warning(f"Slither analysis failed: {e}. Falling back to regex analysis.")
        
        # Fallback to regex-based analysis
        return self._analyze_with_regex(cleaned_code)
    
    def _preprocess_code(self, code: str) -> str:
        """Clean and preprocess Solidity code"""
        
        # Remove comments
        code = re.sub(r'//.*$', '', code, flags=re.MULTILINE)
        code = re.sub(r'/\*.*?\*/', '', code, flags=re.DOTALL)
        
        # Normalize whitespace
        code = re.sub(r'\s+', ' ', code)
        
        # Remove empty lines
        lines = [line.strip() for line in code.split('\n') if line.strip()]
        
        return '\n'.join(lines)
    
    def _analyze_with_slither(self, contract_code: str) -> Optional[ContractFeatures]:
        """
        Analyze contract using Slither for AST-based analysis
        
        Args:
            contract_code: Preprocessed Solidity code
            
        Returns:
            ContractFeatures if successful, None if failed
        """
        
        if not SLITHER_AVAILABLE:
            return None
            
        # Create temporary file for analysis
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sol', delete=False) as tmp_file:
            tmp_file.write(contract_code)
            tmp_file_path = tmp_file.name
        
        try:
            # Initialize Slither
            slither = Slither(tmp_file_path)
            
            # Extract features from Slither analysis
            features = self._extract_slither_features(slither)
            
            return features
            
        except Exception as e:
            self.logger.error(f"Slither analysis error: {e}")
            return None
        finally:
            # Clean up temporary file
            try:
                os.unlink(tmp_file_path)
            except:
                pass
    
    def _extract_slither_features(self, slither: 'Slither') -> ContractFeatures:
        """Extract features from Slither analysis results"""
        
        # Aggregate metrics across all contracts
        total_functions = 0
        total_modifiers = 0
        total_events = 0
        total_external_calls = 0
        total_loops = 0
        total_conditionals = 0
        
        has_payable = False
        has_fallback = False
        has_receive = False
        uses_delegatecall = False
        uses_assembly = False
        is_token = False
        is_proxy = False
        is_multisig = False
        has_upgradeable = False
        handles_ether = False
        has_withdrawal = False
        has_access_control = False
        
        max_inheritance_depth = 0
        total_interfaces = 0
        
        # Analyze each contract
        for contract in slither.contracts:
            if contract.is_interface:
                total_interfaces += 1
                continue
                
            # Count functions
            total_functions += len(contract.functions)
            
            # Count modifiers  
            total_modifiers += len(contract.modifiers)
            
            # Count events
            total_events += len(contract.events)
            
            # Check inheritance depth
            inheritance_depth = len(contract.inheritance)
            max_inheritance_depth = max(max_inheritance_depth, inheritance_depth)
            
            # Analyze functions
            for function in contract.functions:
                # Check for special functions
                if function.is_fallback:
                    has_fallback = True
                if function.is_receive:
                    has_receive = True
                if function.payable:
                    has_payable = True
                    
                # Count external calls
                for call in function.external_calls_as_expressions:
                    total_external_calls += 1
                    
                # Check for delegatecall
                for call in function.solidity_calls:
                    if 'delegatecall' in str(call):
                        uses_delegatecall = True
                        
                # Check for assembly usage
                if function.contains_assembly:
                    uses_assembly = True
                    
                # Count control structures (approximation)
                if function.nodes:
                    for node in function.nodes:
                        if 'for' in str(node) or 'while' in str(node):
                            total_loops += 1
                        if 'if' in str(node) or 'require' in str(node):
                            total_conditionals += 1
            
            # Check contract patterns
            contract_name = contract.name.lower()
            contract_source = str(contract.source_mapping) if contract.source_mapping else ""
            
            # Token contract detection
            if any(pattern in contract_name for pattern in ['erc20', 'erc721', 'erc1155', 'token']):
                is_token = True
            if any(func.name in ['transfer', 'transferFrom', 'balanceOf'] for func in contract.functions):
                is_token = True
                
            # Proxy contract detection
            if any(pattern in contract_name for pattern in ['proxy', 'upgradeable']):
                is_proxy = True
            if uses_delegatecall:
                is_proxy = True
                
            # Access control detection
            if any(pattern in contract_name for pattern in ['ownable', 'accesscontrol']):
                has_access_control = True
            if any('onlyOwner' in modifier.name for modifier in contract.modifiers):
                has_access_control = True
                
            # Other patterns
            if 'multisig' in contract_name:
                is_multisig = True
            if any(pattern in contract_name for pattern in ['upgradeable', 'initializable']):
                has_upgradeable = True
            if has_payable or any('withdraw' in func.name.lower() for func in contract.functions):
                handles_ether = True
            if any('withdraw' in func.name.lower() for func in contract.functions):
                has_withdrawal = True
        
        # Calculate lines of code (approximation)
        lines_of_code = sum(len(str(contract.source_mapping).split('\n')) 
                          for contract in slither.contracts if not contract.is_interface)
        
        return ContractFeatures(
            lines_of_code=max(lines_of_code, 1),
            function_count=total_functions,
            modifier_count=total_modifiers,
            event_count=total_events,
            external_calls=total_external_calls,
            internal_calls=0,  # Difficult to count precisely with Slither
            loop_count=total_loops,
            conditional_count=total_conditionals,
            has_payable_functions=has_payable,
            has_fallback_function=has_fallback,
            has_receive_function=has_receive,
            uses_delegatecall=uses_delegatecall,
            uses_assembly=uses_assembly,
            is_token_contract=is_token,
            is_proxy_contract=is_proxy,
            is_multisig_contract=is_multisig,
            has_upgradeable_pattern=has_upgradeable,
            inheritance_depth=max_inheritance_depth,
            interface_count=total_interfaces,
            handles_ether=handles_ether,
            has_withdrawal_pattern=has_withdrawal,
            has_access_control=has_access_control,
        )
    
    def _analyze_with_regex(self, contract_code: str) -> ContractFeatures:
        """
        Analyze contract using regex patterns
        
        This is the fallback method when AST analysis is not available.
        """
        
        # Basic metrics
        lines = contract_code.split('\n')
        non_empty_lines = [line for line in lines if line.strip()]
        lines_of_code = len(non_empty_lines)
        
        # Count functions
        function_count = len(re.findall(SolidityPatterns.FUNCTION_PATTERN, contract_code, re.IGNORECASE))
        function_count += len(re.findall(SolidityPatterns.CONSTRUCTOR_PATTERN, contract_code, re.IGNORECASE))
        
        # Count modifiers and events
        modifier_count = len(re.findall(SolidityPatterns.MODIFIER_PATTERN, contract_code, re.IGNORECASE))
        event_count = len(re.findall(SolidityPatterns.EVENT_PATTERN, contract_code, re.IGNORECASE))
        
        # Count external calls
        external_calls = 0
        for pattern in SolidityPatterns.EXTERNAL_CALL_PATTERNS:
            external_calls += len(re.findall(pattern, contract_code, re.IGNORECASE))
        
        # Count internal calls (approximation)
        internal_calls = len(re.findall(r'\bthis\.', contract_code))
        
        # Count loops
        loop_count = 0
        for pattern in SolidityPatterns.LOOP_PATTERNS:
            loop_count += len(re.findall(pattern, contract_code, re.IGNORECASE))
        
        # Count conditionals
        conditional_count = len(re.findall(SolidityPatterns.IF_PATTERN, contract_code, re.IGNORECASE))
        conditional_count += len(re.findall(SolidityPatterns.REQUIRE_PATTERN, contract_code, re.IGNORECASE))
        
        # Security patterns
        has_payable_functions = bool(re.search(SolidityPatterns.PAYABLE_PATTERN, contract_code, re.IGNORECASE))
        has_fallback_function = bool(re.search(SolidityPatterns.FALLBACK_PATTERN, contract_code, re.IGNORECASE))
        has_receive_function = bool(re.search(SolidityPatterns.RECEIVE_PATTERN, contract_code, re.IGNORECASE))
        uses_delegatecall = bool(re.search(r'\bdelegatecall\b', contract_code, re.IGNORECASE))
        uses_assembly = bool(re.search(SolidityPatterns.ASSEMBLY_PATTERN, contract_code, re.IGNORECASE))
        
        # Contract type detection
        is_token_contract = any(bool(re.search(pattern, contract_code, re.IGNORECASE)) 
                              for pattern in SolidityPatterns.TOKEN_PATTERNS)
        
        is_proxy_contract = any(bool(re.search(pattern, contract_code, re.IGNORECASE))
                              for pattern in SolidityPatterns.PROXY_PATTERNS)
        
        is_multisig_contract = bool(re.search(r'\bmultisig\b', contract_code, re.IGNORECASE))
        
        has_upgradeable_pattern = any(bool(re.search(pattern, contract_code, re.IGNORECASE))
                                    for pattern in SolidityPatterns.UPGRADEABLE_PATTERNS)
        
        # Inheritance and interfaces
        inheritance_depth = len(re.findall(r'\bis\s+', contract_code, re.IGNORECASE))
        interface_count = len(re.findall(r'\binterface\s+', contract_code, re.IGNORECASE))
        
        # Value handling
        handles_ether = (has_payable_functions or 
                        bool(re.search(SolidityPatterns.MSG_VALUE_PATTERN, contract_code, re.IGNORECASE)))
        
        has_withdrawal_pattern = bool(re.search(r'\bwithdraw\b', contract_code, re.IGNORECASE))
        
        # Access control
        has_access_control = any(bool(re.search(pattern, contract_code, re.IGNORECASE))
                               for pattern in SolidityPatterns.ACCESS_CONTROL_PATTERNS)
        
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

class ContractComplexityCalculator:
    """
    Calculate various complexity metrics for smart contracts
    
    These metrics help the RL system understand the relative complexity
    of different contracts for better mode selection.
    """
    
    @staticmethod
    def calculate_cyclomatic_complexity(contract_code: str) -> int:
        """
        Calculate approximated cyclomatic complexity
        
        Counts decision points in the code (if, while, for, etc.)
        """
        complexity = 1  # Base complexity
        
        # Count decision points
        patterns = [
            r'\bif\s*\(',
            r'\belse\s+if\s*\(',
            r'\bwhile\s*\(',
            r'\bfor\s*\(',
            r'\bcatch\s*\(',
            r'\b\?\s*.*\s*:',  # Ternary operator
            r'&&',
            r'\|\|',
        ]
        
        for pattern in patterns:
            complexity += len(re.findall(pattern, contract_code, re.IGNORECASE))
        
        return complexity
    
    @staticmethod
    def calculate_halstead_metrics(contract_code: str) -> Dict[str, float]:
        """
        Calculate Halstead complexity metrics
        
        Measures program vocabulary and length
        """
        
        # Solidity operators
        operators = [
            '+', '-', '*', '/', '%', '**',
            '=', '+=', '-=', '*=', '/=', '%=',
            '==', '!=', '<', '>', '<=', '>=',
            '&&', '||', '!',
            '&', '|', '^', '~', '<<', '>>',
            '.', '->', '=>',
        ]
        
        # Solidity keywords
        keywords = [
            'function', 'modifier', 'event', 'struct', 'enum',
            'if', 'else', 'while', 'for', 'do', 'break', 'continue',
            'return', 'require', 'assert', 'revert',
            'public', 'private', 'internal', 'external',
            'view', 'pure', 'payable', 'constant',
            'memory', 'storage', 'calldata',
        ]
        
        # Count operators and operands
        n1 = 0  # Number of distinct operators
        n2 = 0  # Number of distinct operands  
        N1 = 0  # Total number of operators
        N2 = 0  # Total number of operands
        
        # Count operators
        found_operators = set()
        for op in operators:
            count = len(re.findall(re.escape(op), contract_code))
            if count > 0:
                found_operators.add(op)
                N1 += count
        
        # Count keywords as operators
        for keyword in keywords:
            pattern = r'\b' + re.escape(keyword) + r'\b'
            count = len(re.findall(pattern, contract_code, re.IGNORECASE))
            if count > 0:
                found_operators.add(keyword)
                N1 += count
        
        n1 = len(found_operators)
        
        # Count operands (approximation using identifiers and literals)
        identifier_pattern = r'\b[a-zA-Z_][a-zA-Z0-9_]*\b'
        number_pattern = r'\b\d+\b'
        string_pattern = r'"[^"]*"'
        
        identifiers = re.findall(identifier_pattern, contract_code)
        numbers = re.findall(number_pattern, contract_code)
        strings = re.findall(string_pattern, contract_code)
        
        all_operands = identifiers + numbers + strings
        # Remove keywords that are not operands
        all_operands = [op for op in all_operands if op.lower() not in keywords]
        
        n2 = len(set(all_operands))
        N2 = len(all_operands)
        
        # Calculate Halstead metrics
        if n1 == 0 or n2 == 0:
            return {
                'vocabulary': 0,
                'length': 0,
                'calculated_length': 0,
                'volume': 0,
                'difficulty': 0,
                'effort': 0,
            }
        
        vocabulary = n1 + n2
        length = N1 + N2
        calculated_length = n1 * np.log2(n1) + n2 * np.log2(n2) if n1 > 0 and n2 > 0 else 0
        volume = length * np.log2(vocabulary) if vocabulary > 0 else 0
        difficulty = (n1 / 2.0) * (N2 / n2) if n2 > 0 else 0
        effort = difficulty * volume
        
        return {
            'vocabulary': vocabulary,
            'length': length,
            'calculated_length': calculated_length,
            'volume': volume,
            'difficulty': difficulty,
            'effort': effort,
        }

def analyze_contract_file(file_path: str, use_slither: bool = True) -> Tuple[ContractFeatures, Dict[str, Any]]:
    """
    Convenience function to analyze a contract from file
    
    Args:
        file_path: Path to Solidity file
        use_slither: Whether to use Slither for analysis
        
    Returns:
        Tuple of (ContractFeatures, additional_metrics)
    """
    
    with open(file_path, 'r', encoding='utf-8') as f:
        contract_code = f.read()
    
    analyzer = ContractAnalyzer(use_slither=use_slither)
    features = analyzer.analyze_contract(contract_code, Path(file_path).stem)
    
    # Calculate additional complexity metrics
    complexity_calc = ContractComplexityCalculator()
    cyclomatic_complexity = complexity_calc.calculate_cyclomatic_complexity(contract_code)
    halstead_metrics = complexity_calc.calculate_halstead_metrics(contract_code)
    
    additional_metrics = {
        'cyclomatic_complexity': cyclomatic_complexity,
        'halstead_metrics': halstead_metrics,
        'file_size_bytes': len(contract_code.encode('utf-8')),
        'file_path': file_path,
    }
    
    return features, additional_metrics

# Example usage and testing
if __name__ == "__main__":
    # Test with a sample contract
    sample_contract = """
    pragma solidity ^0.8.0;
    
    contract SimpleToken {
        mapping(address => uint256) public balances;
        uint256 public totalSupply;
        address public owner;
        
        event Transfer(address indexed from, address indexed to, uint256 value);
        
        modifier onlyOwner() {
            require(msg.sender == owner, "Not owner");
            _;
        }
        
        constructor(uint256 _totalSupply) {
            totalSupply = _totalSupply;
            owner = msg.sender;
            balances[msg.sender] = _totalSupply;
        }
        
        function transfer(address to, uint256 amount) public returns (bool) {
            require(balances[msg.sender] >= amount, "Insufficient balance");
            require(to != address(0), "Invalid address");
            
            balances[msg.sender] -= amount;
            balances[to] += amount;
            
            emit Transfer(msg.sender, to, amount);
            return true;
        }
        
        function withdraw() public onlyOwner {
            payable(owner).transfer(address(this).balance);
        }
        
        receive() external payable {}
    }
    """
    
    analyzer = ContractAnalyzer(use_slither=False)  # Use regex for testing
    features = analyzer.analyze_contract(sample_contract)
    
    print("Extracted Contract Features:")
    print(json.dumps(asdict(features), indent=2))
    
    # Calculate complexity metrics
    calc = ContractComplexityCalculator()
    complexity = calc.calculate_cyclomatic_complexity(sample_contract)
    halstead = calc.calculate_halstead_metrics(sample_contract)
    
    print(f"\nCyclomatic Complexity: {complexity}")
    print("Halstead Metrics:")
    print(json.dumps(halstead, indent=2))