"""Fill the CMS with a hypothetical company, NJ Tech Studio (Frappe/ERPNext + AI services), for demos and
for testing the assistants' knowledge base. Safe to run again: records are updated, never duplicated.

    python manage.py seed_demo_company

Demo only: projects and testimonials are fictional and say so ("Demo client"); FAQs give no invented prices.
Founder name, contact email, phone and social links are left as they are.
Every saved record is synced into the "company-public" knowledge base by the worker (cms/ai_knowledge.py).
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from cms.models import (
    FAQ,
    AboutBlock,
    AboutProfile,
    BlogPost,
    Project,
    ProjectSection,
    SiteSettings,
    Solution,
    TechSkill,
    Testimonial,
    WorkflowStep,
)

DEMO = "Demo client (fictional)"

SOLUTIONS = [
    {
        "slug": "erpnext-implementation",
        "tag": "ERPNext & Frappe",
        "title": "ERPNext Implementation",
        "icon": "Layers",
        "description": "End-to-end ERPNext rollout for manufacturers, distributors and service companies: "
        "requirement mapping, chart of accounts, stock and manufacturing setup, data migration from Tally or "
        "Excel, user training and go-live support. We configure standard ERPNext first and customise only "
        "where the business truly differs.",
        "tech_stack": ["ERPNext", "Frappe", "MariaDB", "Redis"],
    },
    {
        "slug": "custom-frappe-apps",
        "tag": "Frappe Framework",
        "title": "Custom Frappe Apps",
        "icon": "Code2",
        "description": "Custom apps on the Frappe Framework: new DocTypes, workflows, approval chains, print "
        "formats, reports and REST APIs. Built as separate apps so ERPNext upgrades stay safe, with tests and "
        "patches for every schema change.",
        "tech_stack": ["Frappe", "Python", "JavaScript", "Vue"],
    },
    {
        "slug": "ai-assistants-for-erp",
        "tag": "AI + ERP",
        "title": "AI Assistants for ERPNext",
        "icon": "Sparkles",
        "description": "Chat assistants that answer questions on live ERP data (sales, stock, receivables) in "
        "plain English or Hinglish, draft documents like quotations and emails, and always ask for approval "
        "before changing a record. Works with hosted models or fully local models for data privacy.",
        "tech_stack": ["Python", "LiteLLM", "Mistral", "Ollama", "Frappe"],
    },
    {
        "slug": "ai-chatbots-rag",
        "tag": "AI Chatbots",
        "title": "Website & Support Chatbots (RAG)",
        "icon": "MessageSquare",
        "description": "Website and support chatbots grounded in your own documents, product catalogue and "
        "FAQs using retrieval-augmented generation (RAG), so answers come from your content instead of guesses. "
        "Includes lead capture, human hand-off and analytics on what customers ask.",
        "tech_stack": ["Django", "Milvus", "Mistral", "Redis"],
    },
    {
        "slug": "workflow-automation",
        "tag": "Automation",
        "title": "Business Workflow Automation",
        "icon": "Workflow",
        "description": "Automations that remove repetitive work: invoice reminders, WhatsApp and email "
        "notifications, document extraction from PDFs and scanned bills into ERPNext, scheduled reports and "
        "integrations with payment gateways, Shopify and government e-invoicing.",
        "tech_stack": ["Python", "Frappe", "Dramatiq", "WhatsApp Cloud API"],
    },
    {
        "slug": "erp-support-amc",
        "tag": "Support",
        "title": "ERPNext Support & AMC",
        "icon": "ShieldCheck",
        "description": "Annual maintenance for ERPNext and Frappe sites: version upgrades, backups and "
        "disaster recovery, performance tuning, security patches and a fixed-response support desk.",
        "tech_stack": ["Frappe Bench", "Nginx", "Ubuntu", "Docker"],
    },
]

PROJECTS = [
    {
        "slug": "textile-erpnext-rollout",
        "title": "ERPNext for a Textile Manufacturer",
        "subtitle": "Manufacturing, job work and GST in one system",
        "category": "ERP Implementation",
        "featured": True,
        "technologies": ["ERPNext", "Frappe", "MariaDB"],
        "description": "A mid-size textile unit moved from Tally and spreadsheets to ERPNext.",
        "overview": "Production planning, job-work tracking with outside processors, and GST-ready accounting.",
        "challenge": "Stock was reconciled monthly by hand and job-work material went missing between vendors.",
        "solution": "Work orders with BOMs per fabric type, subcontracting receipts and a daily stock dashboard.",
        "sections": [
            ("Results", "- Month-end closing from 10 days to 2\n- Job-work material fully traceable per vendor"),
            ("Scope", "Manufacturing, Stock, Buying, Selling, Accounts; data migration from Tally."),
        ],
    },
    {
        "slug": "erp-sales-assistant",
        "title": "AI Sales Assistant on ERPNext",
        "subtitle": "Ask your ERP questions in Hinglish",
        "category": "AI Integration",
        "featured": True,
        "technologies": ["Frappe", "LiteLLM", "Mistral", "Redis"],
        "description": "A chat assistant inside ERPNext for a distribution company's sales team.",
        "overview": "Answers 'aaj kitne orders pending hain?' style questions from live ERP data.",
        "challenge": "Sales managers waited on the accounts team for every outstanding or stock query.",
        "solution": "Read-only ERP tools with role permissions, approval before any write, and a daily summary.",
        "sections": [
            ("Results", "- Most routine queries answered without the accounts team\n- No data leaves role permissions"),
            ("How it works", "The assistant calls permission-checked tools; it never runs raw SQL."),
        ],
    },
    {
        "slug": "clinic-support-chatbot",
        "title": "Support Chatbot for a Clinic Chain",
        "subtitle": "RAG over policies, timings and services",
        "category": "AI Chatbot",
        "featured": False,
        "technologies": ["Django", "Milvus", "Mistral"],
        "description": "A website chatbot answering patient questions from the clinic's own documents.",
        "overview": "Timings, services, insurance and preparation instructions, with appointment lead capture.",
        "challenge": "Front-desk staff spent hours on repeated phone questions.",
        "solution": "RAG chatbot grounded in clinic documents that hands off to staff when unsure.",
        "sections": [("Results", "- Common questions answered 24x7\n- Leads go straight to the front desk")],
    },
    {
        "slug": "invoice-extraction-automation",
        "title": "Purchase Invoice Extraction into ERPNext",
        "subtitle": "From PDF and scanned bills to draft invoices",
        "category": "Automation",
        "featured": False,
        "technologies": ["Python", "Frappe", "Mistral", "Dramatiq"],
        "description": "Vendor bills arriving by email are read by AI and created as draft purchase invoices.",
        "overview": "Extraction of supplier, GSTIN, items and taxes, matched against purchase orders.",
        "challenge": "Hundreds of bills a month were typed in manually with frequent errors.",
        "solution": "AI extraction with validation rules; every invoice stays a draft until an accountant approves.",
        "sections": [("Results", "- Data entry time cut sharply\n- Accountant approval kept for every bill")],
    },
]

WORKFLOW = [
    ("01", "Discovery & Process Mapping", "Discovery", "We study how your business runs today, map every process "
     "to standard ERPNext first, and list the real gaps. You get a written scope before any build starts.",
     "Search"),
    ("02", "Solution Design", "Design", "Data model, roles and permissions, workflows, integrations and AI use "
     "cases are designed and signed off with your team.", "PenTool"),
    ("03", "Build & Configure", "Build", "Configuration and custom apps are built in short sprints with a demo "
     "every week, on a staging site you can use.", "Code2"),
    ("04", "Data Migration & Training", "Go-live prep", "Masters and opening balances are migrated and "
     "verified; every role gets hands-on training.", "Database"),
    ("05", "Go-live & Support", "Launch", "Go-live with on-call support, followed by an AMC for upgrades, "
     "backups and improvements.", "Rocket"),
]

FAQS = [
    ("What does NJ Tech Studio do?", "We implement and customise ERPNext / Frappe and build AI solutions on "
     "top of it: chat assistants on your business data, RAG chatbots, document extraction and workflow "
     "automation.", "General"),
    ("Do you only work with ERPNext?", "ERPNext and the Frappe Framework are our core. Our AI chatbots and "
     "automations also work standalone with Django websites and other systems through APIs.", "General"),
    ("How long does an ERPNext implementation take?", "Small companies usually go live in 6 to 10 weeks; "
     "manufacturing setups with migration take 3 to 5 months. The exact plan comes out of the discovery "
     "phase.", "ERPNext"),
    ("Can you migrate our data from Tally or Excel?", "Yes. We migrate masters (customers, suppliers, items), "
     "opening balances and open documents, and reconcile them with you before go-live.", "ERPNext"),
    ("Will customisations break when ERPNext is upgraded?", "No. We build changes as separate Frappe apps "
     "with patches and tests, never by editing ERPNext core, so upgrades stay safe.", "ERPNext"),
    ("Is our data safe with AI?", "The assistant only uses permission-checked tools, never raw SQL, and asks "
     "for approval before changing any record. For sensitive data we can run fully local models so nothing "
     "leaves your server.", "AI"),
    ("Which AI models do you use?", "We are model-agnostic: hosted models like Mistral or OpenAI, or local "
     "models through Ollama, chosen by cost, speed and privacy needs.", "AI"),
    ("Can the chatbot answer in Hindi or Hinglish?", "Yes. Assistants understand Hindi, Hinglish and English "
     "and can reply in the language your team or customers prefer.", "AI"),
    ("How is pricing decided?", "Every project starts with a short paid discovery. After it you get a fixed "
     "quote for the agreed scope, or a monthly retainer for ongoing work. Our team shares exact numbers.",
     "Pricing"),
    ("Do you provide support after go-live?", "Yes, through an annual maintenance contract covering upgrades, "
     "backups, monitoring and a support desk with agreed response times.", "Support"),
]

BLOGS = [
    {
        "slug": "erpnext-implementation-checklist",
        "title": "ERPNext Implementation Checklist for Indian SMEs",
        "category": "Guide",
        "content": "## Before you start\nMap your processes to standard ERPNext before asking for "
        "customisation. Most companies need far less custom code than they expect.\n\n## Key steps\n"
        "1. Discovery and gap list\n2. Chart of accounts and GST setup\n3. Item, customer and supplier "
        "masters\n4. Opening stock and balances\n5. Role-wise training\n6. Parallel run for one month\n\n"
        "## Common mistakes\nSkipping data cleaning, customising core files, and going live without a "
        "parallel run.",
    },
    {
        "slug": "ai-assistant-on-erp-data",
        "title": "Putting an AI Assistant on Your ERP Data, Safely",
        "category": "AI",
        "content": "An AI assistant on ERP data is useful only if it is safe. Our rules:\n\n"
        "- The model never writes SQL; it calls tools that check Django or Frappe permissions.\n"
        "- Every change (create, update) waits for a human to approve it.\n"
        "- Data from documents and records is treated as untrusted text, never as instructions.\n"
        "- Sensitive companies can run local models through Ollama.\n\n"
        "With these rules a sales manager can ask 'kal ke pending orders dikhao' and get a correct, "
        "permission-scoped answer in seconds.",
    },
    {
        "slug": "rag-chatbot-explained",
        "title": "RAG Chatbots Explained for Business Owners",
        "category": "AI",
        "content": "RAG (retrieval-augmented generation) means the chatbot first searches your own content "
        "(website pages, FAQs, policies), then answers from what it found. This keeps answers accurate and "
        "lets you update the bot by editing your website instead of retraining a model.\n\n"
        "Good RAG needs clean content, sensible chunking, and a similarity threshold so unrelated questions "
        "do not pull in random text.",
    },
]

SKILLS = {
    "backend": ["Frappe Framework", "ERPNext", "Python", "Django", "REST APIs"],
    "frontend": ["JavaScript", "Vue.js", "Frappe UI", "Jinja"],
    "database": ["MariaDB", "PostgreSQL", "Redis", "Milvus"],
    "ai_ml": ["LLM Integration (LiteLLM)", "Mistral", "Ollama (local LLMs)", "RAG", "Document AI"],
    "cloud": ["Frappe Cloud", "AWS", "Docker", "Nginx"],
    "tools": ["Frappe Bench", "Git", "Dramatiq", "WhatsApp Cloud API"],
}

TESTIMONIALS = [
    (f"{DEMO}: textile manufacturer", "Director", "Month-end closing went from ten days to two after the "
     "ERPNext rollout, and job-work stock finally matches reality."),
    (f"{DEMO}: distribution company", "Sales Head", "My team asks the ERP assistant about pending orders in "
     "Hinglish and gets answers instantly, without calling accounts."),
    (f"{DEMO}: clinic chain", "Operations Manager", "The website chatbot handles routine patient questions "
     "day and night and passes real leads to our front desk."),
]

ABOUT_BLOCKS = [
    ("Why Frappe", "Values", "ERPNext and Frappe are open source, so clients own their system with no "
     "per-user licence fees, and every customisation stays upgrade-safe."),
    ("AI with guardrails", "Approach", "We add AI where it saves real time, with permission checks, human "
     "approval for changes and the option to keep data on your own servers."),
    ("Small, senior team", "Culture", "You work directly with the engineers who build your system: weekly "
     "demos, written scope and no hand-offs to juniors."),
]


class Command(BaseCommand):
    help = "Fill the CMS with the hypothetical NJ Tech Studio (Frappe/ERPNext + AI) for demos"

    @transaction.atomic
    def handle(self, *args, **options):
        site = SiteSettings.get_solo()
        site.brand_name = "NJ Tech Studio"
        site.tagline = "ERPNext, Frappe & AI Solutions"
        site.bio = (
            "NJ Tech Studio implements ERPNext and builds custom Frappe apps and AI solutions: chat assistants "
            "on business data, RAG chatbots and workflow automation for growing companies."
        )
        site.location = "Ahmedabad, Gujarat, India"
        site.meta_title = "NJ Tech Studio | ERPNext, Frappe & AI Solutions"
        site.meta_description = "ERPNext implementation, custom Frappe apps and AI assistants for businesses."
        site.save()

        about = AboutProfile.get_solo()
        about.studio_badge = "NJ Tech Studio"
        about.studio_title = "ERPNext, Frappe & AI, built properly"
        about.intro_text = (
            "We are a Frappe-first studio: we implement ERPNext, build custom Frappe apps, and add AI where "
            "it removes real work, always with permissions and human approval in place."
        )
        about.mission_text = "Give growing companies one reliable system for their operations, made smarter with AI."
        about.vision_text = "Every business decision answered from its own data, in its own language."
        about.save()

        for i, (title, badge, content) in enumerate(ABOUT_BLOCKS):
            AboutBlock.objects.update_or_create(
                title=title, defaults={"badge": badge, "content": content, "order": i, "is_active": True}
            )
        for i, item in enumerate(SOLUTIONS, start=1):
            Solution.objects.update_or_create(
                slug=item["slug"], defaults={**item, "num": f"{i:02d}", "order": i, "is_active": True}
            )
        for i, item in enumerate(PROJECTS):
            data = {k: v for k, v in item.items() if k != "sections"}
            project, _ = Project.objects.update_or_create(
                slug=item["slug"], defaults={**data, "client": DEMO, "status": "Demo", "order": i, "is_active": True}
            )
            project.sections.all().delete()
            for j, (title, content) in enumerate(item["sections"]):
                ProjectSection.objects.create(project=project, title=title, content=content, order=j)
        for num, title, tag, description, icon in WORKFLOW:
            WorkflowStep.objects.update_or_create(
                step_num=num,
                defaults={
                    "title": title,
                    "tag": tag,
                    "description": description,
                    "icon": icon,
                    "phase_milestone": f"Phase {num}",
                    "order": int(num),
                    "is_active": True,
                },
            )
        for i, (question, answer, category) in enumerate(FAQS):
            FAQ.objects.update_or_create(
                question=question, defaults={"answer": answer, "category": category, "order": i, "is_active": True}
            )
        for post in BLOGS:
            BlogPost.objects.update_or_create(slug=post["slug"], defaults={**post, "is_published": True})
        for category, names in SKILLS.items():
            for order, name in enumerate(names):
                TechSkill.objects.update_or_create(
                    name=name, defaults={"category": category, "proficiency_order": order, "is_active": True}
                )
        for i, (name, role, text) in enumerate(TESTIMONIALS):
            Testimonial.objects.update_or_create(
                name=name, defaults={"role": role, "text": text, "order": i, "is_active": True}
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"NJ Tech Studio demo data ready: {len(SOLUTIONS)} services, {len(PROJECTS)} projects, "
                f"{len(FAQS)} FAQs, {len(BLOGS)} articles, {sum(map(len, SKILLS.values()))} skills, "
                f"{len(WORKFLOW)} process steps, {len(TESTIMONIALS)} sample testimonials. "
                "The worker now indexes them into the 'company-public' knowledge base."
            )
        )
