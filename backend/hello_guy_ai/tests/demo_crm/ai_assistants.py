"""Test-only assistant (autodiscovered because demo_crm is installed in test settings).

Library tests run against this, never against a host app's assistants, so they pass without `cms`.
"""

from hello_guy_ai import AIAssistant


class DemoAssistant(AIAssistant):
    id = "demo"
    name = "Demo"
    has_rag = True
    knowledge_bases = ["hello_guy"]  # the `kb` fixture's slug
    memory = True
    builtin_tools = [
        "get_current_datetime",
        "db_list_models",
        "db_describe",
        "db_read",
        "db_aggregate",
        "db_create",
        "db_update",
        "db_delete",
        "create_reminder",
        "list_reminders",
        "update_reminder",
        "cancel_reminder",
        "list_notifications",
    ]
