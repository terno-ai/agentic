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
                    text = text[:MAX_CONTENT] + f"\n... (truncated at {MAX_CONTENT} chars)"

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
                    "User-Agent": "Mozilla/5.0 (compatible; agentic/0.1)",
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
        results = []
        # Extract result blocks
        blocks = re.findall(
            r'<div class="result__body">(.*?)</div>\s*</div>',
            html, re.DOTALL
        )
        for block in blocks[:limit]:
            title_m = re.search(r'<a[^>]+class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL)
            url_m = re.search(r'href="([^"]+)"', block)
            snippet_m = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL)

            title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else ""
            url = url_m.group(1) if url_m else ""
            snippet = re.sub(r"<[^>]+>", " ", snippet_m.group(1)).strip() if snippet_m else ""

            if title and url:
                results.append({"title": title, "url": url, "snippet": snippet})
        return results
