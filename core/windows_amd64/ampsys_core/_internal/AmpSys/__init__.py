"""
AmpSys - Current-Specified Analog Amplifier Synthesis Engine
=============================================================

基于 YAMI 核心引擎的电流指定型模拟运放综合器。

核心理念:
    用户显式指定每个晶体管的漏极电流 (Id)，
    系统负责 gmid/L 全局优化和物理一致性验证。

用法:
    from AmpSys import AmpFlow, MosIntent, PassiveIntent

    I_tail = 100e-6
    topology = [
        MosIntent('M5', 'nmos', ['Vs','Vb_tail','GND','GND'], Id=I_tail),
        MosIntent('M1', 'nmos', ['D1','Vin','Vs','GND'],      Id=I_tail/2, match_group='diff'),
        MosIntent('M2', 'nmos', ['Vout','Vb_inn','Vs','GND'], Id=I_tail/2, match_group='diff'),
        MosIntent('M3', 'pmos', ['D1','D1','VDD','VDD'],      Id=I_tail/2, match_group='load'),
        MosIntent('M4', 'pmos', ['Vout','D1','VDD','VDD'],    Id=I_tail/2, match_group='load'),
    ]

    flow = AmpFlow.from_pdk(pdk_path, 'n18', 'p18', L_min=0.18e-6)
    flow.set_topology(topology)
    result = flow.optimize(strategy='Balanced', specs={...})
"""

from .core.intent import MosIntent, PassiveIntent
from .core.validator import KCLValidator
from .engine.flow import AmpFlow, AmpFlowConfig

__all__ = [
    'AmpFlow',
    'AmpFlowConfig',
    'MosIntent',
    'PassiveIntent',
    'KCLValidator',
]
