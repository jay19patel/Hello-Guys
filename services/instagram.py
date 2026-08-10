import logging

import httpx

logger = logging.getLogger("ig-automation")


class InstagramService:
    """Parses Instagram webhook payloads and sends replies via the Graph API."""

    def __init__(self, access_token: str, api_version: str = "v25.0"):
        self.access_token = access_token
        self.base_url = f"https://graph.instagram.com/{api_version}"

    def extract_incoming_messages(self, payload: dict) -> list[dict]:
        """Pulls out real, human-sent text messages from a webhook payload.

        Skips read receipts, delivery receipts, and echoes of our own replies
        (which Meta also delivers via this same webhook) so we never reply to
        ourselves in a loop.
        """
        if payload.get("object") != "instagram":
            return []

        messages = []
        for entry in payload.get("entry", []):
            for event in entry.get("messaging", []):
                message = event.get("message")
                if not message or message.get("is_echo"):
                    continue
                text = message.get("text")
                sender_id = event.get("sender", {}).get("id")
                ig_business_id = event.get("recipient", {}).get("id")
                if not (text and sender_id and ig_business_id):
                    continue
                messages.append({
                    "ig_business_id": ig_business_id,
                    "sender_id": sender_id,
                    "text": text,
                    "mid": message.get("mid"),
                })
        return messages

    async def send_text_message(self, recipient_id: str, text: str) -> dict:
        url = f"{self.base_url}/me/messages"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        body = {"recipient": {"id": recipient_id}, "message": {"text": text}}

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, headers=headers, json=body)

        if response.is_error:
            logger.error(f"Failed to send IG message to {recipient_id}: {response.text}")
        else:
            logger.info(f"Sent IG reply to {recipient_id}: {text}")

        return response.json()

    async def handle_webhook(self, payload: dict, reply_text: str = "Hi This is Test message") -> list[dict]:
        """Auto-replies to every real incoming message found in the payload."""
        results = []
        for message in self.extract_incoming_messages(payload):
            result = await self.send_text_message(
                recipient_id=message["sender_id"],
                text=reply_text,
            )
            results.append(result)
        return results
