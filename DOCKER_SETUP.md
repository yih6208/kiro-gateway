# Docker 部署指南 - HTTP 連接池優化

## 已完成的修改

### 1. 代碼修改
- ✅ `kiro/config.py` - 添加 HTTP 連接池環境變數支援
- ✅ `main.py` - 使用環境變數配置連接池
- ✅ `.env.example` - 添加配置文檔

### 2. 配置文件
- ✅ `.env` - 添加優化的連接池設置（針對 512MB RAM / 2 CPU）
- ✅ `docker/docker-compose.deploy.yml` - 更新為使用 .env 文件

## 當前配置（針對 512MB RAM 優化）

```env
# HTTP 連接池設置
HTTP_MAX_CONNECTIONS=300              # 最大總連接數
HTTP_MAX_KEEPALIVE_CONNECTIONS=150    # 保持活躍的連接數
HTTP_KEEPALIVE_EXPIRY=60.0            # 連接保持時間（秒）
HTTP_POOL_TIMEOUT=none                # 無限等待（防止 PoolTimeout）
```

### 為什麼這些值？

- **300 總連接數**: 足夠處理突發流量（你的日誌顯示峰值 37 個並發）
- **150 keep-alive**: 可以重用大部分連接，減少建立新連接的開銷
- **60 秒過期**: 更長的重用時間，減少連接建立/關閉的頻率
- **無限等待**: 避免 PoolTimeout 錯誤

### 內存消耗估算

- 基礎應用: ~200MB
- 150 個連接: ~7.5MB (每個連接約 50KB)
- **總計**: ~210MB（剩餘 ~300MB 緩衝）

## 如何使用

### 方法 1: 重新構建並啟動（推薦）

```bash
cd /home/tim/kiro-gateway

# 重新構建 Docker image（包含新代碼）
docker build -t kiro-gateway_kiro-gateway:latest -f docker/Dockerfile .

# 停止舊容器
docker-compose -f docker/docker-compose.deploy.yml down

# 啟動新容器（會自動讀取 .env）
docker-compose -f docker/docker-compose.deploy.yml up -d

# 查看日誌
docker logs -f kiro-gateway
```

### 方法 2: 直接修改 .env 並重啟（如果已經構建過）

```bash
cd /home/tim/kiro-gateway

# 編輯 .env 文件（如果需要調整參數）
nano .env

# 重啟容器
docker-compose -f docker/docker-compose.deploy.yml restart

# 查看日誌確認新配置
docker logs -f kiro-gateway | grep "HTTP connection pool configured"
```

## 驗證配置

啟動後，你應該在日誌中看到：

```
HTTP connection pool configured: max_connections=300, max_keepalive=150, keepalive_expiry=60.0s, pool_timeout=None
```

## 調整建議

### 如果還是遇到 PoolTimeout

增加連接數：
```env
HTTP_MAX_CONNECTIONS=500
HTTP_MAX_KEEPALIVE_CONNECTIONS=250
```

### 如果內存不足（OOM）

減少連接數：
```env
HTTP_MAX_CONNECTIONS=200
HTTP_MAX_KEEPALIVE_CONNECTIONS=100
```

### 如果想要更激進的連接重用

延長過期時間：
```env
HTTP_KEEPALIVE_EXPIRY=120.0  # 2 分鐘
```

## 監控

### 查看實時日誌
```bash
docker logs -f kiro-gateway
```

### 查看 PoolTimeout 錯誤
```bash
docker logs kiro-gateway | grep PoolTimeout
```

### 查看內存使用
```bash
docker stats kiro-gateway
```

## 故障排除

### 問題: 容器啟動失敗
```bash
# 查看詳細錯誤
docker logs kiro-gateway

# 檢查 .env 文件格式
cat .env | grep HTTP_
```

### 問題: 配置沒有生效
```bash
# 確認環境變數已傳入容器
docker exec kiro-gateway env | grep HTTP_

# 重新構建 image
docker build -t kiro-gateway_kiro-gateway:latest -f docker/Dockerfile .
docker-compose -f docker/docker-compose.deploy.yml up -d --force-recreate
```

### 問題: 還是有 PoolTimeout
1. 檢查 `HTTP_POOL_TIMEOUT` 是否為 `none`
2. 增加 `HTTP_MAX_KEEPALIVE_CONNECTIONS` 到 200 或更高
3. 檢查網絡連接是否穩定

## 文件位置

- 主配置: `/home/tim/kiro-gateway/.env`
- Docker Compose: `/home/tim/kiro-gateway/docker/docker-compose.deploy.yml`
- 日誌: `/home/tim/kiro-gateway/debug_logs/`
- 代碼配置: `/home/tim/kiro-gateway/kiro/config.py`

## 下一步

1. 重新構建 Docker image
2. 重啟容器
3. 監控日誌，確認沒有 PoolTimeout 錯誤
4. 如果需要，根據實際情況調整參數
