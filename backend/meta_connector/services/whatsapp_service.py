import logging
from typing import Optional, Tuple
import requests
from meta_connector.models import PlatformAccount, MetaConfig

logger = logging.getLogger(__name__)


class WhatsAppService:
    """
    Handles API communication with Meta WhatsApp Cloud API.
    Docs: https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages
    """

    @classmethod
    def send_whatsapp_message(
        cls,
        to_number: str,
        text: str,
        template_name: Optional[str] = None,
        template_language: str = 'en_US',
        platform_account: Optional[PlatformAccount] = None
    ) -> Tuple[bool, dict, str]:
        """
        Sends an outgoing WhatsApp message (Text or Template) via Meta Cloud API.

        :param to_number: Recipient's phone number with international country code (no + or spaces).
        :param text: Message body text (used for standard text messages).
        :param template_name: Optional Meta-approved template name.
        :param template_language: Language code for the template (e.g., 'en_US').
        :param platform_account: Optional PlatformAccount instance to pull credentials from.
        :return: (success: bool, response_data: dict, error_message: str)
        """
        phone_number_id = (
            (platform_account.phone_number_id if platform_account else None)
        )
        access_token = (
            (platform_account.get_access_token() if platform_account else None)
        )

        if not phone_number_id:
            msg = "Missing WhatsApp Phone Number ID. Set it in Dashboard > Settings > Accounts."
            logger.error(msg)
            return False, {}, msg

        if not access_token:
            msg = "Missing WhatsApp Access Token. Set it in Dashboard > Settings > Accounts."
            logger.error(msg)
            return False, {}, msg

        # Clean recipient phone number
        clean_to = "".join(filter(str.isdigit, str(to_number)))

        endpoint = f"{MetaConfig.get_solo().graph_base_url}/{phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        if template_name:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": clean_to,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": template_language},
                },
            }
        else:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": clean_to,
                "type": "text",
                "text": {
                    "preview_url": False,
                    "body": text,
                },
            }

        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=10
            )

            try:
                response_data = response.json()
            except ValueError:
                response_data = {"raw_text": response.text}

            if 200 <= response.status_code < 300:
                logger.info(
                    "WhatsApp message sent successfully to %s. Meta Message ID: %s",
                    clean_to,
                    response_data.get('messages', [{}])[0].get('id')
                )
                return True, response_data, ""
            else:
                err_msg = response_data.get('error', {}).get('message') or response.text
                logger.error(
                    "Failed to send WhatsApp message to %s (HTTP %s): %s",
                    clean_to,
                    response.status_code,
                    err_msg
                )
                return False, response_data, f"Meta HTTP {response.status_code}: {err_msg}"

        except requests.Timeout:
            err_msg = "Meta Cloud API request timed out after 10 seconds."
            logger.error(err_msg)
            return False, {}, err_msg
        except requests.RequestException as exc:
            err_msg = f"Network error sending WhatsApp message: {exc}"
            logger.error(err_msg)
            return False, {}, err_msg


# Standalone function shortcut matching requirements
