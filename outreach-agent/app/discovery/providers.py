from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from html import unescape
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen
import re
import time


class DiscoveryProvider(Protocol):
    name: str
    def search(self, query: str, target_count: int = 10) -> list[dict[str, Any]]: ...
    def get_business_details(self, result: dict[str, Any]) -> dict[str, Any]: ...
    def extract_contacts(self, result: dict[str, Any]) -> list[dict[str, Any]]: ...


@dataclass
class PublicSourceProvider:
    """Provider contract for manually reviewed public-source results.

    The first real test uses reviewed source records collected from 2GIS/Yandex/
    official pages. This adapter keeps source provenance instead of hiding it in
    one search-engine-specific implementation.
    """
    name: str
    records: list[dict[str, Any]]
    def search(self, query: str, target_count: int = 10) -> list[dict[str, Any]]:
        _ = query; return self.records[:target_count]
    def get_business_details(self, result: dict[str, Any]) -> dict[str, Any]: return result
    def extract_contacts(self, result: dict[str, Any]) -> list[dict[str, Any]]: return result.get("contacts", [])


class SourceUnavailable(RuntimeError):
    pass


class OpenStreetMapProvider:
    name = "openstreetmap"

    def __init__(self, timeout: int = 20): self.timeout = timeout

    def search(self, query: str, target_count: int = 100) -> list[dict[str, Any]]:
        city = query.rsplit(" ", 1)[-1] if query else "Астана"
        geocode = quote(city)
        nominatim = f"https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q={geocode}"
        try:
            raw, _ = _get(nominatim, timeout=self.timeout)
            places = __import__("json").loads(raw)
            if not places: raise SourceUnavailable(f"OSM city not found: {city}")
            place = places[0]; lat, lon = float(place["lat"]), float(place["lon"])
            delta = 0.22
            bbox = f"{lat-delta},{lon-delta},{lat+delta},{lon+delta}"
            overpass = "[out:json][timeout:15];(nwr[amenity=dentist]({bbox});nwr[healthcare=dentist]({bbox}););out center tags;".format(bbox=bbox)
            request = Request("https://overpass-api.de/api/interpreter", data=overpass.encode(), headers={"User-Agent": "B2B-Lead-Research/1.0", "Content-Type": "application/x-www-form-urlencoded"}, method="POST")
            started = time.perf_counter(); print(f"[DISCOVERY] HTTP start provider_domain=overpass-api.de timeout={self.timeout}s", flush=True)
            with urlopen(request, timeout=self.timeout) as response:
                data = response.read(2_000_000).decode("utf-8", errors="replace")
            print(f"[DISCOVERY] HTTP finish provider_domain=overpass-api.de status=200 elapsed={time.perf_counter()-started:.2f}s", flush=True)
            elements = __import__("json").loads(data).get("elements", [])
        except SourceUnavailable: raise
        except Exception as exc:
            raise SourceUnavailable(f"OpenStreetMap unavailable: {type(exc).__name__}: {str(exc)[:160]}") from exc
        results = []
        for element in elements:
            tags = element.get("tags") or {}; name = tags.get("name")
            if not name: continue
            element_id = f"{element.get('type')}/{element.get('id')}"
            lat = element.get("lat", (element.get("center") or {}).get("lat")); lon = element.get("lon", (element.get("center") or {}).get("lon"))
            address = ", ".join(x for x in (tags.get("addr:postcode"), tags.get("addr:street"), tags.get("addr:housenumber")) if x) or None
            website = tags.get("website") or tags.get("contact:website")
            source_url = f"https://www.openstreetmap.org/{element_id}"
            results.append({"name": name, "address": address, "city": city, "phone": tags.get("phone") or tags.get("contact:phone"), "website": website, "latitude": lat, "longitude": lon, "osm_id": element_id, "source_url": source_url, "source": "openstreetmap", "source_mode": "live", "category": "dental", "discovery_timestamp": utc_now() if 'utc_now' in globals() else time.strftime('%Y-%m-%dT%H:%M:%SZ'), "sources": [{"source": "openstreetmap", "source_url": source_url, "source_mode": "live"}]})
        if not results: raise SourceUnavailable("OpenStreetMap returned no named dental organizations")
        return results[:target_count]

    def get_business_details(self, result: dict[str, Any]) -> dict[str, Any]: return result
    def extract_contacts(self, result: dict[str, Any]) -> list[dict[str, Any]]: return result.get("contacts", [])


def _get(url: str, timeout: int = 12) -> tuple[str, str]:
    started = time.perf_counter(); host = urlparse(url).netloc
    print(f"[DISCOVERY] HTTP start provider_domain={host} timeout={timeout}s url={url}", flush=True)
    try:
        request = Request(url, headers={"User-Agent": "B2B-Lead-Research/1.0"})
        with urlopen(request, timeout=timeout) as response:
            body = response.read(800_000).decode("utf-8", errors="ignore")
            print(f"[DISCOVERY] HTTP finish provider_domain={host} status={getattr(response, 'status', 200)} elapsed={time.perf_counter()-started:.2f}s", flush=True)
            return body, response.geturl()
    except Exception as exc:
        print(f"[DISCOVERY] HTTP error provider_domain={host} type={type(exc).__name__} elapsed={time.perf_counter()-started:.2f}s reason={str(exc)[:180]}", flush=True)
        raise SourceUnavailable(str(exc)[:180]) from exc


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def _usable_result(name: str, href: str) -> bool:
    """Reject search-engine interstitials and non-business navigation links."""
    lowered_url = href.lower()
    lowered_name = name.casefold()
    blocked_fragments = (
        "api.whatsapp.com", "link.2gis.ru", "support.google.com",
        "accounts.google.com", "smartcaptcha", "yandex.com/support",
        "yandex.ru/support", "yandex.cloud", "/captcha", "/support/",
        "law.2gis.", "info.2gis.", "help.2gis.", "download.2gis.",
    )
    if any(fragment in lowered_url for fragment in blocked_fragments):
        return False
    if "�" in name or len(name.strip()) < 3:
        return False
    generic_names = ("why might this happen", "how to enable javascript", "yandex smartcaptcha", "whatsapp")
    return not any(value in lowered_name for value in generic_names)


class _SearchProvider:
    name = "web"
    search_url = ""
    result_pattern = re.compile(r"$^")

    def search(self, query: str, target_count: int = 10) -> list[dict[str, Any]]:
        url = self.search_url.format(query=quote(query)); started = time.perf_counter()
        print(f"[DISCOVERY] Starting {self.name} query={query}", flush=True)
        html, final_url = _get(url, timeout=12)
        results = []
        for match in self.result_pattern.finditer(html):
            name = _clean(match.groupdict().get("name", "")); href = unescape(match.groupdict().get("url", "")); text = _clean(match.groupdict().get("text", ""))
            if not name or not href.startswith("http") or not _usable_result(name, href): continue
            results.append({"name": name[:180], "description": text[:500], "source": self.name, "source_url": href, "sources": [{"source": self.name, "source_url": href}], "website": None})
            if len(results) >= target_count: break
        if not results: raise SourceUnavailable(f"{self.name}: no parseable public results from {final_url}")
        print(f"[DISCOVERY] Finished {self.name} candidates={len(results)} elapsed={time.perf_counter()-started:.2f}s", flush=True)
        return results

    def get_business_details(self, result: dict[str, Any]) -> dict[str, Any]: return result
    def extract_contacts(self, result: dict[str, Any]) -> list[dict[str, Any]]: return result.get("contacts", [])


class WebProvider(_SearchProvider):
    name = "web"
    search_url = "https://www.google.com/search?q={query}"
    result_pattern = re.compile(r'<a[^>]+href="(?P<url>https?://[^"&]+)"[^>]*>\s*(?P<name>.*?)</a>', re.I | re.S)


class YandexProvider(_SearchProvider):
    name = "yandex"
    search_url = "https://yandex.com/search/?text={query}"
    result_pattern = re.compile(r'<a[^>]+href="(?P<url>https?://[^"#]+)"[^>]*>(?P<name>.*?)</a>', re.I | re.S)


class TwoGISProvider(_SearchProvider):
    name = "2gis"
    search_url = "https://2gis.kz/astana/search/{query}"
    result_pattern = re.compile(r'<a[^>]+href="(?P<url>https?://[^"#]+)"[^>]*>(?P<name>.*?)</a>', re.I | re.S)

    def search(self, query: str, target_count: int = 10) -> list[dict[str, Any]]:
        # The public shell contains many footer/legal links; only firm pages are
        # business candidates. The generic parser must never promote those links.
        original = self.result_pattern
        self.result_pattern = re.compile(r'<a[^>]+href="(?P<url>https?://[^"#]*?/firm/[^"#]+)"[^>]*>(?P<name>.*?)</a>', re.I | re.S)
        try:
            return super().search(query, target_count)
        finally:
            self.result_pattern = original


class PlaywrightTwoGISProvider:
    """Bounded, non-stealth browser probe for the public 2GIS search page."""
    name = "playwright_2gis"

    def __init__(self, timeout_ms: int = 20_000):
        self.timeout_ms = timeout_ms

    def search(self, query: str, target_count: int = 10) -> list[dict[str, Any]]:
        started = time.perf_counter()
        print(f"[DISCOVERY] Starting PlaywrightTwoGIS query={query} timeout={self.timeout_ms}ms", flush=True)
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
        except ImportError as exc:
            raise SourceUnavailable("Playwright is not installed") from exc
        url = f"https://2gis.kz/astana/search/{quote(query)}"
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                page.wait_for_timeout(500)
                body = page.locator("body").inner_text(timeout=min(self.timeout_ms, 5_000))
                current_url = page.url
                if any(token in (current_url + " " + body).casefold() for token in ("captcha", "smartcaptcha", "robot check")):
                    raise SourceUnavailable("2GIS presented CAPTCHA or anti-bot challenge")
                anchors = page.locator('a[href*="/firm/"]')
                count = anchors.count()
                results = []
                seen = set()
                for index in range(min(count, target_count * 3)):
                    anchor = anchors.nth(index)
                    href = anchor.get_attribute("href") or ""
                    name = _clean(anchor.inner_text())
                    if href.startswith("/"): href = urljoin(current_url, href)
                    if not href.startswith("http") or href in seen or not _usable_result(name, href): continue
                    seen.add(href)
                    text = _clean(anchor.locator("xpath=ancestor::*[self::li or @role='article'][1]").inner_text()) if anchor else name
                    phone = next(iter(re.findall(r"(?:\+7|8)[\d\s()\-]{7,}", text)), None)
                    results.append({"name": name[:180], "address": None, "phone": phone, "website": None,
                                    "source": "2GIS", "source_mode": "live_browser", "source_url": href,
                                    "category": "стоматология", "sources": [{"source": "2GIS", "source_url": href, "source_mode": "live_browser"}]})
                    if len(results) >= target_count: break
                browser.close()
            if not results: raise SourceUnavailable("2GIS page loaded but no business cards were found")
            print(f"[DISCOVERY] Finished PlaywrightTwoGIS candidates={len(results)} elapsed={time.perf_counter()-started:.2f}s", flush=True)
            return results
        except SourceUnavailable:
            raise
        except Exception as exc:
            if "Timeout" in type(exc).__name__:
                raise SourceUnavailable(f"Playwright timeout after {self.timeout_ms}ms") from exc
            raise SourceUnavailable(f"Playwright 2GIS error: {str(exc)[:180]}") from exc

    def get_business_details(self, result: dict[str, Any]) -> dict[str, Any]: return result
    def extract_contacts(self, result: dict[str, Any]) -> list[dict[str, Any]]: return result.get("contacts", [])


class PlaywrightWebProvider:
    """Public browser search without API access or anti-bot workarounds."""
    name = "web_browser"

    def __init__(self, timeout_ms: int = 20_000): self.timeout_ms = timeout_ms

    def search(self, query: str, target_count: int = 10) -> list[dict[str, Any]]:
        started = time.perf_counter()
        print(f"[DISCOVERY] Starting PlaywrightWeb query={query} timeout={self.timeout_ms}ms", flush=True)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise SourceUnavailable("Playwright is not installed") from exc
        url = "https://www.bing.com/search?q=" + quote(query)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                body = page.locator("body").inner_text(timeout=5_000)
                current_url = page.url
                if any(token in (current_url + " " + body).casefold() for token in ("captcha", "verify you are human", "robot check")):
                    raise SourceUnavailable("Web search presented CAPTCHA or anti-bot challenge")
                cards = page.locator("li.b_algo")
                results = []; seen_domains = set()
                for index in range(min(cards.count(), target_count * 3)):
                    card = cards.nth(index)
                    link = card.locator("h2 a").first
                    href = link.get_attribute("href") or ""
                    name = _clean(link.inner_text())
                    text = _clean(card.inner_text())
                    host = urlparse(href).netloc.lower()
                    if not href.startswith("http") or not _usable_result(name, href) or not host: continue
                    if host in seen_domains: continue
                    if any(x in host for x in ("bing.com", "microsoft.com", "wikipedia.org")): continue
                    seen_domains.add(host)
                    results.append({"name": name[:180], "website": href, "source": "web", "source_mode": "live_browser", "source_url": href, "description": text[:500], "sources": [{"source": "web", "source_url": href, "source_mode": "live_browser"}]})
                    if len(results) >= target_count: break
                browser.close()
            if not results: raise SourceUnavailable("Web search loaded but no reliable organization results were found")
            print(f"[DISCOVERY] Finished PlaywrightWeb candidates={len(results)} elapsed={time.perf_counter()-started:.2f}s", flush=True)
            return results
        except SourceUnavailable: raise
        except Exception as exc:
            if "Timeout" in type(exc).__name__: raise SourceUnavailable(f"Playwright timeout after {self.timeout_ms}ms") from exc
            raise SourceUnavailable(f"Playwright web error: {str(exc)[:180]}") from exc

    def get_business_details(self, result: dict[str, Any]) -> dict[str, Any]: return result
    def extract_contacts(self, result: dict[str, Any]) -> list[dict[str, Any]]: return result.get("contacts", [])


def merge_provider_results(providers: list[DiscoveryProvider], query: str, target_count: int) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for provider in providers:
        for raw in provider.search(query, target_count):
            key = (raw.get("name", "").lower(), raw.get("city", "").lower())
            if key not in merged: merged[key] = raw | {"source_names": [provider.name]}
            else: merged[key]["source_names"] = sorted(set(merged[key].get("source_names", []) + [provider.name]))
    return list(merged.values())[:target_count]
