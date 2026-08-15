#!/usr/bin/env bash
# deploy.sh — 重建镜像并部署,带健康检查 + 模块自检 + 失败回滚指引
#
# 解决"手动 docker cp 导致镜像漂移":容器镜像与源码同步靠重建而非 cp。
# 用法(git-bash/WSL):
#   ./deploy.sh              # 跑测试 + 重建 + 启动 + 健康检查
#   ./deploy.sh --skip-test  # 跳过测试(快)
#   ./deploy.sh --no-build   # 不重建,仅重启现有镜像
#
# 合规:本脚本仅部署数据筛选/回测研究工具,不涉自动下单。

set -euo pipefail

PORT=8000
HEALTH_URL="http://localhost:${PORT}/api/health"
SKIP_TEST=0; NO_BUILD=0
for a in "$@"; do
  case "$a" in
    --skip-test) SKIP_TEST=1;;
    --no-build)  NO_BUILD=1;;
    *) echo "未知参数: $a"; exit 2;;
  esac
done

cd "$(dirname "$0")"

echo "=== 1. 跑单测(镜像里无 tests/,宿主验证不依赖网络) ==="
if [ "$SKIP_TEST" = 0 ]; then
  python -m pytest tests/ -q || { echo "✗ 测试失败,中止部署(先修测试再上线)"; exit 1; }
else
  echo "(--skip-test 跳过)"
fi

echo "=== 2. 记录旧镜像(回滚用) ==="
OLD_ID=$(docker images -q a-screener:local 2>/dev/null || true)
echo "旧镜像 id: ${OLD_ID:-(无,首次部署)}"

if [ "$NO_BUILD" = 0 ]; then
  echo "=== 3. 重建镜像(docker compose build,清华 PyPI 加速) ==="
  docker compose build
fi

echo "=== 4. 启动新容器(docker compose up -d,SQLite 卷保留) ==="
docker compose up -d

echo "=== 5. 健康检查(轮询 /api/health,最多 60s) ==="
ok=0
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo 000)
  if [ "$code" = 200 ]; then ok=1; break; fi
  sleep 2
done

if [ "$ok" != 1 ]; then
  echo "✗ 健康检查失败(60s 内 /api/health 未返回 200)"
  echo "  查日志: docker logs a-screener --tail 80"
  if [ -n "$OLD_ID" ]; then
    echo "  手动回滚: docker tag $OLD_ID a-screener:local && docker compose up -d"
  fi
  exit 1
fi

echo "✓ /api/health 返回 200"

echo "=== 6. 模块自检(抓镜像漂移:旧镜像缺新函数如 _is_in_session) ==="
SELF=$(docker exec a-screener python -c \
  "from backtest import quality,buffett
m=['quality._is_in_session' if hasattr(quality,'_is_in_session') else 'MISSING quality._is_in_session',
     'buffett.analyze_many deadline_s' if 'deadline_s' in __import__('inspect').signature(buffett.analyze_many).parameters else 'MISSING buffett.deadline_s']
print(' | '.join(m))" 2>/dev/null || echo "exec 失败(容器可能未就绪)")
echo "  $SELF"

echo "=== 7. 路由冒烟(快路由断言非5xx,抓运行时崩溃;quality 慢路由不在此列) ==="
smoke_ok=1
for path in /api/health /api/fields /api/boards /api/market; do
  code=$(curl -s -o /dev/null -w "%{http_code}" -m 10 "http://localhost:${PORT}${path}" 2>/dev/null || echo 000)
  if [ "$code" = 200 ]; then
    echo "  ✓ $path 200"
  else
    echo "  ✗ $path $code(非200,可能有运行时错误)"
    smoke_ok=0
  fi
done
if [ "$smoke_ok" != 1 ]; then
  echo "✗ 路由冒烟有非200,查: docker logs a-screener --tail 80"
fi

echo "=== 部署完成: http://localhost:${PORT}/web/index.html ==="
