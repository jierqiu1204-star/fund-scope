from __future__ import annotations

from datetime import datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup


async def fetch_news_for_fund(fund_code: str) -> list[dict[str, str | datetime]]:
    url = f"https://fund.eastmoney.com/{fund_code}.html"
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    items: list[dict[str, str | datetime]] = []
    seen_urls: set[str] = set()
    for link in soup.select("a[href*='news'], a[href*='gg'], a[href*='fund']")[:20]:
        title = link.get_text(strip=True)
        href = link.get("href")
        if not title or not href:
            continue
        absolute_url = urljoin(url, str(href))
        if absolute_url in seen_urls:
            continue
        seen_urls.add(absolute_url)
        items.append(
            {
                "title": title,
                "url": absolute_url,
                "published_at": datetime.utcnow(),
                "raw_content": title,
            }
        )
        if len(items) >= 10:
            break
    return items
