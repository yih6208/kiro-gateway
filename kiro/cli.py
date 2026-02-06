"""
CLI tool for Kiro Gateway admin management.

Usage:
    python -m kiro.cli init-db
    python -m kiro.cli create-admin --username admin --email admin@example.com --password <password>
    python -m kiro.cli list-keys
    python -m kiro.cli list-accounts
"""

import asyncio
import sys
from pathlib import Path

import bcrypt
import click
from loguru import logger

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from kiro.config import DATABASE_URL, ENCRYPTION_KEY
from kiro.database import Database, User


@click.group()
def cli():
    """Kiro Gateway CLI - Admin management tool."""
    pass


@cli.command()
def init_db():
    """Initialize database tables."""
    click.echo("Initializing database...")

    if not ENCRYPTION_KEY:
        click.echo("ERROR: ENCRYPTION_KEY not set in .env file", err=True)
        click.echo("Generate a key with:", err=True)
        click.echo('  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"', err=True)
        sys.exit(1)

    async def _init():
        db = Database(DATABASE_URL, ENCRYPTION_KEY)
        await db.init_db()
        await db.close()
        click.echo(f"✓ Database initialized at: {DATABASE_URL}")

    asyncio.run(_init())


@cli.command()
@click.option("--username", required=True, help="Admin username")
@click.option("--email", required=True, help="Admin email")
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True, help="Admin password")
def create_admin(username: str, email: str, password: str):
    """Create admin user."""
    click.echo(f"Creating admin user: {username}")

    if not ENCRYPTION_KEY:
        click.echo("ERROR: ENCRYPTION_KEY not set in .env file", err=True)
        sys.exit(1)

    async def _create():
        db = Database(DATABASE_URL, ENCRYPTION_KEY)

        # Hash password
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()

        async with db.SessionLocal() as session:
            # Check if user already exists
            from sqlalchemy import select
            stmt = select(User).where(User.username == username)
            result = await session.execute(stmt)
            existing_user = result.scalar_one_or_none()

            if existing_user:
                click.echo(f"ERROR: User '{username}' already exists", err=True)
                await db.close()
                sys.exit(1)

            # Create user
            user = User(
                username=username,
                email=email,
                password_hash=password_hash,
                is_admin=True,
            )
            session.add(user)
            await session.commit()

            click.echo(f"✓ Admin user created successfully")
            click.echo(f"  Username: {username}")
            click.echo(f"  Email: {email}")
            click.echo(f"\nYou can now login at: http://localhost:8000/admin/login")

        await db.close()

    asyncio.run(_create())


@cli.command()
def list_keys():
    """List all API keys."""
    if not ENCRYPTION_KEY:
        click.echo("ERROR: ENCRYPTION_KEY not set in .env file", err=True)
        sys.exit(1)

    async def _list():
        db = Database(DATABASE_URL, ENCRYPTION_KEY)
        from kiro.api_key_manager import APIKeyManager

        api_key_manager = APIKeyManager(db)
        keys = await api_key_manager.list_keys()

        if not keys:
            click.echo("No API keys found.")
        else:
            click.echo(f"\nFound {len(keys)} API key(s):\n")
            for key in keys:
                status = "✓ Active" if key["is_active"] else "✗ Inactive"
                click.echo(f"  {status} - {key['name'] or 'Unnamed'}")
                click.echo(f"    Key ID: {key['key_id']}")
                click.echo(f"    Created: {key['created_at']}")
                click.echo(f"    Last used: {key['last_used_at'] or 'Never'}")

                # Display usage statistics
                click.echo(f"    Usage: {key['total_requests']:,} requests, {key['total_tokens']:,} tokens")
                click.echo(f"           (Input: {key['input_tokens']:,}, Output: {key['output_tokens']:,})")

                # Display limits if set
                if key.get('usage_limit_tokens') or key.get('usage_limit_requests'):
                    limits = []
                    if key.get('usage_limit_tokens'):
                        token_pct = (key['total_tokens'] / key['usage_limit_tokens'] * 100)
                        limits.append(f"Tokens: {key['total_tokens']:,}/{key['usage_limit_tokens']:,} ({token_pct:.1f}%)")
                    if key.get('usage_limit_requests'):
                        req_pct = (key['total_requests'] / key['usage_limit_requests'] * 100)
                        limits.append(f"Requests: {key['total_requests']:,}/{key['usage_limit_requests']:,} ({req_pct:.1f}%)")
                    click.echo(f"    Limits: {', '.join(limits)}")

                click.echo()

        await db.close()

    asyncio.run(_list())


@cli.command()
def list_accounts():
    """List all Kiro accounts."""
    if not ENCRYPTION_KEY:
        click.echo("ERROR: ENCRYPTION_KEY not set in .env file", err=True)
        sys.exit(1)

    async def _list():
        db = Database(DATABASE_URL, ENCRYPTION_KEY)
        from kiro.account_pool import AccountPool
        from kiro.config import ACCOUNT_POOL_ERROR_THRESHOLD

        account_pool = AccountPool(db, ACCOUNT_POOL_ERROR_THRESHOLD)
        accounts = await account_pool.list_accounts()

        if not accounts:
            click.echo("No Kiro accounts found.")
            click.echo("\nAdd accounts via the admin UI at: http://localhost:8000/admin/kiro-accounts")
        else:
            click.echo(f"\nFound {len(accounts)} Kiro account(s):\n")
            for account in accounts:
                status = "✓ Active" if account["is_active"] else "✗ Inactive"
                health = f"({account['error_count']} errors)" if account['error_count'] > 0 else "(healthy)"
                click.echo(f"  {status} - {account['name']} {health}")
                click.echo(f"    ID: {account['id']}")
                click.echo(f"    Auth Type: {account['auth_type']}")
                click.echo(f"    Region: {account['region']}")
                click.echo(f"    Last success: {account['last_success_at'] or 'Never'}")
                if account['last_error']:
                    click.echo(f"    Last error: {account['last_error'][:100]}")
                click.echo()

        await db.close()

    asyncio.run(_list())


@cli.command()
@click.option("--key-id", required=True, help="API key ID to delete")
def delete_key(key_id: int):
    """Delete an API key."""
    if not ENCRYPTION_KEY:
        click.echo("ERROR: ENCRYPTION_KEY not set in .env file", err=True)
        sys.exit(1)

    async def _delete():
        db = Database(DATABASE_URL, ENCRYPTION_KEY)
        from kiro.api_key_manager import APIKeyManager

        api_key_manager = APIKeyManager(db)
        success = await api_key_manager.deactivate_key(key_id)

        if success:
            click.echo(f"✓ API key {key_id} deactivated successfully")
        else:
            click.echo(f"ERROR: API key {key_id} not found", err=True)

        await db.close()

    asyncio.run(_delete())


@cli.command()
def generate_keys():
    """Generate encryption and session keys for .env file."""
    from cryptography.fernet import Fernet
    import secrets

    click.echo("\n=== Generated Keys for .env ===\n")

    encryption_key = Fernet.generate_key().decode()
    click.echo(f"ENCRYPTION_KEY=\"{encryption_key}\"")

    session_secret = secrets.token_urlsafe(32)
    click.echo(f"ADMIN_SESSION_SECRET=\"{session_secret}\"")

    click.echo("\nCopy these to your .env file.")


if __name__ == "__main__":
    cli()
