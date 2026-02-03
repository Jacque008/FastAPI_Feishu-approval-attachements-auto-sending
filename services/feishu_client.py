import time
import httpx
from typing import Optional


class FeishuClient:
    BASE_URL = "https://open.feishu.cn/open-apis"

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0

    async def _get_tenant_access_token(self) -> str:
        """Get and cache tenant access token."""
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.BASE_URL}/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": self.app_id,
                    "app_secret": self.app_secret,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                raise Exception(f"Failed to get access token: {data.get('msg')}")

            self._access_token = data["tenant_access_token"]
            # Expire 5 minutes early to be safe
            self._token_expires_at = time.time() + data["expire"] - 300
            return self._access_token

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> dict:
        """Make authenticated request to Feishu API."""
        token = await self._get_tenant_access_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient() as client:
            resp = await client.request(
                method,
                f"{self.BASE_URL}{path}",
                headers=headers,
                **kwargs,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_approval_instance(self, instance_code: str) -> dict:
        """Get approval instance details."""
        result = await self._request(
            "GET",
            f"/approval/v4/instances/{instance_code}",
        )
        if result.get("code") != 0:
            raise Exception(f"Failed to get approval instance: {result.get('msg')}")
        return result["data"]

    async def get_file_download_urls(self, file_tokens: list[str]) -> dict[str, str]:
        """Get temporary download URLs for files.

        Returns a dict mapping file_token to download_url.
        """
        if not file_tokens:
            return {}

        result = await self._request(
            "GET",
            "/drive/v1/medias/batch_get_tmp_download_url",
            params={"file_tokens": ",".join(file_tokens)},
        )
        if result.get("code") != 0:
            raise Exception(f"Failed to get download URLs: {result.get('msg')}")

        return {
            item["file_token"]: item["tmp_download_url"]
            for item in result.get("data", {}).get("tmp_download_urls", [])
        }

    async def download_file(self, url: str) -> bytes:
        """Download file content from URL."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            return resp.content
