from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.application.accounts import AccountConfigDTO, create_account, list_accounts


class TestCreateAccount:
    def test_invalid_type_raises_400(self) -> None:
        session = MagicMock()
        with pytest.raises(Exception) as exc_info:
            create_account(session, "t1", "TEST", "Test", "INVALID_TYPE")
        assert exc_info.value.status_code == 400

    def test_valid_types_accepted(self) -> None:
        for t in ("ASSET", "LIABILITY", "EQUITY", "REVENUE", "EXPENSE"):
            session = MagicMock()
            session.execute.return_value.scalar_one_or_none.return_value = None
            session.begin.return_value.__enter__ = MagicMock(return_value=None)
            session.begin.return_value.__exit__ = MagicMock(return_value=False)
            session.flush = MagicMock()
            # mock the AccountConfig that gets created
            from unittest.mock import ANY
            result = create_account(session, "t1", f"ACC_{t}", f"Account {t}", t)
            assert result.account_type == t

    def test_duplicate_raises_409(self) -> None:
        session = MagicMock()
        existing = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = existing
        session.begin.return_value.__enter__ = MagicMock(return_value=None)
        session.begin.return_value.__exit__ = MagicMock(return_value=False)
        with pytest.raises(Exception) as exc_info:
            create_account(session, "t1", "CASH", "Cash", "ASSET")
        assert exc_info.value.status_code == 409


class TestAccountConfigDTO:
    def test_dto_fields(self) -> None:
        dto = AccountConfigDTO(
            id="abc", code="CASH", label="Cash", account_type="ASSET", is_default=True
        )
        assert dto.code == "CASH"
        assert dto.is_default is True
