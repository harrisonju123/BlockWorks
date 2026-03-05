"""Revenue sharing protocol for multi-agent workflows.

Provides split calculation, settlement through state channels, and
earnings tracking. Protocol fee + burn mechanics incentivize network
participation while maintaining deflationary token pressure.
"""

from blockthrough.revenue.calculator import calculate_shares
from blockthrough.revenue.settlement import SettlementEngine
from blockthrough.revenue.types import (
    ProtocolFee,
    RevenueConfig,
    RevenueShare,
    Settlement,
    SplitBasis,
    SplitRule,
)

__all__ = [
    "calculate_shares",
    "ProtocolFee",
    "RevenueConfig",
    "RevenueShare",
    "Settlement",
    "SettlementEngine",
    "SplitBasis",
    "SplitRule",
]
