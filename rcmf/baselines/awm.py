from rcmf.baselines.external import require_official_baseline


def build_awm_baseline(*args, **kwargs):
    require_official_baseline("AWM")

