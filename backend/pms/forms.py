from django import forms
from django.contrib.auth import get_user_model

from pms.models import Project, Task

DATE_INPUT = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class UserChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, user):
        return user.get_full_name() or user.get_username()


class UserMultipleChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, user):
        return user.get_full_name() or user.get_username()


class ProjectForm(forms.ModelForm):
    members = UserMultipleChoiceField(
        queryset=get_user_model().objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Project
        fields = ["name", "status", "start_date", "due_date", "description", "members"]
        widgets = {
            "start_date": DATE_INPUT,
            "due_date": DATE_INPUT,
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["members"].queryset = get_user_model().objects.filter(is_active=True).order_by("username")

    def clean(self):
        cleaned = super().clean()
        start, due = cleaned.get("start_date"), cleaned.get("due_date")
        if start and due and due < start:
            self.add_error("due_date", "Due date cannot be before start date.")
        return cleaned


class TaskForm(forms.ModelForm):
    assignee = UserChoiceField(queryset=get_user_model().objects.none(), required=False)

    class Meta:
        model = Task
        fields = [
            "title",
            "parent",
            "is_group",
            "status",
            "priority",
            "assignee",
            "due_date",
            "expected_hours",
            "actual_hours",
            "description",
        ]
        widgets = {
            "due_date": DATE_INPUT,
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, project: Project, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.project = project
        self.fields["assignee"].queryset = get_user_model().objects.filter(is_active=True).order_by("username")

        parent_qs = Task.objects.filter(project=project, is_group=True).order_by("created_at", "pk")
        if self.instance.pk:
            excluded = {self.instance.pk, *self.instance.descendant_ids()}
            parent_qs = parent_qs.exclude(pk__in=excluded)
        self.fields["parent"].queryset = parent_qs
        self.fields["parent"].empty_label = "— Top level —"
