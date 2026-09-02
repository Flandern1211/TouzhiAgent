import pytest

from fund_agent.funds.identity import identify_share_class, normalize_fund_input


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("天弘机器人ETF发起式联接C", "C"),
        ("南方致远混合 E", "E"),
        ("示例基金A类", "A"),
        ("没有份额后缀", None),
        (None, None),
    ],
)
def test_identify_share_class_from_name_suffix(name, expected):
    assert identify_share_class(name) == expected


def test_normalize_fund_input_uses_metadata_and_strips_code():
    fund = normalize_fund_input(
        " 012345 ",
        {"name": "天弘机器人ETF发起式联接C", "category": "ETF联接"},
    )

    assert fund.code == "012345"
    assert fund.product_id == "天弘机器人ETF发起式联接"
    assert fund.share_class == "C"
    assert fund.category == "ETF联接"


def test_normalize_fund_input_rejects_invalid_code():
    with pytest.raises(ValueError, match="six digits"):
        normalize_fund_input("123")
