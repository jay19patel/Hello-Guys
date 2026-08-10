import asyncio
import os

from dotenv import load_dotenv

from services.instagram import InstagramService

load_dotenv()

USER_ID = "1409296267740821"          # sender.id from the webhook payload — the real user to message


async def main():
    service = InstagramService(
        access_token=os.getenv("IG_ACCESS_TOKEN"),
        api_version=os.getenv("GRAPH_API_VERSION", "v25.0"),
    )
    result = await service.send_text_message(
        recipient_id=USER_ID,
        text="Hi This is Test message",
    )
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
