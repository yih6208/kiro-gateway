FROM astral/uv:python3.11-bookworm-slim

WORKDIR /app

# 安裝必要工具（kiro-cli 需要 curl, bash, unzip, expect）
RUN apt-get update && \
    apt-get install -y curl bash unzip expect && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 安裝 kiro-cli
RUN curl -fsSL https://cli.kiro.dev/install | bash

# 將 kiro-cli 加入 PATH
ENV PATH="/root/.local/bin:${PATH}"

# 複製依賴文件（從根目錄）
COPY requirements.txt .

# 使用 uv 安裝 Python 依賴
RUN uv pip install --system --no-cache -r requirements.txt

# 複製應用程式碼（從根目錄）
COPY kiro ./kiro
COPY main.py .
COPY templates ./templates
COPY migrate_add_usage_limits.py .

# 創建必要的目錄
RUN mkdir -p debug_logs /root/.local/share/kiro-cli

# 創建空的 .env 文件以通過驗證檢查
# 實際配置來自 docker-compose.yml 的環境變數
RUN touch /app/.env

# 複製啟動腳本（從 docker/ 子目錄）
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

# 使用自定義啟動腳本
ENTRYPOINT ["/entrypoint.sh"]
