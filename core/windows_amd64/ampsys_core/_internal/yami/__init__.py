"""
Yami - Analog IC Autopilot
==========================

通用的、以设计目标为导向的模拟 IC 自动尺寸综合引擎。

Yami 基于《Analog IC Autopilot 终极架构文档 v3.2》实现，
采用 gm/ID 方法学和因果倒置 (Inversion of Causality) 核心算法。

核心特性:
---------
1. **因果倒置**: 从设计意图 (gm/ID, L) 反推几何尺寸 (W)，100% 确保 DC 收敛
2. **自举负载迭代**: 精确考虑寄生电容的收敛计算
3. **通用引擎**: 拓扑由用户注入，引擎只负责计算
4. **设计导向策略**: 支持功耗优先、高速优先、高增益优先等多种优化目标
5. **两级约束系统**: 硬约束 (Stage 1) 和软惩罚 (Stage 2)

快速开始:
--------
```python
from yami import (
    DesignContext, Genome, GeneBlock,
    Synthesizer, Evaluator, ObjectiveFactory,
    ProcessConfig, PerformanceSpec, FunctionalBlock, BlockType
)

# 1. 创建设计上下文
context = DesignContext(
    process=ProcessConfig.tsmc180(),
    spec=PerformanceSpec(gain_min=60, gbw_min=10e6, pm_min=60),
    netlist="..."  # 用户提供的 SPICE 网表
)

# 2. 定义功能模块
context.add_block(FunctionalBlock(
    name="Input_Pair",
    block_type=BlockType.INPUT_PAIR,
    mos_names=["M1", "M2"],
    mos_type="nmos"
))

# 3. 设置回调函数
context.current_distributor = my_current_distributor
context.headroom_callback = my_headroom_callback

# 4. 加载 LUT 数据库 (来自 TheScanner)
from TheScanner import load_db
context.nmos_db = load_db("nmos_lut.pkl")
context.pmos_db = load_db("pmos_lut.pkl")

# 5. 创建基因组和综合器
genome = Genome(I_total=100e-6)
genome.set_block("Input_Pair", GeneBlock(gmid=15, L=0.5e-6))

synth = Synthesizer(context)
sized_data = synth.synthesize(genome)

# 6. AC 评估
evaluator = Evaluator(context)
ac_result = evaluator.evaluate(sized_data)

# 7. 计算适应度
objective = ObjectiveFactory.create("High_Speed", context.spec)
fitness = objective.calculate_fitness(sized_data, ac_result)
```

模块结构:
--------
- `yami.core`: 核心数据结构 (DesignContext, Genome, SizedData, ACResult)
- `yami.objectives`: 设计导向策略模式 (LowPower, HighSpeed, HighGain, Balanced)
- `yami.synthesizer`: 尺寸综合器，实现自举负载迭代
- `yami.evaluator`: AC 评估器，连接 ACSolver
- `yami.sanity_check`: 物理约束检查函数

依赖:
----
- NumPy
- SymPy
- TheScanner (用于 LUT 查询)
- ACSolver (用于符号化 AC 分析)

版本: 1.0.0
作者: Analog IC Autopilot Team
"""

__version__ = "1.0.0"
__author__ = "Analog IC Autopilot Team"

# ============================================================================
# Core Data Structures
# ============================================================================
from .core import (
    # 枚举类型
    BlockType,
    
    # 配置类
    ProcessConfig,
    PerformanceSpec,
    
    # 功能模块
    FunctionalBlock,
    
    # 基因编码
    GeneBlock,
    Genome,
    PassiveIntent,
    
    # 综合结果
    SizedMOS,
    SizedData,
    
    # AC 结果
    ACResult,
    
    # 设计上下文
    DesignContext,
    
    # 优化结果
    OptimizationResult,
    
    # 物理常数和工具函数
    K_BOLTZMANN,
    Q_ELECTRON,
    thermal_voltage,
    vdsat_jespers,
    thermal_noise_psd,
)

# ============================================================================
# Design Objectives (Strategy Pattern)
# ============================================================================
from .objectives import (
    # 基类
    DesignObjective,
    FitnessResult,
    
    # 具体策略
    LowPowerStrategy,
    HighSpeedStrategy,
    HighGainStrategy,
    BalancedStrategy,
    NoiseAwareStrategy,
    
    # 工厂类
    ObjectiveFactory,
    
    # 工具函数
    calculate_slew_rate,
    check_pole_separation,
)

# ============================================================================
# Synthesizer (Self-Loading Iteration)
# ============================================================================
from .synthesizer import (
    # 配置
    SynthesizerConfig,
    
    # 核心综合器
    Synthesizer,
    
    # LUT 接口
    LUTInterface,
    
    # 偏置网络
    BiasNetworkSynthesizer,
    
    # 工具函数
    estimate_vds_distribution,
    create_initial_genome,
    create_random_genome,
)

# ============================================================================
# Evaluator (ACSolver Integration)
# ============================================================================
from .evaluator import (
    # 配置
    EvaluatorConfig,
    
    # 参数映射
    ParameterMapper,
    
    # 评估器
    Evaluator,
    NumericEvaluator,
    
    # 工具函数
    map_params_to_acsolver,
    calculate_fom,
    check_stability_criteria,
)

# ============================================================================
# Optimizer (Genetic Algorithm)
# ============================================================================
from .optimizer import (
    # 配置
    OptimizerConfig,
    
    # 个体
    Individual,
    
    # 选择算子
    SelectionOperator,
    TournamentSelection,
    RouletteSelection,
    
    # 交叉算子
    CrossoverOperator,
    BlendCrossover,
    UniformCrossover,
    
    # 变异算子
    MutationOperator,
    
    # 优化器
    GeneticOptimizer,
    DifferentialEvolution,
    
    # 工厂函数
    create_optimizer,
)

# ============================================================================
# Sanity Checks (Physics Constraints)
# ============================================================================
from .sanity_check import (
    # 检查结果
    CheckResult,
    
    # 基础检查函数
    check_saturation,
    check_headroom_jespers,
    check_bias_master_vdsat,
    check_icmr_lower,
    check_icmr_upper,
    check_folded_cascode_starvation,
    check_mirror_vds_matching,
    check_current_consistency,
    check_kcl_node,
    check_lut_bounds,
    check_geometry_constraints,
    check_output_swing,
    
    # 电压余度检查器
    HeadroomChecker,
    
    # 通用检查
    run_all_checks,
)

# ============================================================================
# v4.0: Graph-Based DC Checker (通用拓扑检查)
# ============================================================================
from .graph_checker import (
    # 数据结构
    ComponentType,
    DCComponent,
    DCPath,
    DCViolation,
    CircuitGraph,
    
    # 检查器
    GraphDCChecker,
    
    # 便捷函数
    run_generic_drc,
)

# ============================================================================
# v4.0: Layout Rules (确定性 Finger 计算)
# ============================================================================
from .layout_rules import (
    # 配置
    LayoutConfig,
    
    # 计算函数
    calculate_fingers,
    snap_to_grid,
    validate_finger_geometry,
    recalculate_mos_geometry,
)

# ============================================================================
# Public API
# ============================================================================
__all__ = [
    # Version
    '__version__',
    '__author__',
    
    # Core - Enums
    'BlockType',
    
    # Core - Config
    'ProcessConfig',
    'PerformanceSpec',
    
    # Core - Blocks
    'FunctionalBlock',
    
    # Core - Genome
    'GeneBlock',
    'Genome',
    
    # Core - Results
    'SizedMOS',
    'SizedData',
    'ACResult',
    'OptimizationResult',
    
    # Core - Context
    'DesignContext',
    
    # Core - Constants
    'K_BOLTZMANN',
    'Q_ELECTRON',
    'thermal_voltage',
    'vdsat_jespers',
    'thermal_noise_psd',
    
    # Objectives
    'DesignObjective',
    'FitnessResult',
    'LowPowerStrategy',
    'HighSpeedStrategy',
    'HighGainStrategy',
    'BalancedStrategy',
    'NoiseAwareStrategy',
    'ObjectiveFactory',
    'calculate_slew_rate',
    'check_pole_separation',
    
    # Synthesizer
    'SynthesizerConfig',
    'Synthesizer',
    'LUTInterface',
    'BiasNetworkSynthesizer',
    'estimate_vds_distribution',
    'create_initial_genome',
    'create_random_genome',
    
    # Evaluator
    'EvaluatorConfig',
    'ParameterMapper',
    'Evaluator',
    'NumericEvaluator',
    'map_params_to_acsolver',
    'calculate_fom',
    'check_stability_criteria',
    
    # Optimizer
    'OptimizerConfig',
    'Individual',
    'SelectionOperator',
    'TournamentSelection',
    'RouletteSelection',
    'CrossoverOperator',
    'BlendCrossover',
    'UniformCrossover',
    'MutationOperator',
    'GeneticOptimizer',
    'DifferentialEvolution',
    'create_optimizer',
    
    # Sanity Check
    'CheckResult',
    'check_saturation',
    'check_headroom_jespers',
    'check_bias_master_vdsat',
    'check_icmr_lower',
    'check_icmr_upper',
    'check_folded_cascode_starvation',
    'check_mirror_vds_matching',
    'check_current_consistency',
    'check_lut_bounds',
    'check_geometry_constraints',
    'check_output_swing',
    'HeadroomChecker',
    'create_5t_ota_headroom_checker',
    'create_folded_cascode_headroom_checker',
    'run_all_checks',
]

def get_version() -> str:
    """获取版本号"""
    return __version__

def print_banner():
    """打印启动横幅"""
    banner = f"""
╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║   ██╗   ██╗ █████╗ ███╗   ███╗██╗                            ║
║   ╚██╗ ██╔╝██╔══██╗████╗ ████║██║                            ║
║    ╚████╔╝ ███████║██╔████╔██║██║                            ║
║     ╚██╔╝  ██╔══██║██║╚██╔╝██║██║                            ║
║      ██║   ██║  ██║██║ ╚═╝ ██║██║                            ║
║      ╚═╝   ╚═╝  ╚═╝╚═╝     ╚═╝╚═╝                            ║
║                                                               ║
║   Analog IC Autopilot - v{__version__:<36}║
║   Generic Design-Objective-Oriented Sizing Engine            ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
"""
    print(banner)
