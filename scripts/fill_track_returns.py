# -*- coding: utf-8 -*-
"""清单前视收益回填脚本（幂等可重跑）。

用法：
    python -m scripts.fill_track_returns [limit]

从容器/宿主跑均可；只读 stock_daily 现有历史，不自动拉数据。
对应路由 /api/track/fill（GET/POST）。
"""
from __future__ import annotations

import json
import sys

from backtest import tracker


def main() -> int:
    limit = 0
    if len(sys.argv) > 1:
        try:
            limit = max(int(sys.argv[1]), 0)
        except ValueError:
            print("limit 须为非负整数，忽略", file=sys.stderr)
    res = tracker.fill_returns(limit=limit)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())