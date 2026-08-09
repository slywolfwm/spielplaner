import base64
import json
import os
from time import time

import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric import rsa

from access_control import (
    EDITOR_ROLE,
    TENANT_CLAIM,
    VIEWER_ROLE,
    parse_cloudflare_access_claims,
    parse_client_principal,
    parse_oidc_user,
    validate_cloudflare_access_token,
    validate_proxy_access_proof,
)


def encode_principal(claims: list[dict[str, str]]) -> str:
    payload = {
        "claims": claims,
        "role_typ": "http://schemas.microsoft.com/ws/2008/06/identity/claims/role",
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


def test_anonymous_user_has_no_pairing_access():
    access = parse_client_principal(None)

    assert not access.authenticated
    assert not access.can_view_pairings
    assert not access.can_edit_pairings


def test_viewer_can_see_but_not_edit_pairings():
    principal = encode_principal(
        [
            {"typ": "preferred_username", "val": "viewer@example.com"},
            {
                "typ": "http://schemas.microsoft.com/ws/2008/06/identity/claims/role",
                "val": VIEWER_ROLE,
            },
        ]
    )

    access = parse_client_principal(principal)

    assert access.authenticated
    assert access.display_name == "viewer@example.com"
    assert access.can_view_pairings
    assert not access.can_edit_pairings


def test_editor_can_see_and_edit_pairings():
    principal = encode_principal(
        [
            {"typ": "roles", "val": EDITOR_ROLE},
            {"typ": "oid", "val": "user-object-id"},
        ]
    )

    access = parse_client_principal(principal)

    assert access.can_view_pairings
    assert access.can_edit_pairings
    assert access.object_id == "user-object-id"


def test_oidc_viewer_can_see_saved_pairings():
    access = parse_oidc_user(
        {
            "is_logged_in": True,
            "preferred_username": "viewer@example.com",
            "oid": "oidc-user-id",
            "roles": [VIEWER_ROLE],
        }
    )

    assert access.authenticated
    assert access.display_name == "viewer@example.com"
    assert access.object_id == "oidc-user-id"
    assert access.can_view_pairings
    assert not access.can_edit_pairings


def test_oidc_editor_role_can_be_a_single_string():
    access = parse_oidc_user(
        {
            "is_logged_in": True,
            "email": "editor@example.com",
            "role": EDITOR_ROLE,
        }
    )

    assert access.can_view_pairings
    assert access.can_edit_pairings


def test_oidc_claims_without_login_are_anonymous():
    access = parse_oidc_user({"roles": [EDITOR_ROLE]})

    assert not access.authenticated


def test_oidc_user_belongs_only_to_matching_tenant():
    access = parse_oidc_user(
        {
            "is_logged_in": True,
            "preferred_username": "member@example.com",
            "tid": "tenant-id",
        }
    )

    assert access.belongs_to_tenant("tenant-id")
    assert not access.belongs_to_tenant("another-tenant")


def test_client_principal_reads_tenant_claim():
    principal = encode_principal(
        [
            {"typ": "preferred_username", "val": "member@example.com"},
            {"typ": TENANT_CLAIM, "val": "tenant-id"},
        ]
    )

    access = parse_client_principal(principal)

    assert access.belongs_to_tenant("tenant-id")


def test_anonymous_user_never_belongs_to_tenant():
    assert not parse_oidc_user(None).belongs_to_tenant("tenant-id")


def test_cloudflare_claims_include_custom_roles():
    access = parse_cloudflare_access_claims(
        {
            "type": "app",
            "email": "editor@example.com",
            "sub": "cloudflare-user-id",
            "custom": {"roles": [EDITOR_ROLE]},
        },
        "tenant-id",
    )

    assert access.authenticated
    assert access.display_name == "editor@example.com"
    assert access.object_id == "cloudflare-user-id"
    assert access.can_edit_pairings
    assert access.belongs_to_tenant("tenant-id")


def test_cloudflare_claims_reject_service_tokens_and_missing_email():
    assert not parse_cloudflare_access_claims(
        {"type": "app"}, "tenant-id"
    ).authenticated
    assert not parse_cloudflare_access_claims(
        {"type": "org", "email": "member@example.com"}, "tenant-id"
    ).authenticated


def test_cloudflare_token_requires_valid_signature_issuer_and_audience(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    now = int(time())
    token = jwt.encode(
        {
            "type": "app",
            "email": "viewer@example.com",
            "sub": "cloudflare-user-id",
            "custom": {"roles": [VIEWER_ROLE]},
            "aud": ["expected-audience"],
            "iss": "https://example.cloudflareaccess.com",
            "iat": now,
            "nbf": now,
            "exp": now + 60,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )

    class TestSigningKey:
        key = public_key

    class TestJwkClient:
        def get_signing_key_from_jwt(self, _token):
            return TestSigningKey()

    monkeypatch.setattr(
        "access_control._cloudflare_jwk_client", lambda _domain: TestJwkClient()
    )

    access = validate_cloudflare_access_token(
        token,
        "https://example.cloudflareaccess.com",
        "expected-audience",
        "tenant-id",
    )
    wrong_audience = validate_cloudflare_access_token(
        token,
        "https://example.cloudflareaccess.com",
        "wrong-audience",
        "tenant-id",
    )

    assert access.can_view_pairings
    assert not access.can_edit_pairings
    assert not wrong_audience.authenticated


def encode_proxy_proof(payload: dict[str, object], key: bytes) -> str:
    nonce = os.urandom(12)
    encrypted = nonce + AESGCM(key).encrypt(
        nonce, json.dumps(payload).encode(), None
    )
    return base64.urlsafe_b64encode(encrypted).rstrip(b"=").decode()


def test_proxy_access_proof_is_authenticated_and_expires():
    key = os.urandom(32)
    secret = base64.urlsafe_b64encode(key).rstrip(b"=").decode()
    expires_at = int(time()) + 60
    proof = encode_proxy_proof(
        {
            "type": "app",
            "email": "editor@example.com",
            "sub": "cloudflare-user-id",
            "custom": {"roles": [EDITOR_ROLE]},
            "exp": expires_at,
        },
        key,
    )

    access, returned_expiry = validate_proxy_access_proof(
        proof, secret, "tenant-id"
    )

    assert access.can_edit_pairings
    assert access.belongs_to_tenant("tenant-id")
    assert returned_expiry == expires_at


def test_proxy_access_proof_rejects_tampering_and_expiry():
    key = os.urandom(32)
    secret = base64.urlsafe_b64encode(key).rstrip(b"=").decode()
    expired = encode_proxy_proof(
        {
            "type": "app",
            "email": "editor@example.com",
            "custom": {"roles": [EDITOR_ROLE]},
            "exp": int(time()) - 1,
        },
        key,
    )
    valid = encode_proxy_proof(
        {
            "type": "app",
            "email": "editor@example.com",
            "exp": int(time()) + 60,
        },
        key,
    )
    tampered = valid[:-1] + ("A" if valid[-1] != "A" else "B")

    assert not validate_proxy_access_proof(
        expired, secret, "tenant-id"
    )[0].authenticated
    assert not validate_proxy_access_proof(
        tampered, secret, "tenant-id"
    )[0].authenticated
