from __future__ import annotations

import re
from typing import Any

from fund_agent.domain.models import FundShare


SHARE_SUFFIX = re.compile(r"(?:\s*[-/]?\s*)([A-Za-z])(?:类)?\s*$")


def identify_share_class(name: str | None) -> str | None:
    if not name:
        return None
    match = SHARE_SUFFIX.search(name.strip())
    return match.group(1).upper() if match else None


def _product_name(name: str | None, share_class: str | None) -> str | None:
    if not name or not share_class:
        return name
    return SHARE_SUFFIX.sub("", name).strip() or None


def normalize_fund_input(value: str, metadata: dict[str, Any] | None = None) -> FundShare:
    code = value.strip()
    if not code.isdigit() or len(code) != 6:
        raise ValueError("fund code must contain exactly six digits")
    metadata = metadata or {}
    name_value = metadata.get("name")
    name = str(name_value).strip() if name_value else None
    declared_share = metadata.get("share_class")
    share_class = str(declared_share).upper() if declared_share else identify_share_class(name)
    product_value = metadata.get("product_id")
    product_id = str(product_value).strip() if product_value else _product_name(name, share_class)
    category_value = metadata.get("category")
    category = str(category_value).strip() if category_value else None
    return FundShare(
        code=code,
        product_id=product_id,
        name=name,
        category=category,
        share_class=share_class,
    )
