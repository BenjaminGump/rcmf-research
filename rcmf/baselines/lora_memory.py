from rcmf.baselines.external import require_official_baseline


def build_lora_memory_baseline(*args, **kwargs):
    require_official_baseline("LoRA/SFT memory")

