"""
Database layer with SQLAlchemy models and encryption utilities.
"""

from datetime import datetime
from typing import Optional

from cryptography.fernet import Fernet
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class User(Base):
    """Admin users table."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class APIKey(Base):
    """Client API keys table."""

    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key_id = Column(String(64), unique=True, nullable=False, index=True)
    key_hash = Column(String(255), nullable=False)
    key_encrypted = Column(Text)  # Fernet-encrypted full key for admin viewing
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255))
    is_active = Column(Boolean, default=True, nullable=False)
    rate_limit_rpm = Column(Integer)
    rate_limit_tpm = Column(Integer)
    usage_limit_tokens = Column(Integer)  # Total token limit (optional)
    usage_limit_requests = Column(Integer)  # Total request limit (optional)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_used_at = Column(DateTime)


class KiroAccount(Base):
    """Kiro credentials pool table."""

    __tablename__ = "kiro_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    auth_type = Column(String(50), nullable=False)  # 'kiro_desktop' or 'aws_sso_oidc'
    refresh_token_encrypted = Column(Text)
    access_token_encrypted = Column(Text)
    client_id_encrypted = Column(Text)
    client_secret_encrypted = Column(Text)
    profile_arn = Column(String(255))
    region = Column(String(50), default="us-east-1", nullable=False)
    expires_at = Column(DateTime)
    is_active = Column(Boolean, default=True, nullable=False)
    error_count = Column(Integer, default=0, nullable=False)
    last_error = Column(Text)
    last_success_at = Column(DateTime)
    priority = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class UsageRecord(Base):
    """Usage tracking table."""

    __tablename__ = "usage_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    api_key_id = Column(
        Integer, ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kiro_account_id = Column(
        Integer, ForeignKey("kiro_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model = Column(String(100))
    endpoint = Column(String(100))
    input_tokens = Column(Integer, default=0, nullable=False)
    output_tokens = Column(Integer, default=0, nullable=False)
    total_tokens = Column(Integer, default=0, nullable=False)
    status_code = Column(Integer)
    request_duration_ms = Column(Integer)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class Database:
    """Database manager with encryption utilities."""

    def __init__(self, database_url: str, encryption_key: str):
        """
        Initialize database manager.

        Args:
            database_url: SQLAlchemy database URL (e.g., sqlite+aiosqlite:///./db.db)
            encryption_key: Fernet encryption key for sensitive data
        """
        self.database_url = database_url
        self.engine = create_async_engine(
            database_url,
            echo=False,
            future=True,
        )
        self.SessionLocal = sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        self.cipher = Fernet(encryption_key.encode())

    async def init_db(self):
        """Create all tables if they don't exist."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_all(self):
        """Drop all tables (for testing)."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    def encrypt(self, plaintext: Optional[str]) -> Optional[str]:
        """
        Encrypt sensitive data.

        Args:
            plaintext: Data to encrypt

        Returns:
            Encrypted data as base64 string, or None if input is None
        """
        if plaintext is None:
            return None
        return self.cipher.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: Optional[str]) -> Optional[str]:
        """
        Decrypt sensitive data.

        Args:
            ciphertext: Encrypted data as base64 string

        Returns:
            Decrypted plaintext, or None if input is None
        """
        if ciphertext is None:
            return None
        return self.cipher.decrypt(ciphertext.encode()).decode()

    async def close(self):
        """Close database connection."""
        await self.engine.dispose()
