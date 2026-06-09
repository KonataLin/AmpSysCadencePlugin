"""
ACSolver - 模拟IC小信号传递函数求解器
=======================================

一个基于Python的模拟集成电路小信号分析工具，支持Virtuoso网表输入，
自动求解s域传递函数表达式。

主要功能:
- 解析Virtuoso格式网表
- 支持理想R、C、L元件
- 支持拉扎维MOS管小信号模型
- 自动求解任意节点的传递函数
- 输出LaTeX格式表达式

作者: ACSolver Team
版本: 1.0.0
"""

__version__ = "1.0.0"
__author__ = "ACSolver Team"

from .api.circuit_analyzer import CircuitAnalyzer, AnalysisConfig, AnalysisResult
from .core.symbols import SymbolManager
from .core.nodes import Node, NodeManager
from .core.components import (
    Component,
    Resistor,
    Capacitor,
    Inductor,
    MOSFET,
    MOSFETConfig
)
from .parser.netlist_parser import NetlistParser
from .solver.mna_builder import MNABuilder
from .solver.equation_solver import EquationSolver
from .solver.transfer_function import TransferFunctionSolver
from .solver.noise_analyzer import NoiseAnalyzer
from .core.noise import NoiseSource, NoiseResult
from .output.latex_formatter import LaTeXFormatter

__all__ = [
    'CircuitAnalyzer',
    'AnalysisConfig',
    'AnalysisResult',
    'SymbolManager',
    'Node',
    'NodeManager',
    'Component',
    'Resistor',
    'Capacitor',
    'Inductor',
    'MOSFET',
    'MOSFETConfig',
    'NetlistParser',
    'MNABuilder',
    'EquationSolver',
    'TransferFunctionSolver',
    'LaTeXFormatter',
]
