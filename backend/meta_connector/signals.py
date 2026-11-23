"""Hooks other apps can listen to, so they can react to Meta traffic without meta_connector knowing them.

    from django.dispatch import receiver
    from meta_connector.signals import message_received

    @receiver(message_received)
    def on_dm(sender, message, **kwargs): ...   # message = the saved incoming Message
"""

from django.dispatch import Signal

message_received = Signal()  # kwargs: message (incoming WhatsApp / Instagram DM, already saved)
