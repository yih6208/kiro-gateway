"""
Usage tracker for recording and analyzing API usage.
"""

import asyncio
from datetime import datetime
from typing import Optional

from loguru import logger
from sqlalchemy import case, func, select

from kiro.database import Database, UsageRecord


class UsageTracker:
    """Tracks API usage with batched database insertions."""

    def __init__(self, db: Database, batch_size: int = 100):
        """
        Initialize usage tracker.

        Args:
            db: Database instance
            batch_size: Number of records to batch before inserting
        """
        self.db = db
        self.batch_size = batch_size
        self.pending_records = []
        self.lock = asyncio.Lock()

    async def record_request(
        self,
        api_key_id: str,
        kiro_account_id: int,
        model: str,
        endpoint: str,
        input_tokens: int,
        output_tokens: int,
        status_code: int,
        duration_ms: int,
    ):
        """
        Record API request usage (batched for performance).

        Args:
            api_key_id: API key ID that made the request
            kiro_account_id: Kiro account ID that served the request
            model: Model name used
            endpoint: API endpoint called
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            status_code: HTTP status code
            duration_ms: Request duration in milliseconds
        """
        async with self.lock:
            self.pending_records.append(
                {
                    "api_key_id": api_key_id,
                    "kiro_account_id": kiro_account_id,
                    "model": model,
                    "endpoint": endpoint,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                    "status_code": status_code,
                    "request_duration_ms": duration_ms,
                    "timestamp": datetime.utcnow(),
                }
            )

            # Batch insert when threshold reached
            if len(self.pending_records) >= self.batch_size:
                await self._flush_records()

    async def _flush_records(self):
        """
        Flush pending records to database.

        This method should be called with lock already acquired.
        """
        if not self.pending_records:
            return

        records_to_insert = self.pending_records.copy()
        self.pending_records.clear()

        try:
            async with self.db.SessionLocal() as session:
                # Bulk insert
                session.add_all(
                    [UsageRecord(**record) for record in records_to_insert]
                )
                await session.commit()
                logger.debug(f"Flushed {len(records_to_insert)} usage records to database")
        except Exception as e:
            logger.error(f"Failed to flush usage records: {str(e)}")
            # Re-add records to pending queue for retry
            async with self.lock:
                self.pending_records.extend(records_to_insert)

    async def flush(self):
        """
        Manually flush all pending records.

        Should be called on application shutdown.
        """
        async with self.lock:
            await self._flush_records()

    async def get_usage_stats(
        self,
        api_key_id: Optional[str] = None,
        kiro_account_id: Optional[int] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> dict:
        """
        Get aggregated usage statistics.

        Args:
            api_key_id: Filter by API key ID (optional)
            kiro_account_id: Filter by Kiro account ID (optional)
            start_date: Filter by start date (optional)
            end_date: Filter by end date (optional)

        Returns:
            Dict with aggregated statistics
        """
        async with self.db.SessionLocal() as session:
            # Build query
            stmt = select(
                func.count(UsageRecord.id).label("total_requests"),
                func.sum(UsageRecord.input_tokens).label("total_input_tokens"),
                func.sum(UsageRecord.output_tokens).label("total_output_tokens"),
                func.sum(UsageRecord.total_tokens).label("total_tokens"),
                func.avg(UsageRecord.request_duration_ms).label("avg_duration_ms"),
                func.sum(case(
                    (UsageRecord.status_code.between(200, 299), 1),
                    else_=0
                )).label("success_count"),
                func.sum(case(
                    (UsageRecord.status_code >= 400, 1),
                    else_=0
                )).label("fail_count"),
            )

            # Apply filters
            if api_key_id is not None:
                stmt = stmt.where(UsageRecord.api_key_id == api_key_id)
            if kiro_account_id is not None:
                stmt = stmt.where(UsageRecord.kiro_account_id == kiro_account_id)
            if start_date is not None:
                stmt = stmt.where(UsageRecord.timestamp >= start_date)
            if end_date is not None:
                stmt = stmt.where(UsageRecord.timestamp <= end_date)

            result = await session.execute(stmt)
            row = result.one()

            return {
                "total_requests": row.total_requests or 0,
                "total_input_tokens": row.total_input_tokens or 0,
                "total_output_tokens": row.total_output_tokens or 0,
                "total_tokens": row.total_tokens or 0,
                "avg_duration_ms": float(row.avg_duration_ms) if row.avg_duration_ms else 0.0,
                "success_count": row.success_count or 0,
                "fail_count": row.fail_count or 0,
            }

    async def get_usage_by_model(
        self,
        api_key_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> list[dict]:
        """
        Get usage statistics grouped by model.

        Args:
            api_key_id: Filter by API key ID (optional)
            start_date: Filter by start date (optional)
            end_date: Filter by end date (optional)

        Returns:
            List of dicts with per-model statistics
        """
        async with self.db.SessionLocal() as session:
            stmt = select(
                UsageRecord.model,
                func.count(UsageRecord.id).label("request_count"),
                func.sum(UsageRecord.input_tokens).label("input_tokens"),
                func.sum(UsageRecord.output_tokens).label("output_tokens"),
                func.sum(UsageRecord.total_tokens).label("total_tokens"),
                func.sum(case(
                    (UsageRecord.status_code.between(200, 299), 1),
                    else_=0
                )).label("success_count"),
                func.sum(case(
                    (UsageRecord.status_code >= 400, 1),
                    else_=0
                )).label("fail_count"),
            ).group_by(UsageRecord.model)

            # Apply filters
            if api_key_id is not None:
                stmt = stmt.where(UsageRecord.api_key_id == api_key_id)
            if start_date is not None:
                stmt = stmt.where(UsageRecord.timestamp >= start_date)
            if end_date is not None:
                stmt = stmt.where(UsageRecord.timestamp <= end_date)

            stmt = stmt.order_by(func.sum(UsageRecord.total_tokens).desc())

            result = await session.execute(stmt)
            rows = result.all()

            return [
                {
                    "model": row.model,
                    "request_count": row.request_count,
                    "input_tokens": row.input_tokens or 0,
                    "output_tokens": row.output_tokens or 0,
                    "total_tokens": row.total_tokens or 0,
                    "success_count": row.success_count or 0,
                    "fail_count": row.fail_count or 0,
                }
                for row in rows
            ]

    async def get_usage_by_endpoint(
        self,
        api_key_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> list[dict]:
        """
        Get usage statistics grouped by endpoint.

        Args:
            api_key_id: Filter by API key ID (optional)
            start_date: Filter by start date (optional)
            end_date: Filter by end date (optional)

        Returns:
            List of dicts with per-endpoint statistics
        """
        async with self.db.SessionLocal() as session:
            stmt = select(
                UsageRecord.endpoint,
                func.count(UsageRecord.id).label("request_count"),
                func.sum(UsageRecord.total_tokens).label("total_tokens"),
                func.avg(UsageRecord.request_duration_ms).label("avg_duration_ms"),
            ).group_by(UsageRecord.endpoint)

            # Apply filters
            if api_key_id is not None:
                stmt = stmt.where(UsageRecord.api_key_id == api_key_id)
            if start_date is not None:
                stmt = stmt.where(UsageRecord.timestamp >= start_date)
            if end_date is not None:
                stmt = stmt.where(UsageRecord.timestamp <= end_date)

            stmt = stmt.order_by(func.count(UsageRecord.id).desc())

            result = await session.execute(stmt)
            rows = result.all()

            return [
                {
                    "endpoint": row.endpoint,
                    "request_count": row.request_count,
                    "total_tokens": row.total_tokens or 0,
                    "avg_duration_ms": float(row.avg_duration_ms) if row.avg_duration_ms else 0.0,
                }
                for row in rows
            ]

    async def get_recent_requests(
        self,
        api_key_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """
        Get recent requests.

        Args:
            api_key_id: Filter by API key ID (optional)
            limit: Maximum number of records to return

        Returns:
            List of recent request records
        """
        async with self.db.SessionLocal() as session:
            stmt = select(UsageRecord).order_by(UsageRecord.timestamp.desc()).limit(limit)

            if api_key_id is not None:
                stmt = stmt.where(UsageRecord.api_key_id == api_key_id)

            result = await session.execute(stmt)
            records = result.scalars().all()

            return [
                {
                    "id": record.id,
                    "api_key_id": record.api_key_id,
                    "kiro_account_id": record.kiro_account_id,
                    "model": record.model,
                    "endpoint": record.endpoint,
                    "input_tokens": record.input_tokens,
                    "output_tokens": record.output_tokens,
                    "total_tokens": record.total_tokens,
                    "status_code": record.status_code,
                    "request_duration_ms": record.request_duration_ms,
                    "timestamp": record.timestamp.isoformat(),
                }
                for record in records
            ]
