# -*- coding: utf-8 -*-
"""quality 盘口精排测试。mock pytdx_client.get_quote，不真连网。"""
import datetime as dt
from unittest.mock import patch

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import quality


def test_is_in_session_morning():
    # 周一 10:00 = 盘中
    mon = dt.datetime(2026, 8, 10, 10, 0)  # 2026-08-10 周一
    assert quality._is_in_session(mon) is True


def test_is_in_session_afternoon():
    tue = dt.datetime(2026, 8, 11, 14, 30)  # 周二 14:30
    assert quality._is_in_session(tue) is True


def test_is_in_session_lunch():
    mon = dt.datetime(2026, 8, 10, 12, 0)  # 午休 12:00
    assert quality._is_in_session(mon) is False


def test_is_in_session_preopen():
    mon = dt.datetime(2026, 8, 10, 9, 0)  # 盘前 9:00
    assert quality._is_in_session(mon) is False


def test_is_in_session_afterclose():
    mon = dt.datetime(2026, 8, 10, 16, 0)  # 盘后 16:00
    assert quality._is_in_session(mon) is False


def test_is_in_session_weekend():
    sat = dt.datetime(2026, 8, 15, 10, 0)  # 周六
    assert quality._is_in_session(sat) is False
