"""Plan claim coercion, normalization e tier ABAC (alinhado a node-b2b-orders)."""

from src.application.security import (
    _coerce_plan_claim,
    _normalize_abac_slug_list,
    _normalize_jwt_region,
    _normalize_plan_slug,
    _plan_allowed,
    build_principal,
)


def test_normalize_plan_uppercase_and_unicode() -> None:
    assert _normalize_plan_slug("ENTERPRISE") == "enterprise"
    assert _normalize_plan_slug("  Pro  ") == "pro"
    assert _normalize_plan_slug("professional") == "pro"
    assert _normalize_plan_slug(" Professional ") == "pro"


def test_coerce_plan_from_list_or_dict() -> None:
    assert _coerce_plan_claim(["enterprise"]) == "enterprise"
    assert _coerce_plan_claim({"slug": "pro"}) == "pro"
    assert _coerce_plan_claim(None) == "free"


def test_normalize_jwt_region_legacy_aws() -> None:
    assert _normalize_jwt_region(None) == "region-a"
    assert _normalize_jwt_region("") == "region-a"
    assert _normalize_jwt_region("us-east-1") == "region-a"
    assert _normalize_jwt_region("US-EAST-1") == "region-a"
    assert _normalize_jwt_region("region-b") == "region-b"


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


def test_build_principal_maps_professional_and_us_east() -> None:
    p = build_principal(
        {
            "sub": "u@x.com",
            "tid": "t1",
            "roles": ["ops"],
            "perms": ["ledger:read"],
            "plan": "professional",
            "region": "us-east-1",
            "jti": "j1",
        }
    )
    assert p.plan == "pro"
    assert p.region == "region-a"


def test_plan_allowed_tier_enterprise_when_policy_only_pro() -> None:
    assert _plan_allowed("enterprise", ["pro"])
    assert _plan_allowed("pro", ["pro"])
    assert not _plan_allowed("free", ["pro"])


def test_plan_allowed_unknown_policy_slugs_need_exact_match() -> None:
    assert not _plan_allowed("enterprise", ["custom_vendor_plan"])


def test_normalize_abac_slug_list_json_array_string() -> None:
    assert _normalize_abac_slug_list('["free","pro","enterprise"]') == [
        "free",
        "pro",
        "enterprise",
    ]


def test_normalize_abac_slug_list_pg_brace_literal() -> None:
    assert _normalize_abac_slug_list("{free,pro,enterprise}") == ["free", "pro", "enterprise"]


def test_normalize_abac_slug_list_json_string_value_not_char_split() -> None:
    """json.loads pode devolver str; não iterar caractere a caractere (bug staging)."""
    raw = '"free,pro,enterprise"'
    assert _normalize_abac_slug_list(raw) == ["free", "pro", "enterprise"]


def test_normalize_abac_slug_list_plain_list() -> None:
    assert _normalize_abac_slug_list(["Pro", " Enterprise "]) == ["pro", "enterprise"]
