"""
TheScanner - HSPICE gm/ID 扫描引擎

轻量级本地 HSPICE 仿真工具，用于 gm/ID 方法学的 MOS 管参数扫描与查询。

快速开始:

    from TheScanner import ScannerConfig, HSPICENetlistGenerator, HSPICESimulator
    
    # 1. 配置
    config = ScannerConfig(
        hspice_cmd="hspice",
        model_path="C:/PDK/models.lib",
        model_lib="tt",
        dut_name="nch",
        mos_type="nmos",
        L_list=[0.18e-6, 0.5e-6, 1e-6]
    )
    
    # 2. 生成网表
    gen = HSPICENetlistGenerator(config)
    netlist = gen.generate(L=0.18e-6, vds=0.6, vsb=0)
    
    # 3. 仿真
    sim = HSPICESimulator(config)
    result = sim.run_and_parse(netlist, L=0.18e-6, VDS=0.6, VSB=0)
    
    # 4. 访问结果
    print(f"ID: {result.id}")
    print(f"gm: {result.gm}")
"""

__version__ = "2.0.0"
__author__ = "ACSolver Team"

# 配置模块
from .config import ScannerConfig

# HSPICE 网表生成器
from .netlist_generator import HSPICENetlistGenerator

# HSPICE 仿真器
from .simulator import HSPICESimulator, HSPICEError, SweepResult

# 扫描器
from .scanner import MOSScanner, run_scan

# 数据库
from .database import MOSDatabase, save_db, load_db, export_to_numpy, export_to_csv
from .database import K_BOLTZMANN, Q_ELECTRON
from .database import set_temp_dir, get_temp_dir

# 查询接口
from .lookup import query, Lookup, design_transistor
from .lookup import lookup_vgs, extract_ekv_params

# HSPICE 缓存和并行执行
from .lookup import HSPICECache, get_hspice_cache, run_parallel_hspice

# .DATA 批量扫描 (High Accuracy Mode)
from .lookup import run_data_batch_hspice

# Fast Mode (纯数据库查询,不调用 HSPICE)
from .lookup import set_fast_mode, is_fast_mode

# 批量仿真管理器
from .batch_manager import (
    BatchHSPICEManager, 
    BatchTimeoutError, 
    BatchSimulationError,
    batch_query
)

__all__ = [
    # 配置
    "ScannerConfig",
    
    # HSPICE 网表生成
    "HSPICENetlistGenerator",
    
    # HSPICE 仿真
    "HSPICESimulator",
    "HSPICEError",
    "SweepResult",
    
    # 扫描器
    "MOSScanner",
    "run_scan",
    
    # 数据库
    "MOSDatabase",
    "save_db",
    "load_db",
    "export_to_numpy",
    "export_to_csv",
    "K_BOLTZMANN",
    "Q_ELECTRON",
    "set_temp_dir",
    "get_temp_dir",
    
    # 查询
    "query",
    "Lookup",
    "design_transistor",
    "lookup_vgs",
    "extract_ekv_params",
    
    # HSPICE 缓存和并行
    "HSPICECache",
    "get_hspice_cache",
    "run_parallel_hspice",
    
    # .DATA 批量扫描 (High Accuracy)
    "run_data_batch_hspice",
    
    # Fast Mode
    "set_fast_mode",
    "is_fast_mode",
    
    # 批量仿真
    "BatchHSPICEManager",
    "BatchTimeoutError",
    "BatchSimulationError",
    "batch_query",
]
