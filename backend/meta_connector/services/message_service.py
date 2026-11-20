import logging
import uuid
from typing import Optional, Tuple
from django.utils import timezone
from meta_connector.models import PlatformAccount, Contact, Message
from .whatsapp_service import WhatsAppService
from .instagram_service import InstagramService

logger = logging.getLogger(__name__)


class MessageService:
    """
    High-level business service orchestrating outbound messages across WhatsApp and Instagram.
    Persists records to the Message database model with direction=outgoing.
    """

    @classmethod
    def get_or_create_default_account(cls, platform: str) -> PlatformAccount:
        """
        Locates or creates a default PlatformAccount if not already in database.
        """
        account = PlatformAccount.objects.filter(platform=platform, is_active=True).first()
        if not account:
            # Credentials are filled in later from Dashboard > Settings.
            account = PlatformAccount.objects.create(
                platform=platform,
                display_name=f"Default {platform.title()} Account",
                is_active=True
            )
        return account

    @classmethod
    def send_message(
        cls,
        platform: str,
        to: str,
        text: str,
        template_name: Optional[str] = None,
        account_id: Optional[int] = None,
        sent_by=None,
    ) -> Tuple[Message, bool, str]:
        """
        Sends an outbound message to WhatsApp or Instagram, and records it in Message table.

        :param platform: 'whatsapp' or 'instagram'
        :param to: Phone number for WhatsApp, or IGSID for Instagram.
        :param text: Text content of message.
        :param template_name: Optional WhatsApp template name.
        :param account_id: Specific PlatformAccount ID to send from.
        :return: (Message instance, success: bool, error_message: str)
        """
        platform = platform.lower().strip()
        if platform not in [PlatformAccount.PLATFORM_WHATSAPP, PlatformAccount.PLATFORM_INSTAGRAM]:
            raise ValueError(f"Unsupported platform: {platform}")

        # Resolve PlatformAccount
        if account_id:
            platform_account = PlatformAccount.objects.filter(id=account_id, is_active=True).first()
            if not platform_account:
                return None, False, f"PlatformAccount #{account_id} not found or inactive."
        else:
            platform_account = cls.get_or_create_default_account(platform)

        # Resolve or create Contact
        contact, _ = Contact.objects.get_or_create(
            platform_account=platform_account,
            platform_user_id=str(to).strip(),
            defaults={
                'phone': str(to).strip() if platform == PlatformAccount.PLATFORM_WHATSAPP else '',
                'username': str(to).strip() if platform == PlatformAccount.PLATFORM_INSTAGRAM else '',
            }
        )

        # Dispatch via platform service
        if platform == PlatformAccount.PLATFORM_WHATSAPP:
            success, response_data, error_message = WhatsAppService.send_whatsapp_message(
                to_number=to,
                text=text,
                template_name=template_name,
                platform_account=platform_account
            )
            # Extract Meta wamid if available
            meta_id = None
            if success and 'messages' in response_data and len(response_data['messages']) > 0:
                meta_id = response_data['messages'][0].get('id')
            message_id = meta_id or f"wamid_out_{uuid.uuid4().hex}"

        else:  # Instagram
            success, response_data, error_message = InstagramService.send_instagram_message(
                recipient_id=to,
                text=text,
                platform_account=platform_account
            )
            meta_id = response_data.get('message_id') if success else None
            message_id = meta_id or f"mid_out_{uuid.uuid4().hex}"

        # Persist outgoing message in DB
        status = Message.STATUS_SENT if success else Message.STATUS_FAILED
        msg_type = Message.TYPE_TEMPLATE if template_name else Message.TYPE_TEXT

        # update_or_create: Meta's echo webhook for this same message can arrive (and be saved) first.
        message_obj, _ = Message.objects.update_or_create(message_id=message_id, defaults=dict(
            platform_account=platform_account,
            contact=contact,
            sent_by=sent_by if getattr(sent_by, 'is_authenticated', False) else None,
            direction=Message.DIRECTION_OUTGOING,
            message_type=msg_type,
            message_text=text,
            raw_payload=response_data,
            status=status,
            is_read=True,
        ))

        # Update contact last message time
        contact.update_last_message_time()

        return message_obj, success, error_message
