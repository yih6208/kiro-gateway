#!/usr/bin/env python3
"""
Kiro Gateway 流式請求壓力測試
測試高並發流式請求的性能
"""

import asyncio
import aiohttp
import time
import random
from datetime import datetime

# 配置
BASE_URL = "https://claude.connexmatrix.com"  # Kiro Gateway 代理地址
API_KEY = "TimLiHomeServer"
CONCURRENT_REQUESTS = 20  # 並發流式請求數
TOTAL_REQUESTS = 100  # 總請求數（流式請求較慢，建議減少）
BATCH_DELAY = 3.0  # 每批請求之間的延遲（秒）- 流式請求建議更長
RAMP_UP = True  # 是否逐步增加並發數（更安全）
ADD_JITTER = True  # 是否添加隨機延遲（模擬真實用戶）

# 測試請求（流式）
REQUEST_BODY = {
    "model": "claude-haiku-4.5",  # 使用正確的模型名稱
    "messages": [{"role": "user", "content": "Write a short poem about coding."}],
    "stream": True,
    "max_tokens": 200
}

# 統計數據
results = {
    "success": 0,
    "failed": 0,
    "timeouts": 0,
    "pool_timeout": 0,
    "first_token_times": [],
    "total_times": [],
    "errors": []
}


async def make_streaming_request(session, request_id):
    """發送流式請求"""
    # 添加隨機延遲（模擬真實用戶行為）
    if ADD_JITTER:
        jitter = random.uniform(0, 1.0)  # 0-1秒隨機延遲（流式請求較慢，延遲可以更長）
        await asyncio.sleep(jitter)

    start_time = time.time()
    first_token_time = None
    chunks_received = 0

    try:
        async with session.post(
            f"{BASE_URL}/v1/chat/completions",
            json=REQUEST_BODY,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            },
            timeout=aiohttp.ClientTimeout(total=120)
        ) as response:

            if response.status != 200:
                results["failed"] += 1
                error_text = await response.text()
                results["errors"].append(f"Request {request_id}: {response.status}")

                if "PoolTimeout" in error_text or "pool" in error_text.lower():
                    results["pool_timeout"] += 1

                print(f"✗ Request {request_id}: {response.status}")
                return

            # 讀取流式響應
            async for line in response.content:
                if first_token_time is None:
                    first_token_time = time.time() - start_time
                    results["first_token_times"].append(first_token_time)

                chunks_received += 1

            total_time = time.time() - start_time
            results["success"] += 1
            results["total_times"].append(total_time)

            print(f"✓ Request {request_id}: {chunks_received} chunks, "
                  f"first token: {first_token_time:.2f}s, total: {total_time:.2f}s")

    except asyncio.TimeoutError:
        elapsed = time.time() - start_time
        results["timeouts"] += 1
        results["errors"].append(f"Request {request_id}: Timeout after {elapsed:.2f}s")
        print(f"⏱ Request {request_id}: Timeout")
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"Request {request_id}: {type(e).__name__}")
        print(f"✗ Request {request_id}: {type(e).__name__}")


async def run_streaming_stress_test():
    """執行流式壓力測試"""
    print("=" * 80)
    print("Kiro Gateway 流式請求壓力測試")
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
            # 從小並發開始，逐步增加（流式請求建議更保守）
            ramp_steps = [
                (2, "熱身階段"),
                (5, "低並發"),
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
                    task = make_streaming_request(session, request_count)
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
                task = make_streaming_request(session, request_count)
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

    if results["first_token_times"]:
        import statistics
        print("首個 Token 時間統計:")
        print(f"  平均: {statistics.mean(results['first_token_times']):.2f}s")
        print(f"  中位數: {statistics.median(results['first_token_times']):.2f}s")
        print(f"  最小: {min(results['first_token_times']):.2f}s")
        print(f"  最大: {max(results['first_token_times']):.2f}s")
        print()

    if results["total_times"]:
        import statistics
        print("總響應時間統計:")
        print(f"  平均: {statistics.mean(results['total_times']):.2f}s")
        print(f"  中位數: {statistics.median(results['total_times']):.2f}s")
        print(f"  最小: {min(results['total_times']):.2f}s")
        print(f"  最大: {max(results['total_times']):.2f}s")
        print()

    print(f"吞吐量: {results['success'] / total_time:.2f} 請求/秒")
    print()

    if results["errors"]:
        print("錯誤詳情（前 10 個）:")
        for error in results["errors"][:10]:
            print(f"  - {error}")

    print("=" * 80)

    if results["pool_timeout"] > 0:
        print("⚠️  警告: 檢測到 PoolTimeout 錯誤！")
    elif total_completed > 0 and results["success"] == total_completed:
        print("✅ 測試通過！所有流式請求都成功。")
    elif total_completed > 0 and (results["success"] / total_completed) > 0.95:
        print(f"✅ 測試基本通過！成功率 > 95%")
    elif total_completed > 0:
        print(f"❌ 測試失敗！成功率: {(results['success'] / total_completed) * 100:.1f}%")
    else:
        print("❌ 測試失敗！沒有完成任何請求。")


if __name__ == "__main__":
    asyncio.run(run_streaming_stress_test())
