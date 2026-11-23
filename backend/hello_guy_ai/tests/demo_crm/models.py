from django.conf import settings
from django.db import models


class Lead(models.Model):
    STATUS_CHOICES = [
        ("new", "new"),
        ("contacted", "contacted"),
        ("won", "won"),
        ("lost", "lost"),
    ]

    name = models.CharField(max_length=100)
    company = models.CharField(max_length=100, blank=True)
    value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=20, default="new", choices=STATUS_CHOICES)
    tags = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True, null=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        verbose_name = "lead"
        verbose_name_plural = "leads"

    def __str__(self) -> str:
        return self.name

    @classmethod
    def hello_guy_queryset(cls, user):
        if user.is_staff or user.is_superuser:
            return cls.objects.all()
        return cls.objects.filter(owner=user)
