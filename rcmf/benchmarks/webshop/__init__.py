"""AgentBench-FC WebShop adaptation surface for portable RCMF."""

from rcmf.benchmarks.webshop.adapter import (
    PROMPT_PROFILE,
    SPLIT_RANGES,
    WebShopPortableAdapterV2,
    create_webshop_portable_adapter_v1,
)

__all__ = [
    "PROMPT_PROFILE",
    "SPLIT_RANGES",
    "WebShopPortableAdapterV2",
    "create_webshop_portable_adapter_v1",
]
