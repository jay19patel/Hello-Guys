import re
import logging
from typing import Optional
import requests
from meta_connector.models import Contact, PlatformAccount, MetaConfig

logger = logging.getLogger(__name__)

# Regex for capturing email addresses from conversation messages
EMAIL_REGEX = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b')


class ProfileService:
    """
    Enriches Contact records with profile information fetched from Meta Graph API
    (e.g., Name, Instagram handle, Profile Picture CDN URL) and automatic extraction
    of email and phone numbers from user conversations.
    """

    @classmethod
    def enrich_contact_profile(
        cls,
        contact: Contact,
        platform_account: Optional[PlatformAccount] = None
    ) -> bool:
        """
        Calls Meta Graph API to fetch profile information for the contact.
        For Instagram: Fetches 'name', 'username', and 'profile_pic'.
        """
        account = platform_account or contact.platform_account
        if not account:
            return False

        if account.platform == PlatformAccount.PLATFORM_INSTAGRAM:
            return cls._fetch_instagram_profile(contact, account)
        elif account.platform == PlatformAccount.PLATFORM_WHATSAPP:
            return cls._fetch_whatsapp_profile(contact, account)
        return False

    @classmethod
    def _fetch_instagram_profile(cls, contact: Contact, account: PlatformAccount) -> bool:
        """
        Instagram User Profile API:
        GET https://graph.facebook.com/v21.0/{IGSID}?fields=name,username,profile_pic&access_token={PAGE_ACCESS_TOKEN}
        Docs: https://developers.facebook.com/docs/messenger-platform/instagram/features/user-profile
        """
        token = account.get_access_token()
        if not token:
            logger.warning("No access token on account '%s'; cannot fetch Instagram profile for %s. Set it in Dashboard > Settings.", account.display_name, contact.platform_user_id)
            return False

        igsid = str(contact.platform_user_id).strip()
        url = f"{MetaConfig.get_solo().graph_base_url}/{igsid}"
        params = {
            'fields': 'name,username,profile_pic',
            'access_token': token
        }

        try:
            response = requests.get(url, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json()
                updated_fields = []

                if data.get('name') and not contact.name:
                    contact.name = data['name']
                    updated_fields.append('name')

                # Webhook stores the numeric IGSID as a placeholder username; replace it with the real handle.
                if data.get('username') and contact.username in ('', contact.platform_user_id):
                    contact.username = data['username']
                    updated_fields.append('username')

                if data.get('profile_pic'):
                    contact.profile_pic_url = data['profile_pic']
                    updated_fields.append('profile_pic_url')

                contact.raw_profile_data = data
                updated_fields.append('raw_profile_data')

                if updated_fields:
                    contact.save(update_fields=updated_fields)
                    logger.info("Successfully enriched Instagram profile for user %s: %s", igsid, data.get('username') or data.get('name'))
                return True
            else:
                logger.warning("Could not fetch Instagram profile for %s (HTTP %s): %s", igsid, response.status_code, response.text)
                return False
        except Exception as exc:
            logger.warning("Error fetching Instagram profile for %s: %s", igsid, exc)
            return False

    @classmethod
    def _fetch_whatsapp_profile(cls, contact: Contact, account: PlatformAccount) -> bool:
        """
        Formats and parses WhatsApp profile details.
        """
        # WhatsApp Cloud API does not expose direct profile pictures via public API for privacy,
        # but contact name is captured directly from webhook contacts array.
        return True

    @classmethod
    def extract_contact_info_from_message(cls, contact: Contact, text_content: str) -> bool:
        """
        Scans incoming contact messages for shared email addresses or contact details.
        If contact provides their email (e.g. 'My email is user@example.com'),
        it automatically populates contact.email.
        """
        if not text_content:
            return False

        updated = False
        # Extract email if not already present
        if not contact.email:
            match = EMAIL_REGEX.search(text_content)
            if match:
                extracted_email = match.group(0).lower().strip()
                contact.email = extracted_email
                contact.save(update_fields=['email'])
                logger.info("Auto-captured email '%s' for contact #%s (%s)", extracted_email, contact.id, contact.name or contact.platform_user_id)
                updated = True

        return updated
