from __future__ import annotations

import base64
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache

import jwt


LOGGER = logging.getLogger(__name__)


VIEWER_ROLE = "Pairings.Viewer"
EDITOR_ROLE = "Pairings.Editor"
ROLE_CLAIM = "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"
TENANT_CLAIM = "http://schemas.microsoft.com/identity/claims/tenantid"


@dataclass(frozen=True)
class UserAccess:
    authenticated: bool = False
    display_name: str = ""
    object_id: str = ""
    roles: frozenset[str] = frozenset()
    tenant_id: str = ""

    @property
    def can_view_pairings(self) -> bool:
        return EDITOR_ROLE in self.roles or VIEWER_ROLE in self.roles

    @property
    def can_edit_pairings(self) -> bool:
        return EDITOR_ROLE in self.roles

    def belongs_to_tenant(self, expected_tenant_id: str) -> bool:
        return (
            self.authenticated
            and bool(expected_tenant_id)
            and self.tenant_id.casefold() == expected_tenant_id.casefold()
        )


def parse_client_principal(encoded_principal: str | None) -> UserAccess:
    if not encoded_principal:
        return UserAccess()

    try:
        padding = "=" * (-len(encoded_principal) % 4)
        payload = json.loads(base64.b64decode(encoded_principal + padding))
        claims = payload.get("claims", [])
    except (ValueError, TypeError, json.JSONDecodeError):
        return UserAccess()

    values: dict[str, list[str]] = {}
    for claim in claims:
        claim_type = str(claim.get("typ", ""))
        claim_value = str(claim.get("val", ""))
        if claim_type and claim_value:
            values.setdefault(claim_type, []).append(claim_value)

    role_type = str(payload.get("role_typ", ROLE_CLAIM))
    roles = set(values.get(role_type, []))
    roles.update(values.get("roles", []))
    roles.update(values.get("role", []))

    display_name = _first_claim(
        values,
        "preferred_username",
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
        "name",
    )
    object_id = _first_claim(
        values,
        "http://schemas.microsoft.com/identity/claims/objectidentifier",
        "oid",
    )
    tenant_id = _first_claim(values, TENANT_CLAIM, "tid")
    return UserAccess(
        authenticated=True,
        display_name=display_name,
        object_id=object_id,
        roles=frozenset(roles),
        tenant_id=tenant_id,
    )


def parse_oidc_user(user: Mapping[str, object] | None) -> UserAccess:
    if not user or not bool(user.get("is_logged_in")):
        return UserAccess()

    roles = set()
    for claim_type in (ROLE_CLAIM, "roles", "role"):
        roles.update(_claim_values(user.get(claim_type)))

    display_name = _first_mapping_value(
        user,
        "preferred_username",
        "email",
        "name",
    )
    object_id = _first_mapping_value(user, "oid", "sub")
    tenant_id = _first_mapping_value(user, "tid", TENANT_CLAIM)
    return UserAccess(
        authenticated=True,
        display_name=display_name,
        object_id=object_id,
        roles=frozenset(roles),
        tenant_id=tenant_id,
    )


def validate_cloudflare_access_token(
    token: str | None,
    team_domain: str,
    audience: str,
    tenant_id: str,
) -> UserAccess:
    if not token:
        LOGGER.warning("Cloudflare Access JWT is missing")
        return UserAccess()
    if not team_domain or not audience:
        LOGGER.warning("Cloudflare Access JWT configuration is incomplete")
        return UserAccess()

    issuer = team_domain.rstrip("/")
    try:
        signing_key = _cloudflare_jwk_client(issuer).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
            options={"require": ["aud", "exp", "iss"]},
        )
    except jwt.PyJWTError as exc:
        LOGGER.warning(
            "Cloudflare Access JWT validation failed: %s",
            type(exc).__name__,
        )
        return UserAccess()

    access = parse_cloudflare_access_claims(claims, tenant_id)
    if not access.authenticated:
        LOGGER.warning("Cloudflare Access JWT has no identity-based app claims")
    return access


def parse_cloudflare_access_claims(
    claims: Mapping[str, object], tenant_id: str
) -> UserAccess:
    email = _first_mapping_value(claims, "email")
    if claims.get("type") != "app" or not email:
        return UserAccess()

    custom_claims = claims.get("custom")
    roles = set()
    if isinstance(custom_claims, Mapping):
        roles.update(_claim_values(custom_claims.get("roles")))

    return UserAccess(
        authenticated=True,
        display_name=email,
        object_id=_first_mapping_value(claims, "sub"),
        roles=frozenset(roles),
        tenant_id=tenant_id,
    )


@lru_cache(maxsize=4)
def _cloudflare_jwk_client(team_domain: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        f"{team_domain}/cdn-cgi/access/certs",
        cache_keys=True,
        lifespan=3600,
        timeout=5,
    )


def _first_claim(values: dict[str, list[str]], *claim_types: str) -> str:
    for claim_type in claim_types:
        matches = values.get(claim_type)
        if matches:
            return matches[0]
    return ""


def _claim_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item) for item in value if item]
    return []


def _first_mapping_value(values: Mapping[str, object], *claim_types: str) -> str:
    for claim_type in claim_types:
        value = values.get(claim_type)
        if isinstance(value, str) and value:
            return value
    return ""
