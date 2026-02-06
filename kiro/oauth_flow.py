"""
OAuth flow manager for IAM SSO Authorization Code Flow with PKCE.
Uses dynamic client registration like kiro-account-manager.
"""

import base64
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Dict, Optional
from urllib.parse import urlencode

import httpx
from loguru import logger


class OAuthFlowManager:
    """Manages OAuth 2.0 Authorization Code Flow with PKCE and dynamic client registration."""

    def __init__(
        self,
        region: str = "us-east-1",
        start_url: str = "https://view.awsapps.com/start",
    ):
        """
        Initialize OAuth flow manager with dynamic client registration.

        Args:
            region: AWS region (default: us-east-1)
            start_url: IAM Identity Center start URL (can be changed per-request)
        """
        self.region = region
        self.start_url = start_url
        self.oidc_base = f"https://oidc.{region}.amazonaws.com"
        self.scopes = [
            "codewhisperer:completions",
            "codewhisperer:analysis",
            "codewhisperer:conversations",
        ]

        # In-memory storage for pending flows (state -> flow_data)
        self.pending_flows: Dict[str, dict] = {}

    async def register_client(self) -> tuple[str, str]:
        """
        Dynamically register an OIDC client with AWS.

        Note: For public clients, AWS OIDC requires registering with loopback
        interface WITHOUT a port. Any port can then be used in the actual redirect.
        Use a simple path like /oauth/callback (not /admin/oauth/callback).

        Returns:
            Tuple of (client_id, client_secret)

        Raises:
            Exception: If registration fails
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.oidc_base}/client/register",
                json={
                    "clientName": "Kiro Gateway",
                    "clientType": "public",
                    "scopes": self.scopes,
                    "grantTypes": ["authorization_code", "refresh_token"],
                    "redirectUris": ["http://127.0.0.1/oauth/callback"],  # Simple path, no /admin prefix
                    "issuerUrl": self.start_url,
                },
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            )

            if response.status_code != 200:
                error_text = response.text
                logger.error(f"Client registration failed: {response.status_code} - {error_text}")
                raise Exception(f"Client registration failed: {error_text}")

            data = response.json()
            client_id = data["clientId"]
            client_secret = data["clientSecret"]

            logger.info(f"Registered OIDC client: {client_id[:30]}...")
            return client_id, client_secret

    def generate_pkce_pair(self) -> tuple[str, str]:
        """
        Generate PKCE code_verifier and code_challenge.

        Returns:
            Tuple of (code_verifier, code_challenge)
        """
        # Generate code_verifier: 43-128 character random string
        code_verifier = secrets.token_urlsafe(32)

        # Generate code_challenge: SHA256(code_verifier) base64url-encoded
        code_challenge_bytes = hashlib.sha256(code_verifier.encode()).digest()
        code_challenge = base64.urlsafe_b64encode(code_challenge_bytes).decode().rstrip("=")

        return code_verifier, code_challenge

    async def build_authorization_url(
        self, redirect_uri: str
    ) -> tuple[str, str, str, str, str]:
        """
        Build OAuth authorization URL with dynamic client registration and PKCE.

        Args:
            redirect_uri: OAuth redirect URI with port (e.g., http://127.0.0.1:8000/admin/kiro-accounts/oauth/callback)

        Returns:
            Tuple of (authorization_url, state, client_id, client_secret, code_verifier)
        """
        # Step 1: Register client dynamically (without port in registration)
        client_id, client_secret = await self.register_client()

        # Step 2: Generate PKCE pair
        code_verifier, code_challenge = self.generate_pkce_pair()

        # Step 3: Generate state for CSRF protection
        state = secrets.token_urlsafe(32)

        # Store flow data
        self.pending_flows[state] = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(minutes=10),
        }

        # Step 4: Build authorization URL (with port in actual redirect)
        # Note: Use 'scopes' (comma-separated) not 'scope' (space-separated)
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,  # Use actual redirect URI with port
            "scopes": ",".join(self.scopes),  # Comma-separated!
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        auth_url = f"{self.oidc_base}/authorize?{urlencode(params)}"
        logger.info(f"Generated OAuth authorization URL with state={state}")

        return auth_url, state, client_id, client_secret, code_verifier

    async def exchange_code_for_tokens(self, code: str, state: str) -> dict:
        """
        Exchange authorization code for access and refresh tokens.

        Args:
            code: Authorization code from callback
            state: State parameter from callback (for CSRF validation)

        Returns:
            Dict with tokens: {access_token, refresh_token, expires_in, client_id, client_secret}

        Raises:
            ValueError: If state is invalid or token exchange fails
        """
        # Validate state
        if state not in self.pending_flows:
            logger.error(f"Invalid state parameter: {state}")
            raise ValueError("Invalid state parameter. Flow may have expired or been tampered with.")

        flow_data = self.pending_flows.pop(state)

        # Check expiration
        if datetime.utcnow() > flow_data["expires_at"]:
            logger.error(f"OAuth flow expired for state={state}")
            raise ValueError("OAuth flow expired. Please try again.")

        # Exchange code for tokens
        token_request = {
            "clientId": flow_data["client_id"],
            "clientSecret": flow_data["client_secret"],
            "grantType": "authorization_code",
            "redirectUri": flow_data["redirect_uri"],
            "code": code,
            "codeVerifier": flow_data["code_verifier"],
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.oidc_base}/token",
                    json=token_request,
                    headers={"Content-Type": "application/json"},
                    timeout=30.0,
                )

                if response.status_code != 200:
                    error_detail = response.text
                    logger.error(f"Token exchange failed: {response.status_code} - {error_detail}")
                    raise ValueError(f"Token exchange failed: {error_detail}")

                tokens = response.json()
                logger.info("Successfully exchanged authorization code for tokens")

                # Return tokens with client credentials for storage
                return {
                    "access_token": tokens.get("accessToken"),
                    "refresh_token": tokens.get("refreshToken"),
                    "expires_in": tokens.get("expiresIn"),
                    "client_id": flow_data["client_id"],
                    "client_secret": flow_data["client_secret"],
                }

        except httpx.HTTPError as e:
            logger.error(f"HTTP error during token exchange: {str(e)}")
            raise ValueError(f"Token exchange failed: {str(e)}")

    def cleanup_expired_flows(self):
        """Remove expired pending flows from memory."""
        now = datetime.utcnow()
        expired_states = [
            state
            for state, flow_data in self.pending_flows.items()
            if now > flow_data["expires_at"]
        ]

        for state in expired_states:
            del self.pending_flows[state]

        if expired_states:
            logger.info(f"Cleaned up {len(expired_states)} expired OAuth flows")

    def get_pending_flow_count(self) -> int:
        """Get number of pending OAuth flows."""
        return len(self.pending_flows)
