import logging
from django.db.models import Q
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView
from meta_connector.roles import HasRole
from meta_connector.models import Contact, Message
from meta_connector.serializers import ContactSerializer, MessageSerializer, SendMessageSerializer
from meta_connector.services.message_service import MessageService

logger = logging.getLogger(__name__)


class ContactListAPIView(ListAPIView):
    """
    GET /api/meta/contacts/
    Lists contacts with filtering by platform, search by name/phone/username, and ordering.
    """
    serializer_class = ContactSerializer
    permission_classes = [HasRole]

    def get_queryset(self):
        queryset = Contact.objects.select_related('platform_account').all()

        platform = self.request.query_params.get('platform')
        if platform:
            queryset = queryset.filter(platform_account__platform=platform.lower())

        is_blocked = self.request.query_params.get('is_blocked')
        if is_blocked is not None:
            queryset = queryset.filter(is_blocked=is_blocked.lower() in ['true', '1'])

        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(username__icontains=search)
                | Q(phone__icontains=search)
                | Q(email__icontains=search)
                | Q(platform_user_id__icontains=search)
            )

        return queryset.order_by('-last_message_at', '-first_seen')


class MessageListAPIView(ListAPIView):
    """
    GET /api/meta/messages/?platform=whatsapp&contact_id=1
    Paginated list of messages with filters for platform, contact, direction, status, and message type.
    """
    serializer_class = MessageSerializer
    permission_classes = [HasRole]

    def get_queryset(self):
        queryset = Message.objects.select_related('platform_account', 'contact').all()

        platform = self.request.query_params.get('platform')
        if platform:
            queryset = queryset.filter(platform_account__platform=platform.lower())

        contact_id = self.request.query_params.get('contact_id')
        if contact_id:
            queryset = queryset.filter(contact_id=contact_id)

        direction = self.request.query_params.get('direction')
        if direction:
            queryset = queryset.filter(direction=direction.lower())

        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status=status_param.lower())

        message_type = self.request.query_params.get('message_type')
        if message_type:
            queryset = queryset.filter(message_type=message_type.lower())

        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(message_text__icontains=search)
                | Q(contact__name__icontains=search)
                | Q(contact__phone__icontains=search)
            )

        return queryset.order_by('-created_at')


class SendMessageAPIView(APIView):
    """
    POST /api/meta/send/
    Sends a message to WhatsApp or Instagram via MessageService and saves record in SQLite.
    Payload:
    {
        "platform": "whatsapp" | "instagram",
        "to": "919876543210" | "instagram_user_id",
        "text": "Hello there!",
        "template_name": optional string,
        "account_id": optional int
    }
    """
    permission_classes = [HasRole]

    def post(self, request, *args, **kwargs):
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        platform = data['platform']
        to = data['to']
        text = data['text']
        template_name = data.get('template_name')
        account_id = data.get('account_id')

        # Direct synchronous dispatch
        message_obj, success, error_message = MessageService.send_message(
            platform=platform,
            to=to,
            text=text,
            template_name=template_name,
            account_id=account_id,
            sent_by=request.user,
        )

        response_serializer = MessageSerializer(message_obj)
        if success:
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)
        else:
            return Response(
                {
                    'error': error_message,
                    'message_details': response_serializer.data
                },
                status=status.HTTP_502_BAD_GATEWAY
            )
