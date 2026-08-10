import logging

import httpx

logger = logging.getLogger("ig-automation")


class WhatsAppService:
    """Parses WhatsApp Cloud API webhook payloads and sends replies via the Graph API."""

    def __init__(self, access_token: str, phone_number_id: str | None = None, api_version: str = "v26.0"):
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.base_url = f"https://graph.facebook.com/{api_version}"

    def extract_incoming_messages(self, payload: dict) -> list[dict]:
        """Pulls out real, user-sent text messages from a webhook payload.

        The same `changes[].value` object also carries `statuses` (sent /
        delivered / read receipts for messages we sent) — those have no
        `messages` key so they're naturally skipped here.
        """
        if payload.get("object") != "whatsapp_business_account":
            return []

        messages = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                if change.get("field") != "messages":
                    continue
                value = change.get("value", {})
                phone_number_id = value.get("metadata", {}).get("phone_number_id")
                for message in value.get("messages", []):
                    sender_id = message.get("from")
                    text = message.get("text", {}).get("body")
                    if not (sender_id and text and phone_number_id):
                        continue
                    messages.append({
                        "phone_number_id": phone_number_id,
                        "sender_id": sender_id,
                        "text": text,
                        "message_id": message.get("id"),
                    })
        return messages

    async def send_text_message(self, recipient_id: str, text: str, phone_number_id: str | None = None) -> dict:
        phone_number_id = phone_number_id or self.phone_number_id
        url = f"{self.base_url}/{phone_number_id}/messages"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_id,
            "type": "text",
            "text": {"body": text},
        }

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, headers=headers, json=body)

        if response.is_error:
            logger.error(f"Failed to send WhatsApp message to {recipient_id}: {response.text}")
        else:
            logger.info(f"Sent WhatsApp reply to {recipient_id}: {text}")

        return response.json()

    async def handle_webhook(self, payload: dict, reply_text: str = "Thank you for messaging us!") -> list[dict]:
        """Auto-replies to every real incoming message found in the payload."""
        results = []
        for message in self.extract_incoming_messages(payload):
            result = await self.send_text_message(
                recipient_id=message["sender_id"],
                text=reply_text,
                phone_number_id=message["phone_number_id"],
            )
            results.append(result)
        return results
