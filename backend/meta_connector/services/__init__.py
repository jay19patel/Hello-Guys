"""Public API of meta_connector for other apps:

    from meta_connector.services import send_message, SendError
    send_message(contact, "Hi!", sent_by=request.user)   # WhatsApp / Instagram / Messenger, raises SendError
"""

from .auto_dm_service import AutoDMService
from .instagram_service import InstagramService
from .message_service import MessageService, SendError, send_message
from .profile_service import ProfileService
from .webhook_processor import process_webhook_payload
from .whatsapp_service import WhatsAppService

__all__ = [
    'AutoDMService',
    'InstagramService',
    'MessageService',
    'ProfileService',
    'SendError',
    'WhatsAppService',
    'process_webhook_payload',
    'send_message',
]
