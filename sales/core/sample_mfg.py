"""제조업 샘플 데이터 — core/sample_industry.py 의 '제조' 업종 (예전 이름 그대로 쓸 수 있게 남겨 둠)."""
from __future__ import annotations

from typing import Optional

from . import sample_industry

PRODUCTS = sample_industry.PRESETS["제조"]["products"]


def seed(customers: int = 20, months: int = 12, rnd_seed: Optional[int] = None) -> dict:
    return sample_industry.seed("제조", customers=customers, months=months, rnd_seed=rnd_seed)
