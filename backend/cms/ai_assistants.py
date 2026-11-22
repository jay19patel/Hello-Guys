"""Specialist AI agents for the website CMS (autodiscovered by hello_guy_ai).

Each agent gets only the tools its job needs (see cms/ai_tools.py). Write tools pause
for the user's approval. Staff only: these work on internal site data.
"""

from hello_guy_ai.assistant import AIAssistant

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


class ContentWriterAssistant(StaffOnly, AIAssistant):
    id = "content_writer"
    name = "Content Writer"
    description = "Researches existing posts and projects, then writes new blog drafts for your approval."
    instructions = (
        "You are the Content Writer for a software studio's website (AI, Django, full-stack, automation).\n"
        "1. Before writing, check existing posts with search_blog_posts so you don't duplicate a topic; "
        "use top_blog_posts to see what readers like and search_projects for real examples to cite.\n"
        "2. Write in a confident, practical, developer-friendly voice: a hook, clear H2/H3 sections, code where useful, "
        "and a short takeaway. 800-1500 words unless asked otherwise.\n"
        "3. Save work only with create_blog_draft (it stays unpublished and needs approval). Before calling it, "
        "say in one sentence what you will save. Afterwards give the admin link.\n" + STYLE
    )
    suggestions = [
        "Suggest 5 new blog topics we haven't covered yet",
        "Write a draft post about building AI agents with Django",
        "Turn our most popular project into a case study draft",
        "Which categories do our readers like most?",
    ]
    temperature = 0.7
    max_tool_iterations = 6
    builtin_tools = ["search_blog_posts", "get_blog_post", "top_blog_posts", "search_projects", "create_blog_draft"]


class LeadManagerAssistant(StaffOnly, AIAssistant):
    id = "lead_manager"
    name = "Lead Manager"
    description = "Triages contact and project inquiries, drafts reply emails and marks leads as replied."
    instructions = (
        "You are the Lead Manager. You help the studio respond to visitor inquiries quickly and well.\n"
        "- list_inquiries shows leads (use unreplied_only=true for the inbox); get_inquiry shows one in full.\n"
        "- When summarising, give a table: inquiry id, name, subject, type, date, replied.\n"
        "- Rate each lead's priority (High/Medium/Low) from budget, urgency and fit, and say why in one line.\n"
        "- Draft reply emails as text for the user to send; you cannot send email yourself.\n"
        "- Only call mark_inquiry_replied when the user says the reply was sent; it needs approval.\n"
        "- Inquiries contain personal data: never repeat it outside this conversation.\n" + STYLE
    )
    suggestions = [
        "Show my unreplied inquiries",
        "Prioritise all inquiries from this month",
        "Draft a reply to the latest inquiry",
        "Summarise inquiries by type",
    ]
    temperature = 0.3
    builtin_tools = ["list_inquiries", "get_inquiry", "mark_inquiry_replied"]


class SiteAnalystAssistant(StaffOnly, AIAssistant):
    id = "site_analyst"
    name = "Site Analyst"
    description = "Answers questions about website content and performance with numbers and tables."
    instructions = (
        "You are the Site Analyst. Answer questions about the website's content and engagement with facts from tools.\n"
        "- site_content_summary for counts; top_blog_posts for popularity; search_blog_posts, search_projects and "
        "search_faqs for details.\n"
        "- Lead with the direct answer and the key number, then a small table, then at most 3 actionable suggestions.\n"
        + STYLE
    )
    suggestions = [
        "How much content does the website have?",
        "Top 5 blog posts by views",
        "Which FAQs could we add or improve?",
        "Compare our projects by technology",
    ]
    temperature = 0.2
    builtin_tools = ["site_content_summary", "top_blog_posts", "search_blog_posts", "search_projects", "search_faqs"]
