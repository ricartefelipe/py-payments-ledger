from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.worker.handlers.tenants import handle_tenant_event


class TestHandleTenantEvent:
    def _make_session(self, existing_tenant: object | None = None) -> MagicMock:
        session = MagicMock()
        session.get.return_value = existing_tenant
        session.begin.return_value.__enter__ = MagicMock(return_value=None)
        session.begin.return_value.__exit__ = MagicMock(return_value=False)
        session.flush = MagicMock()
        return session

    def test_missing_tenant_id_logs_warning(self) -> None:
        session = self._make_session()
        handle_tenant_event(session, "tenant.created", {})
        session.add.assert_not_called()

    @patch("src.worker.handlers.tenants.seed_default_accounts")
    def test_tenant_created(self, mock_seed: MagicMock) -> None:
        session = self._make_session(existing_tenant=None)
        handle_tenant_event(
            session,
            "tenant.created",
            {
                "tenant_id": "t_new",
                "name": "New Tenant",
                "plan": "enterprise",
                "region": "region-b",
            },
        )
        session.add.assert_called()
        mock_seed.assert_called_once_with(session, "t_new")

    @patch("src.worker.handlers.tenants.seed_default_accounts")
    def test_tenant_created_spring_saas_envelope(self, mock_seed: MagicMock) -> None:
        session = self._make_session(existing_tenant=None)
        handle_tenant_event(
            session,
            "saas.TENANT.tenant.created",
            {
                "aggregateType": "TENANT",
                "aggregateId": "550e8400-e29b-41d4-a716-446655440000",
                "eventType": "tenant.created",
                "payload": {
                    "name": "Acme",
                    "plan": "pro",
                    "region": "eu-west-1",
                },
            },
        )
        session.add.assert_called()
        mock_seed.assert_called_once_with(
            session, "550e8400-e29b-41d4-a716-446655440000"
        )

    def test_tenant_created_already_exists(self) -> None:
        existing = MagicMock()
        session = self._make_session(existing_tenant=existing)
        handle_tenant_event(session, "tenant.created", {"tenant_id": "t_existing"})
        session.add.assert_not_called()

    def test_tenant_updated(self) -> None:
        tenant = MagicMock()
        tenant.name = "Old Name"
        session = self._make_session(existing_tenant=tenant)
        handle_tenant_event(
            session,
            "tenant.updated",
            {
                "tenant_id": "t1",
                "name": "New Name",
                "plan": "enterprise",
            },
        )
        assert tenant.name == "New Name"
        assert tenant.plan == "enterprise"

    def test_tenant_updated_spring_saas_envelope(self) -> None:
        tenant = MagicMock()
        tenant.name = "Old"
        session = self._make_session(existing_tenant=tenant)
        handle_tenant_event(
            session,
            "saas.TENANT.tenant.updated",
            {
                "aggregateType": "TENANT",
                "aggregateId": "bb0e8400-e29b-41d4-a716-446655440088",
                "eventType": "tenant.updated",
                "payload": {"name": "NewCo", "plan": "enterprise"},
            },
        )
        assert tenant.name == "NewCo"
        assert tenant.plan == "enterprise"

    def test_tenant_deleted_soft_delete(self) -> None:
        tenant = MagicMock()
        tenant.name = "My Tenant"
        session = self._make_session(existing_tenant=tenant)
        handle_tenant_event(session, "tenant.deleted", {"tenant_id": "t1"})
        assert tenant.name == "[DELETED] My Tenant"

    def test_tenant_deleted_spring_saas_envelope(self) -> None:
        tenant = MagicMock()
        tenant.name = "Acme"
        session = self._make_session(existing_tenant=tenant)
        handle_tenant_event(
            session,
            "saas.TENANT.tenant.deleted",
            {
                "aggregateType": "TENANT",
                "aggregateId": "cc0e8400-e29b-41d4-a716-446655440077",
                "eventType": "tenant.deleted",
                "payload": {"name": "Acme", "plan": "pro"},
            },
        )
        assert tenant.name == "[DELETED] Acme"
