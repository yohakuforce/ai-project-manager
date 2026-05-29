# ============================================================
# AI-Project-Manager Dockerfile
# Windows (Docker Desktop for Windows / WSL2) 前提で動作検証済みのこと
# Windows 固有の注意:
#   - CRLF 問題: .gitattributes で text=auto を設定し LF 統一
#   - ボリュームマウント: compose.yaml の volumes は相対パスを使用
# ============================================================

FROM python:3.12-slim AS base

# システム依存ライブラリ
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- 依存関係レイヤー（キャッシュ最大化） ---
FROM base AS deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- 開発用（ホットリロード対応） ---
FROM deps AS development
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY . .
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]

# --- 本番用 ---
FROM deps AS production
COPY . .
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
USER appuser
EXPOSE 8001
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2"]
