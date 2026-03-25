"""Unit tests for SaaS routing key and envelope parsing (worker tenant handler)."""

from __future__ import annotations

from src.worker.handlers.tenants import _event_type_from_routing_key, _tenant_id_and_fields


class TestEventTypeFromRoutingKey:
    def test_short_legacy_key_unchanged(self) -> None:
        assert _event_type_from_routing_key("tenant.created") == "tenant.created"

    def test_spring_saas_tenant_event(self) -> None:
        assert (
            _event_type_from_routing_key("saas.TENANT.tenant.updated")
            == "tenant.updated"
        )

    def test_event_type_with_multiple_dots(self) -> None:
        assert (
            _event_type_from_routing_key("saas.USER.user.password_reset_requested")
            == "user.password_reset_requested"
        )


class TestTenantIdAndFields:
    def test_flat_payload(self) -> None:
        body = {"tenant_id": "t1", "name": "A"}
        tid, fields = _tenant_id_and_fields(body)
        assert tid == "t1"
        assert fields == body

    def test_core_envelope_uses_aggregate_id_for_tenant(self) -> None:
        body = {
            "aggregateType": "TENANT",
            "aggregateId": "aa0e8400-e29b-41d4-a716-446655440099",
            "eventType": "tenant.created",
            "payload": {"name": "Co", "plan": "pro", "region": "eu"},
        }
        tid, fields = _tenant_id_and_fields(body)
        assert tid == "aa0e8400-e29b-41d4-a716-446655440099"
        assert fields["name"] == "Co"

    def test_envelope_prefers_tenant_id_in_payload(self) -> None:
        body = {
            "aggregateType": "TENANT",
            "aggregateId": "ignored-if-payload-has-tenant",
            "eventType": "tenant.updated",
            "payload": {"tenantId": "t-pref", "plan": "basic"},
        }
        tid, fields = _tenant_id_and_fields(body)
        assert tid == "t-pref"
        assert fields["plan"] == "basic"
