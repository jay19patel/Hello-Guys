"""Example code assistant. Any installed app can add its own ai_assistants.py.

Admin-configured agents (Hello Guy AI > Agents) need no code at all.
"""

from django.utils import timezone

from hello_guy_ai.assistant import AIAssistant, tool


class HelloGuyAssistant(AIAssistant):
    id = "hello_guy"
    name = "Hello Guy"
    description = "General assistant for your website: content, projects, inquiries and data changes."
    instructions = (
        "You are Hello Guy, a friendly and precise assistant. Always reply in clear, professional English, "
        "even if the user writes in Hindi or Hinglish. Be concise. Format answers in Markdown: short "
        "paragraphs, bullet or numbered lists for several items, tables for record lists, **bold** for key facts.\n"
        "WEBSITE DATA: answer questions about the site with the direct tools: site_content_summary for counts, "
        "search_blog_posts / get_blog_post, search_projects, search_faqs, list_inquiries. One call is usually "
        "enough; never guess data, and if a tool returns nothing, say so plainly.\n"
        "CHANGES: only to create/update/delete records, use db_list_models and db_describe, then the db_ write "
        "tools. They pause for the user's approval; before calling one, say in one sentence what you will do.\n"
        "Answer only the latest message; earlier turns are context, not questions to re-answer.\n"
        "MEMORY: save stable facts the user teaches you (preferences, mappings, rules) with update_memory."
    )
    suggestions = [
        "Give me an overview of all website content",
        "Which blog posts are the most popular?",
        "Show unreplied inquiries",
        "List our featured projects",
    ]
    has_rag = True  # retrieves from the knowledge base with slug "hello_guy" every turn
    memory = True
    builtin_tools = [
        # cms/ai_tools.py: one direct call per question
        "site_content_summary",
        "search_blog_posts",
        "get_blog_post",
        "top_blog_posts",
        "search_projects",
        "search_faqs",
        "list_inquiries",
        "get_inquiry",
        # generic ORM tools, for changes (allowlist: HELLO_GUY_AI["ORM_TOOLS_MODELS"])
        "db_list_models",
        "db_describe",
        "db_read",
        "db_create",
        "db_update",
        "db_delete",
    ]

    @tool
    def get_current_datetime(self) -> str:
        """Get the current server date and time (ISO 8601, with timezone)."""
        return timezone.localtime().isoformat()

    @tool
    def get_my_profile(self) -> dict:
        """Get the current user's username, full name and email."""
        return {
            "username": self.user.get_username(),
            "full_name": self.user.get_full_name(),
            "email": self.user.email,
        }
