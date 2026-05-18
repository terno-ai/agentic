"""WebFetch and WebSearch tools."""

from __future__ import annotations

import re
from typing import Any

import httpx

from agentic.tools.base import Tool, ToolResult

MAX_CONTENT = 50_000


class WebFetchTool(Tool):
    name = "WebFetch"
    description = (
        "Fetch a URL and return its text content. "
        "Strips HTML tags, returns readable text."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "prompt": {"type": "string", "description": "What to extract from the page"},
        },
        "required": ["url"],
    }

    async def execute(self, url: str, prompt: str = "") -> ToolResult:
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=30,
                headers={"User-Agent": "agentic/0.1 (autonomous agent)"},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                text = resp.text

                if "html" in content_type:
                    text = self._strip_html(text)

                if len(text) > MAX_CONTENT:
                    omitted = len(text) - MAX_CONTENT
                    text = f"[...{omitted:,} chars omitted from start...]\n" + text[-MAX_CONTENT:]

                return ToolResult.ok(text, url=url, status=resp.status_code)
        except httpx.HTTPStatusError as e:
            return ToolResult.error(f"HTTP {e.response.status_code} for {url}")
        except Exception as e:
            return ToolResult.error(f"Failed to fetch {url}: {e}")

    @staticmethod
    def _strip_html(html: str) -> str:
        # Remove scripts, styles, head
        html = re.sub(r"<(script|style|head)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
        # Remove tags
        text = re.sub(r"<[^>]+>", " ", html)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        return text


class WebSearchTool(Tool):
    name = "WebSearch"
    description = (
        "Search the web using DuckDuckGo. "
        "Returns a list of results with titles, URLs, and snippets."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "num_results": {"type": "integer", "description": "Number of results (default 5, max 10)"},
        },
        "required": ["query"],
    }

    async def execute(self, query: str, num_results: int = 5) -> ToolResult:
        num_results = min(num_results, 10)
        try:
            # Use DuckDuckGo Lite HTML endpoint (no API key required)
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=15,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Accept": "text/html",
                },
            ) as client:
                resp = await client.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": query, "kl": "us-en"},
                )
                text = resp.text

            results = self._parse_ddg(text, num_results)
            if not results:
                return ToolResult.ok(f"No results found for: {query}")

            formatted = f"Search results for: {query}\n\n"
            for i, r in enumerate(results, 1):
                formatted += f"{i}. **{r['title']}**\n   {r['url']}\n   {r['snippet']}\n\n"

            return ToolResult.ok(formatted.strip())
        except Exception as e:
            return ToolResult.error(f"Search failed: {e}")

    @staticmethod
    def _parse_ddg(html: str, limit: int) -> list[dict[str, str]]:
        """
        Parse DuckDuckGo HTML results page.

        DDG's HTML layout (as of 2025):
          <a class="result__a" href="DIRECT_URL">TITLE</a>
          <a class="result__snippet" href="...">SNIPPET TEXT</a>

        Both elements appear sequentially; we extract them independently
        and pair them by position, which is robust to minor HTML changes.
        """
        from html import unescape

        def clean(s: str) -> str:
            return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(s))).strip()

        titles = re.findall(
            r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html, re.DOTALL,
        )
        snippets = re.findall(
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            html, re.DOTALL,
        )

        results = []
        for i, (url, raw_title) in enumerate(titles):
            if len(results) >= limit:
                break
            title = clean(raw_title)
            snippet = clean(snippets[i]) if i < len(snippets) else ""
            # Skip DDG ad redirect URLs (y.js) and non-http links
            if title and url.startswith("http") and "duckduckgo.com/y.js" not in url:
                results.append({"title": title, "url": url, "snippet": snippet})
        return results
