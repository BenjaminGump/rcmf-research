from rcmf.baselines.external import require_official_baseline


def build_ace_baseline(*args, **kwargs):
    require_official_baseline("ACE")

