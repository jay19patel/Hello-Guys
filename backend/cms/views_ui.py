import logging
from datetime import datetime, timezone
from typing import Any
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import (
    TemplateView,
    ListView,
    CreateView,
    UpdateView,
    DeleteView,
    DetailView,
)

from cms.models import (
    InquirySubmission,
    Project,
    Solution,
    BlogPost,
    BlogComment,
    SiteSettings,
    Testimonial,
    FAQ,
    TechSkill,
)
from cms.forms import (
    InquiryForm,
    ProjectForm,
    SolutionForm,
    BlogPostForm,
    SiteSettingsForm,
    TestimonialForm,
    FAQForm,
    TechSkillForm,
)

logger = logging.getLogger(__name__)


# ==============================================================================
# Overview Dashboard
# ==============================================================================

class CMSOverviewView(LoginRequiredMixin, TemplateView):
    """
    CMS studio home overview displaying KPI metrics, quick shortcuts,
    and recent lead inquiries & published projects.
    """
    template_name = 'cms/overview.html'

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        
        # Inquiries stats
        total_inquiries = InquirySubmission.objects.count()
        unreplied_inquiries = InquirySubmission.objects.filter(is_replied=False).count()
        replied_inquiries = total_inquiries - unreplied_inquiries
        
        # Projects & Content stats
        total_projects = Project.objects.count()
        featured_projects = Project.objects.filter(featured=True, is_active=True).count()
        total_solutions = Solution.objects.count()
        total_blogs = BlogPost.objects.count()
        published_blogs = BlogPost.objects.filter(is_published=True).count()
        
        context.update({
            'total_inquiries': total_inquiries,
            'unreplied_inquiries': unreplied_inquiries,
            'replied_inquiries': replied_inquiries,
            'total_projects': total_projects,
            'featured_projects': featured_projects,
            'total_solutions': total_solutions,
            'total_blogs': total_blogs,
            'published_blogs': published_blogs,
            'total_testimonials': Testimonial.objects.count(),
            'total_faqs': FAQ.objects.count(),
            'total_skills': TechSkill.objects.count(),
            'recent_inquiries': InquirySubmission.objects.order_by('-created_at')[:6],
            'recent_projects': Project.objects.order_by('order', '-created_at')[:4],
            'recent_blogs': BlogPost.objects.order_by('-created_at')[:4],
            'site_settings': SiteSettings.get_solo(),
        })
        return context


# ==============================================================================
# Inquiries / Contacts Management
# ==============================================================================

class InquiryListView(LoginRequiredMixin, ListView):
    """
    Searchable and filterable table of lead submissions / contacts.
    """
    model = InquirySubmission
    template_name = 'cms/inquiries/list.html'
    context_object_name = 'inquiries'
    paginate_by = 15

    def get_queryset(self):
        qs = InquirySubmission.objects.all().order_by('-created_at')
        
        status_filter = self.request.GET.get('status', 'all')
        if status_filter == 'unreplied':
            qs = qs.filter(is_replied=False)
        elif status_filter == 'replied':
            qs = qs.filter(is_replied=True)
            
        search_query = self.request.GET.get('q', '').strip()
        if search_query:
            qs = qs.filter(
                Q(name__icontains=search_query) |
                Q(email__icontains=search_query) |
                Q(phone__icontains=search_query) |
                Q(subject__icontains=search_query) |
                Q(message__icontains=search_query)
            )
            
        inquiry_type = self.request.GET.get('type', '').strip()
        if inquiry_type:
            qs = qs.filter(inquiry_type=inquiry_type)
            
        return qs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        base_qs = InquirySubmission.objects.all()
        context['all_count'] = base_qs.count()
        context['unreplied_count'] = base_qs.filter(is_replied=False).count()
        context['replied_count'] = base_qs.filter(is_replied=True).count()
        context['current_status'] = self.request.GET.get('status', 'all')
        context['search_query'] = self.request.GET.get('q', '').strip()
        context['selected_type'] = self.request.GET.get('type', '').strip()
        return context


class InquiryCreateView(LoginRequiredMixin, CreateView):
    """
    Create a new contact lead / inquiry manually.
    """
    model = InquirySubmission
    form_class = InquiryForm
    template_name = 'cms/inquiries/form.html'
    success_url = reverse_lazy('cms_ui:inquiry-list')

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context['is_new'] = True
        context['page_title'] = 'Add New Contact / Lead'
        return context

    def form_valid(self, form: InquiryForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(
            self.request,
            f'Contact inquiry for "{self.object.name}" was successfully added.'
        )
        return response


class InquiryDetailView(LoginRequiredMixin, DetailView):
    """
    View inquiry details, conversation log, and add follow-up notes.
    """
    model = InquirySubmission
    template_name = 'cms/inquiries/detail.html'
    context_object_name = 'inquiry'

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        inquiry: InquirySubmission = self.get_object()
        action = request.POST.get('action')

        if action == 'add_note':
            note_text = request.POST.get('note', '').strip()
            if note_text:
                replies_log = list(inquiry.replies or [])
                replies_log.append({
                    'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
                    'author': request.user.get_full_name() or request.user.username,
                    'note': note_text,
                })
                inquiry.replies = replies_log
                inquiry.is_replied = True
                inquiry.save(update_fields=['replies', 'is_replied', 'updated_at'])
                messages.success(request, 'Follow-up note logged and marked as replied.')
            else:
                messages.error(request, 'Please provide a non-empty note.')

        elif action == 'toggle_status':
            inquiry.is_replied = not inquiry.is_replied
            inquiry.save(update_fields=['is_replied', 'updated_at'])
            status_label = 'Replied' if inquiry.is_replied else 'Pending Reply'
            messages.success(request, f'Status updated to: {status_label}.')

        return redirect('cms_ui:inquiry-detail', pk=inquiry.pk)


class InquiryUpdateView(LoginRequiredMixin, UpdateView):
    """
    Edit existing inquiry submission / contact details.
    """
    model = InquirySubmission
    form_class = InquiryForm
    template_name = 'cms/inquiries/form.html'

    def get_success_url(self) -> str:
        return reverse('cms_ui:inquiry-detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context['is_new'] = False
        context['page_title'] = f'Edit Contact: {self.object.name}'
        return context

    def form_valid(self, form: InquiryForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, f'Contact details for "{self.object.name}" updated successfully.')
        return response


class InquiryDeleteView(LoginRequiredMixin, DeleteView):
    """
    Delete an inquiry submission.
    """
    model = InquirySubmission
    template_name = 'cms/common_confirm_delete.html'
    success_url = reverse_lazy('cms_ui:inquiry-list')

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context['object_name'] = f'Inquiry from {self.object.name} ({self.object.email})'
        context['cancel_url'] = reverse('cms_ui:inquiry-detail', kwargs={'pk': self.object.pk})
        return context

    def form_valid(self, form: Any) -> HttpResponse:
        name = self.object.name
        response = super().form_valid(form)
        messages.success(self.request, f'Inquiry from "{name}" has been deleted.')
        return response


class InquiryToggleRepliedView(LoginRequiredMixin, View):
    """
    Quick one-click toggle for inquiry replied status from the list table.
    """
    def post(self, request: HttpRequest, pk: int) -> HttpResponseRedirect:
        inquiry = get_object_or_404(InquirySubmission, pk=pk)
        inquiry.is_replied = not inquiry.is_replied
        inquiry.save(update_fields=['is_replied', 'updated_at'])
        next_url = request.POST.get('next') or reverse('cms_ui:inquiry-list')
        return redirect(next_url)


# ==============================================================================
# Projects & Case Studies
# ==============================================================================

class ProjectListView(LoginRequiredMixin, ListView):
    model = Project
    template_name = 'cms/projects/list.html'
    context_object_name = 'projects'
    ordering = ['order', '-created_at']


class ProjectCreateView(LoginRequiredMixin, CreateView):
    model = Project
    form_class = ProjectForm
    template_name = 'cms/projects/form.html'
    success_url = reverse_lazy('cms_ui:project-list')

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context['is_new'] = True
        context['page_title'] = 'Create New Project'
        return context

    def form_valid(self, form: ProjectForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, f'Project "{self.object.title}" created successfully.')
        return response


class ProjectUpdateView(LoginRequiredMixin, UpdateView):
    model = Project
    form_class = ProjectForm
    template_name = 'cms/projects/form.html'
    success_url = reverse_lazy('cms_ui:project-list')

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context['is_new'] = False
        context['page_title'] = f'Edit Project: {self.object.title}'
        return context

    def form_valid(self, form: ProjectForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, f'Project "{self.object.title}" updated successfully.')
        return response


class ProjectDeleteView(LoginRequiredMixin, DeleteView):
    model = Project
    template_name = 'cms/common_confirm_delete.html'
    success_url = reverse_lazy('cms_ui:project-list')

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context['object_name'] = f'Project "{self.object.title}"'
        context['cancel_url'] = reverse('cms_ui:project-list')
        return context


# ==============================================================================
# Solutions & Architectures
# ==============================================================================

class SolutionListView(LoginRequiredMixin, ListView):
    model = Solution
    template_name = 'cms/solutions/list.html'
    context_object_name = 'solutions'
    ordering = ['order', 'created_at']


class SolutionCreateView(LoginRequiredMixin, CreateView):
    model = Solution
    form_class = SolutionForm
    template_name = 'cms/solutions/form.html'
    success_url = reverse_lazy('cms_ui:solution-list')

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context['is_new'] = True
        context['page_title'] = 'Add New Solution'
        return context

    def form_valid(self, form: SolutionForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, f'Solution "{self.object.title}" added.')
        return response


class SolutionUpdateView(LoginRequiredMixin, UpdateView):
    model = Solution
    form_class = SolutionForm
    template_name = 'cms/solutions/form.html'
    success_url = reverse_lazy('cms_ui:solution-list')

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context['is_new'] = False
        context['page_title'] = f'Edit Solution: {self.object.title}'
        return context

    def form_valid(self, form: SolutionForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, f'Solution "{self.object.title}" updated.')
        return response


class SolutionDeleteView(LoginRequiredMixin, DeleteView):
    model = Solution
    template_name = 'cms/common_confirm_delete.html'
    success_url = reverse_lazy('cms_ui:solution-list')

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context['object_name'] = f'Solution "{self.object.title}"'
        context['cancel_url'] = reverse('cms_ui:solution-list')
        return context


# ==============================================================================
# Blog Posts
# ==============================================================================

class BlogListView(LoginRequiredMixin, ListView):
    model = BlogPost
    template_name = 'cms/blogs/list.html'
    context_object_name = 'blogs'
    ordering = ['-created_at']


class BlogCreateView(LoginRequiredMixin, CreateView):
    model = BlogPost
    form_class = BlogPostForm
    template_name = 'cms/blogs/form.html'
    success_url = reverse_lazy('cms_ui:blog-list')

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context['is_new'] = True
        context['page_title'] = 'Write New Blog Post'
        return context

    def form_valid(self, form: BlogPostForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, f'Blog post "{self.object.title}" created.')
        return response


class BlogUpdateView(LoginRequiredMixin, UpdateView):
    model = BlogPost
    form_class = BlogPostForm
    template_name = 'cms/blogs/form.html'
    success_url = reverse_lazy('cms_ui:blog-list')

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context['is_new'] = False
        context['page_title'] = f'Edit Blog Post: {self.object.title}'
        context['comments'] = self.object.comments.all().order_by('-created_at')
        return context

    def form_valid(self, form: BlogPostForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, f'Blog post "{self.object.title}" updated.')
        return response


class BlogDeleteView(LoginRequiredMixin, DeleteView):
    model = BlogPost
    template_name = 'cms/common_confirm_delete.html'
    success_url = reverse_lazy('cms_ui:blog-list')

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context['object_name'] = f'Blog "{self.object.title}"'
        context['cancel_url'] = reverse('cms_ui:blog-list')
        return context


class BlogCommentDeleteView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponseRedirect:
        comment = get_object_or_404(BlogComment, pk=pk)
        post_id = comment.post.pk
        comment.delete()
        messages.success(request, 'Comment removed successfully.')
        return redirect('cms_ui:blog-update', pk=post_id)


# ==============================================================================
# Site Settings (Singleton)
# ==============================================================================

class SiteSettingsUpdateView(LoginRequiredMixin, UpdateView):
    """
    Edit global site settings singleton instance.
    """
    model = SiteSettings
    form_class = SiteSettingsForm
    template_name = 'cms/settings/form.html'
    success_url = reverse_lazy('cms_ui:site-settings')

    def get_object(self, queryset: Any = None) -> SiteSettings:
        return SiteSettings.get_solo()

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context['page_title'] = 'Website Global Settings'
        return context

    def form_valid(self, form: SiteSettingsForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, 'Website settings saved successfully!')
        return response


# ==============================================================================
# Testimonials, FAQs, Tech Skills Quick Management
# ==============================================================================

class TestimonialListView(LoginRequiredMixin, ListView):
    model = Testimonial
    template_name = 'cms/testimonials/list.html'
    context_object_name = 'testimonials'
    ordering = ['order', '-created_at']


class TestimonialCreateView(LoginRequiredMixin, CreateView):
    model = Testimonial
    form_class = TestimonialForm
    template_name = 'cms/testimonials/form.html'
    success_url = reverse_lazy('cms_ui:testimonial-list')

    def form_valid(self, form: TestimonialForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, f'Testimonial by "{self.object.name}" added.')
        return response


class TestimonialUpdateView(LoginRequiredMixin, UpdateView):
    model = Testimonial
    form_class = TestimonialForm
    template_name = 'cms/testimonials/form.html'
    success_url = reverse_lazy('cms_ui:testimonial-list')

    def form_valid(self, form: TestimonialForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, f'Testimonial by "{self.object.name}" updated.')
        return response


class TestimonialDeleteView(LoginRequiredMixin, DeleteView):
    model = Testimonial
    template_name = 'cms/common_confirm_delete.html'
    success_url = reverse_lazy('cms_ui:testimonial-list')

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context['object_name'] = f'Testimonial from {self.object.name}'
        context['cancel_url'] = reverse('cms_ui:testimonial-list')
        return context


class FAQListView(LoginRequiredMixin, ListView):
    model = FAQ
    template_name = 'cms/faqs/list.html'
    context_object_name = 'faqs'
    ordering = ['order', 'id']


class FAQCreateView(LoginRequiredMixin, CreateView):
    model = FAQ
    form_class = FAQForm
    template_name = 'cms/faqs/form.html'
    success_url = reverse_lazy('cms_ui:faq-list')

    def form_valid(self, form: FAQForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, 'FAQ item added successfully.')
        return response


class FAQUpdateView(LoginRequiredMixin, UpdateView):
    model = FAQ
    form_class = FAQForm
    template_name = 'cms/faqs/form.html'
    success_url = reverse_lazy('cms_ui:faq-list')

    def form_valid(self, form: FAQForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, 'FAQ item updated successfully.')
        return response


class FAQDeleteView(LoginRequiredMixin, DeleteView):
    model = FAQ
    template_name = 'cms/common_confirm_delete.html'
    success_url = reverse_lazy('cms_ui:faq-list')

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context['object_name'] = f'FAQ "{self.object.question}"'
        context['cancel_url'] = reverse('cms_ui:faq-list')
        return context


class TechSkillListView(LoginRequiredMixin, ListView):
    model = TechSkill
    template_name = 'cms/skills/list.html'
    context_object_name = 'skills'
    ordering = ['category', 'proficiency_order', 'name']


class TechSkillCreateView(LoginRequiredMixin, CreateView):
    model = TechSkill
    form_class = TechSkillForm
    template_name = 'cms/skills/form.html'
    success_url = reverse_lazy('cms_ui:skill-list')

    def form_valid(self, form: TechSkillForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, f'Skill "{self.object.name}" added.')
        return response


class TechSkillUpdateView(LoginRequiredMixin, UpdateView):
    model = TechSkill
    form_class = TechSkillForm
    template_name = 'cms/skills/form.html'
    success_url = reverse_lazy('cms_ui:skill-list')

    def form_valid(self, form: TechSkillForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, f'Skill "{self.object.name}" updated.')
        return response


class TechSkillDeleteView(LoginRequiredMixin, DeleteView):
    model = TechSkill
    template_name = 'cms/common_confirm_delete.html'
    success_url = reverse_lazy('cms_ui:skill-list')

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context['object_name'] = f'Skill "{self.object.name}"'
        context['cancel_url'] = reverse('cms_ui:skill-list')
        return context

