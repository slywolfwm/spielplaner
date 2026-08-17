import base64
import json

from access_control import (
    EDITOR_ROLE,
    TENANT_CLAIM,
    VIEWER_ROLE,
    parse_client_principal,
    parse_oidc_user,
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
