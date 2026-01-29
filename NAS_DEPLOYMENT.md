# NAS 部署指南

## 文件結構（NAS 上）

在你的 NAS 上，所有文件應該在同一個目錄下：

```
/volume1/docker/kiro-gateway/  (或你的 NAS 路徑)
├── compose.nas.yml          # Docker Compose 配置
├── .env                     # 環境變數配置
├── debug_logs/              # 日誌目錄（自動創建）
└── (不需要源代碼)
```

## 部署步驟

### 1. 準備 Docker Image

#### 方法 A: 在本地構建並傳輸到 NAS（推薦）

```bash
# 在你的開發機器上（/home/tim/kiro-gateway）
cd /home/tim/kiro-gateway

# 構建 Docker image
docker build -t kiro-gateway:latest -f docker/Dockerfile .

# 保存 image 為 tar 文件
docker save kiro-gateway:latest -o kiro-gateway.tar

# 傳輸到 NAS（替換為你的 NAS IP 和路徑）
scp kiro-gateway.tar admin@your-nas-ip:/volume1/docker/kiro-gateway/

# 在 NAS 上加載 image
ssh admin@your-nas-ip
cd /volume1/docker/kiro-gateway
docker load -i kiro-gateway.tar
```

#### 方法 B: 直接在 NAS 上構建（如果 NAS 有源代碼）

```bash
# SSH 到 NAS
ssh admin@your-nas-ip

# 進入項目目錄
cd /volume1/docker/kiro-gateway

# 構建 image
docker build -t kiro-gateway:latest -f Dockerfile .
```

### 2. 準備配置文件

在 NAS 上創建目錄並複製文件：

```bash
# SSH 到 NAS
ssh admin@your-nas-ip

# 創建目錄
mkdir -p /volume1/docker/kiro-gateway/debug_logs

# 從開發機器複製配置文件
# 在你的開發機器上執行：
scp /home/tim/kiro-gateway/compose.nas.yml admin@your-nas-ip:/volume1/docker/kiro-gateway/
scp /home/tim/kiro-gateway/.env admin@your-nas-ip:/volume1/docker/kiro-gateway/
```

### 3. 編輯 .env 文件（如果需要）

```bash
# 在 NAS 上
cd /volume1/docker/kiro-gateway
vi .env  # 或使用 nano

# 確認以下設置：
# PROXY_API_KEY=TimLiHomeServer
# HTTP_MAX_CONNECTIONS=300
# HTTP_MAX_KEEPALIVE_CONNECTIONS=150
# HTTP_KEEPALIVE_EXPIRY=60.0
# HTTP_POOL_TIMEOUT=none
```

### 4. 啟動容器

```bash
# 在 NAS 上
cd /volume1/docker/kiro-gateway

# 啟動容器
docker-compose -f compose.nas.yml up -d

# 查看日誌
docker logs -f kiro-gateway

# 或使用 docker compose（新版本）
docker compose -f compose.nas.yml up -d
```

### 5. 首次登入 kiro-cli

```bash
# 進入容器
docker exec -it kiro-gateway bash

# 執行 kiro-cli 登入
kiro-cli login

# 按照提示完成登入
# 登入完成後，按 Ctrl+D 退出容器
```

## 驗證部署

### 檢查容器狀態
```bash
docker ps | grep kiro-gateway
```

### 檢查日誌
```bash
docker logs kiro-gateway | grep "HTTP connection pool configured"
```

應該看到：
```
HTTP connection pool configured: max_connections=300, max_keepalive=150, keepalive_expiry=60.0s, pool_timeout=None
```

### 測試 API
```bash
# 從 NAS 或其他機器測試
curl -X POST http://your-nas-ip:8001/v1/chat/completions \
  -H "Authorization: Bearer TimLiHomeServer" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": false
  }'
```

## 管理命令

### 查看日誌
```bash
cd /volume1/docker/kiro-gateway
docker logs -f kiro-gateway
```

### 重啟容器
```bash
cd /volume1/docker/kiro-gateway
docker-compose -f compose.nas.yml restart
```

### 停止容器
```bash
cd /volume1/docker/kiro-gateway
docker-compose -f compose.nas.yml down
```

### 更新容器
```bash
# 1. 傳輸新的 image 到 NAS
# 2. 加載新 image
docker load -i kiro-gateway.tar

# 3. 重新創建容器
cd /volume1/docker/kiro-gateway
docker-compose -f compose.nas.yml down
docker-compose -f compose.nas.yml up -d
```

### 查看資源使用
```bash
docker stats kiro-gateway
```

## NAS 特定注意事項

### 1. 持久化數據
- kiro-cli 憑證存儲在 Docker volume `kiro-cli-data` 中
- 即使容器重啟，憑證也會保留
- 如果需要備份憑證：
  ```bash
  docker run --rm -v kiro-cli-data:/data -v $(pwd):/backup alpine tar czf /backup/kiro-cli-backup.tar.gz -C /data .
  ```

### 2. 端口映射
- 默認映射：`8001:8000`（NAS 的 8001 端口 → 容器的 8000 端口）
- 如果 8001 端口被佔用，修改 `compose.nas.yml` 中的 `ports` 設置

### 3. 資源限制
- 當前設置：2 核心 / 512MB RAM
- 如果 NAS 資源充足，可以調整 `compose.nas.yml` 中的 `deploy.resources` 設置

### 4. 自動啟動
- `restart: unless-stopped` 確保容器在 NAS 重啟後自動啟動
- 如果需要禁用自動啟動，改為 `restart: "no"`

### 5. 網絡模式
- 使用 `network_mode: bridge`
- 如果需要使用 host 網絡，改為 `network_mode: host`（端口映射會失效）

## 故障排除

### 問題：容器無法啟動
```bash
# 查看詳細錯誤
docker logs kiro-gateway

# 檢查 .env 文件
cat .env | grep -v "^#" | grep -v "^$"

# 檢查端口是否被佔用
netstat -tuln | grep 8001
```

### 問題：無法連接到 API
```bash
# 檢查容器是否運行
docker ps | grep kiro-gateway

# 檢查防火牆設置（NAS 防火牆）
# 確保 8001 端口已開放

# 測試本地連接
docker exec kiro-gateway curl -I http://localhost:8000/health
```

### 問題：PoolTimeout 錯誤
```bash
# 檢查環境變數是否生效
docker exec kiro-gateway env | grep HTTP_

# 如果沒有生效，重新創建容器
docker-compose -f compose.nas.yml down
docker-compose -f compose.nas.yml up -d --force-recreate
```

### 問題：kiro-cli 憑證丟失
```bash
# 檢查 volume 是否存在
docker volume ls | grep kiro-cli-data

# 如果 volume 不存在，重新登入
docker exec -it kiro-gateway kiro-cli login
```

## 升級指南

### 升級到新版本
1. 在開發機器上構建新 image
2. 保存並傳輸到 NAS
3. 在 NAS 上執行：
   ```bash
   cd /volume1/docker/kiro-gateway
   docker-compose -f compose.nas.yml down
   docker load -i kiro-gateway-new.tar
   docker-compose -f compose.nas.yml up -d
   ```

### 回滾到舊版本
1. 保留舊版本的 tar 文件
2. 加載舊版本 image
3. 重新創建容器

## 監控建議

### 設置日誌輪轉
已在 `compose.nas.yml` 中配置：
- 最大日誌大小：10MB
- 保留文件數：3 個

### 定期檢查
```bash
# 每週檢查一次
docker logs kiro-gateway | grep -i error | tail -20
docker stats kiro-gateway --no-stream
```

## 安全建議

1. **修改默認密碼**：在 `.env` 中修改 `PROXY_API_KEY`
2. **限制訪問**：在 NAS 防火牆中只允許特定 IP 訪問 8001 端口
3. **定期更新**：定期更新 Docker image 以獲取安全補丁
4. **備份憑證**：定期備份 `kiro-cli-data` volume

## 聯繫支持

如果遇到問題：
1. 查看 `debug_logs/` 目錄中的詳細日誌
2. 檢查 GitHub Issues: https://github.com/jwadow/kiro-gateway/issues
3. 提供日誌和錯誤信息以便診斷
