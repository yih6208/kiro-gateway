"""
Account pool for managing multiple Kiro accounts with load balancing.
"""

import asyncio
from datetime import datetime
from typing import Dict, Optional

from fastapi import HTTPException
from loguru import logger
from sqlalchemy import delete as sa_delete, select, update

from kiro.auth import AuthType, KiroAuthManager
from kiro.database import Database, KiroAccount


class AccountPool:
    """Manages pool of Kiro accounts with intelligent load balancing."""

    def __init__(self, db: Database, error_threshold: int = 3):
        """
        Initialize account pool.

        Args:
            db: Database instance
            error_threshold: Number of consecutive errors before deactivating account
        """
        self.db = db
        self.error_threshold = error_threshold
        self.auth_managers: Dict[int, KiroAuthManager] = {}
        self.lock = asyncio.Lock()
        self.current_index = 0

    async def get_account(self) -> tuple[int, KiroAuthManager]:
        """
        Select best available Kiro account using weighted round-robin.

        Returns:
            Tuple of (account_id, auth_manager)

        Raises:
            HTTPException: If no healthy accounts available
        """
        async with self.lock:
            accounts = await self._get_healthy_accounts()

            if not accounts:
                logger.error("No healthy Kiro accounts available")
                raise HTTPException(
                    status_code=503,
                    detail="No healthy Kiro accounts available. Please add accounts via admin UI.",
                )

            # Weighted round-robin: prioritize by priority field, then round-robin
            selected = self._select_next(accounts)

            # Get or create KiroAuthManager
            if selected.id not in self.auth_managers:
                logger.info(f"Creating auth manager for account {selected.id} ({selected.name})")
                self.auth_managers[selected.id] = await self._create_auth_manager(selected)

            return selected.id, self.auth_managers[selected.id]

    async def report_error(self, account_id: int, error: str):
        """
        Increment error_count for account and deactivate if threshold exceeded.

        Args:
            account_id: Account ID that encountered error
            error: Error message
        """
        async with self.db.SessionLocal() as session:
            stmt = select(KiroAccount).where(KiroAccount.id == account_id)
            result = await session.execute(stmt)
            account = result.scalar_one_or_none()

            if not account:
                return

            new_error_count = account.error_count + 1
            logger.warning(
                f"Account {account_id} ({account.name}) error: {error} "
                f"(error_count: {new_error_count})"
            )

            # Update error tracking
            update_stmt = (
                update(KiroAccount)
                .where(KiroAccount.id == account_id)
                .values(
                    error_count=new_error_count,
                    last_error=error[:500],  # Truncate long errors
                )
            )

            # Deactivate if threshold exceeded
            if new_error_count >= self.error_threshold:
                logger.error(
                    f"Account {account_id} ({account.name}) exceeded error threshold "
                    f"({new_error_count} >= {self.error_threshold}), deactivating"
                )
                update_stmt = update_stmt.values(is_active=False)

            await session.execute(update_stmt)
            await session.commit()

            # Remove from cache if deactivated
            if new_error_count >= self.error_threshold and account_id in self.auth_managers:
                del self.auth_managers[account_id]

    async def report_success(self, account_id: int):
        """
        Reset error_count for account after successful request.

        Args:
            account_id: Account ID that succeeded
        """
        async with self.db.SessionLocal() as session:
            stmt = (
                update(KiroAccount)
                .where(KiroAccount.id == account_id)
                .values(
                    error_count=0,
                    last_error=None,
                    last_success_at=datetime.utcnow(),
                )
            )
            await session.execute(stmt)
            await session.commit()

    async def _get_healthy_accounts(self) -> list[KiroAccount]:
        """
        Get active accounts with error_count below threshold.

        Returns:
            List of healthy KiroAccount objects
        """
        async with self.db.SessionLocal() as session:
            stmt = (
                select(KiroAccount)
                .where(
                    KiroAccount.is_active == True,
                    KiroAccount.error_count < self.error_threshold,
                )
                .order_by(KiroAccount.priority.desc(), KiroAccount.id)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    def _select_next(self, accounts: list[KiroAccount]) -> KiroAccount:
        """
        Select next account using weighted round-robin.

        Accounts with higher priority are selected more frequently.

        Args:
            accounts: List of healthy accounts

        Returns:
            Selected account
        """
        if not accounts:
            raise ValueError("No accounts available")

        # Simple round-robin for now (can be enhanced with priority weighting)
        selected = accounts[self.current_index % len(accounts)]
        self.current_index += 1
        return selected

    async def _create_auth_manager(self, account: KiroAccount) -> KiroAuthManager:
        """
        Create KiroAuthManager from account credentials.

        Args:
            account: KiroAccount database record

        Returns:
            Initialized KiroAuthManager
        """
        # Decrypt credentials
        refresh_token = self.db.decrypt(account.refresh_token_encrypted)
        access_token = self.db.decrypt(account.access_token_encrypted)
        client_id = self.db.decrypt(account.client_id_encrypted)
        client_secret = self.db.decrypt(account.client_secret_encrypted)

        # Determine auth type
        auth_type = AuthType(account.auth_type)

        # Create auth manager based on type
        if auth_type == AuthType.AWS_SSO_OIDC:
            auth_manager = KiroAuthManager(
                refresh_token=refresh_token,
                profile_arn=account.profile_arn,
                region=account.region,
                client_id=client_id,
                client_secret=client_secret,
                multi_tenant_db=self.db,
                multi_tenant_account_id=account.id,
            )
        else:  # KIRO_DESKTOP
            auth_manager = KiroAuthManager(
                refresh_token=refresh_token,
                profile_arn=account.profile_arn,
                region=account.region,
                multi_tenant_db=self.db,
                multi_tenant_account_id=account.id,
            )

        # Pre-populate access token if available and not expired
        if access_token and account.expires_at and account.expires_at > datetime.utcnow():
            auth_manager._access_token = access_token
            auth_manager._expires_at = account.expires_at

        return auth_manager

    async def refresh_account_token(self, account_id: int):
        """
        Manually refresh token for a specific account.

        Args:
            account_id: Account ID to refresh

        Raises:
            HTTPException: If account not found or refresh fails
        """
        async with self.lock:
            if account_id not in self.auth_managers:
                # Load account and create auth manager
                async with self.db.SessionLocal() as session:
                    stmt = select(KiroAccount).where(KiroAccount.id == account_id)
                    result = await session.execute(stmt)
                    account = result.scalar_one_or_none()

                    if not account:
                        raise HTTPException(404, f"Account {account_id} not found")

                    self.auth_managers[account_id] = await self._create_auth_manager(account)

            # Force token refresh
            auth_manager = self.auth_managers[account_id]
            try:
                await auth_manager.get_access_token()
                await self.report_success(account_id)
                logger.info(f"Successfully refreshed token for account {account_id}")
            except Exception as e:
                await self.report_error(account_id, str(e))
                raise HTTPException(500, f"Failed to refresh token: {str(e)}")

    async def list_accounts(self) -> list[dict]:
        """
        List all accounts with their status.

        Returns:
            List of account metadata dicts
        """
        async with self.db.SessionLocal() as session:
            stmt = select(KiroAccount).order_by(KiroAccount.priority.desc(), KiroAccount.id)
            result = await session.execute(stmt)
            accounts = result.scalars().all()

            return [
                {
                    "id": account.id,
                    "name": account.name,
                    "auth_type": account.auth_type,
                    "region": account.region,
                    "is_active": account.is_active,
                    "error_count": account.error_count,
                    "last_error": account.last_error,
                    "last_success_at": (
                        account.last_success_at.isoformat() if account.last_success_at else None
                    ),
                    "priority": account.priority,
                    "created_at": account.created_at.isoformat(),
                }
                for account in accounts
            ]

    async def delete_account(self, account_id: int) -> bool:
        """
        Permanently delete a Kiro account.

        Args:
            account_id: Account ID to delete

        Returns:
            True if deleted, False if not found
        """
        # Remove auth manager from cache
        if account_id in self.auth_managers:
            del self.auth_managers[account_id]

        async with self.db.SessionLocal() as session:
            stmt = sa_delete(KiroAccount).where(KiroAccount.id == account_id)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0
