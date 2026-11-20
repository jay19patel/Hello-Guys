import logging
import traceback
from decimal import Decimal
from django.utils import timezone
from meta_connector.models import WebhookLog, PlatformAccount, Contact, Message
from meta_connector.services.message_service import MessageService
from meta_connector.services.profile_service import ProfileService
from meta_connector.services.auto_dm_service import AutoDMService

logger = logging.getLogger(__name__)


def process_webhook_payload(webhook_log_id: int):
    """
    Synchronously processes incoming Meta webhook payloads without needing Celery/Redis.
    Handles message extraction, Contact upsert, deduplication, and status updates.
    """
    try:
        webhook_log = WebhookLog.objects.get(id=webhook_log_id)
    except WebhookLog.DoesNotExist:
        logger.error("WebhookLog #%s does not exist.", webhook_log_id)
        return

    payload = webhook_log.payload or {}
    meta_object = payload.get('object', '')

    try:
        if meta_object == 'whatsapp_business_account':
            _process_whatsapp_webhook(payload, webhook_log)
        elif meta_object in ['instagram', 'page']:
            _process_instagram_webhook(payload, webhook_log)
        else:
            logger.info("Unrecognized or unhandled Meta object: %s", meta_object)

        webhook_log.processed = True
        webhook_log.error_message = ''  # clear an earlier failure when a retry succeeds
        webhook_log.save(update_fields=['processed', 'error_message'])
        logger.info("Successfully processed WebhookLog #%s for object: %s", webhook_log_id, meta_object)

    except Exception as exc:
        err_trace = traceback.format_exc()
        logger.error("Error processing WebhookLog #%s: %s\n%s", webhook_log_id, exc, err_trace)
        webhook_log.error_message = f"{exc}\n{err_trace}"
        webhook_log.save(update_fields=['error_message'])


def _process_whatsapp_webhook(payload: dict, webhook_log: WebhookLog):
    """
    Parses WhatsApp Cloud API webhook structure:
    payload -> entry[] -> changes[] -> value -> {metadata, contacts, messages, statuses}
    """
    entries = payload.get('entry', [])
    for entry in entries:
        changes = entry.get('changes', [])
        for change in changes:
            if change.get('field') != 'messages':
                continue

            value = change.get('value', {})
            metadata = value.get('metadata', {})
            phone_number_id = str(metadata.get('phone_number_id', '')).strip()

            # Locate or create PlatformAccount
            account = PlatformAccount.objects.filter(
                platform=PlatformAccount.PLATFORM_WHATSAPP,
                phone_number_id=phone_number_id,
                is_active=True
            ).first()

            if not account:
                account = MessageService.get_or_create_default_account(PlatformAccount.PLATFORM_WHATSAPP)
                if not account.phone_number_id and phone_number_id:
                    account.phone_number_id = phone_number_id
                    account.save(update_fields=['phone_number_id'])

            # 1. Handle Status updates (sent, delivered, read, failed)
            statuses = value.get('statuses', [])
            for st in statuses:
                wamid = st.get('id')
                status_name = st.get('status')
                if wamid and status_name:
                    mapped_status = {
                        'sent': Message.STATUS_SENT,
                        'delivered': Message.STATUS_DELIVERED,
                        'read': Message.STATUS_READ,
                        'failed': Message.STATUS_FAILED,
                    }.get(status_name.lower(), Message.STATUS_DELIVERED)
                    Message.objects.filter(message_id=wamid).update(
                        status=mapped_status,
                        updated_at=timezone.now()
                    )

            # 2. Map contacts for names
            wa_names = {}
            for wa_contact in value.get('contacts', []):
                wa_id = str(wa_contact.get('wa_id', '')).strip()
                name = wa_contact.get('profile', {}).get('name', '')
                if wa_id:
                    wa_names[wa_id] = name

            # 3. Handle Incoming Messages
            messages = value.get('messages', [])
            for msg in messages:
                message_id = msg.get('id')
                if not message_id:
                    continue

                # Deduplication check: skip if message_id already stored
                if Message.objects.filter(message_id=message_id).exists():
                    logger.info("WhatsApp duplicate message skipped: %s", message_id)
                    continue

                from_number = str(msg.get('from', '')).strip()
                contact_name = wa_names.get(from_number, '')

                contact, _ = Contact.objects.get_or_create(
                    platform_account=account,
                    platform_user_id=from_number,
                    defaults={
                        'phone': from_number,
                        'name': contact_name,
                    }
                )
                if contact_name and not contact.name:
                    contact.name = contact_name
                contact.last_message_at = timezone.now()
                contact.save(update_fields=['name', 'last_message_at'])

                # Parse message content according to type
                msg_type = msg.get('type', Message.TYPE_UNKNOWN)
                text_content = ""
                media_id = ""
                media_mime = ""
                caption = ""
                lat = None
                lon = None

                if msg_type == 'text':
                    text_content = msg.get('text', {}).get('body', '')
                    msg_type = Message.TYPE_TEXT
                    if text_content:
                        ProfileService.extract_contact_info_from_message(contact, text_content)
                elif msg_type == 'image':
                    img_data = msg.get('image', {})
                    media_id = img_data.get('id', '')
                    media_mime = img_data.get('mime_type', '')
                    caption = img_data.get('caption', '')
                    msg_type = Message.TYPE_IMAGE
                elif msg_type == 'video':
                    vid_data = msg.get('video', {})
                    media_id = vid_data.get('id', '')
                    media_mime = vid_data.get('mime_type', '')
                    caption = vid_data.get('caption', '')
                    msg_type = Message.TYPE_VIDEO
                elif msg_type == 'audio':
                    aud_data = msg.get('audio', {})
                    media_id = aud_data.get('id', '')
                    media_mime = aud_data.get('mime_type', '')
                    msg_type = Message.TYPE_AUDIO
                elif msg_type == 'document':
                    doc_data = msg.get('document', {})
                    media_id = doc_data.get('id', '')
                    media_mime = doc_data.get('mime_type', '')
                    caption = doc_data.get('filename', '') or doc_data.get('caption', '')
                    msg_type = Message.TYPE_DOCUMENT
                elif msg_type == 'sticker':
                    stk_data = msg.get('sticker', {})
                    media_id = stk_data.get('id', '')
                    media_mime = stk_data.get('mime_type', '')
                    msg_type = Message.TYPE_STICKER
                elif msg_type == 'location':
                    loc_data = msg.get('location', {})
                    lat = Decimal(str(loc_data.get('latitude', 0))) if loc_data.get('latitude') else None
                    lon = Decimal(str(loc_data.get('longitude', 0))) if loc_data.get('longitude') else None
                    text_content = loc_data.get('name') or loc_data.get('address') or "Location Shared"
                    msg_type = Message.TYPE_LOCATION
                elif msg_type == 'reaction':
                    rx_data = msg.get('reaction', {})
                    text_content = rx_data.get('emoji', '')
                    caption = f"Reaction to {rx_data.get('message_id', '')}"
                    msg_type = Message.TYPE_REACTION
                elif msg_type == 'interactive':
                    interactive = msg.get('interactive', {})
                    text_content = (
                        interactive.get('button_reply', {}).get('title')
                        or interactive.get('list_reply', {}).get('title')
                        or "Interactive Response"
                    )
                    msg_type = Message.TYPE_TEXT
                else:
                    msg_type = Message.TYPE_UNKNOWN

                Message.objects.create(
                    platform_account=account,
                    contact=contact,
                    message_id=message_id,
                    direction=Message.DIRECTION_INCOMING,
                    message_type=msg_type,
                    message_text=text_content,
                    media_id=media_id,
                    media_mime_type=media_mime,
                    caption=caption,
                    latitude=lat,
                    longitude=lon,
                    raw_payload=msg,
                    status=Message.STATUS_DELIVERED
                )
                logger.info("Saved incoming WhatsApp message: %s from %s", message_id, from_number)


IG_ATTACHMENT_TYPES = {'image': Message.TYPE_IMAGE, 'video': Message.TYPE_VIDEO, 'audio': Message.TYPE_AUDIO, 'file': Message.TYPE_DOCUMENT}


def _save_instagram_echo(account: PlatformAccount, item: dict) -> None:
    """Stores a reply sent from the Instagram app as outgoing, so the chat shows both sides."""
    msg = item.get('message') or {}
    mid = msg.get('mid')
    recipient = str((item.get('recipient') or {}).get('id', '')).strip()
    if not mid or not recipient or Message.objects.filter(message_id=mid).exists():
        return  # already saved when the dashboard / auto-DM sent it
    contact, _ = Contact.objects.get_or_create(
        platform_account=account, platform_user_id=recipient, defaults={'username': recipient})
    attachment = (msg.get('attachments') or [{}])[0]
    msg_type = Message.TYPE_TEXT if msg.get('text') or not msg.get('attachments') else IG_ATTACHMENT_TYPES.get(attachment.get('type'), Message.TYPE_UNKNOWN)
    Message.objects.create(
        platform_account=account,
        contact=contact,
        message_id=mid,
        direction=Message.DIRECTION_OUTGOING,
        message_type=msg_type,
        message_text=msg.get('text', ''),
        media_url=(attachment.get('payload') or {}).get('url', ''),
        raw_payload={**msg, 'is_echo': True},
        status=Message.STATUS_SENT,
        is_read=True,
    )
    contact.update_last_message_time()
    logger.info("Saved Instagram echo (sent from the app): %s to %s", mid, recipient)


def _process_instagram_webhook(payload: dict, webhook_log: WebhookLog):
    """
    Parses Instagram Messaging API webhook structure:
    payload -> entry[] -> messaging[] -> {sender, recipient, message}
    """
    entries = payload.get('entry', [])
    for entry in entries:
        entry_id = str(entry.get('id', '')).strip()

        account = PlatformAccount.objects.filter(
            platform=PlatformAccount.PLATFORM_INSTAGRAM,
            instagram_id=entry_id,
            is_active=True
        ).first()

        if not account:
            account = MessageService.get_or_create_default_account(PlatformAccount.PLATFORM_INSTAGRAM)
            if not account.instagram_id and entry_id:
                account.instagram_id = entry_id
                account.save(update_fields=['instagram_id'])

        # Comments on Reels/Posts arrive as entry.changes (needs the 'comments' webhook field).
        for change in entry.get('changes', []):
            if change.get('field') == 'comments':
                AutoDMService.handle_comment(account, change.get('value', {}))

        messaging_events = entry.get('messaging', [])
        for item in messaging_events:
            sender_id = str(item.get('sender', {}).get('id', '')).strip()

            # Echo = something the business sent (Instagram app on the phone, or our own API send).
            if (item.get('message') or {}).get('is_echo') or sender_id == account.instagram_id:
                _save_instagram_echo(account, item)
                continue

            msg = item.get('message')
            if not msg:
                reaction = item.get('reaction')
                if reaction:
                    mid = reaction.get('mid') or f"ig_rx_{timezone.now().timestamp()}"
                    if not Message.objects.filter(message_id=mid).exists():
                        contact, _ = Contact.objects.get_or_create(
                            platform_account=account,
                            platform_user_id=sender_id,
                            defaults={'username': sender_id}
                        )
                        contact.update_last_message_time()
                        Message.objects.create(
                            platform_account=account,
                            contact=contact,
                            message_id=mid,
                            direction=Message.DIRECTION_INCOMING,
                            message_type=Message.TYPE_REACTION,
                            message_text=reaction.get('emoji', ''),
                            raw_payload=reaction,
                            status=Message.STATUS_DELIVERED
                        )
                continue

            mid = msg.get('mid')
            if not mid:
                continue

            # Deduplication
            if Message.objects.filter(message_id=mid).exists():
                logger.info("Instagram duplicate message skipped: %s", mid)
                continue

            contact, created = Contact.objects.get_or_create(
                platform_account=account,
                platform_user_id=sender_id,
                defaults={'username': sender_id}
            )
            contact.update_last_message_time()

            # Enrich profile (name, username, profile_pic_url) from Meta Graph API
            if created or not contact.profile_pic_url or not contact.name:
                ProfileService.enrich_contact_profile(contact, account)

            text_content = msg.get('text', '')
            if text_content:
                ProfileService.extract_contact_info_from_message(contact, text_content)
            msg_type = Message.TYPE_TEXT
            media_url = ""
            media_id = ""

            attachments = msg.get('attachments', [])
            if attachments:
                first_att = attachments[0]
                att_type = first_att.get('type')
                payload_data = first_att.get('payload', {})
                media_url = payload_data.get('url', '')

                if att_type == 'image':
                    msg_type = Message.TYPE_IMAGE
                elif att_type == 'video':
                    msg_type = Message.TYPE_VIDEO
                elif att_type == 'audio':
                    msg_type = Message.TYPE_AUDIO
                elif att_type == 'file':
                    msg_type = Message.TYPE_DOCUMENT
                else:
                    msg_type = Message.TYPE_UNKNOWN

            Message.objects.create(
                platform_account=account,
                contact=contact,
                message_id=mid,
                direction=Message.DIRECTION_INCOMING,
                message_type=msg_type,
                message_text=text_content,
                media_url=media_url,
                media_id=media_id,
                raw_payload=msg,
                status=Message.STATUS_DELIVERED
            )
            logger.info("Saved incoming Instagram message: %s from user: %s", mid, sender_id)
