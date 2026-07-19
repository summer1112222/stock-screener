# A股板块/ETF 筛选器 —— 本地/Docker Desktop 镜像
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    SCREENER_DB=/app/var/stock.db

WORKDIR /app

# 先装依赖(利用层缓存)；用清华镜像加速国内下载
COPY requirements.txt .
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    --trusted-host pypi.tuna.tsinghua.edu.cn \
    -r requirements.txt

# 复制代码
COPY . .
RUN mkdir -p /app/var

EXPOSE 8000

# FastAPI 同源托管 /api/* 与 /web/*；访问 http://localhost:8000/web/index.html
CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000"]
