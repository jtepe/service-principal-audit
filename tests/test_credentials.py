"""Tests for the pure Credential mapping and derived status (no Graph client).

Status is derived from both `startDateTime` and `endDateTime` against an
injected timezone-aware UTC now, so every case is deterministic. The two Graph
collections map to distinct credential types: `passwordCredentials` -> `secret`,
`keyCredentials` -> `certificate`.
"""

from __future__ import annotations

import datetime
import uuid

from msgraph.generated.models.key_credential import KeyCredential
from msgraph.generated.models.password_credential import PasswordCredential

from sp_audit.credentials import map_credentials

NOW = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
KEY_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
CERT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def test_secret_with_end_in_past_is_expired() -> None:
    secret = PasswordCredential(
        key_id=KEY_ID,
        display_name="rotated-out",
        start_date_time=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
        end_date_time=datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC),
    )

    credentials = map_credentials("servicePrincipal", [secret], [], NOW)

    assert credentials == [
        {
            "owner": "servicePrincipal",
            "credentialType": "secret",
            "displayName": "rotated-out",
            "keyId": str(KEY_ID),
            "startDateTime": "2024-01-01T00:00:00+00:00",
            "endDateTime": "2025-01-01T00:00:00+00:00",
            "status": "expired",
        }
    ]


def test_secret_with_start_in_future_is_not_yet_valid() -> None:
    secret = PasswordCredential(
        key_id=KEY_ID,
        start_date_time=datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC),
        end_date_time=datetime.datetime(2028, 1, 1, tzinfo=datetime.UTC),
    )

    [credential] = map_credentials("servicePrincipal", [secret], [], NOW)

    assert credential["status"] == "not-yet-valid"


def test_credential_started_with_future_end_is_active() -> None:
    secret = PasswordCredential(
        key_id=KEY_ID,
        start_date_time=datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC),
        end_date_time=datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC),
    )

    [credential] = map_credentials("servicePrincipal", [secret], [], NOW)

    assert credential["status"] == "active"


def test_credential_started_with_no_end_is_active() -> None:
    secret = PasswordCredential(
        key_id=KEY_ID,
        start_date_time=datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC),
        end_date_time=None,
    )

    [credential] = map_credentials("servicePrincipal", [secret], [], NOW)

    assert credential["status"] == "active"
    assert credential["endDateTime"] is None


def test_key_credentials_map_to_certificates_from_application() -> None:
    certificate = KeyCredential(
        key_id=CERT_ID,
        display_name="signing-cert",
        start_date_time=datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC),
        end_date_time=datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC),
    )

    credentials = map_credentials("application", [], [certificate], NOW)

    assert credentials == [
        {
            "owner": "application",
            "credentialType": "certificate",
            "displayName": "signing-cert",
            "keyId": str(CERT_ID),
            "startDateTime": "2025-01-01T00:00:00+00:00",
            "endDateTime": "2027-01-01T00:00:00+00:00",
            "status": "active",
        }
    ]


def test_secrets_and_certificates_flatten_in_order() -> None:
    secret = PasswordCredential(key_id=KEY_ID)
    certificate = KeyCredential(key_id=CERT_ID)

    credentials = map_credentials("servicePrincipal", [secret], [certificate], NOW)

    assert [(c["credentialType"], c["keyId"]) for c in credentials] == [
        ("secret", str(KEY_ID)),
        ("certificate", str(CERT_ID)),
    ]
