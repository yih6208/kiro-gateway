#!/bin/bash
set -e

echo "=========================================="
echo "🚀 Kiro Gateway 啟動中..."
echo "=========================================="
echo ""

# 檢查是否已經有憑證
KIRO_CLI_DB="/root/.local/share/kiro-cli/data.sqlite3"

if [ -f "$KIRO_CLI_DB" ]; then
    echo "✅ 發現現有的 kiro-cli 憑證"
    echo "📝 如需重新登入，請刪除 Docker volume: docker-compose down -v"
    echo ""
else
    echo "🔐 首次啟動，需要進行 kiro-cli 登入"
    echo "=========================================="
    echo ""
    echo "請按照以下步驟操作："
    echo "1. 複製下方顯示的登入網址"
    echo "2. 在瀏覽器中打開該網址"
    echo "3. 完成 AWS Builder ID 或企業帳號登入"
    echo "4. 登入成功後，容器將自動啟動 Kiro Gateway"
    echo ""
    echo "=========================================="
    echo ""

    # 檢查是否設置了登入參數
    if [ -z "$KIRO_START_URL" ] || [ -z "$KIRO_LOGIN_REGION" ]; then
        echo "⚠️  錯誤: 缺少必要的登入參數"
        echo ""
        echo "請在 .env 文件中設置以下環境變數："
        echo "  KIRO_START_URL=\"https://amzn.awsapps.com/start\""
        echo "  KIRO_LOGIN_REGION=\"us-east-1\""
        echo ""
        echo "或者，您可以手動進入容器執行登入："
        echo "  docker exec -it kiro-gateway bash"
        echo "  kiro-cli login --license=pro"
        echo ""
        exit 1
    fi

    # 設置 license 參數（預設為 pro）
    export LICENSE="${KIRO_LICENSE:-pro}"

    # 確保環境變數可用
    export KIRO_START_URL
    export KIRO_LOGIN_REGION

    echo "📝 使用以下配置進行登入："
    echo "   Start URL: $KIRO_START_URL"
    echo "   Region: $KIRO_LOGIN_REGION"
    echo "   License: $LICENSE"
    echo ""

    # 執行 kiro-cli login（使用 expect 或手動輸入）
    # 由於 kiro-cli 需要互動式輸入，我們使用 expect 來自動化
    if command -v expect >/dev/null 2>&1; then
        expect << 'EOF'
set timeout 300
set start_url $env(KIRO_START_URL)
set region $env(KIRO_LOGIN_REGION)
set license $env(LICENSE)

spawn kiro-cli login --license=$license --use-device-flow

# 選擇登入方式
expect {
    "Select login method" {
        send "Use with IDC Account\r"
        exp_continue
    }
    "Enter Start URL" {
        send "$start_url\r"
    }
    timeout {
        puts "\n❌ 錯誤: 等待登入提示超時"
        exit 1
    }
}

# 輸入 Region
expect {
    "Enter Region" {
        send "$region\r"
    }
    timeout {
        puts "\n❌ 錯誤: 等待 Region 提示超時"
        exit 1
    }
}

# 等待登入成功訊息
expect {
    "Logged in successfully" {
        puts "\n✅ 登入成功確認！"
        # 不要立即退出，等待進程自然結束以確保數據寫入完成
        exp_continue
    }
    "Device authorized" {
        # 看到 Device authorized 後繼續等待 Logged in successfully
        exp_continue
    }
    "Open this URL:" {
        # 顯示登入 URL，繼續等待
        exp_continue
    }
    "Confirm the following code" {
        # 顯示確認碼，繼續等待
        exp_continue
    }
    eof {
        # 進程正常結束，這是我們想要的
        puts "\n✅ kiro-cli 登入流程完成"
        exit 0
    }
    timeout {
        puts "\n❌ 錯誤: 登入超時（5分鐘內未完成）"
        puts "請確保您已在瀏覽器中完成登入授權"
        exit 1
    }
}
EOF

        if [ $? -ne 0 ]; then
            echo ""
            echo "❌ 登入失敗！"
            echo ""
            echo "請檢查："
            echo "  1. Start URL 是否正確"
            echo "  2. Region 是否正確"
            echo "  3. 是否在瀏覽器中完成了授權"
            echo ""
            exit 1
        fi
    else
        echo "⚠️  警告: 未安裝 expect，將使用互動式登入"
        echo "請手動輸入以下資訊："
        kiro-cli login --license=$LICENSE
    fi

    echo ""
    echo "=========================================="
    echo "✅ 登入成功！"
    echo "=========================================="
    echo ""
fi

# 啟動 Kiro Gateway
echo "🌐 啟動 Kiro Gateway 服務..."
echo "📡 服務地址: http://localhost:8000"
echo "=========================================="
echo ""

# 執行 Python 主程序
exec python main.py
