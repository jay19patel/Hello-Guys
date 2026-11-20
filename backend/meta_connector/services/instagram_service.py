import logging
from typing import Optional, Tuple
import requests
from meta_connector.models import PlatformAccount, MetaConfig

logger = logging.getLogger(__name__)


class InstagramService:
    """
    Handles API communication with Meta Instagram Messaging API.
    Docs: https://developers.facebook.com/docs/messenger-platform/instagram/overview
    """

    @classmethod
    def send_instagram_message(
        cls,
        recipient_id: str,
        text: str,
        platform_account: Optional[PlatformAccount] = None
    ) -> Tuple[bool, dict, str]:
        """
        Sends an outgoing direct message to an Instagram user via Meta Graph API.

        :param recipient_id: Instagram-scoped user ID (IGSID).
        :param text: Message text body.
        :param platform_account: Optional PlatformAccount instance to pull credentials from.
        :return: (success: bool, response_data: dict, error_message: str)
        """
        access_token = (
            (platform_account.get_access_token() if platform_account else None)
        )

        if not access_token:
            msg = "Missing Instagram Access Token. Set it in Dashboard > Settings > Accounts."
            logger.error(msg)
            return False, {}, msg

        # Page token (Instagram via Facebook Login): "me" is the Page, /me/messages is the Instagram Send API.
        endpoint = f"{MetaConfig.get_solo().graph_base_url}/me/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "recipient": {"id": str(recipient_id)},
            "message": {"text": text},
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
                    "Instagram message sent successfully to recipient %s. Message ID: %s",
                    recipient_id,
                    response_data.get('message_id')
                )
                return True, response_data, ""
            else:
                err_msg = response_data.get('error', {}).get('message') or response.text
                logger.error(
                    "Failed to send Instagram message to %s (HTTP %s): %s",
                    recipient_id,
                    response.status_code,
                    err_msg
                )
                return False, response_data, f"Meta HTTP {response.status_code}: {err_msg}"

        except requests.Timeout:
            err_msg = "Instagram API request timed out after 10 seconds."
            logger.error(err_msg)
            return False, {}, err_msg
        except requests.RequestException as exc:
            err_msg = f"Network error sending Instagram message: {exc}"
            logger.error(err_msg)
            return False, {}, err_msg

    # --- Graph API reads/writes used by the account page and auto-DM ---

    @classmethod
    def _graph(cls, method: str, account: PlatformAccount, path: str, **kwargs) -> Tuple[bool, dict, str]:
        """Calls the Graph API with the account's token. Returns (ok, data, error_message)."""
        token = account.get_access_token()
        if not token:
            return False, {}, "No access token saved for this account. Set it in Dashboard > Settings."
        try:
            response = requests.request(
                method, f"{MetaConfig.get_solo().graph_base_url}/{path}",
                headers={"Authorization": f"Bearer {token}"}, timeout=10, **kwargs
            )
            data = response.json()
        except requests.RequestException as exc:
            return False, {}, f"Network error talking to Meta: {exc}"
        except ValueError:
            return False, {}, f"Meta returned a non-JSON response (HTTP {response.status_code})."
        if 200 <= response.status_code < 300:
            return True, data, ""
        error = data.get('error', {})
        if error.get('code') == 190:  # invalid / expired / revoked token
            from meta_connector.services.token_service import TokenService
            TokenService.mark_invalid(account, error.get('message', 'Access token is no longer valid.'))
        return False, data, error.get('message') or f"Meta HTTP {response.status_code}"

    @classmethod
    def get_profile(cls, account: PlatformAccount) -> Tuple[bool, dict, str]:
        return cls._graph('GET', account, account.instagram_id, params={
            'fields': 'username,name,profile_picture_url,followers_count,follows_count,media_count,biography'
        })

    @classmethod
    def list_media(cls, account: PlatformAccount, after: str = '', limit: int = 24) -> Tuple[bool, dict, str]:
        """One page of the account's Reels/Posts, newest first. data['paging']['cursors']['after'] gives the next page."""
        params = {
            'fields': 'id,caption,media_type,media_product_type,media_url,thumbnail_url,permalink,'
                      'timestamp,like_count,comments_count',
            'limit': limit,
        }
        if after:
            params['after'] = after
        return cls._graph('GET', account, f"{account.instagram_id}/media", params=params)

    @classmethod
    def send_private_reply(cls, account: PlatformAccount, comment_id: str, text: str) -> Tuple[bool, dict, str]:
        """Private Reply: DMs the commenter. Meta allows one per comment, within 7 days of it."""
        return cls._graph('POST', account, 'me/messages', json={
            'recipient': {'comment_id': comment_id},
            'message': {'text': text},
        })

    @classmethod
    def reply_to_comment(cls, account: PlatformAccount, comment_id: str, text: str) -> Tuple[bool, dict, str]:
        """Public reply shown under the comment."""
        return cls._graph('POST', account, f"{comment_id}/replies", data={'message': text})


# Standalone function shortcut matching requirements
send_instagram_message = InstagramService.send_instagram_message
