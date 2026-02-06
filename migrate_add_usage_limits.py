#!/usr/bin/env python3
"""
Database migration script to add usage limit columns to api_keys table.

This script adds:
- usage_limit_tokens: Total token usage limit (optional)
- usage_limit_requests: Total request count limit (optional)

Usage:
    python migrate_add_usage_limits.py
"""

import asyncio
import sys
from pathlib import Path

from loguru import logger
from sqlalchemy import text

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from kiro.config import DATABASE_URL, ENCRYPTION_KEY
from kiro.database import Database


async def migrate():
    """Run migration to add usage limit columns."""
    logger.info("Starting database migration: add usage limit columns")

    db = Database(DATABASE_URL, ENCRYPTION_KEY)

    try:
        async with db.engine.begin() as conn:
            # Check if columns already exist
            result = await conn.execute(text("PRAGMA table_info(api_keys)"))
            columns = [row[1] for row in result.fetchall()]

            if "usage_limit_tokens" in columns and "usage_limit_requests" in columns:
                logger.info("Migration already applied - columns exist")
                return

            # Add usage_limit_tokens column if it doesn't exist
            if "usage_limit_tokens" not in columns:
                logger.info("Adding usage_limit_tokens column...")
                await conn.execute(text(
                    "ALTER TABLE api_keys ADD COLUMN usage_limit_tokens INTEGER"
                ))
                logger.success("Added usage_limit_tokens column")

            # Add usage_limit_requests column if it doesn't exist
            if "usage_limit_requests" not in columns:
                logger.info("Adding usage_limit_requests column...")
                await conn.execute(text(
                    "ALTER TABLE api_keys ADD COLUMN usage_limit_requests INTEGER"
                ))
                logger.success("Added usage_limit_requests column")

            logger.success("Migration completed successfully")

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        raise

    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(migrate())
