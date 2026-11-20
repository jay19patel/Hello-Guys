import logging
from meta_connector.models import AutoDMLog, AutoDMRule, Contact, Message, PlatformAccount
from .instagram_service import InstagramService

logger = logging.getLogger(__name__)


class AutoDMService:
    """Instagram comment webhook → save the comment → send the post's auto-DM (Private Reply)."""

    @classmethod
    def handle_comment(cls, account: PlatformAccount, value: dict) -> None:
        """value = webhook entry.changes[field=comments].value: {id, text, from: {id, username}, media: {id}}"""
        comment_id = str(value.get('id', '')).strip()
        sender = value.get('from', {}) or {}
        sender_id = str(sender.get('id', '')).strip()
        username = sender.get('username', '')
        media_id = str((value.get('media') or {}).get('id', '')).strip()
        text = value.get('text', '')

        if not comment_id or not sender_id:
            return
        if sender_id == account.instagram_id:
            return  # our own comment or public reply; never DM ourselves
        if Message.objects.filter(message_id=comment_id).exists():
            return  # Meta re-delivered the same webhook

        contact, _ = Contact.objects.get_or_create(
            platform_account=account,
            platform_user_id=sender_id,
            defaults={'username': username or sender_id},
        )
        if username and contact.username in ('', sender_id):
            contact.username = username
            contact.save(update_fields=['username'])
        contact.update_last_message_time()

        Message.objects.create(
            platform_account=account,
            contact=contact,
            message_id=comment_id,
            direction=Message.DIRECTION_INCOMING,
            message_type=Message.TYPE_COMMENT,
            message_text=text,
            media_id=media_id,
            raw_payload=value,
            status=Message.STATUS_DELIVERED,
        )
        cls._auto_dm(account, contact, comment_id, media_id, text)

    @classmethod
    def _auto_dm(cls, account, contact, comment_id, media_id, text) -> None:
        # A post's own rule wins (even when switched off); otherwise the account default.
        rule = (AutoDMRule.objects.filter(platform_account=account, media_id=media_id).first()
                or AutoDMRule.objects.filter(platform_account=account, media_id='').first())
        if not rule:
            return

        def log(status, detail=''):
            AutoDMLog.objects.create(
                platform_account=account, contact=contact, media_id=media_id,
                comment_id=comment_id, comment_text=text, status=status, detail=detail,
            )
            logger.info("Auto-DM for comment %s: %s %s", comment_id, status, detail)

        if not rule.is_enabled:
            return log(AutoDMLog.STATUS_SKIPPED, "Auto-DM is off for this post.")
        if not rule.matches(text):
            return log(AutoDMLog.STATUS_SKIPPED, "Comment has none of the keywords.")
        if AutoDMLog.objects.filter(platform_account=account, media_id=media_id, contact=contact,
                                    status=AutoDMLog.STATUS_SENT).exists():
            return log(AutoDMLog.STATUS_SKIPPED, "This user already got the DM for this post.")

        ok, data, error = InstagramService.send_private_reply(account, comment_id, rule.message)
        if not ok:
            return log(AutoDMLog.STATUS_FAILED, error)

        # update_or_create: the echo webhook for this DM may already have saved it.
        Message.objects.update_or_create(message_id=data.get('message_id') or f"autodm_{comment_id}", defaults=dict(
            platform_account=account,
            contact=contact,
            direction=Message.DIRECTION_OUTGOING,
            message_type=Message.TYPE_TEXT,
            message_text=rule.message,
            raw_payload={**data, 'auto_dm': True},  # marks it as automated in the chat view
            status=Message.STATUS_SENT,
            is_read=True,
        ))
        detail = ''
        if rule.public_reply:
            ok, _, error = InstagramService.reply_to_comment(account, comment_id, rule.public_reply)
            if not ok:
                detail = f"DM sent, but the public reply failed: {error}"
        log(AutoDMLog.STATUS_SENT, detail)
