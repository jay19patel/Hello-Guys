import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Optional, Tuple
import requests
from django.utils import timezone
from meta_connector.models import MetaConfig, PlatformAccount

logger = logging.getLogger(__name__)

STALE_AFTER = timedelta(hours=12)


def _get(path: str, **params) -> Tuple[Optional[dict], str]:
    """GET a Graph API path. Returns (json, '') or (None, error_message)."""
    try:
        response = requests.get(f"{MetaConfig.get_solo().graph_base_url}/{path}", params=params, timeout=10)
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Meta Graph request to %s failed: %s", path, exc)
        return None, f"Could not reach Meta: {exc}"
    if 'error' in data:
        return None, data['error'].get('message', 'Unknown Meta error')
    return data, ''


def _ts(value) -> Optional[datetime]:
    # Meta uses 0 for "never".
    return datetime.fromtimestamp(value, tz=dt_timezone.utc) if value else None


class TokenService:
    """Checks tokens with Meta's /debug_token and upgrades User tokens to permanent Page tokens."""

    @classmethod
    def inspect(cls, token: str) -> Tuple[Optional[dict], str]:
        """Meta's view of a token: {type, is_valid, expires_at, data_access_expires_at, scopes, app_id, ...}.
        An expired/revoked token can't inspect itself, so Meta answers with an error, which callers treat as invalid."""
        data, error = _get('debug_token', input_token=token, access_token=token)
        if data is None:
            return None, error
        return data.get('data', {}), ''

    @classmethod
    def prepare(cls, platform: str, instagram_id: str, raw_token: str) -> Tuple[str, Optional[dict], str, str]:
        """
        Turns whatever token the user pasted into the best one we can store.
        Returns (token_to_save, debug_info, note_for_user, error). error != '' means: don't save.
        """
        raw_token = raw_token.strip()
        info, error = cls.inspect(raw_token)
        if info is None:
            if error.startswith('Could not reach Meta'):
                return raw_token, None, "Saved, but Meta could not be reached to check the token.", ''
            return '', None, '', f"Meta rejected this token: {error}"
        if not info.get('is_valid'):
            return '', None, '', f"Meta says this token is not valid: {info.get('error', {}).get('message', 'expired or revoked')}"
        if info.get('type') != 'USER':
            return raw_token, info, '', ''  # PAGE / SYSTEM_USER: store as is

        notes = []
        token = raw_token
        app_secret = MetaConfig.get_solo().app_secret
        if app_secret and info.get('expires_at'):
            data, err = _get('oauth/access_token', grant_type='fb_exchange_token', client_id=info.get('app_id'),
                             client_secret=app_secret, fb_exchange_token=raw_token)
            if data and data.get('access_token'):
                token = data['access_token']
            else:
                notes.append(f"Could not extend the User token ({err}).")
        elif not app_secret:
            notes.append("Add the App Secret above so pasted User tokens become permanent automatically.")

        if platform == PlatformAccount.PLATFORM_INSTAGRAM:
            data, err = _get('me/accounts', fields='name,access_token,instagram_business_account', access_token=token, limit=100)
            if data is None:
                return '', None, '', f"Could not list your Pages with this User token: {err}"
            pages = data.get('data', [])
            page = next((p for p in pages if (p.get('instagram_business_account') or {}).get('id') == instagram_id), None)
            if not page:
                linked = ', '.join(f"{p.get('name')} → {(p.get('instagram_business_account') or {}).get('id', 'no Instagram')}" for p in pages) or 'none'
                return '', None, '', (f"This User token has no Page linked to Instagram ID {instagram_id}. "
                                      f"Pages it can see: {linked}. Tick the Page when generating the token.")
            token = page['access_token']
            notes.insert(0, f"Converted your User token into the Page token for \"{page.get('name')}\".")
        else:
            notes.append("WhatsApp needs a System User token to never expire (Business Settings → System users).")

        info, error = cls.inspect(token)
        return token, info, ' '.join(notes), ''

    @classmethod
    def apply(cls, account: PlatformAccount, info: Optional[dict], error: str = '') -> None:
        """Copies debug info (or an error) onto the account's token_* fields. Caller saves."""
        account.token_checked_at = timezone.now()  # also on network errors, so page loads don't retry every time
        if info is None:
            if not error.startswith('Could not reach Meta'):
                account.token_is_valid = False  # Meta answered: the token is dead
            account.token_error = error
            return
        account.token_type = info.get('type', '')
        account.token_is_valid = bool(info.get('is_valid'))
        account.token_expires_at = _ts(info.get('expires_at'))
        account.token_data_access_expires_at = _ts(info.get('data_access_expires_at'))
        account.token_scopes = info.get('scopes', [])
        account.token_error = '' if account.token_is_valid else info.get('error', {}).get('message', '')

    @classmethod
    def refresh(cls, account: PlatformAccount) -> None:
        """Re-checks the saved token with Meta and stores the result."""
        token = account.get_access_token()
        if not token:
            return
        info, error = cls.inspect(token)
        cls.apply(account, info, error)
        account.save(update_fields=[f.name for f in PlatformAccount._meta.fields if f.name.startswith('token_')])

    @classmethod
    def refresh_stale(cls) -> None:
        """Cheap to call on page loads: only re-checks tokens not checked in the last 12 hours."""
        cutoff = timezone.now() - STALE_AFTER
        for account in PlatformAccount.objects.filter(is_active=True).exclude(access_token_encrypted=''):
            if not account.token_checked_at or account.token_checked_at < cutoff:
                try:
                    cls.refresh(account)
                except Exception:  # a bad token/DB row must not 500 every dashboard page
                    logger.exception("Token refresh failed for account '%s'", account.display_name)

    @classmethod
    def mark_invalid(cls, account: PlatformAccount, message: str) -> None:
        """Called when a live API call fails with error 190, so the warning shows up right away."""
        account.token_is_valid = False
        account.token_error = message
        account.token_checked_at = timezone.now()
        account.save(update_fields=['token_is_valid', 'token_error', 'token_checked_at'])
        logger.warning("Access token for account '%s' is no longer valid: %s", account.display_name, message)

    @classmethod
    def alerts(cls):
        """Active accounts whose token needs attention."""
        return [a for a in PlatformAccount.objects.filter(is_active=True)
                if a.token_health in ('missing', 'invalid', 'expired', 'expiring') or a.missing_scopes]
