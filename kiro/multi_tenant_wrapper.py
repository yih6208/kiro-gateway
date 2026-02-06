"""
Multi-tenant request wrapper for tracking usage and managing account pool.
"""

import time
from typing import Any, Dict, Optional

from loguru import logger


class MultiTenantRequestContext:
    """Context manager for multi-tenant requests with usage tracking."""

    def __init__(
        self,
        account_pool,
        usage_tracker,
        api_key_metadata: dict,
        model: str,
        endpoint: str,
    ):
        """
        Initialize request context.

        Args:
            account_pool: AccountPool instance
            usage_tracker: UsageTracker instance
            api_key_metadata: API key metadata from verify_api_key
            model: Model name being used
            endpoint: API endpoint path
        """
        self.account_pool = account_pool
        self.usage_tracker = usage_tracker
        self.api_key_metadata = api_key_metadata
        self.model = model
        self.endpoint = endpoint

        self.account_id: Optional[int] = None
        self.auth_manager = None
        self.start_time = time.time()
        self.input_tokens = 0
        self.output_tokens = 0
        self.status_code = 200

    async def __aenter__(self):
        """Get account from pool."""
        self.account_id, self.auth_manager = await self.account_pool.get_account()
        logger.debug(f"Using Kiro account {self.account_id} for request")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Track usage and report status."""
        duration_ms = int((time.time() - self.start_time) * 1000)

        if exc_type is not None:
            # Error occurred
            self.status_code = 500
            error_msg = str(exc_val) if exc_val else "Unknown error"
            await self.account_pool.report_error(self.account_id, error_msg)
        else:
            # Success
            await self.account_pool.report_success(self.account_id)

        # Track usage
        await self.usage_tracker.record_request(
            api_key_id=self.api_key_metadata["api_key_id"],
            kiro_account_id=self.account_id,
            model=self.model,
            endpoint=self.endpoint,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            status_code=self.status_code,
            duration_ms=duration_ms,
        )

        logger.debug(
            f"Request completed: {self.status_code}, "
            f"tokens={self.input_tokens}/{self.output_tokens}, "
            f"duration={duration_ms}ms"
        )

    def update_usage(self, input_tokens: int = 0, output_tokens: int = 0):
        """
        Update token usage counters.

        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
        """
        if input_tokens > 0:
            self.input_tokens = input_tokens
        if output_tokens > 0:
            self.output_tokens = output_tokens

    def set_status(self, status_code: int):
        """
        Set HTTP status code.

        Args:
            status_code: HTTP status code
        """
        self.status_code = status_code
