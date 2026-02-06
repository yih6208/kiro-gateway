"""
API key manager for generating and validating client API keys.
"""

import secrets
from datetime import datetime
from typing import Optional

import bcrypt
from sqlalchemy import func, select, update

from kiro.database import APIKey, Database, UsageRecord


class APIKeyManager:
    """Manages API key lifecycle and validation."""

    def __init__(self, db: Database):
        """
        Initialize API key manager.

        Args:
            db: Database instance
        """
        self.db = db

    async def create_key(
        self,
        user_id: int,
        name: str,
        rate_limit_rpm: Optional[int] = None,
        rate_limit_tpm: Optional[int] = None,
        usage_limit_tokens: Optional[int] = None,
        usage_limit_requests: Optional[int] = None,
    ) -> tuple[str, dict]:
        """
        Generate new API key.

        Args:
            user_id: User ID who owns this key
            name: Descriptive name for the key
            rate_limit_rpm: Requests per minute limit (optional)
            rate_limit_tpm: Tokens per minute limit (optional)
            usage_limit_tokens: Total token usage limit (optional)
            usage_limit_requests: Total request count limit (optional)

        Returns:
            Tuple of (plaintext_key, metadata_dict)
        """
        # Generate sk-xxxxx format key
        random_part = secrets.token_urlsafe(32)
        key_plaintext = f"sk-{random_part}"

        # Extract key_id (first 12 chars after sk-)
        key_id = key_plaintext[:15]  # sk- + first 12 chars

        # Hash the full key with bcrypt (cost factor 12)
        key_hash = bcrypt.hashpw(key_plaintext.encode(), bcrypt.gensalt(12)).decode()

        # Store in database
        async with self.db.SessionLocal() as session:
            api_key = APIKey(
                key_id=key_id,
                key_hash=key_hash,
                user_id=user_id,
                name=name,
                is_active=True,
                rate_limit_rpm=rate_limit_rpm,
                rate_limit_tpm=rate_limit_tpm,
                usage_limit_tokens=usage_limit_tokens,
                usage_limit_requests=usage_limit_requests,
                created_at=datetime.utcnow(),
            )
            session.add(api_key)
            await session.commit()
            await session.refresh(api_key)

            metadata = {
                "api_key_id": api_key.id,
                "key_id": api_key.key_id,
                "name": api_key.name,
                "user_id": api_key.user_id,
                "rate_limit_rpm": api_key.rate_limit_rpm,
                "rate_limit_tpm": api_key.rate_limit_tpm,
                "usage_limit_tokens": api_key.usage_limit_tokens,
                "usage_limit_requests": api_key.usage_limit_requests,
                "created_at": api_key.created_at.isoformat(),
            }

        return key_plaintext, metadata

    async def validate_key(self, key_plaintext: str) -> Optional[dict]:
        """
        Validate API key and return metadata.

        Args:
            key_plaintext: Full API key (sk-xxxxx...)

        Returns:
            Metadata dict if valid, None if invalid/expired
        """
        if not key_plaintext.startswith("sk-"):
            return None

        # Extract key_id prefix
        key_id = key_plaintext[:15]

        async with self.db.SessionLocal() as session:
            # Query by key_id prefix
            stmt = select(APIKey).where(APIKey.key_id == key_id)
            result = await session.execute(stmt)
            api_key = result.scalar_one_or_none()

            if not api_key:
                return None

            # Verify hash with bcrypt
            if not bcrypt.checkpw(key_plaintext.encode(), api_key.key_hash.encode()):
                return None

            # Check if active
            if not api_key.is_active:
                return None

            # Update last_used_at
            stmt = (
                update(APIKey)
                .where(APIKey.id == api_key.id)
                .values(last_used_at=datetime.utcnow())
            )
            await session.execute(stmt)
            await session.commit()

            return {
                "api_key_id": api_key.id,
                "key_id": api_key.key_id,
                "name": api_key.name,
                "user_id": api_key.user_id,
                "rate_limit_rpm": api_key.rate_limit_rpm,
                "rate_limit_tpm": api_key.rate_limit_tpm,
                "usage_limit_tokens": api_key.usage_limit_tokens,
                "usage_limit_requests": api_key.usage_limit_requests,
                "is_active": api_key.is_active,
            }

    async def deactivate_key(self, api_key_id: int) -> bool:
        """
        Deactivate an API key.

        Args:
            api_key_id: API key ID to deactivate

        Returns:
            True if successful, False if key not found
        """
        async with self.db.SessionLocal() as session:
            stmt = (
                update(APIKey)
                .where(APIKey.id == api_key_id)
                .values(is_active=False)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def delete_key(self, api_key_id: int) -> bool:
        """
        Permanently delete an API key.

        Args:
            api_key_id: API key ID to delete

        Returns:
            True if successful, False if key not found
        """
        async with self.db.SessionLocal() as session:
            from sqlalchemy import delete
            stmt = delete(APIKey).where(APIKey.id == api_key_id)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def get_usage_stats(self, api_key_id: int) -> dict:
        """
        Get usage statistics for an API key.

        Args:
            api_key_id: API key ID

        Returns:
            Dict with total_tokens, total_requests, input_tokens, output_tokens
        """
        async with self.db.SessionLocal() as session:
            stmt = select(
                func.count(UsageRecord.id).label("total_requests"),
                func.sum(UsageRecord.total_tokens).label("total_tokens"),
                func.sum(UsageRecord.input_tokens).label("input_tokens"),
                func.sum(UsageRecord.output_tokens).label("output_tokens"),
            ).where(UsageRecord.api_key_id == api_key_id)

            result = await session.execute(stmt)
            row = result.first()

            return {
                "total_requests": row.total_requests or 0,
                "total_tokens": row.total_tokens or 0,
                "input_tokens": row.input_tokens or 0,
                "output_tokens": row.output_tokens or 0,
            }

    async def get_usage_by_model(self, api_key_id: int) -> list[dict]:
        """
        Get usage statistics for an API key grouped by model.

        Args:
            api_key_id: API key ID

        Returns:
            List of dicts with per-model usage stats
        """
        async with self.db.SessionLocal() as session:
            stmt = (
                select(
                    UsageRecord.model,
                    func.count(UsageRecord.id).label("requests"),
                    func.sum(UsageRecord.input_tokens).label("input_tokens"),
                    func.sum(UsageRecord.output_tokens).label("output_tokens"),
                    func.sum(UsageRecord.total_tokens).label("total_tokens"),
                )
                .where(UsageRecord.api_key_id == api_key_id)
                .group_by(UsageRecord.model)
                .order_by(func.sum(UsageRecord.total_tokens).desc())
            )

            result = await session.execute(stmt)
            rows = result.all()

            return [
                {
                    "model": row.model,
                    "requests": row.requests or 0,
                    "input_tokens": row.input_tokens or 0,
                    "output_tokens": row.output_tokens or 0,
                    "total_tokens": row.total_tokens or 0,
                }
                for row in rows
            ]

    async def check_usage_limits(self, api_key_id: int) -> tuple[bool, Optional[str]]:
        """
        Check if API key has exceeded usage limits.

        Args:
            api_key_id: API key ID

        Returns:
            Tuple of (is_within_limits, error_message)
        """
        async with self.db.SessionLocal() as session:
            # Get API key limits
            stmt = select(APIKey).where(APIKey.id == api_key_id)
            result = await session.execute(stmt)
            api_key = result.scalar_one_or_none()

            if not api_key:
                return False, "API key not found"

            # If no limits set, always within limits
            if not api_key.usage_limit_tokens and not api_key.usage_limit_requests:
                return True, None

            # Get current usage
            stats = await self.get_usage_stats(api_key_id)

            # Check token limit
            if api_key.usage_limit_tokens and stats["total_tokens"] >= api_key.usage_limit_tokens:
                return False, f"Token limit exceeded ({stats['total_tokens']}/{api_key.usage_limit_tokens})"

            # Check request limit
            if api_key.usage_limit_requests and stats["total_requests"] >= api_key.usage_limit_requests:
                return False, f"Request limit exceeded ({stats['total_requests']}/{api_key.usage_limit_requests})"

            return True, None

    async def update_limits(
        self,
        api_key_id: int,
        usage_limit_tokens: Optional[int] = None,
        usage_limit_requests: Optional[int] = None,
    ) -> bool:
        """
        Update usage limits for an API key.

        Args:
            api_key_id: API key ID
            usage_limit_tokens: New token limit (None to remove limit)
            usage_limit_requests: New request limit (None to remove limit)

        Returns:
            True if successful, False if key not found
        """
        async with self.db.SessionLocal() as session:
            stmt = (
                update(APIKey)
                .where(APIKey.id == api_key_id)
                .values(
                    usage_limit_tokens=usage_limit_tokens,
                    usage_limit_requests=usage_limit_requests,
                )
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def list_keys(self, user_id: Optional[int] = None) -> list[dict]:
        """
        List all API keys with usage statistics, optionally filtered by user.

        Args:
            user_id: Filter by user ID (optional)

        Returns:
            List of API key metadata dicts with usage stats
        """
        async with self.db.SessionLocal() as session:
            stmt = select(APIKey)
            if user_id is not None:
                stmt = stmt.where(APIKey.user_id == user_id)
            stmt = stmt.order_by(APIKey.created_at.desc())

            result = await session.execute(stmt)
            keys = result.scalars().all()

            keys_with_stats = []
            for key in keys:
                stats = await self.get_usage_stats(key.id)
                model_usage = await self.get_usage_by_model(key.id)
                keys_with_stats.append({
                    "api_key_id": key.id,
                    "key_id": key.key_id,
                    "name": key.name,
                    "user_id": key.user_id,
                    "is_active": key.is_active,
                    "rate_limit_rpm": key.rate_limit_rpm,
                    "rate_limit_tpm": key.rate_limit_tpm,
                    "usage_limit_tokens": key.usage_limit_tokens,
                    "usage_limit_requests": key.usage_limit_requests,
                    "created_at": key.created_at.isoformat(),
                    "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
                    "total_tokens": stats["total_tokens"],
                    "total_requests": stats["total_requests"],
                    "input_tokens": stats["input_tokens"],
                    "output_tokens": stats["output_tokens"],
                    "model_usage": model_usage,
                })

            return keys_with_stats
