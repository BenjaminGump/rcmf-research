from rcmf.baselines.external import require_official_baseline


def build_amem_baseline(*args, **kwargs):
    require_official_baseline("A-MEM/Mem0")

