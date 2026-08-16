# -*- coding: utf-8 -*-
from data.fundamentals import _parse_cn_amount

def test_parse_cn_amount():
    assert _parse_cn_amount("445.1688亿") == 44516880000.0  # 445.1688×1e8 (plan原文44516800000.0少一个8,数学笔误修正)
    assert _parse_cn_amount("57.0895万") == 570895.0
    assert _parse_cn_amount("5234.12") == 5234.12
    assert _parse_cn_amount("-1.2亿") == -120000000.0
    assert _parse_cn_amount("-") is None
    assert _parse_cn_amount("") is None
    assert _parse_cn_amount("89.5552%") == 89.5552
    assert _parse_cn_amount(None) is None
    assert _parse_cn_amount("  16.75 ") == 16.75  # 空白容忍
