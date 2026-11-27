from rest_framework import serializers
from meta_connector.models import PlatformAccount, Contact, Message


class PlatformAccountSerializer(serializers.ModelSerializer):
    raw_access_token = serializers.CharField(
        write_only=True,
        required=False,
        help_text="Provide token in plain text; it will be encrypted before storage."
    )

    class Meta:
        model = PlatformAccount
        fields = [
            'id',
            'platform',
            'display_name',
            'phone_number_id',
            'instagram_id',
            'verify_token',
            'is_active',
            'raw_access_token',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def create(self, validated_data):
        raw_token = validated_data.pop('raw_access_token', None)
        account = super().create(validated_data)
        if raw_token:
            account.set_access_token(raw_token)
            account.save(update_fields=['access_token_encrypted'])
        return account

    def update(self, instance, validated_data):
        raw_token = validated_data.pop('raw_access_token', None)
        account = super().update(instance, validated_data)
        if raw_token:
            account.set_access_token(raw_token)
            account.save(update_fields=['access_token_encrypted'])
        return account


class ContactSerializer(serializers.ModelSerializer):
    platform = serializers.CharField(source='platform_account.platform', read_only=True)
    platform_name = serializers.CharField(source='platform_account.display_name', read_only=True)
    message_count = serializers.SerializerMethodField()

    class Meta:
        model = Contact
        fields = [
            'id',
            'platform_account',
            'platform',
            'platform_name',
            'platform_user_id',
            'name',
            'username',
            'phone',
            'email',
            'profile_pic_url',
            'raw_profile_data',
            'message_count',
            'first_seen',
            'last_message_at',
            'is_blocked',
        ]
        read_only_fields = ['id', 'first_seen', 'last_message_at']

    def get_message_count(self, obj) -> int:
        # ContactListAPIView annotates msg_count to avoid N+1 queries.
        return obj.msg_count if hasattr(obj, 'msg_count') else obj.messages.count()


class MessageSerializer(serializers.ModelSerializer):
    platform = serializers.CharField(source='platform_account.platform', read_only=True)
    contact_name = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            'id',
            'platform_account',
            'platform',
            'contact',
            'contact_name',
            'message_id',
            'direction',
            'message_type',
            'message_text',
            'media_id',
            'media_url',
            'media_mime_type',
            'caption',
            'latitude',
            'longitude',
            'raw_payload',
            'is_read',
            'is_replied',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_contact_name(self, obj) -> str:
        return obj.contact.name or obj.contact.username or obj.contact.phone or obj.contact.platform_user_id


class SendMessageSerializer(serializers.Serializer):
    platform = serializers.ChoiceField(
        choices=[PlatformAccount.PLATFORM_WHATSAPP, PlatformAccount.PLATFORM_INSTAGRAM, PlatformAccount.PLATFORM_FACEBOOK],
        help_text="Target platform ('whatsapp', 'instagram' or 'facebook')"
    )
    to = serializers.CharField(
        max_length=255,
        help_text="Recipient phone number (WhatsApp) or IGSID (Instagram)"
    )
    text = serializers.CharField(
        help_text="Text content to send"
    )
    template_name = serializers.CharField(
        required=False,
        allow_blank=True,
        default=None,
        help_text="Optional approved WhatsApp template name"
    )
    account_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        default=None,
        help_text="Optional PlatformAccount ID to send from"
    )
