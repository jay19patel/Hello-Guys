import json
from typing import Any
from django import forms
from django.utils.text import slugify
from cms.models import (
    InquirySubmission,
    Project,
    Solution,
    BlogPost,
    SiteSettings,
    Testimonial,
    FAQ,
    TechSkill,
)


BASE_INPUT_ATTRS = {
    'class': 'w-full h-10 px-3 rounded-lg border border-line bg-surface text-ink text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors'
}
BASE_TEXTAREA_ATTRS = {
    'class': 'w-full p-3 rounded-lg border border-line bg-surface text-ink text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors min-h-[100px]',
    'rows': 4,
}
BASE_SELECT_ATTRS = {
    'class': 'w-full h-10 px-3 rounded-lg border border-line bg-surface text-ink text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-colors'
}
BASE_CHECKBOX_ATTRS = {
    'class': 'w-4 h-4 rounded border-line text-accent accent-accent focus:ring-accent'
}


class InquiryForm(forms.ModelForm):
    """
    Form for creating and editing lead inquiries / contacts.
    """
    class Meta:
        model = InquirySubmission
        fields = [
            'name',
            'email',
            'phone',
            'subject',
            'inquiry_type',
            'message',
            'is_replied',
        ]
        widgets = {
            'name': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'e.g. Jane Doe'}),
            'email': forms.EmailInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'jane@example.com'}),
            'phone': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': '+1 (555) 012-3456'}),
            'subject': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'Project Collaboration / Inquiry'}),
            'inquiry_type': forms.Select(
                choices=[
                    ('Project Inquiry Modal', 'Project Inquiry Modal'),
                    ('Contact Form', 'Contact Form'),
                    ('Quick Consultation', 'Quick Consultation'),
                    ('Direct Lead', 'Direct Lead'),
                ],
                attrs=BASE_SELECT_ATTRS,
            ),
            'message': forms.Textarea(attrs={**BASE_TEXTAREA_ATTRS, 'placeholder': 'Detailed project inquiry or message notes...'}),
            'is_replied': forms.CheckboxInput(attrs=BASE_CHECKBOX_ATTRS),
        }


class ProjectForm(forms.ModelForm):
    """
    Form for adding and editing portfolio projects & case studies.
    """
    technologies_input = forms.CharField(
        required=False,
        label='Technologies (comma-separated)',
        help_text='e.g. Next.js, Django REST, PostgreSQL, TailwindCSS',
        widget=forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'Next.js, Python, PostgreSQL'}),
    )

    class Meta:
        model = Project
        fields = [
            'title',
            'slug',
            'category',
            'subtitle',
            'description',
            'featured',
            'image',
            'live_url',
            'github_url',
            'status',
            'client',
            'overview',
            'challenge',
            'solution',
            'order',
            'is_active',
        ]
        widgets = {
            'title': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'e.g. Trade Buddy Broker'}),
            'slug': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'e.g. trade-buddy-broker (auto-generated if empty)'}),
            'category': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'Web Application / AI Automation'}),
            'subtitle': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'Short one-line subtitle'}),
            'description': forms.Textarea(attrs={**BASE_TEXTAREA_ATTRS, 'placeholder': 'Project summary for listing cards...'}),
            'featured': forms.CheckboxInput(attrs=BASE_CHECKBOX_ATTRS),
            'image': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': '/projects/cover.png or image URL'}),
            'live_url': forms.URLInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'https://example.com'}),
            'github_url': forms.URLInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'https://github.com/...'}),
            'status': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'Live / Beta / Completed'}),
            'client': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'Client name or Personal'}),
            'overview': forms.Textarea(attrs={**BASE_TEXTAREA_ATTRS, 'placeholder': 'In-depth overview...'}),
            'challenge': forms.Textarea(attrs={**BASE_TEXTAREA_ATTRS, 'placeholder': 'The challenge faced...'}),
            'solution': forms.Textarea(attrs={**BASE_TEXTAREA_ATTRS, 'placeholder': 'The solution delivered...'}),
            'order': forms.NumberInput(attrs={**BASE_INPUT_ATTRS, 'min': 0}),
            'is_active': forms.CheckboxInput(attrs=BASE_CHECKBOX_ATTRS),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.technologies:
            if isinstance(self.instance.technologies, list):
                self.fields['technologies_input'].initial = ', '.join(self.instance.technologies)

    def clean_slug(self) -> str:
        slug = self.cleaned_data.get('slug')
        title = self.cleaned_data.get('title')
        if not slug and title:
            slug = slugify(title)
        return slug or ''

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        tech_str = cleaned_data.get('technologies_input', '')
        if tech_str:
            tech_list = [t.strip() for t in tech_str.split(',') if t.strip()]
            self.instance.technologies = tech_list
        else:
            self.instance.technologies = []
        return cleaned_data


class SolutionForm(forms.ModelForm):
    """
    Form for featured solutions / architectural highlights.
    """
    tech_stack_input = forms.CharField(
        required=False,
        label='Tech Stack (comma-separated)',
        widget=forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'Python, Celery, Redis'}),
    )

    class Meta:
        model = Solution
        fields = [
            'num',
            'tag',
            'title',
            'slug',
            'description',
            'icon',
            'badge',
            'action_text',
            'image',
            'order',
            'is_active',
        ]
        widgets = {
            'num': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': '01'}),
            'tag': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'Fintech & Algorithmic Trading'}),
            'title': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'High-Frequency Execution Engine'}),
            'slug': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'execution-engine'}),
            'description': forms.Textarea(attrs={**BASE_TEXTAREA_ATTRS, 'placeholder': 'Architecture breakdown...'}),
            'icon': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'Rocket / Layers / Shield'}),
            'badge': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'Production Live'}),
            'action_text': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'Explore Architecture'}),
            'image': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': '/solutions/preview.png'}),
            'order': forms.NumberInput(attrs={**BASE_INPUT_ATTRS, 'min': 0}),
            'is_active': forms.CheckboxInput(attrs=BASE_CHECKBOX_ATTRS),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.tech_stack:
            if isinstance(self.instance.tech_stack, list):
                self.fields['tech_stack_input'].initial = ', '.join(self.instance.tech_stack)

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        stack_str = cleaned_data.get('tech_stack_input', '')
        if stack_str:
            self.instance.tech_stack = [s.strip() for s in stack_str.split(',') if s.strip()]
        else:
            self.instance.tech_stack = []
        return cleaned_data


class BlogPostForm(forms.ModelForm):
    """
    Form for adding and editing blog posts.
    """
    class Meta:
        model = BlogPost
        fields = [
            'title',
            'slug',
            'category',
            'cover_image',
            'link',
            'link_text',
            'content',
            'is_published',
        ]
        widgets = {
            'title': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'Article Title'}),
            'slug': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'article-slug (auto-generated if empty)'}),
            'category': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'Tutorial / Case Study / AI'}),
            'cover_image': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': '/blog/cover.jpg or image URL'}),
            'link': forms.URLInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'https://example.com/demo'}),
            'link_text': forms.TextInput(attrs={**BASE_INPUT_ATTRS, 'placeholder': 'Read More'}),
            'content': forms.Textarea(attrs={**BASE_TEXTAREA_ATTRS, 'rows': 12, 'placeholder': 'Write content in Markdown...'}),
            'is_published': forms.CheckboxInput(attrs=BASE_CHECKBOX_ATTRS),
        }

    def clean_slug(self) -> str:
        slug = self.cleaned_data.get('slug')
        title = self.cleaned_data.get('title')
        if not slug and title:
            slug = slugify(title)
        return slug or ''


class SiteSettingsForm(forms.ModelForm):
    """
    Form for editing global site settings and identity.
    """
    class Meta:
        model = SiteSettings
        fields = [
            'brand_name',
            'tagline',
            'bio',
            'avatar_url',
            'contact_email',
            'phone_number',
            'location',
            'github_url',
            'linkedin_url',
            'youtube_url',
            'twitter_url',
            'resume_url',
            'audio_track_url',
            'meta_title',
            'meta_description',
            # Maintenance & Offline CMS
            'maintenance_mode',
            'maintenance_title',
            'maintenance_message',
            'maintenance_estimated_uptime',
            # API Switches
            'api_projects_enabled',
            'api_blog_enabled',
            'api_testimonials_enabled',
            'api_faqs_enabled',
            'api_videos_enabled',
            'api_skills_enabled',
            'api_workflow_enabled',
            'api_about_enabled',
            'api_inquiries_enabled',
        ]
        widgets = {
            'brand_name': forms.TextInput(attrs=BASE_INPUT_ATTRS),
            'tagline': forms.TextInput(attrs=BASE_INPUT_ATTRS),
            'bio': forms.Textarea(attrs={**BASE_TEXTAREA_ATTRS, 'rows': 3}),
            'avatar_url': forms.TextInput(attrs=BASE_INPUT_ATTRS),
            'contact_email': forms.EmailInput(attrs=BASE_INPUT_ATTRS),
            'phone_number': forms.TextInput(attrs=BASE_INPUT_ATTRS),
            'location': forms.TextInput(attrs=BASE_INPUT_ATTRS),
            'github_url': forms.URLInput(attrs=BASE_INPUT_ATTRS),
            'linkedin_url': forms.URLInput(attrs=BASE_INPUT_ATTRS),
            'youtube_url': forms.URLInput(attrs=BASE_INPUT_ATTRS),
            'twitter_url': forms.URLInput(attrs=BASE_INPUT_ATTRS),
            'resume_url': forms.TextInput(attrs=BASE_INPUT_ATTRS),
            'audio_track_url': forms.TextInput(attrs=BASE_INPUT_ATTRS),
            'meta_title': forms.TextInput(attrs=BASE_INPUT_ATTRS),
            'meta_description': forms.Textarea(attrs={**BASE_TEXTAREA_ATTRS, 'rows': 3}),
            # Maintenance widgets
            'maintenance_mode': forms.CheckboxInput(attrs={'class': 'w-4 h-4 rounded text-amber-500 focus:ring-amber-500/20 bg-line border-line'}),
            'maintenance_title': forms.TextInput(attrs=BASE_INPUT_ATTRS),
            'maintenance_message': forms.Textarea(attrs={**BASE_TEXTAREA_ATTRS, 'rows': 3}),
            'maintenance_estimated_uptime': forms.TextInput(attrs=BASE_INPUT_ATTRS),
            # API switches widgets
            'api_projects_enabled': forms.CheckboxInput(attrs={'class': 'w-4 h-4 rounded text-accent focus:ring-accent/20 bg-line border-line'}),
            'api_blog_enabled': forms.CheckboxInput(attrs={'class': 'w-4 h-4 rounded text-accent focus:ring-accent/20 bg-line border-line'}),
            'api_testimonials_enabled': forms.CheckboxInput(attrs={'class': 'w-4 h-4 rounded text-accent focus:ring-accent/20 bg-line border-line'}),
            'api_faqs_enabled': forms.CheckboxInput(attrs={'class': 'w-4 h-4 rounded text-accent focus:ring-accent/20 bg-line border-line'}),
            'api_videos_enabled': forms.CheckboxInput(attrs={'class': 'w-4 h-4 rounded text-accent focus:ring-accent/20 bg-line border-line'}),
            'api_skills_enabled': forms.CheckboxInput(attrs={'class': 'w-4 h-4 rounded text-accent focus:ring-accent/20 bg-line border-line'}),
            'api_workflow_enabled': forms.CheckboxInput(attrs={'class': 'w-4 h-4 rounded text-accent focus:ring-accent/20 bg-line border-line'}),
            'api_about_enabled': forms.CheckboxInput(attrs={'class': 'w-4 h-4 rounded text-accent focus:ring-accent/20 bg-line border-line'}),
            'api_inquiries_enabled': forms.CheckboxInput(attrs={'class': 'w-4 h-4 rounded text-accent focus:ring-accent/20 bg-line border-line'}),
        }


class TestimonialForm(forms.ModelForm):
    class Meta:
        model = Testimonial
        fields = ['name', 'role', 'text', 'avatar', 'rating', 'order', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs=BASE_INPUT_ATTRS),
            'role': forms.TextInput(attrs=BASE_INPUT_ATTRS),
            'text': forms.Textarea(attrs=BASE_TEXTAREA_ATTRS),
            'avatar': forms.TextInput(attrs=BASE_INPUT_ATTRS),
            'rating': forms.NumberInput(attrs={**BASE_INPUT_ATTRS, 'min': 1, 'max': 5}),
            'order': forms.NumberInput(attrs={**BASE_INPUT_ATTRS, 'min': 0}),
            'is_active': forms.CheckboxInput(attrs=BASE_CHECKBOX_ATTRS),
        }


class FAQForm(forms.ModelForm):
    class Meta:
        model = FAQ
        fields = ['question', 'answer', 'category', 'order', 'is_active']
        widgets = {
            'question': forms.TextInput(attrs=BASE_INPUT_ATTRS),
            'answer': forms.Textarea(attrs=BASE_TEXTAREA_ATTRS),
            'category': forms.TextInput(attrs=BASE_INPUT_ATTRS),
            'order': forms.NumberInput(attrs={**BASE_INPUT_ATTRS, 'min': 0}),
            'is_active': forms.CheckboxInput(attrs=BASE_CHECKBOX_ATTRS),
        }


class TechSkillForm(forms.ModelForm):
    class Meta:
        model = TechSkill
        fields = ['name', 'category', 'icon_url', 'proficiency_order', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs=BASE_INPUT_ATTRS),
            'category': forms.Select(choices=TechSkill.CATEGORY_CHOICES, attrs=BASE_SELECT_ATTRS),
            'icon_url': forms.TextInput(attrs=BASE_INPUT_ATTRS),
            'proficiency_order': forms.NumberInput(attrs={**BASE_INPUT_ATTRS, 'min': 0}),
            'is_active': forms.CheckboxInput(attrs=BASE_CHECKBOX_ATTRS),
        }

