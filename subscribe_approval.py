"""Subscribe to approval events for a specific approval definition."""
import asyncio
import httpx
from config import get_settings
from services.feishu_client import FeishuClient


async def subscribe(approval_code: str):
    """Subscribe to approval events."""
    settings = get_settings()
    client = FeishuClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
    )

    token = await client._get_tenant_access_token()

    async with httpx.AsyncClient() as http:
        resp = await http.post(
            "https://www.feishu.cn/approval/openapi/v1/subscription/subscribe",
            headers={"Authorization": f"Bearer {token}"},
            json={"definition_code": approval_code},
        )
        data = resp.json()

        if data.get("code") == 0:
            print(f"✓ Subscribed to {approval_code}")
        else:
            print(f"✗ Failed to subscribe {approval_code}: {data}")

        return data


async def main():
    # Add all approval codes here
    approval_codes = [
        "96FDDC67-4638-4F4A-B220-90C222EADFE7",  # 付款-瑞典对公-SHIC
        "EFD8B67C-B895-404D-993F-C52D65F960A6",  # 费用报销
        # Add more approval codes as needed
    ]

    print("Subscribing to approval events...\n")
    for code in approval_codes:
        await subscribe(code)


if __name__ == "__main__":
    asyncio.run(main())
