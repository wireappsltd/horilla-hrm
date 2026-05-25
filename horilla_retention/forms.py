"""Admin-facing forms."""

from django import forms
from django.utils.translation import gettext_lazy as _

from horilla_retention.models import DataRetentionPolicy, RetentionAction


class DataRetentionPolicyForm(forms.ModelForm):
    class Meta:
        model = DataRetentionPolicy
        fields = (
            "company_id",
            "category",
            "retention_years",
            "grace_period_days",
            "is_enabled",
            "notes",
        )
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2, "class": "oh-input w-100"}),
            "company_id": forms.Select(attrs={"class": "retention-select"}),
            "category": forms.Select(attrs={"class": "retention-select"}),
            "retention_years": forms.NumberInput(
                attrs={"min": 1, "max": 50, "class": "oh-input w-100"}
            ),
            "grace_period_days": forms.NumberInput(
                attrs={"min": 0, "max": 365, "class": "oh-input w-100"}
            ),
        }

    def clean_retention_years(self):
        years = self.cleaned_data.get("retention_years")
        if years is not None and years < 1:
            raise forms.ValidationError(
                _("Retention period must be at least 1 year.")
            )
        return years


class DeferActionForm(forms.Form):
    deferred_until = forms.DateField(
        label=_("Defer until"),
        widget=forms.DateInput(
            attrs={"type": "date", "class": "oh-input w-100"}, format="%Y-%m-%d"
        ),
    )
    deferral_reason = forms.CharField(
        label=_("Reason"),
        widget=forms.Textarea(attrs={"rows": 3, "class": "oh-input w-100"}),
    )
