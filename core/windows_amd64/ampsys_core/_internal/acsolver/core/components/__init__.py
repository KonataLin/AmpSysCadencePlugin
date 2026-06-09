"""
元件模块
=======

定义电路中使用的各种元件，包括：
- 基础元件类
- 无源元件（电阻、电容、电感）
- MOS管及其小信号模型
"""

from .base import Component, ComponentType, Terminal
from .passive import Resistor, Capacitor, Inductor
from .mosfet import MOSFET, MOSFETConfig, MOSFETType

__all__ = [
    'Component',
    'ComponentType',
    'Terminal',
    'Resistor',
    'Capacitor',
    'Inductor',
    'MOSFET',
    'MOSFETConfig',
    'MOSFETType',
]
