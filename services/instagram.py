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

    def extract_incoming_comments(self, payload: dict) -> list[dict]:
        """Pulls out new-comment events (someone commenting on one of our posts).

        For the Instagram API with Instagram Login, Meta puts `field` and
        `value` directly on the entry (no `changes[]` wrapper — that shape is
        only used by the older Instagram API with Facebook Login).

        Skips comments made by our own IG account so a private reply we send
        can never be picked up as a new comment and trigger another reply.
        """
        if payload.get("object") != "instagram":
            return []

        comments = []
        for entry in payload.get("entry", []):
            if entry.get("field") not in ("comments", "live_comments"):
                continue
            ig_business_id = entry.get("id")
            value = entry.get("value", {})
            commenter = value.get("from", {})
            commenter_id = commenter.get("id")
            comment_id = value.get("id")
            text = value.get("text")
            if not (comment_id and commenter_id and text):
                continue
            if commenter_id == ig_business_id:
                continue
            comments.append({
                "ig_business_id": ig_business_id,
                "comment_id": comment_id,
                "commenter_id": commenter_id,
                "commenter_username": commenter.get("username"),
                "text": text,
                "media_id": value.get("media", {}).get("id"),
            })
        return comments

    async def send_text_message(self, recipient_id: str, text: str, ig_business_id: str) -> dict:
        """POSTs to /<IG_ID>/messages, as required by the Instagram API with
        Instagram Login (graph.instagram.com) — this host does not support
        the older /me/messages alias from the Facebook Login flow."""
        url = f"{self.base_url}/{ig_business_id}/messages"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        body = {"recipient": {"id": recipient_id}, "message": {"text": text}}

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, headers=headers, json=body)

        if response.is_error:
            logger.error(f"Failed to send IG message to {recipient_id}: {response.text}")
        else:
            logger.info(f"Sent IG reply to {recipient_id}: {text}")

        return response.json()

    async def send_private_reply(self, comment_id: str, text: str, ig_business_id: str) -> dict:
        """DMs the person who left `comment_id`, via IG's private-reply API."""
        url = f"{self.base_url}/{ig_business_id}/messages"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        body = {"recipient": {"comment_id": comment_id}, "message": {"text": text}}

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, headers=headers, json=body)

        if response.is_error:
            logger.error(f"Failed to send private reply for comment {comment_id}: {response.text}")
        else:
            logger.info(f"Sent private-reply DM for comment {comment_id}: {text}")

        return response.json()

    async def handle_webhook(
        self,
        payload: dict,
        message_reply_text: str = "Hello!",
        comment_reply_text_template: str = 'Thank you for your comment: "{text}"',
    ) -> dict:
        """Handles every IG event in a webhook payload: auto-replies to DMs
        and sends a thank-you DM (quoting the comment) to anyone who comments
        on our posts."""
        message_results = []
        for message in self.extract_incoming_messages(payload):
            result = await self.send_text_message(
                recipient_id=message["sender_id"],
                text=message_reply_text,
                ig_business_id=message["ig_business_id"],
            )
            message_results.append(result)

        comment_results = []
        for comment in self.extract_incoming_comments(payload):
            result = await self.send_private_reply(
                comment_id=comment["comment_id"],
                text=comment_reply_text_template.format(text=comment["text"]),
                ig_business_id=comment["ig_business_id"],
            )
            comment_results.append(result)

        return {"messages": message_results, "comments": comment_results}
