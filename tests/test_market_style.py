# -*- coding: utf-8 -*-
"""market_style 宏观风格标注层测试。纯 mock 不触网。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from screener import market_style as ms


def test_macro_baseline_aligned():
    """基线字段齐全：as_of/tilt/note/support_boards/weak_boards。"""
    bl = ms.MACRO_BASELINE
    assert bl.get("as_of")
    assert bl.get("tilt")
    assert bl.get("note")
    assert bl.get("support_boards") and bl.get("weak_boards")


def test_boards_aligned_to_industry():
    """support/weak 板块名应对齐 industry_board 真实行业板块名，不得混风格/概念名。"""
    style_names = {"先进制造", "智能驾驶", "算力", "CPO", "人形机器人", "智能家居", "减速器"}
    for b in ms.MACRO_BASELINE["support_boards"] + ms.MACRO_BASELINE["weak_boards"]:
        assert isinstance(b, str) and b
        assert b not in style_names, f"{b} 是风格/概念名,industry_board 反查不到个股"


def test_style_context_structure():
    """style_context 返回标注 dict：tilt/macro_note/support_boards/weak_boards/as_of/maturity。"""
    ctx = ms.style_context()
    for k in ("tilt", "macro_note", "support_boards", "weak_boards", "as_of", "maturity"):
        assert k in ctx
    assert isinstance(ctx["support_boards"], list)
    assert isinstance(ctx["weak_boards"], list)


def test_style_context_returns_copy():
    """返回投影拷贝，改返回值不污染模块级 MACRO_BASELINE（防多请求状态泄漏）。"""
    ctx = ms.style_context()
    ctx["support_boards"].append("污染")
    assert "污染" not in ms.MACRO_BASELINE["support_boards"]
