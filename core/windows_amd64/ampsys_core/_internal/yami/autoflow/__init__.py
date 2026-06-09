"""
Yami AutoFlow Package
=====================

高级封装层，实现"一键式综合 (One-Click Synthesis)"。

用户只需提供带标签的器件列表和性能规格，
系统自动完成拓扑分析、KCL 传播、尺寸综合。

核心特性:
1. 双向驱动 (Dual-Driven):
   - Supply-Driven: 有 Tail/Reference 时，电流向下传播
   - Demand-Driven: 无 Tail 时，从 GBW 反推电流

2. 通用标签体系:
   - 锚点类: REFERENCE, TAIL, VOLTAGE_BIAS
   - 信号处理类: INPUT_DIFF, INPUT_COMMON, ACTIVE_LOAD, CASCODE
   - 无源器件类: RESISTOR_LOAD, LOAD_CAP, MILLER_C

使用示例:
    from yami.autoflow import AutoFlow, Device, Tag
    
    # 定义电路拓扑 (无尾电流源的电阻负载共源级)
    topology = [
        Device(name='M1', type='nmos', tag=Tag.INPUT_COMMON, 
               nodes=['d', 'g', 'gnd', 'gnd']),
        Device(name='R1', type='res', tag=Tag.RESISTOR_LOAD, 
               nodes=['vdd', 'd']),
        Device(name='CL', type='cap', tag=Tag.LOAD_CAP, 
               nodes=['d', 'gnd'], value=1e-12)
    ]
    
    # 定义基因 (只需 L 和 gm/ID)
    genome = {
        'L_M1': 0.18e-6,
        'gmid_M1': 15.0
    }
    
    # 定义规格 (GBW 驱动)
    specs = {
        'gbw': 200e6,
        'v_drop_res': 0.4
    }
    
    # 一键综合
    flow = AutoFlow(topology, db_nmos)
    result = flow.compile(genome, specs)
    
    # 查看结果
    print(f"M1: W={result.devices['M1'].W*1e6:.2f}um, Id={result.devices['M1'].Id*1e6:.2f}uA")
    print(f"R1: R={result.devices['R1'].value/1e3:.2f}kΩ")

基于《Analog IC Autopilot 终极架构文档》实现。
"""

from .tags import Tag, Device, is_anchor_tag, is_input_tag, is_passive_tag, get_default_current_ratio
from .topology import CircuitTopology, DependencySolver
from .flow import AutoFlow, AutoFlowConfig
from .cosim import run_cosim, cosim_from_result

__all__ = [
    'Tag',
    'Device', 
    'CircuitTopology',
    'DependencySolver',
    'AutoFlow',
    'AutoFlowConfig',
    'is_anchor_tag',
    'is_input_tag',
    'is_passive_tag',
    'get_default_current_ratio',
    'run_cosim',
    'cosim_from_result',
]

__version__ = '1.0.0'
