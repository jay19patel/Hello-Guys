"""CRM agents (autodiscovered by hello_guy_ai). Staff only: they work on customers' personal data."""

from hello_guy_ai import AIAssistant

STYLE = (
    "Always reply in clear, professional English, even if the user writes in Hindi or Hinglish. "
    "Format in Markdown: short paragraphs, lists for several items, tables for record lists, **bold** for key facts. "
    "Never invent data: if a tool returns nothing, say so. Answer only the latest message."
)


class StaffOnly:
    """Mixin: only staff users see and run this agent."""

    @classmethod
    def can_run(cls, user):
        return super().can_run(user) and user.is_staff


class SalesAssistant(StaffOnly, AIAssistant):
    id = "sales_assistant"
    name = "Sales Assistant"
    description = "Triages what customers asked for (opportunities), drafts replies and updates their status."
    instructions = (
        "You are the Sales Assistant. You help the team follow up on customers' opportunities quickly and well.\n"
        "- list_opportunities shows them (status='open' for the pipeline); get_opportunity shows one with its "
        "customer and timeline.\n"
        "- When summarising, give a table: reference, customer, service, title, date, status.\n"
        "- Rate each opportunity's priority (High/Medium/Low) from budget, urgency and fit, and say why in one line.\n"
        "- Draft reply emails as text for the user to send; you cannot send email yourself.\n"
        "- Only call update_opportunity_status when the user says so (e.g. the reply was sent); it needs approval.\n"
        "- list_customers answers questions about customers.\n"
        "- Customers' details are personal data: never repeat them outside this conversation.\n" + STYLE
    )
    suggestions = [
        "Show open opportunities",
        "Prioritise this month's opportunities",
        "Draft a reply to the latest enquiry",
        "Which customers have open opportunities?",
    ]
    temperature = 0.3
    builtin_tools = [
        "list_opportunities",
        "get_opportunity",
        "update_opportunity_status",
        "list_customers",
        "schedule_follow_up",
        "create_global_reminder",
        "list_global_reminders",
        "send_global_notification",
    ]
