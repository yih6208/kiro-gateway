#!/usr/bin/env python3
"""
Kiro Gateway 壓力測試腳本
測試高並發情況下的連接池性能
"""

import asyncio
import aiohttp
import time
import random
from datetime import datetime
import statistics

# 配置
BASE_URL = "http://localhost:8000"  # 本地測試
API_KEY = "TimLiHomeServer"
CONCURRENT_REQUESTS = 20  # 並發請求數（建議從小開始）
TOTAL_REQUESTS = 200  # 總請求數
BATCH_DELAY = 2.0  # 每批請求之間的延遲（秒）
RAMP_UP = True  # 是否逐步增加並發數（更安全）
ADD_JITTER = True  # 是否添加隨機延遲（模擬真實用戶）

# 測試請求
REQUEST_BODY = {
    "model": "claude-haiku-4.5",  # 使用正確的模型名稱
    "messages": [{"role": "user", "content": "Hello, this is a stress test."}],
    "stream": False,
    "max_tokens": 100
}

# 統計數據
results = {
    "success": 0,
    "failed": 0,
    "timeouts": 0,
    "pool_timeout": 0,
    "response_times": [],
    "errors": []
}


async def make_request(session, request_id):
    """發送單個請求"""
    # 添加隨機延遲（模擬真實用戶行為）
    if ADD_JITTER:
        jitter = random.uniform(0, 0.5)  # 0-0.5秒隨機延遲
        await asyncio.sleep(jitter)

    start_time = time.time()
    try:
        async with session.post(
            f"{BASE_URL}/v1/chat/completions",
            json=REQUEST_BODY,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            },
            timeout=aiohttp.ClientTimeout(total=60)
        ) as response:
            elapsed = time.time() - start_time

            if response.status == 200:
                results["success"] += 1
                results["response_times"].append(elapsed)
                print(f"✓ Request {request_id}: {response.status} ({elapsed:.2f}s)")
            else:
                results["failed"] += 1
                error_text = await response.text()
                results["errors"].append(f"Request {request_id}: {response.status} - {error_text[:100]}")
                print(f"✗ Request {request_id}: {response.status}")

                # 檢查是否是 PoolTimeout 錯誤
                if "PoolTimeout" in error_text or "pool" in error_text.lower():
                    results["pool_timeout"] += 1

    except asyncio.TimeoutError:
        elapsed = time.time() - start_time
        results["timeouts"] += 1
        results["errors"].append(f"Request {request_id}: Timeout after {elapsed:.2f}s")
        print(f"⏱ Request {request_id}: Timeout")
    except Exception as e:
        elapsed = time.time() - start_time
        results["failed"] += 1
        results["errors"].append(f"Request {request_id}: {type(e).__name__} - {str(e)}")
        print(f"✗ Request {request_id}: {type(e).__name__}")


async def run_stress_test():
    """執行壓力測試"""
    print("=" * 80)
    print("Kiro Gateway 壓力測試")
    print("=" * 80)
    print(f"目標: {BASE_URL}")
    print(f"並發數: {CONCURRENT_REQUESTS}")
    print(f"總請求數: {TOTAL_REQUESTS}")
    print(f"批次延遲: {BATCH_DELAY}s")
    print(f"漸進式測試: {'是' if RAMP_UP else '否'}")
    print(f"隨機抖動: {'是' if ADD_JITTER else '否'}")
    print(f"開始時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    print()

    # 創建連接池
    connector = aiohttp.TCPConnector(
        limit=CONCURRENT_REQUESTS,
        limit_per_host=CONCURRENT_REQUESTS
    )

    start_time = time.time()

    async with aiohttp.ClientSession(connector=connector) as session:
        request_count = 0
        batch_num = 0

        # 漸進式測試：逐步增加並發數
        if RAMP_UP:
            # 從小並發開始，逐步增加
            ramp_steps = [
                (5, "熱身階段"),
                (10, "低並發"),
                (CONCURRENT_REQUESTS // 2, "中並發"),
                (CONCURRENT_REQUESTS, "目標並發")
            ]

            for concurrent, phase_name in ramp_steps:
                if request_count >= TOTAL_REQUESTS:
                    break

                print(f"\n--- {phase_name}: {concurrent} 並發 ---")

                # 計算這個階段要發送多少請求
                # 如果是最後一個階段，發送所有剩餘請求
                if phase_name == "目標並發":
                    remaining = TOTAL_REQUESTS - request_count
                else:
                    # 其他階段發送 concurrent * 3 個請求（更多測試）
                    remaining = min(concurrent * 3, TOTAL_REQUESTS - request_count)

                tasks = []
                for i in range(remaining):
                    request_count += 1
                    task = make_request(session, request_count)
                    tasks.append(task)

                    # 每達到並發數就執行一批
                    if len(tasks) >= concurrent:
                        await asyncio.gather(*tasks)
                        tasks = []

                        # 批次之間延遲（但不在階段最後一批後延遲）
                        if BATCH_DELAY > 0 and len(tasks) == 0 and i < remaining - 1:
                            print(f"  ⏸  批次延遲 {BATCH_DELAY}s...")
                            await asyncio.sleep(BATCH_DELAY)

                # 處理剩餘任務
                if tasks:
                    await asyncio.gather(*tasks)

                # 階段之間延遲（但不在最後一個階段後）
                if BATCH_DELAY > 0 and request_count < TOTAL_REQUESTS:
                    await asyncio.sleep(BATCH_DELAY)

        # 標準測試：固定並發數
        else:
            tasks = []
            for i in range(TOTAL_REQUESTS):
                request_count += 1
                task = make_request(session, request_count)
                tasks.append(task)

                # 每 CONCURRENT_REQUESTS 個請求執行一批
                if len(tasks) >= CONCURRENT_REQUESTS:
                    batch_num += 1
                    print(f"\n--- 批次 {batch_num} ---")
                    await asyncio.gather(*tasks)
                    tasks = []

                    # 批次之間延遲
                    if BATCH_DELAY > 0 and request_count < TOTAL_REQUESTS:
                        print(f"  ⏸  批次延遲 {BATCH_DELAY}s...")
                        await asyncio.sleep(BATCH_DELAY)

            # 處理剩餘的任務
            if tasks:
                batch_num += 1
                print(f"\n--- 批次 {batch_num} (最後) ---")
                await asyncio.gather(*tasks)

    total_time = time.time() - start_time

    # 打印結果
    print()
    print("=" * 80)
    print("測試結果")
    print("=" * 80)
    print(f"總耗時: {total_time:.2f}s")
    print(f"實際發送: {request_count} 個請求")
    print(f"成功: {results['success']}")
    print(f"失敗: {results['failed']}")
    print(f"超時: {results['timeouts']}")
    print(f"PoolTimeout 錯誤: {results['pool_timeout']}")

    # 計算實際完成的請求數
    total_completed = results['success'] + results['failed'] + results['timeouts']
    if total_completed > 0:
        success_rate = (results['success'] / total_completed) * 100
        print(f"成功率: {success_rate:.1f}% ({results['success']}/{total_completed})")
    print()

    if results["response_times"]:
        print("響應時間統計:")
        print(f"  平均: {statistics.mean(results['response_times']):.2f}s")
        print(f"  中位數: {statistics.median(results['response_times']):.2f}s")
        print(f"  最小: {min(results['response_times']):.2f}s")
        print(f"  最大: {max(results['response_times']):.2f}s")
        print(f"  標準差: {statistics.stdev(results['response_times']):.2f}s")

    print()
    print(f"吞吐量: {results['success'] / total_time:.2f} 請求/秒")
    print()

    if results["errors"]:
        print("錯誤詳情（前 10 個）:")
        for error in results["errors"][:10]:
            print(f"  - {error}")
        if len(results["errors"]) > 10:
            print(f"  ... 還有 {len(results['errors']) - 10} 個錯誤")

    print("=" * 80)

    # 判斷測試是否通過
    if results["pool_timeout"] > 0:
        print("⚠️  警告: 檢測到 PoolTimeout 錯誤！需要增加連接池配置。")
    elif total_completed > 0 and results["success"] == total_completed:
        print("✅ 測試通過！所有請求都成功。")
    elif total_completed > 0 and (results["success"] / total_completed) > 0.95:
        print(f"✅ 測試基本通過！成功率 > 95%")
    elif total_completed > 0:
        print(f"❌ 測試失敗！成功率: {(results['success'] / total_completed) * 100:.1f}%")
    else:
        print("❌ 測試失敗！沒有完成任何請求。")


if __name__ == "__main__":
    asyncio.run(run_stress_test())
