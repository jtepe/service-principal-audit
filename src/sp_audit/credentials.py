"""Pure mapping of Graph password/key credentials into Credential records.

Network-free and clock-free: the "now" used to derive a Credential's status is
injected, so the result is deterministic and unit-testable without a live Graph
client. The two Graph collections carry distinct credential types —
`passwordCredentials` are secrets, `keyCredentials` are certificates — and both
are flattened into one list tagged with the owning object.
"""

from __future__ import annotations

import datetime
from typing import Literal

from msgraph.generated.models.key_credential import KeyCredential
from msgraph.generated.models.password_credential import PasswordCredential

from .models import CredentialRecord

type Owner = Literal["application", "servicePrincipal"]
type GraphCredential = PasswordCredential | KeyCredential


def credential_status(
    start: datetime.datetime | None,
    end: datetime.datetime | None,
    now: datetime.datetime,
) -> Literal["active", "expired", "not-yet-valid"]:
    """Derive a Credential's validity status from both dates against `now`.

    An end date in the past is `expired`; otherwise a start date in the future is
    `not-yet-valid`; otherwise (started, not ended) it is `active`. `now` is
    injected and expected to be timezone-aware UTC.
    """
    if end is not None and end < now:
        return "expired"
    if start is not None and start > now:
        return "not-yet-valid"
    return "active"


def _credential_record(
    credential: GraphCredential,
    owner: Owner,
    credential_type: Literal["secret", "certificate"],
    now: datetime.datetime,
) -> CredentialRecord:
    start = credential.start_date_time
    end = credential.end_date_time
    key_id = credential.key_id
    return {
        "owner": owner,
        "credentialType": credential_type,
        "displayName": credential.display_name,
        "keyId": str(key_id) if key_id is not None else None,
        "startDateTime": start.isoformat() if start is not None else None,
        "endDateTime": end.isoformat() if end is not None else None,
        "status": credential_status(start, end, now),
    }


def map_credentials(
    owner: Owner,
    password_credentials: list[PasswordCredential] | None,
    key_credentials: list[KeyCredential] | None,
    now: datetime.datetime,
) -> list[CredentialRecord]:
    """Flatten one object's password and key credentials into tagged records.

    Secrets (`passwordCredentials`) and certificates (`keyCredentials`) are
    emitted in that order, each tagged with `owner` and its `credentialType` and
    carrying a status derived against the injected `now`.
    """
    records = [
        _credential_record(secret, owner, "secret", now)
        for secret in password_credentials or []
    ]
    records.extend(
        _credential_record(certificate, owner, "certificate", now)
        for certificate in key_credentials or []
    )
    return records
