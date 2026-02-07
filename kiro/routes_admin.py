"""
Admin UI routes for multi-tenant management.
"""

from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from loguru import logger
from sqlalchemy import select

from kiro.config import ADMIN_SESSION_EXPIRY_HOURS, ADMIN_SESSION_SECRET
from kiro.database import KiroAccount, User

router = APIRouter(prefix="/admin", tags=["Admin"])
public_router = APIRouter(tags=["Public"])
templates = Jinja2Templates(directory="templates")

# JWT configuration
JWT_ALGORITHM = "HS256"
JWT_COOKIE_NAME = "admin_session"


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create JWT access token.

    Args:
        data: Data to encode in token
        expires_delta: Token expiration time

    Returns:
        Encoded JWT token
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=ADMIN_SESSION_EXPIRY_HOURS)

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, ADMIN_SESSION_SECRET, algorithm=JWT_ALGORITHM)
    return encoded_jwt


async def verify_admin_session(request: Request) -> dict:
    """
    Verify JWT session from cookie.

    Args:
        request: FastAPI request object

    Returns:
        JWT payload with user info

    Raises:
        HTTPException: If not authenticated or invalid session
    """
    token = request.cookies.get(JWT_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = jwt.decode(token, ADMIN_SESSION_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("user_id")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid session")
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid session")


# ==================================================================================================
# Authentication Routes
# ==================================================================================================


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Display login page."""
    return templates.TemplateResponse("admin/login.html", {"request": request})


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(),
    password: str = Form(),
):
    """
    Authenticate admin user and create session.

    Args:
        username: Username
        password: Password

    Returns:
        Redirect to dashboard on success
    """
    db = request.app.state.db

    async with db.SessionLocal() as session:
        # Find user by username
        stmt = select(User).where(User.username == username)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            logger.warning(f"Login attempt with invalid username: {username}")
            raise HTTPException(status_code=401, detail="Invalid username or password")

        # Verify password
        if not bcrypt.checkpw(password.encode(), user.password_hash.encode()):
            logger.warning(f"Login attempt with invalid password for user: {username}")
            raise HTTPException(status_code=401, detail="Invalid username or password")

        # Create JWT token
        access_token = create_access_token(
            data={"user_id": user.id, "username": user.username, "is_admin": user.is_admin}
        )

        # Set HTTP-only cookie
        response = RedirectResponse(url="/admin/dashboard", status_code=303)
        response.set_cookie(
            key=JWT_COOKIE_NAME,
            value=access_token,
            httponly=True,
            max_age=ADMIN_SESSION_EXPIRY_HOURS * 3600,
            samesite="lax",
        )

        logger.info(f"User {username} logged in successfully")
        return response


@router.post("/logout")
async def logout():
    """Logout and clear session."""
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(JWT_COOKIE_NAME)
    return response


# ==================================================================================================
# Dashboard
# ==================================================================================================


@router.get("/", response_class=HTMLResponse)
async def admin_root(request: Request, admin: dict = Depends(verify_admin_session)):
    """Redirect root to dashboard."""
    return RedirectResponse(url="/admin/dashboard")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, admin: dict = Depends(verify_admin_session)):
    """Display admin dashboard with statistics."""
    db = request.app.state.db
    api_key_manager = request.app.state.api_key_manager
    account_pool = request.app.state.account_pool
    usage_tracker = request.app.state.usage_tracker

    # Get statistics
    api_keys = await api_key_manager.list_keys()
    accounts = await account_pool.list_accounts()
    usage_stats = await usage_tracker.get_usage_stats()

    # Count active/inactive
    active_keys = sum(1 for key in api_keys if key["is_active"])
    active_accounts = sum(1 for acc in accounts if acc["is_active"])

    stats = {
        "total_api_keys": len(api_keys),
        "active_api_keys": active_keys,
        "total_accounts": len(accounts),
        "active_accounts": active_accounts,
        "total_requests": usage_stats["total_requests"],
        "total_tokens": usage_stats["total_tokens"],
    }

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {"request": request, "admin": admin, "stats": stats},
    )


# ==================================================================================================
# API Key Management
# ==================================================================================================


@router.get("/api-keys", response_class=HTMLResponse)
async def api_keys_page(request: Request, admin: dict = Depends(verify_admin_session)):
    """Display API keys management page."""
    api_key_manager = request.app.state.api_key_manager
    keys = await api_key_manager.list_keys()

    return templates.TemplateResponse(
        "admin/api_keys.html",
        {"request": request, "admin": admin, "keys": keys},
    )


@router.post("/api-keys/create")
async def create_api_key(
    request: Request,
    name: str = Form(),
    rate_limit_rpm: Optional[int] = Form(None),
    rate_limit_tpm: Optional[int] = Form(None),
    usage_limit_tokens: Optional[int] = Form(None),
    usage_limit_requests: Optional[int] = Form(None),
    admin: dict = Depends(verify_admin_session),
):
    """
    Create new API key.

    Args:
        name: Descriptive name for the key
        rate_limit_rpm: Requests per minute limit (optional)
        rate_limit_tpm: Tokens per minute limit (optional)
        usage_limit_tokens: Total token usage limit (optional)
        usage_limit_requests: Total request count limit (optional)

    Returns:
        Redirect to API keys page with new key displayed
    """
    api_key_manager = request.app.state.api_key_manager

    # Create key
    key_plaintext, metadata = await api_key_manager.create_key(
        user_id=admin["user_id"],
        name=name,
        rate_limit_rpm=rate_limit_rpm,
        rate_limit_tpm=rate_limit_tpm,
        usage_limit_tokens=usage_limit_tokens,
        usage_limit_requests=usage_limit_requests,
    )

    logger.info(f"Created API key: {metadata['key_id']} by user {admin['username']}")

    # Redirect with key in query param (shown once)
    return RedirectResponse(
        url=f"/admin/api-keys?new_key={key_plaintext}",
        status_code=303,
    )


@router.post("/api-keys/{key_id}/deactivate")
async def deactivate_api_key(
    request: Request,
    key_id: int,
    admin: dict = Depends(verify_admin_session),
):
    """Deactivate an API key."""
    api_key_manager = request.app.state.api_key_manager

    success = await api_key_manager.deactivate_key(key_id)
    if not success:
        raise HTTPException(status_code=404, detail="API key not found")

    logger.info(f"Deactivated API key {key_id} by user {admin['username']}")
    return RedirectResponse(url="/admin/api-keys", status_code=303)


@router.post("/api-keys/{key_id}/delete")
async def delete_api_key(
    request: Request,
    key_id: int,
    admin: dict = Depends(verify_admin_session),
):
    """Permanently delete an API key."""
    api_key_manager = request.app.state.api_key_manager

    success = await api_key_manager.delete_key(key_id)
    if not success:
        raise HTTPException(status_code=404, detail="API key not found")

    logger.info(f"Deleted API key {key_id} by user {admin['username']}")
    return RedirectResponse(url="/admin/api-keys", status_code=303)


@router.post("/api-keys/{key_id}/update-limits")
async def update_api_key_limits(
    request: Request,
    key_id: int,
    usage_limit_tokens: Optional[int] = Form(None),
    usage_limit_requests: Optional[int] = Form(None),
    admin: dict = Depends(verify_admin_session),
):
    """Update usage limits for an API key."""
    api_key_manager = request.app.state.api_key_manager

    success = await api_key_manager.update_limits(
        key_id,
        usage_limit_tokens=usage_limit_tokens,
        usage_limit_requests=usage_limit_requests,
    )
    if not success:
        raise HTTPException(status_code=404, detail="API key not found")

    logger.info(f"Updated limits for API key {key_id} by user {admin['username']}")
    return RedirectResponse(url="/admin/api-keys", status_code=303)


# ==================================================================================================
# Kiro Account Management
# ==================================================================================================


@router.get("/kiro-accounts", response_class=HTMLResponse)
async def kiro_accounts_page(request: Request, admin: dict = Depends(verify_admin_session)):
    """Display Kiro accounts management page."""
    account_pool = request.app.state.account_pool
    accounts = await account_pool.list_accounts()

    return templates.TemplateResponse(
        "admin/kiro_accounts.html",
        {"request": request, "admin": admin, "accounts": accounts},
    )


@router.get("/kiro-accounts/oauth/start")
async def start_oauth(request: Request, admin: dict = Depends(verify_admin_session)):
    """Start OAuth flow for adding Kiro account via IAM SSO."""
    oauth_manager = request.app.state.oauth_manager

    try:
        # Build authorization URL with dynamic client registration
        # IMPORTANT: Must use 127.0.0.1 (loopback) and simple path /oauth/callback
        redirect_uri = f"http://127.0.0.1:8000/oauth/callback"
        auth_url, state, client_id, client_secret, code_verifier = (
            await oauth_manager.build_authorization_url(redirect_uri)
        )

        logger.info(f"Starting OAuth flow for user {admin['username']}, state={state}")
        return RedirectResponse(auth_url)

    except Exception as e:
        logger.error(f"Failed to start OAuth flow: {str(e)}")
        return RedirectResponse(
            url=f"/admin/kiro-accounts?error={str(e)}",
            status_code=303,
        )


# OAuth callback route (outside /admin prefix for proper redirect URI matching)
# This is registered separately in main.py
async def oauth_callback_handler(
    request: Request,
    code: str,
    state: str,
):
    """
    OAuth callback handler (at /oauth/callback to match registration).

    Args:
        code: Authorization code from IAM SSO
        state: State parameter for CSRF protection

    Returns:
        Redirect to accounts page
    """
    oauth_manager = request.app.state.oauth_manager
    db = request.app.state.db

    try:
        # Exchange code for tokens
        tokens = await oauth_manager.exchange_code_for_tokens(code, state)

        # Store in kiro_accounts table
        async with db.SessionLocal() as session:
            account = KiroAccount(
                name=f"IAM SSO Account {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
                auth_type="aws_sso_oidc",
                refresh_token_encrypted=db.encrypt(tokens["refresh_token"]),
                access_token_encrypted=db.encrypt(tokens["access_token"]) if tokens.get("access_token") else None,
                client_id_encrypted=db.encrypt(tokens["client_id"]),
                client_secret_encrypted=db.encrypt(tokens["client_secret"]),
                region=oauth_manager.region,
                is_active=True,
                created_at=datetime.utcnow(),
            )
            session.add(account)
            await session.commit()

            logger.info(f"Added new Kiro account via OAuth: {account.id}")

        return RedirectResponse(url="/admin/kiro-accounts?success=oauth", status_code=303)

    except Exception as e:
        logger.error(f"OAuth callback failed: {str(e)}")
        return RedirectResponse(
            url=f"/admin/kiro-accounts?error={str(e)}",
            status_code=303,
        )


@router.post("/kiro-accounts/{account_id}/toggle")
async def toggle_account(
    request: Request,
    account_id: int,
    admin: dict = Depends(verify_admin_session),
):
    """Toggle account active status."""
    db = request.app.state.db

    async with db.SessionLocal() as session:
        stmt = select(KiroAccount).where(KiroAccount.id == account_id)
        result = await session.execute(stmt)
        account = result.scalar_one_or_none()

        if not account:
            raise HTTPException(status_code=404, detail="Account not found")

        account.is_active = not account.is_active
        await session.commit()

        logger.info(
            f"Toggled account {account_id} to {'active' if account.is_active else 'inactive'} "
            f"by user {admin['username']}"
        )

    return RedirectResponse(url="/admin/kiro-accounts", status_code=303)


@router.post("/kiro-accounts/{account_id}/refresh")
async def refresh_account_token(
    request: Request,
    account_id: int,
    admin: dict = Depends(verify_admin_session),
):
    """Manually refresh token for an account."""
    account_pool = request.app.state.account_pool

    try:
        await account_pool.refresh_account_token(account_id)
        logger.info(f"Refreshed token for account {account_id} by user {admin['username']}")
        return RedirectResponse(url="/admin/kiro-accounts?success=refresh", status_code=303)
    except Exception as e:
        logger.error(f"Failed to refresh account {account_id}: {str(e)}")
        return RedirectResponse(
            url=f"/admin/kiro-accounts?error={str(e)}",
            status_code=303,
        )


@router.post("/kiro-accounts/{account_id}/delete")
async def delete_account(
    request: Request,
    account_id: int,
    admin: dict = Depends(verify_admin_session),
):
    """Permanently delete a Kiro account."""
    account_pool = request.app.state.account_pool

    success = await account_pool.delete_account(account_id)
    if not success:
        raise HTTPException(status_code=404, detail="Account not found")

    logger.info(f"Deleted Kiro account {account_id} by user {admin['username']}")
    return RedirectResponse(url="/admin/kiro-accounts", status_code=303)


# ==================== Public Usage Endpoint ====================


@public_router.get("/usage/{api_key}", response_class=HTMLResponse)
async def public_usage(request: Request, api_key: str):
    """Public usage page for API key owners. The key itself is the auth."""
    api_key_manager = request.app.state.api_key_manager
    metadata = await api_key_manager.validate_key(api_key)

    if not metadata:
        raise HTTPException(status_code=404, detail="Invalid API key")

    api_key_id = metadata["api_key_id"]
    stats = await api_key_manager.get_usage_stats(api_key_id)
    model_usage = await api_key_manager.get_usage_by_model(api_key_id)

    return templates.TemplateResponse(
        "public/usage.html",
        {
            "request": request,
            "key_name": metadata["name"],
            "key_id": metadata["key_id"],
            "stats": stats,
            "model_usage": model_usage,
            "usage_limit_tokens": metadata.get("usage_limit_tokens"),
            "usage_limit_requests": metadata.get("usage_limit_requests"),
        },
    )


# ==================================================================================================
# Usage Analytics
# ==================================================================================================


@router.get("/usage", response_class=HTMLResponse)
async def usage_page(request: Request, admin: dict = Depends(verify_admin_session)):
    """Display usage analytics page."""
    usage_tracker = request.app.state.usage_tracker

    # Get overall stats
    overall_stats = await usage_tracker.get_usage_stats()

    # Get usage by model
    by_model = await usage_tracker.get_usage_by_model()

    # Get usage by endpoint
    by_endpoint = await usage_tracker.get_usage_by_endpoint()

    # Get recent requests
    recent_requests = await usage_tracker.get_recent_requests(limit=50)

    return templates.TemplateResponse(
        "admin/usage.html",
        {
            "request": request,
            "admin": admin,
            "overall_stats": overall_stats,
            "by_model": by_model,
            "by_endpoint": by_endpoint,
            "recent_requests": recent_requests,
        },
    )
