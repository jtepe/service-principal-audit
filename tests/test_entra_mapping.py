"""Tests for the object-id -> record mapping (pure, no Graph client)."""

from __future__ import annotations

import datetime

from msgraph.generated.models.application import Application
from msgraph.generated.models.expiration_pattern import ExpirationPattern
from msgraph.generated.models.group import Group
from msgraph.generated.models.request_schedule import RequestSchedule
from msgraph.generated.models.service_principal import ServicePrincipal
from msgraph.generated.models.unified_role_assignment_schedule import (
    UnifiedRoleAssignmentSchedule,
)
from msgraph.generated.models.unified_role_definition import UnifiedRoleDefinition
from msgraph.generated.models.unified_role_eligibility_schedule import (
    UnifiedRoleEligibilitySchedule,
)

from sp_audit.entra import (
    application_record_from_graph,
    directory_role_from_schedule,
    group_membership_from_graph,
    sp_record_from_graph,
)


def test_group_membership_mapping_labels_membership_type() -> None:
    group = Group(
        id="g-1",
        display_name="role-assignable-group",
        is_assignable_to_role=True,
    )

    assert group_membership_from_graph(group, "direct") == {
        "groupId": "g-1",
        "displayName": "role-assignable-group",
        "membershipType": "direct",
        "isAssignableToRole": True,
        "pimMembership": None,
    }
    assert group_membership_from_graph(group, "transitive")["membershipType"] == (
        "transitive"
    )


def test_sp_record_carries_identity_tags_and_null_application() -> None:
    sp = ServicePrincipal(
        id="22222222-2222-2222-2222-222222222222",
        app_id="11111111-1111-1111-1111-111111111111",
        display_name="app-frontend-sp",
        tags=["terraform-iac", "prod"],
    )

    record = sp_record_from_graph(sp, None)

    assert record == {
        "objectId": "22222222-2222-2222-2222-222222222222",
        "appId": "11111111-1111-1111-1111-111111111111",
        "displayName": "app-frontend-sp",
        "tags": ["terraform-iac", "prod"],
        "application": None,
        "azureRoleAssignments": [],
        "groupMemberships": [],
        "directoryRoles": [],
        "errors": [],
    }


def test_sp_record_attaches_application_when_present() -> None:
    sp = ServicePrincipal(
        id="oid",
        app_id="aid",
        display_name="sp",
        tags=None,
    )
    app = Application(id="app-oid", app_id="aid", display_name="my-app-registration")

    record = sp_record_from_graph(sp, app)

    assert record["tags"] == []  # None tags degrade to empty list
    assert record["application"] == {
        "objectId": "app-oid",
        "appId": "aid",
        "displayName": "my-app-registration",
    }


def test_application_record_mapping() -> None:
    app = Application(id="app-oid", app_id="aid", display_name="name")
    assert application_record_from_graph(app) == {
        "objectId": "app-oid",
        "appId": "aid",
        "displayName": "name",
    }


def test_directory_role_mapping_extracts_raw_facts() -> None:
    start = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    end = datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC)
    schedule = UnifiedRoleAssignmentSchedule(
        role_definition=UnifiedRoleDefinition(display_name="Global Reader"),
        directory_scope_id="/",
        schedule_info=RequestSchedule(
            start_date_time=start,
            expiration=ExpirationPattern(end_date_time=end),
        ),
    )

    assert directory_role_from_schedule(schedule, "active", "direct", None) == {
        "roleName": "Global Reader",
        "assignmentType": "active",
        "source": "direct",
        "sourceGroupId": None,
        "directoryScopeId": "/",
        "startDateTime": start.isoformat(),
        "endDateTime": end.isoformat(),
    }


def test_directory_role_mapping_tolerates_missing_role_definition_and_dates() -> None:
    schedule = UnifiedRoleEligibilitySchedule(
        role_definition=None,
        directory_scope_id=None,
        schedule_info=RequestSchedule(start_date_time=None, expiration=None),
    )

    assert directory_role_from_schedule(
        schedule, "eligible", "finance-admins", "g-1"
    ) == {
        "roleName": None,
        "assignmentType": "eligible",
        "source": "finance-admins",
        "sourceGroupId": "g-1",
        "directoryScopeId": None,
        "startDateTime": None,
        "endDateTime": None,
    }


def test_sp_without_object_id_is_rejected() -> None:
    sp = ServicePrincipal(id=None, app_id="aid", display_name="sp")
    try:
        sp_record_from_graph(sp, None)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for missing object id")
