from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.adapters.base import BaseAdapter
from app.config import get_settings

logger = structlog.get_logger(__name__)

_GITHUB_BASE_URL = "https://api.github.com"


class GitHubAdapter(BaseAdapter):
    """GitHub REST API adapter using personal access token."""

    def __init__(self) -> None:
        settings = get_settings()
        self._token = settings.github_token
        self._org = settings.github_org
        self._connected: bool = False

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def connect(self) -> None:
        if not self._token:
            logger.warning("github_credentials_missing")
            self._connected = False
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{_GITHUB_BASE_URL}/user",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                user = resp.json()
                self._connected = True
                logger.info("github_connected", login=user.get("login"))
        except Exception as exc:
            logger.error("github_connect_failed", error=str(exc))
            self._connected = False
            raise

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("github_disconnected")

    def is_connected(self) -> bool:
        return self._connected

    async def _ensure_connected(self) -> None:
        if not self._connected:
            await self.connect()

    async def get_contact(self, contact_id: str) -> dict[str, Any]:
        await self._ensure_connected()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_GITHUB_BASE_URL}/users/{contact_id}",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def create_note(self, client_id: str, note: str) -> dict[str, Any]:
        # GitHub maps notes to issue comments; client_id format: owner/repo#number
        await self._ensure_connected()
        parts = client_id.split("#")
        if len(parts) != 2:
            raise ValueError("client_id must be 'owner/repo#issue_number' for GitHub notes")
        repo, issue_number = parts
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_GITHUB_BASE_URL}/repos/{repo}/issues/{issue_number}/comments",
                headers=self._headers(),
                json={"body": note},
            )
            resp.raise_for_status()
            data = resp.json()
            return {"success": True, "comment_id": data.get("id"), "html_url": data.get("html_url")}

    async def search_leads(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        query = str(filters.get("query", ""))
        result = await self.search_repos(query)
        return result.get("items", [])

    async def search_repos(self, query: str) -> dict[str, Any]:
        await self._ensure_connected()
        # Use user: qualifier (works for personal accounts); org: only matches GitHub orgs
        if self._org:
            full_query = f"{query} user:{self._org}"
        else:
            full_query = query
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{_GITHUB_BASE_URL}/search/repositories",
                headers=self._headers(),
                params={"q": full_query, "per_page": 30},
            )
            resp.raise_for_status()
            return resp.json()

    async def create_issue(self, repo: str, title: str, body: str) -> dict[str, Any]:
        await self._ensure_connected()
        owner_repo = repo if "/" in repo else f"{self._org}/{repo}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_GITHUB_BASE_URL}/repos/{owner_repo}/issues",
                headers=self._headers(),
                json={"title": title, "body": body},
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "success": True,
                "issue_number": data.get("number"),
                "html_url": data.get("html_url"),
                "title": data.get("title"),
            }

    async def get_pr_list(self, repo: str) -> dict[str, Any]:
        await self._ensure_connected()
        owner_repo = repo if "/" in repo else f"{self._org}/{repo}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_GITHUB_BASE_URL}/repos/{owner_repo}/pulls",
                headers=self._headers(),
                params={"state": "open", "per_page": 50},
            )
            resp.raise_for_status()
            pulls = resp.json()
            return {
                "repo": owner_repo,
                "total": len(pulls),
                "pull_requests": [
                    {
                        "number": pr["number"],
                        "title": pr["title"],
                        "author": pr["user"]["login"],
                        "created_at": pr["created_at"],
                        "html_url": pr["html_url"],
                    }
                    for pr in pulls
                ],
            }


# Singleton
github_adapter = GitHubAdapter()
