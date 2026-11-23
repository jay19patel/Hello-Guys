"""Website content the assistants learn from (autodiscovered by hello_guy_ai; see hello_guy_ai/knowledge.py).

Only public, stable text goes in. Never add inquiries (visitor PII: vector search has no permissions) or
changing numbers like views/likes (the tools read those live). Saving or deleting any of these records
(or a project section) re-indexes it; a daily reconcile catches bulk QuerySet.update() edits.
"""

from hello_guy_ai import KnowledgeSource

from cms.models import FAQ, AboutBlock, BlogPost, Project, Solution, Testimonial, WorkflowStep

KB = "company-public"


def _lines(*parts: str) -> str:
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


class SolutionSource(KnowledgeSource):
    model = "cms.solution"
    knowledge_base = KB

    def get_queryset(self):
        return Solution.objects.filter(is_active=True)

    def get_title(self, obj):
        return obj.title

    def get_text(self, obj):
        return _lines(f"Service area: {obj.tag}", obj.description, "Technologies: " + ", ".join(obj.tech_stack or []))


class ProjectSource(KnowledgeSource):
    model = "cms.project"
    knowledge_base = KB
    related = {"cms.projectsection": "project_id"}  # editing a section re-indexes its project

    def get_queryset(self):
        return Project.objects.filter(is_active=True).prefetch_related("sections")

    def get_title(self, obj):
        return obj.title

    def get_url(self, obj):
        return obj.live_url

    def get_text(self, obj):
        sections = [_lines(s.title, s.content) for s in obj.sections.all()]
        return _lines(
            obj.subtitle,
            f"Category: {obj.category} | Client: {obj.client} | Status: {obj.status}",
            "Technologies: " + ", ".join(obj.technologies or []),
            obj.description,
            obj.overview and f"Overview: {obj.overview}",
            obj.challenge and f"Challenge: {obj.challenge}",
            obj.solution and f"Solution: {obj.solution}",
            *sections,
        )


class BlogPostSource(KnowledgeSource):
    model = "cms.blogpost"
    knowledge_base = KB

    def get_queryset(self):
        return BlogPost.objects.filter(is_published=True)

    def get_title(self, obj):
        return obj.title

    def get_text(self, obj):
        return _lines(f"Category: {obj.category}", obj.content)


class FAQSource(KnowledgeSource):
    model = "cms.faq"
    knowledge_base = KB

    def get_queryset(self):
        return FAQ.objects.filter(is_active=True)

    def get_title(self, obj):
        return obj.question[:255]

    def get_text(self, obj):
        return _lines(f"Q: {obj.question}", f"A: {obj.answer}")


class TestimonialSource(KnowledgeSource):
    model = "cms.testimonial"
    knowledge_base = KB

    def get_queryset(self):
        return Testimonial.objects.filter(is_active=True)

    def get_title(self, obj):
        return f"Testimonial from {obj.name}"

    def get_text(self, obj):
        return _lines(f"{obj.name}, {obj.role}:", obj.text)


class WorkflowStepSource(KnowledgeSource):
    model = "cms.workflowstep"
    knowledge_base = KB

    def get_queryset(self):
        return WorkflowStep.objects.filter(is_active=True)

    def get_title(self, obj):
        return f"How we work, step {obj.step_num}: {obj.title}"

    def get_text(self, obj):
        return _lines(obj.tag, obj.description)


class AboutProfileSource(KnowledgeSource):
    model = "cms.aboutprofile"
    knowledge_base = KB

    def get_title(self, obj):
        return "About the studio and founder"

    def get_text(self, obj):
        return _lines(
            obj.studio_title,
            obj.intro_text,
            f"Founder: {obj.founder_name}, {obj.founder_role}",
            obj.founder_bio,
            f"{obj.mission_title}: {obj.mission_text}",
            f"{obj.vision_title}: {obj.vision_text}",
        )


class AboutBlockSource(KnowledgeSource):
    model = "cms.aboutblock"
    knowledge_base = KB

    def get_queryset(self):
        return AboutBlock.objects.filter(is_active=True)

    def get_title(self, obj):
        return obj.title

    def get_text(self, obj):
        return _lines(obj.badge, obj.content)


class SiteSettingsSource(KnowledgeSource):
    model = "cms.sitesettings"
    knowledge_base = KB

    fields = ["brand_name", "tagline", "bio", "location", "phone_number", "github_url", "linkedin_url", "youtube_url", "twitter_url"]

    def get_title(self, obj):
        return f"{obj.brand_name}: contact and profile"
