"""Plan claim coercion, normalization e tier ABAC (alinhado a node-b2b-orders)."""

from src.application.security import (
    _coerce_plan_claim,
    _normalize_plan_slug,
    _plan_allowed,
    build_principal,
)


def test_normalize_plan_uppercase_and_unicode() -> None:
    assert _normalize_plan_slug("ENTERPRISE") == "enterprise"
    assert _normalize_plan_slug("  Pro  ") == "pro"


def test_coerce_plan_from_list_or_dict() -> None:
    assert _coerce_plan_claim(["enterprise"]) == "enterprise"
    assert _coerce_plan_claim({"slug": "pro"}) == "pro"
    assert _coerce_plan_claim(None) == "free"


def test_build_principal_uses_normalized_plan() -> None:
    p = build_principal(
        {
            "sub": "u@x.com",
            "tid": "t1",
            "roles": ["ops"],
            "perms": ["ledger:read"],
            "plan": "ENTERPRISE",
            "region": "region-a",
            "jti": "j1",
        }
    )
    assert p.plan == "enterprise"


def test_plan_allowed_tier_enterprise_when_policy_only_pro() -> None:
    assert _plan_allowed("enterprise", ["pro"])
    assert _plan_allowed("pro", ["pro"])
    assert not _plan_allowed("free", ["pro"])


def test_plan_allowed_unknown_policy_slugs_need_exact_match() -> None:
    assert not _plan_allowed("enterprise", ["custom_vendor_plan"])
