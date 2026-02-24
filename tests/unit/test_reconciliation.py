from __future__ import annotations

import uuid

from src.application.reconciliation import DiscrepancyDTO


class TestDiscrepancyDTO:
    def test_dto_fields(self) -> None:
        dto = DiscrepancyDTO(
            id=str(uuid.uuid4()),
            tenant_id="t1",
            payment_intent_id=None,
            discrepancy_type="MISSING_LOCAL",
            gateway_ref="pi_123",
            expected_amount=None,
            actual_amount="100.00",
            expected_status=None,
            actual_status="succeeded",
            resolved=False,
            created_at="2026-02-23T10:00:00+00:00",
        )
        assert dto.discrepancy_type == "MISSING_LOCAL"
        assert dto.resolved is False

    def test_all_discrepancy_types(self) -> None:
        for dtype in ("MISSING_LOCAL", "MISSING_REMOTE", "AMOUNT_MISMATCH", "STATUS_MISMATCH"):
            dto = DiscrepancyDTO(
                id=str(uuid.uuid4()),
                tenant_id="t1",
                payment_intent_id=None,
                discrepancy_type=dtype,
                gateway_ref="pi_123",
                expected_amount=None,
                actual_amount=None,
                expected_status=None,
                actual_status=None,
                resolved=False,
                created_at="2026-02-23T10:00:00+00:00",
            )
            assert dto.discrepancy_type == dtype
