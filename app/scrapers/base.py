from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from typing import Awaitable, Callable, Iterable, List, Optional
from urllib.parse import quote_plus, urljoin, urlparse, urlunparse, urlencode, parse_qs

from playwright.async_api import BrowserContext, Page, async_playwright

from app.scrapers.config import ScrapeConfig
from app.scrapers.sites import SITE_CONFIGS

logger = logging.getLogger("scraper")

MAX_CONCURRENCY = 4

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_3_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

DEFAULT_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}

PRODUCT_PAGE_TIMEOUT_MS = 45000

ADD_TO_CART_SELECTORS = [
    "button:has-text('Add to Cart')",
    "button:has-text('Add to Bag')",
    "button:has-text('Add to Basket')",
]

SIZE_TOKEN_REGEX = re.compile(
    r"\b(?:XXXS|XXS|XS|S|M|L|XL|XXL|XXXL|ONE SIZE|OS|OSFA|PETITE|REGULAR|TALL)\b",
    re.IGNORECASE,
)
SIZE_NUMBER_REGEX = re.compile(
    r"\b(?:UK|US|EU)?\s?\d{1,2}(?:\.\d)?(?:-\d{1,2}(?:\.\d)?)?\b",
    re.IGNORECASE,
)
DESCRIPTION_BLACKLIST = [
    "strictly necessary cookies",
    "delivery time",
    "shipping method",
    "free above",
    "cookie",
    "cookies",
    "sort by",
    "prev",
    "next",
    "wishlist",
    "shipping",
]

CATALOG_PATHS = [
    "/collections/all",
    "/collections",
    "/shop",
    "/all",
    "/products",
]

SHOPIFY_PRODUCTS_LIMIT = 250
TRACKING_QUERY_PARAMS = {
    "cid",
    "session",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
}

PAGINATION_SELECTORS = [
    "a[rel='next']",
    "a[aria-label*='Next']",
    "a:has-text('Next')",
    "button:has-text('Next')",
]

DEFAULT_PAGINATION_PARAM = "page"
SCROLL_ATTEMPTS_RANGE = (2, 4)
SCROLL_PAUSE_SECONDS = 1.2


class SiteScraper:
    def __init__(
        self,
        context: BrowserContext,
        config: ScrapeConfig,
        log_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> None:
        self.context = context
        self.config = config
        self.log_callback = log_callback

    async def _log(self, message: str) -> None:
        logger.info(message)
        if self.log_callback:
            await self.log_callback(message)

    async def search(self, query: str) -> List[dict]:
        search_url = self.config.search_url.format(query=quote_plus(query))
        await self._log(f"{self.config.name}: searching {search_url}")
        page = await self.context.new_page()
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            await self._delay()
            product_urls = await self._collect_product_urls(page)
        finally:
            await page.close()

        await self._log(f"{self.config.name}: found {len(product_urls)} product links")
        results: List[dict] = []
        for index, url in enumerate(product_urls[: self.config.max_products], start=1):
            await self._log(
                f"{self.config.name}: scraping product {index}/{self.config.max_products}"
            )
            try:
                product = await self._scrape_product(url)
            except Exception as exc:
                await self._log(
                    f"{self.config.name}: product failed ({type(exc).__name__})"
                )
                continue
            if product:
                results.append(product)
        await self._log(f"{self.config.name}: scraped {len(results)} products")
        return results

    async def crawl_catalog(self) -> List[dict]:
        catalog_url = await self._discover_catalog_url()
        if not catalog_url:
            await self._log(f"{self.config.name}: catalog not found, skipping")
            return []

        await self._log(f"{self.config.name}: catalog start {catalog_url}")
        product_urls = await self._crawl_catalog_urls(catalog_url)
        await self._log(
            f"{self.config.name}: collected {len(product_urls)} catalog products"
        )

        results: List[dict] = []
        for index, url in enumerate(product_urls[: self.config.max_products], start=1):
            await self._log(
                f"{self.config.name}: scraping product {index}/{self.config.max_products}"
            )
            try:
                product = await self._scrape_product(url)
            except Exception as exc:
                await self._log(
                    f"{self.config.name}: product failed ({type(exc).__name__})"
                )
                continue
            if product:
                results.append(product)
        await self._log(f"{self.config.name}: scraped {len(results)} products")
        return results

    async def _discover_catalog_url(self) -> Optional[str]:
        if self.config.catalog_url:
            return self.config.catalog_url
        candidates = [urljoin(self.config.base_url, path) for path in CATALOG_PATHS]
        page = await self.context.new_page()
        try:
            for candidate in candidates:
                try:
                    await page.goto(
                        candidate, wait_until="domcontentloaded", timeout=60000
                    )
                    await self._delay()
                except Exception:
                    continue
                product_urls = await self._collect_product_urls(page)
                if product_urls:
                    self.config.catalog_url = candidate
                    return candidate
        finally:
            await page.close()
        return None

    async def _crawl_catalog_urls(self, start_url: str) -> List[str]:
        shopify_urls = await self._crawl_shopify_catalog_urls(start_url)
        if shopify_urls:
            return shopify_urls

        collected: List[str] = []
        seen_products: set[str] = set()
        visited_pages: set[str] = set()
        current_url: Optional[str] = start_url
        pending_fallback: Optional[str] = None
        page_index = 1

        while current_url and len(collected) < self.config.max_products:
            if current_url in visited_pages:
                break
            visited_pages.add(current_url)
            page = await self.context.new_page()
            try:
                await page.goto(
                    current_url, wait_until="domcontentloaded", timeout=60000
                )
                await self._delay()
                product_urls = await self._collect_product_urls(page)
                product_urls = await self._maybe_scroll_for_more(page, product_urls)
                new_count = 0
                for url in product_urls:
                    normalized = self._normalize_product_url(url)
                    if normalized not in seen_products:
                        seen_products.add(normalized)
                        collected.append(url)
                        new_count += 1
                        if len(collected) >= self.config.max_products:
                            break
                next_url, fallback_url = await self._find_next_page_url(
                    page, current_url, page_index + 1
                )
            finally:
                await page.close()

            if not product_urls or new_count == 0:
                if pending_fallback and pending_fallback not in visited_pages:
                    current_url = pending_fallback
                    pending_fallback = None
                    page_index += 1
                    continue
                break

            pending_fallback = None
            if fallback_url and next_url and fallback_url != next_url:
                pending_fallback = fallback_url

            if not next_url or next_url in visited_pages:
                break
            current_url = next_url
            page_index += 1

        return collected

    async def _find_next_page_url(
        self, page: Page, current_url: str, next_page: int
    ) -> tuple[Optional[str], Optional[str]]:
        selectors = (
            [self.config.pagination_selector] if self.config.pagination_selector else []
        )
        selectors.extend(PAGINATION_SELECTORS)
        for selector in selectors:
            if not selector:
                continue
            locator = page.locator(selector)
            if await locator.count() == 0:
                continue
            href = await locator.first.get_attribute("href")
            if href:
                next_url = urljoin(self.config.base_url, href)
                fallback_url = self._with_pagination_param(
                    current_url,
                    self.config.pagination_param or DEFAULT_PAGINATION_PARAM,
                    next_page,
                )
                return next_url, fallback_url

        param = self.config.pagination_param or DEFAULT_PAGINATION_PARAM
        return self._with_pagination_param(current_url, param, next_page), None

    async def _crawl_shopify_catalog_urls(self, start_url: str) -> List[str]:
        if "/collections/" not in start_url:
            return []

        parsed = urlparse(start_url)
        path = parsed.path.rstrip("/")
        if not path:
            return []

        base = f"{parsed.scheme}://{parsed.netloc}{path}"
        collected: List[str] = []
        seen_products: set[str] = set()
        page_index = 1

        while len(collected) < self.config.max_products:
            api_url = (
                f"{base}/products.json?limit={SHOPIFY_PRODUCTS_LIMIT}&page={page_index}"
            )
            try:
                response = await self.context.request.get(api_url, timeout=45000)
            except Exception:
                return []

            if response.status >= 400:
                return []

            try:
                payload = await response.json()
            except Exception:
                return []

            products = payload.get("products") if isinstance(payload, dict) else None
            if not products:
                break

            for product in products:
                handle = product.get("handle") if isinstance(product, dict) else None
                if not handle:
                    continue
                url = urljoin(self.config.base_url, f"/products/{handle}")
                normalized = self._normalize_product_url(url)
                if normalized not in seen_products:
                    seen_products.add(normalized)
                    collected.append(normalized)
                if len(collected) >= self.config.max_products:
                    break

            page_index += 1

        if not collected:
            return []

        await self._log(
            f"{self.config.name}: Shopify catalog collected {len(collected)} products"
        )
        return collected

    def _normalize_product_url(self, url: str) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        for key in list(query.keys()):
            if key in TRACKING_QUERY_PARAMS or key.startswith("utm_"):
                query.pop(key, None)
        cleaned = parsed._replace(query=urlencode(query, doseq=True))
        return urlunparse(cleaned)

    def _with_pagination_param(self, url: str, param: str, page: int) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        query[param] = [str(page)]
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

    async def _collect_product_urls(self, page: Page) -> List[str]:
        selectors = self.config.product_link_selectors
        urls: List[str] = []
        for selector in selectors:
            locator = page.locator(selector)
            if await locator.count() == 0:
                continue
            hrefs = await locator.evaluate_all(
                "elements => elements.map(el => el.getAttribute('href')).filter(Boolean)"
            )
            for href in hrefs:
                absolute = urljoin(self.config.base_url, href)
                if self._is_product_url(absolute) and absolute not in urls:
                    urls.append(absolute)
        return urls

    async def _maybe_scroll_for_more(
        self, page: Page, product_urls: List[str]
    ) -> List[str]:
        attempts = random.randint(*SCROLL_ATTEMPTS_RANGE)
        best_urls = product_urls
        for _ in range(attempts):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(SCROLL_PAUSE_SECONDS)
            await self._delay()
            fresh_urls = await self._collect_product_urls(page)
            if len(fresh_urls) > len(best_urls):
                best_urls = fresh_urls
            if len(best_urls) >= self.config.max_products:
                break
        return best_urls

    async def _scrape_product(self, url: str) -> Optional[dict]:
        page = await self.context.new_page()
        try:
            try:
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=PRODUCT_PAGE_TIMEOUT_MS
                )
                await self._delay()
            except Exception as exc:
                await self._log(
                    f"{self.config.name}: product page error ({type(exc).__name__})"
                )
                return None
            json_ld = await self._extract_json_ld(page)
            name = await self._first_text(page, self.config.name_selectors)
            if not name:
                name = json_ld.get("name")
            if not name:
                await self._log(f"{self.config.name}: skipped product without name")
                return None
            price = await self._first_text(page, self.config.price_selectors)
            if not price:
                price = json_ld.get("price")
            sizes = await self._all_texts(page, self.config.size_selectors)
            sizes = self._filter_sizes(sizes)
            availability = await self._availability(page)
            if not availability:
                availability = json_ld.get("availability")
            description = await self._first_text(
                page, self.config.description_selectors
            )
            if not description:
                description = json_ld.get("description")
            description = self._clean_description(description)
            return {
                "site": self.config.name,
                "name": name,
                "price": price,
                "url": url,
                "sizes": sizes,
                "availability": availability,
                "description": description,
            }
        finally:
            await page.close()

    async def _first_text(self, page: Page, selectors: Iterable[str]) -> Optional[str]:
        for selector in selectors:
            locator = page.locator(selector)
            if await locator.count() == 0:
                continue
            text = await locator.first.inner_text()
            text = self._clean_text(text)
            if text:
                return text
        return None

    async def _all_texts(self, page: Page, selectors: Iterable[str]) -> List[str]:
        for selector in selectors:
            locator = page.locator(selector)
            if await locator.count() == 0:
                continue
            texts = await locator.all_inner_texts()
            cleaned = [self._clean_text(text) for text in texts]
            cleaned = [text for text in cleaned if text]
            if cleaned:
                return list(dict.fromkeys(cleaned))
        return []

    async def _extract_json_ld(self, page: Page) -> dict:
        scripts = page.locator("script[type='application/ld+json']")
        if await scripts.count() == 0:
            return {}
        raw_items = await scripts.all_inner_texts()
        for raw in raw_items:
            raw = raw.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            product = self._find_product_json_ld(payload)
            if product:
                return product
        return {}

    def _find_product_json_ld(self, payload: object) -> dict:
        if isinstance(payload, list):
            for item in payload:
                found = self._find_product_json_ld(item)
                if found:
                    return found
            return {}
        if not isinstance(payload, dict):
            return {}

        if payload.get("@type") == "Product":
            return self._extract_product_fields(payload)

        if "@graph" in payload:
            return self._find_product_json_ld(payload["@graph"])

        return {}

    def _extract_product_fields(self, product: dict) -> dict:
        result: dict = {}
        name = product.get("name")
        if isinstance(name, str):
            result["name"] = self._clean_text(name)
        description = product.get("description")
        if isinstance(description, str):
            result["description"] = self._clean_description(description)

        offers = product.get("offers")
        if isinstance(offers, list) and offers:
            offers = offers[0]
        if isinstance(offers, dict):
            price = offers.get("price") or offers.get("priceSpecification", {}).get(
                "price"
            )
            if price is not None:
                result["price"] = str(price)
            availability = offers.get("availability")
            if isinstance(availability, str):
                result["availability"] = self._normalize_availability(availability)
        return result

    def _filter_sizes(self, sizes: List[str]) -> List[str]:
        tokens: List[str] = []
        for size in sizes:
            tokens.extend(self._extract_size_tokens(size))
        cleaned = [token.strip() for token in tokens if token.strip()]
        return list(dict.fromkeys(cleaned))

    def _extract_size_tokens(self, text: str) -> List[str]:
        cleaned = re.sub(r"\b\d+\s*reviews?\b", " ", text, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"\b(sort by|prev|next|choose size|find my size|join the waitlist|low stock)\b",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = cleaned.replace("/", " ")
        matches: List[str] = []
        for token in SIZE_TOKEN_REGEX.findall(cleaned):
            matches.append(token.upper())
        for token in SIZE_NUMBER_REGEX.findall(cleaned):
            normalized = token.upper().replace(" ", "")
            if self._is_reasonable_size_number(normalized):
                matches.append(normalized)
        return matches

    def _is_reasonable_size_number(self, token: str) -> bool:
        numeric = re.sub(r"[^0-9.]", "", token)
        if not numeric:
            return False
        try:
            value = float(numeric)
        except ValueError:
            return False
        return value <= 30

    def _clean_description(self, text: Optional[str]) -> Optional[str]:
        if not text:
            return None
        cleaned = self._clean_text(text)
        if not cleaned:
            return None
        parts = re.split(r"(?<=[.!?])\s+", cleaned)
        filtered = []
        for part in parts:
            lowered = part.lower()
            if any(block in lowered for block in DESCRIPTION_BLACKLIST):
                continue
            filtered.append(part)
        cleaned = " ".join(filtered).strip()
        return cleaned or None

    def _normalize_availability(self, text: str) -> Optional[str]:
        lowered = text.lower()
        if (
            "outofstock" in lowered
            or "out of stock" in lowered
            or "sold out" in lowered
        ):
            return "out_of_stock"
        if "instock" in lowered or "in stock" in lowered or "available" in lowered:
            return "in_stock"
        if "waitlist" in lowered:
            return "out_of_stock"
        return None

    async def _availability(self, page: Page) -> Optional[str]:
        availability_text = await self._first_text(
            page, self.config.availability_selectors
        )
        if availability_text:
            normalized = self._normalize_availability(availability_text)
            if normalized:
                return normalized
            lowered = availability_text.lower()
            if "low stock" in lowered:
                return "in_stock"

        for selector in ADD_TO_CART_SELECTORS:
            locator = page.locator(selector)
            if await locator.count() == 0:
                continue
            button = locator.first
            try:
                disabled = await button.is_disabled()
            except Exception:
                disabled = False
            return "out_of_stock" if disabled else "in_stock"

        return None

    def _is_product_url(self, url: str) -> bool:
        return any(segment in url for segment in ["/product/", "/products/", "/p/"])

    async def _delay(self) -> None:
        await asyncio.sleep(random.uniform(0.6, 1.4))

    def _clean_text(self, text: str) -> Optional[str]:
        cleaned = re.sub(r"\s+", " ", text).strip()
        return cleaned or None


async def run_scan(
    query: str,
    on_site_done: Optional[
        Callable[[str, List[dict], Optional[Exception]], Awaitable[None]]
    ] = None,
    on_log: Optional[Callable[[str], Awaitable[None]]] = None,
) -> List[dict]:
    logger.info("scan: starting")
    if on_log:
        await on_log("scan: starting")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1365, "height": 768},
                locale="en-US",
                timezone_id="America/New_York",
                extra_http_headers=DEFAULT_HEADERS,
            )
            await context.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                """
            )

            semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
            results: List[dict] = []

            async def scrape_site(config: ScrapeConfig) -> None:
                async with semaphore:
                    scraper = SiteScraper(context, config, log_callback=on_log)
                    site_results: List[dict] = []
                    error: Optional[Exception] = None
                    try:
                        site_results = await scraper.search(query)
                        results.extend(site_results)
                    except Exception as exc:
                        error = exc
                        logger.warning("%s: error %s", config.name, exc)
                    if on_site_done:
                        await on_site_done(config.name, site_results, error)

            await asyncio.gather(*(scrape_site(config) for config in SITE_CONFIGS))
            logger.info("scan: finished with %d products", len(results))
            if on_log:
                await on_log(f"scan: finished with {len(results)} products")
            return results
        finally:
            await browser.close()


async def run_scan_all(
    on_site_done: Optional[
        Callable[[str, List[dict], Optional[Exception]], Awaitable[None]]
    ] = None,
    on_log: Optional[Callable[[str], Awaitable[None]]] = None,
) -> List[dict]:
    logger.info("scan-all: starting")
    if on_log:
        await on_log("scan-all: starting")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1365, "height": 768},
                locale="en-US",
                timezone_id="America/New_York",
                extra_http_headers=DEFAULT_HEADERS,
            )
            await context.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                """
            )

            semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
            results: List[dict] = []

            async def scrape_site(config: ScrapeConfig) -> None:
                async with semaphore:
                    scraper = SiteScraper(context, config, log_callback=on_log)
                    site_results: List[dict] = []
                    error: Optional[Exception] = None
                    try:
                        site_results = await scraper.crawl_catalog()
                        if site_results:
                            results.extend(site_results)
                    except Exception as exc:
                        error = exc
                        logger.warning("%s: error %s", config.name, exc)
                    if on_site_done:
                        await on_site_done(config.name, site_results, error)

            await asyncio.gather(*(scrape_site(config) for config in SITE_CONFIGS))
            logger.info("scan-all: finished with %d products", len(results))
            if on_log:
                await on_log(f"scan-all: finished with {len(results)} products")
            return results
        finally:
            await browser.close()
