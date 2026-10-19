import json
from pathlib import Path

import httpx

API = "https://api.telegra.ph"


class TelegraphError(Exception):
    pass


class Telegraph:
    def __init__(self, token: str | None, token_file: Path):
        self._token = token
        self._token_file = token_file
        self._client = httpx.AsyncClient(timeout=20)

    async def _call(self, method: str, **params) -> dict:
        resp = await self._client.post(f"{API}/{method}", data=params)
        data = resp.json()
        if not data.get("ok"):
            raise TelegraphError(data.get("error", "unknown error"))
        return data["result"]

    async def _get_token(self) -> str:
        if self._token:
            return self._token
        if self._token_file.exists():
            self._token = self._token_file.read_text().strip()
            return self._token
        account = await self._call("createAccount", short_name="TeleView", author_name="TeleView")
        self._token = account["access_token"]
        self._token_file.write_text(self._token)
        return self._token

    async def create_page(self, title: str, nodes: list, author_name: str = "", author_url: str = "") -> str:
        page = await self._call(
            "createPage",
            access_token=await self._get_token(),
            title=title[:256],
            author_name=author_name[:128],
            author_url=author_url[:512],
            content=json.dumps(nodes, ensure_ascii=False),
            return_content="false",
        )
        return page["url"]

    async def close(self) -> None:
        await self._client.aclose()
