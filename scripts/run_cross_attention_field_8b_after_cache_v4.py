from __future__ import annotations

import _bootstrap  # noqa: F401

import scripts.run_cross_attention_field_8b_after_cache_v2 as base


_RUN = base._run


def _v4_run(**kwargs):
    replacements = {
        "scripts/run_cross_attention_reader_8b_v2.py": "scripts/run_cross_attention_reader_8b_v4.py",
        "scripts/validate_cross_attention_reader_posttrain_8b_v2.py": "scripts/validate_cross_attention_reader_posttrain_8b_v4.py",
    }
    script = str(kwargs["script"])
    kwargs["script"] = replacements.get(script, script)
    return _RUN(**kwargs)


def main() -> None:
    base._run = _v4_run
    base.main()


if __name__ == "__main__":
    main()

