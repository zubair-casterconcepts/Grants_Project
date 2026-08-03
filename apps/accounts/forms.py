from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import PasswordChangeForm as DjangoPasswordChangeForm

from services.location_utils import US_STATE_NAMES, normalize_location

from .models import Profile

STATE_CHOICES = [("", "Select state")] + [
    (abbr, f"{abbr} — {name}") for abbr, name in US_STATE_NAMES.items()
]


class ProfileAccountForm(forms.ModelForm):
    """Account/profile identity fields (not used for grant tool matching)."""

    class Meta:
        model = Profile
        fields = (
            "organization",
            "role_title",
        )
        widgets = {
            "organization": forms.TextInput(attrs={"placeholder": "Organization name"}),
            "role_title": forms.TextInput(attrs={"placeholder": "Your role"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["organization"].required = True
        self.fields["role_title"].required = False


class PasswordUpdateForm(DjangoPasswordChangeForm):
    """Change password: verify current, then set a validated new password."""

    old_password = forms.CharField(
        label="Current password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "placeholder": "Enter your current password",
            }
        ),
    )
    new_password1 = forms.CharField(
        label="New password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "placeholder": "Enter a new password",
            }
        ),
        help_text=password_validation.password_validators_help_text_html(),
    )
    new_password2 = forms.CharField(
        label="Confirm new password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "placeholder": "Re-enter the new password",
            }
        ),
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)
        for name in ("old_password", "new_password1", "new_password2"):
            self.fields[name].required = True


class ProfileIntakeForm(forms.ModelForm):
    """Settings form for project details used in grant matching."""

    location_state = forms.ChoiceField(choices=STATE_CHOICES)

    class Meta:
        model = Profile
        fields = (
            "title",
            "description",
            "priority_area",
            "location_city",
            "location_state",
            "org_type",
            "budget_requested",
            "eligibility_notes",
        )
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "What you seek funding for"}),
            "description": forms.Textarea(
                attrs={"rows": 5, "placeholder": "Describe the work"}
            ),
            "location_city": forms.TextInput(attrs={"placeholder": "City"}),
            "budget_requested": forms.NumberInput(
                attrs={"step": "0.01", "min": "0", "placeholder": "0.00"}
            ),
            "eligibility_notes": forms.Textarea(
                attrs={"rows": 3, "placeholder": "Optional eligibility notes"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        required = (
            "title",
            "description",
            "priority_area",
            "location_city",
            "location_state",
            "org_type",
            "budget_requested",
        )
        for name in required:
            self.fields[name].required = True

    def clean(self):
        cleaned = super().clean()
        city = cleaned.get("location_city", "")
        state = cleaned.get("location_state", "")
        city, state = normalize_location(city, state)
        cleaned["location_city"] = city
        cleaned["location_state"] = state
        if not state:
            self.add_error(
                "location_state",
                "Choose a valid US state (e.g. NY for New York).",
            )
        return cleaned
