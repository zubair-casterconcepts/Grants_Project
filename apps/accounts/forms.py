from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import PasswordChangeForm as DjangoPasswordChangeForm
from django.utils.text import slugify

from services.location_utils import US_STATE_NAMES, normalize_location

from .models import Profile, StarterPrompt

STATE_CHOICES = [("", "Select state")] + [
    (abbr, f"{abbr} — {name}") for abbr, name in US_STATE_NAMES.items()
]


class ProfileAccountForm(forms.ModelForm):
    """Account/profile identity fields (not used for grant tool matching)."""

    email = forms.EmailField(
        required=False,
        label="Email address",
        widget=forms.EmailInput(attrs={"placeholder": "you@organization.org"}),
        help_text="Where your Monday grant digest is delivered.",
    )

    class Meta:
        model = Profile
        fields = (
            "organization",
            "role_title",
            "weekly_digest_enabled",
        )
        labels = {"weekly_digest_enabled": "Email me matching grants every Monday"}
        widgets = {
            "organization": forms.TextInput(attrs={"placeholder": "Organization name"}),
            "role_title": forms.TextInput(attrs={"placeholder": "Your role"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["organization"].required = True
        self.fields["role_title"].required = False
        user = self._user()
        if user is not None and not self.is_bound:
            self.fields["email"].initial = user.email

    def _user(self):
        return self.instance.user if getattr(self.instance, "user_id", None) else None

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("weekly_digest_enabled") and not (cleaned.get("email") or "").strip():
            self.add_error(
                "email",
                "Add an email address so we know where to send your weekly matches.",
            )
        return cleaned

    def save(self, commit=True):
        profile = super().save(commit=commit)
        user = self._user()
        email = (self.cleaned_data.get("email") or "").strip()
        if user is not None and email != (user.email or ""):
            user.email = email
            if commit:
                user.save(update_fields=["email"])
        return profile


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


class StarterPromptForm(forms.ModelForm):
    """Add / edit a customizable chat starter card ("instruction")."""

    class Meta:
        model = StarterPrompt
        # Every card managed here is a grant search that sends its query with the
        # saved profile. `action`/`href` are intentionally not exposed — they are
        # left at their stored/default values so built-in cards keep working.
        fields = (
            "title",
            "description",
            "query",
            "position",
            "is_active",
            "key",
        )
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "e.g. Find grants"}),
            "description": forms.TextInput(
                attrs={"placeholder": "e.g. Search by focus & location"}
            ),
            "query": forms.TextInput(
                attrs={"placeholder": "Text sent to the grant matcher, e.g. find education grants in CA"}
            ),
            "position": forms.NumberInput(attrs={"min": "0", "step": "1"}),
            "key": forms.TextInput(
                attrs={"placeholder": "Auto-generated from title if left blank"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["title"].required = True
        for name in ("description", "query", "key"):
            self.fields[name].required = False
        self.fields["position"].required = False

    def clean_position(self):
        return self.cleaned_data.get("position") or 0

    def clean(self):
        cleaned = super().clean()

        # Auto-fill a unique slug key from the title when left blank.
        key = (cleaned.get("key") or "").strip()
        if not key:
            base = slugify(cleaned.get("title") or "")[:56] or "prompt"
            qs = StarterPrompt.objects.all()
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            existing = set(qs.values_list("key", flat=True))
            candidate, suffix = base, 2
            while candidate in existing:
                candidate = f"{base}-{suffix}"
                suffix += 1
            cleaned["key"] = candidate
        return cleaned


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
