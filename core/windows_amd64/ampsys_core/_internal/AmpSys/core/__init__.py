"""
AmpSys Core - 数据结构与验证
"""

from .intent import MosIntent, PassiveIntent
from .validator import KCLValidator

__all__ = ['MosIntent', 'PassiveIntent', 'KCLValidator']
