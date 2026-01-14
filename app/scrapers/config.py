from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

DEFAULT_PRODUCT_LINK_SELECTORS = [
    "a[href*='/products/']",
    "a[href*='/product/']",
    "a[href*='/p/']",
    "a[data-testid*='product']",
    "a[class*='product']",
]

DEFAULT_NAME_SELECTORS = [
    "[data-testid='product-title']",
    "[data-testid*='product-name']",
    "h1[itemprop='name']",
    "h1[class*='product']",
    "h1",
]

DEFAULT_PRICE_SELECTORS = [
    "[data-testid*='price']",
    "[itemprop='price']",
    "[class*='price']",
    "[data-price]",
]

DEFAULT_SIZE_SELECTORS = [
    "button[aria-label*='Size']",
    "[data-testid*='size'] button",
    "[class*='size'] button",
    "select[name*='size'] option",
]

DEFAULT_AVAILABILITY_SELECTORS = [
    "[data-testid*='availability']",
    "[class*='availability']",
    "[class*='stock']",
]

DEFAULT_DESCRIPTION_SELECTORS = [
    "[data-testid='product-description']",
    "[class*='description']",
    "[itemprop='description']",
]


@dataclass
class ScrapeConfig:
    name: str
    base_url: str
    search_url: str
    product_link_selectors: List[str] = field(
        default_factory=lambda: DEFAULT_PRODUCT_LINK_SELECTORS
    )
    name_selectors: List[str] = field(default_factory=lambda: DEFAULT_NAME_SELECTORS)
    price_selectors: List[str] = field(default_factory=lambda: DEFAULT_PRICE_SELECTORS)
    size_selectors: List[str] = field(default_factory=lambda: DEFAULT_SIZE_SELECTORS)
    availability_selectors: List[str] = field(
        default_factory=lambda: DEFAULT_AVAILABILITY_SELECTORS
    )
    description_selectors: List[str] = field(
        default_factory=lambda: DEFAULT_DESCRIPTION_SELECTORS
    )
    catalog_url: str | None = None
    pagination_selector: str | None = None
    pagination_param: str | None = None
    max_products: int = 75
