"""Enterprise Passwordless Email Link Authentication & Domain-Gated Access Control (CR-7)."""

from __future__ import annotations

import ipaddress
import logging
import time
from collections import defaultdict
from enum import Enum
from typing import Callable

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from contract_parser.config import PipelineConfig

logger = logging.getLogger(__name__)

ALLOWED_DOMAINS: set[str] = {"invenergy.com", "google.com"}

_security_bearer = HTTPBearer(auto_error=False)


class UserRole(str, Enum):
    """Authorized role tiers for portfolio contract intelligence."""
    ADMIN = "admin"
    VIEWER = "viewer"


class UserContext(BaseModel):
    """Validated caller identity context attached to authenticated requests."""
    email: str
    role: UserRole
    domain: str
    uid: str


class SlidingWindowRateLimiter:
    """In-memory sliding-window rate limiter tracking requests per IP and email."""

    def __init__(
        self,
        ip_limit: int = 5,
        ip_window_seconds: int = 600,
        email_limit: int = 3,
        email_window_seconds: int = 900,
    ) -> None:
        self.ip_limit = ip_limit
        self.ip_window_seconds = ip_window_seconds
        self.email_limit = email_limit
        self.email_window_seconds = email_window_seconds
        self._ip_history: dict[str, list[float]] = defaultdict(list)
        self._email_history: dict[str, list[float]] = defaultdict(list)

    def check_and_record(self, ip: str, email: str) -> None:
        now = time.time()
        norm_email = email.strip().lower()

        # 1. Prune and check IP window
        ip_cutoff = now - self.ip_window_seconds
        self._ip_history[ip] = [t for t in self._ip_history[ip] if t > ip_cutoff]
        if len(self._ip_history[ip]) >= self.ip_limit:
            logger.warning("Rate limit exceeded for client IP: %s (%d requests)", ip, len(self._ip_history[ip]))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many sign-in link requests from this network. Please wait 10 minutes.",
            )

        # 2. Prune and check Email window
        email_cutoff = now - self.email_window_seconds
        self._email_history[norm_email] = [t for t in self._email_history[norm_email] if t > email_cutoff]
        if len(self._email_history[norm_email]) >= self.email_limit:
            logger.warning("Rate limit exceeded for recipient email: %s (%d requests)", norm_email, len(self._email_history[norm_email]))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many sign-in link requests for this email address. Please check your inbox or wait 15 minutes.",
            )

        # Record attempt
        self._ip_history[ip].append(now)
        self._email_history[norm_email].append(now)


# Global rate limiter instance
_global_rate_limiter = SlidingWindowRateLimiter()


def get_client_ip(request: Request) -> str:
    """Extract real client IP from X-Forwarded-For leftmost public entry or client host."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        for raw_part in xff.split(","):
            ip_str = raw_part.strip()
            if not ip_str:
                continue
            try:
                ip_obj = ipaddress.ip_address(ip_str)
                if not ip_obj.is_private and not ip_obj.is_loopback:
                    return ip_str
            except ValueError:
                continue
        # Fall back to first token if all private
        first_token = xff.split(",")[0].strip()
        if first_token:
            return first_token

    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


_firebase_app_initialized = False


def ensure_firebase_initialized(config: PipelineConfig) -> None:
    """Initialize firebase_admin app using application default credentials if not yet done."""
    global _firebase_app_initialized
    if not _firebase_app_initialized:
        try:
            import firebase_admin
            try:
                firebase_admin.get_app()
            except ValueError:
                firebase_admin.initialize_app(
                    options={"projectId": config.google_cloud_project}
                )
            _firebase_app_initialized = True
        except Exception as exc:
            logger.warning("Firebase Admin initialization skipped or deferred: %s", exc)


def is_allowed_domain(email: str) -> bool:
    """Validate that the email domain is strictly in ALLOWED_DOMAINS."""
    norm = email.strip().lower()
    if "@" not in norm:
        return False
    domain = norm.split("@")[-1]
    return domain in ALLOWED_DOMAINS


async def dispatch_email_sign_in_link(
    email: str,
    continue_url: str,
    config: PipelineConfig,
    client_ip: str,
) -> dict[str, str]:
    """Validate domain, apply rate limits, and dispatch sign-in link via Google Identity Toolkit."""
    norm_email = email.strip().lower()
    if not is_allowed_domain(norm_email):
        logger.warning("Rejected sign-in link request for unauthorized domain: %s", norm_email)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access restricted. Only @invenergy.com and @google.com email domains are authorized.",
        )

    _global_rate_limiter.check_and_record(client_ip, norm_email)

    if not config.firebase_api_key:
        logger.info(
            "Mock dispatch for %s (no FIREBASE_API_KEY configured in environment). ContinueUrl: %s",
            norm_email,
            continue_url,
        )
        return {
            "status": "sent",
            "email": norm_email,
            "message": "Sign-in link dispatched to corporate inbox (simulated in development mode).",
        }

    # Dispatch to Google Identity Toolkit REST API
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={config.firebase_api_key}"
    payload = {
        "requestType": "EMAIL_SIGNIN",
        "email": norm_email,
        "continueUrl": continue_url,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                logger.error("Identity Toolkit sendOobCode returned %d: %s", resp.status_code, resp.text)
                if "CONFIGURATION_NOT_FOUND" in resp.text:
                    logger.warning(
                        "Firebase Auth is not provisioned in project %s yet. Falling back to development simulated dispatch.",
                        config.google_cloud_project,
                    )
                    return {
                        "status": "sent",
                        "email": norm_email,
                        "message": (
                            f"Sign-in link simulated for {norm_email}. "
                            "Note: To enable live email delivery, enable Email Link authentication in the Firebase Console."
                        ),
                    }
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Failed to dispatch verification email via identity provider. Please try again.",
                )
    except httpx.RequestError as exc:
        logger.error("Identity Toolkit network error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable. Please retry in a few moments.",
        )

    logger.info("Successfully dispatched passwordless sign-in email to %s", norm_email)
    return {
        "status": "sent",
        "email": norm_email,
        "message": f"Sign-in link sent to {norm_email}. Please check your inbox and click the link to enter.",
    }


def make_auth_dependency(get_config: Callable[[], PipelineConfig]):
    """Factory creating the `get_current_user` FastAPI dependency bound to PipelineConfig."""

    async def get_current_user(
        request: Request,
        bearer: HTTPAuthorizationCredentials | None = Depends(_security_bearer),
    ) -> UserContext:
        config = get_config()

        # Development bypass if AUTH_ENABLED=false
        if not config.auth_enabled:
            return UserContext(
                email="dev@invenergy.com",
                role=UserRole.ADMIN,
                domain="invenergy.com",
                uid="dev_bypass_uid",
            )

        if not bearer or not bearer.credentials:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required. Please sign in to access workspace APIs.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = bearer.credentials
        ensure_firebase_initialized(config)

        try:
            import firebase_admin.auth
            decoded = firebase_admin.auth.verify_id_token(token, check_revoked=False)
        except Exception as exc:
            logger.warning("Token verification failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired session token. Please sign in again.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        email = decoded.get("email", "").strip().lower()
        if not email or not is_allowed_domain(email):
            logger.warning("Access denied: token email %s not in allowed domains", email)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Domain for {email} is not authorized for this workspace.",
            )

        domain = email.split("@")[-1]
        uid = decoded.get("uid", "")

        # Role assignment: @google.com or admin whitelist -> Admin; other @invenergy.com -> Viewer
        if domain == "google.com" or email in config.admin_email_whitelist:
            role = UserRole.ADMIN
        else:
            role = UserRole.VIEWER

        return UserContext(
            email=email,
            role=role,
            domain=domain,
            uid=uid,
        )

    return get_current_user


def require_admin(user: UserContext = Depends(None)) -> UserContext:
    """Guard dependency requiring the caller to possess UserRole.ADMIN."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Admin privileges are required to perform this action.",
        )
    return user
