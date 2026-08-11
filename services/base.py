from abc import ABC, abstractmethod


class WebhookService(ABC):
    """Common interface every platform webhook service implements.

    `object_type` must match the value Meta sends in the webhook payload's
    top-level "object" field (e.g. "instagram", "whatsapp_business_account"),
    so main.py can dispatch to the right service without per-platform
    if/elif branching — adding a new platform is just instantiating its
    service and appending it to main.py's ACTIVE_SERVICES list.
    """

    object_type: str

    @abstractmethod
    async def handle_webhook(self, payload: dict) -> dict:
        ...
