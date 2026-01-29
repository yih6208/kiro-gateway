# -*- coding: utf-8 -*-
"""
Global rate limiter for Kiro API requests (FIFO Queue Implementation).

Prevents 429 (Too Many Requests) errors by controlling:
1. Maximum concurrent requests (FIFO queue)
2. Minimum interval between requests (throttling)
3. Global backoff when 429 is detected

Key feature: Requests are processed in FIFO order (first-in, first-out).
"""

import asyncio
import time
from collections import deque
from typing import Optional
from loguru import logger


class GlobalRateLimiter:
    """
    Global rate limiter to prevent 429 errors from Kiro API.

    Features:
    - FIFO queue: Requests are processed in arrival order
    - Limits concurrent requests
    - Enforces minimum interval between requests
    - Global backoff when 429 is detected
    """

    def __init__(
        self,
        max_concurrent: int = 0,  # 0 = unlimited
        min_interval: float = 0.0,  # seconds between requests
        backoff_429: float = 0.0,  # seconds to pause after 429
    ):
        """
        Initialize rate limiter.

        Args:
            max_concurrent: Maximum concurrent requests (0 = unlimited)
            min_interval: Minimum seconds between requests (0 = no throttling)
            backoff_429: Seconds to pause all requests after 429 (0 = disabled)
        """
        self.max_concurrent = max_concurrent
        self.min_interval = min_interval
        self.backoff_429 = backoff_429

        # FIFO queue for concurrent request limiting
        self._current_count = 0  # Number of active requests
        self._waiters: deque[asyncio.Event] = deque()  # FIFO waiting queue
        self._queue_lock = asyncio.Lock()

        # Lock for request interval throttling
        self._throttle_lock = asyncio.Lock()
        self._last_request_time = 0.0

        # Global backoff state
        self._backoff_until = 0.0
        self._backoff_lock = asyncio.Lock()

        # Statistics
        self._total_requests = 0
        self._total_429s = 0
        self._total_wait_time = 0.0
        self._max_queue_length = 0

    async def acquire(self) -> float:
        """
        Acquire permission to make a request (FIFO order).

        This will:
        1. Wait in FIFO queue for concurrent slot
        2. Wait for global backoff (if active)
        3. Wait for minimum interval (if enabled)

        Returns:
            Total wait time in seconds
        """
        wait_start = time.time()

        # Step 1: Wait for concurrent slot (FIFO queue)
        if self.max_concurrent > 0:
            await self._acquire_slot()

        # Step 2: Wait for global backoff (if 429 was received)
        if self.backoff_429 > 0:
            now = time.time()
            if now < self._backoff_until:
                wait_time = self._backoff_until - now
                logger.debug(f"[RateLimiter] Global backoff active, waiting {wait_time:.1f}s...")
                await asyncio.sleep(wait_time)

        # Step 3: Enforce minimum interval between requests
        if self.min_interval > 0:
            async with self._throttle_lock:
                now = time.time()
                elapsed = now - self._last_request_time
                if elapsed < self.min_interval:
                    wait_time = self.min_interval - elapsed
                    await asyncio.sleep(wait_time)
                self._last_request_time = time.time()

        self._total_requests += 1
        total_wait = time.time() - wait_start
        self._total_wait_time += total_wait

        if total_wait > 0.1:
            logger.debug(f"[RateLimiter] Request waited {total_wait:.2f}s in queue")

        return total_wait

    async def _acquire_slot(self):
        """Acquire a concurrent slot using FIFO queue."""
        my_event: Optional[asyncio.Event] = None

        async with self._queue_lock:
            if self._current_count < self.max_concurrent:
                # Slot available, proceed immediately
                self._current_count += 1
                return

            # No slot available, join the queue
            my_event = asyncio.Event()
            self._waiters.append(my_event)

            # Track max queue length for statistics
            queue_len = len(self._waiters)
            if queue_len > self._max_queue_length:
                self._max_queue_length = queue_len

            if queue_len > 0 and queue_len % 10 == 0:
                logger.info(f"[RateLimiter] Queue length: {queue_len} requests waiting")

        # Wait outside the lock to avoid deadlock
        if my_event:
            await my_event.wait()

    async def release(self):
        """Release concurrent slot and wake next waiter (FIFO order)."""
        if self.max_concurrent <= 0:
            return

        async with self._queue_lock:
            if self._waiters:
                # Someone is waiting, wake the first one (FIFO)
                next_event = self._waiters.popleft()
                next_event.set()
                # Don't decrement _current_count, we're passing the slot
            else:
                # No one waiting, release the slot
                self._current_count -= 1

    async def on_429_received(self):
        """
        Called when a 429 error is received.
        Triggers global backoff to pause all requests.
        """
        if self.backoff_429 > 0:
            async with self._backoff_lock:
                new_backoff_until = time.time() + self.backoff_429
                # Only update if this extends the backoff period
                if new_backoff_until > self._backoff_until:
                    self._backoff_until = new_backoff_until
                    self._total_429s += 1
                    logger.warning(
                        f"[RateLimiter] 429 detected! Global backoff for {self.backoff_429}s. "
                        f"All requests paused until {time.strftime('%H:%M:%S', time.localtime(self._backoff_until))}"
                    )

    def is_enabled(self) -> bool:
        """Check if rate limiting is enabled."""
        return (
            self.max_concurrent > 0 or
            self.min_interval > 0 or
            self.backoff_429 > 0
        )

    def get_stats(self) -> dict:
        """Get rate limiter statistics."""
        return {
            "total_requests": self._total_requests,
            "total_429s": self._total_429s,
            "total_wait_time": round(self._total_wait_time, 2),
            "avg_wait_time": round(self._total_wait_time / max(1, self._total_requests), 3),
            "max_queue_length": self._max_queue_length,
            "current_queue_length": len(self._waiters),
            "current_active": self._current_count,
        }


# Global singleton instance
_rate_limiter: Optional[GlobalRateLimiter] = None


def get_rate_limiter() -> Optional[GlobalRateLimiter]:
    """Get the global rate limiter instance."""
    return _rate_limiter


def init_rate_limiter(
    max_concurrent: int = 0,
    min_interval: float = 0.0,
    backoff_429: float = 0.0,
) -> GlobalRateLimiter:
    """
    Initialize the global rate limiter.

    Should be called once at application startup.
    """
    global _rate_limiter
    _rate_limiter = GlobalRateLimiter(
        max_concurrent=max_concurrent,
        min_interval=min_interval,
        backoff_429=backoff_429,
    )

    if _rate_limiter.is_enabled():
        logger.info(
            f"[RateLimiter] Initialized (FIFO mode): "
            f"max_concurrent={max_concurrent}, "
            f"min_interval={min_interval}s, "
            f"backoff_429={backoff_429}s"
        )
    else:
        logger.info("[RateLimiter] Disabled (all limits set to 0)")

    return _rate_limiter
