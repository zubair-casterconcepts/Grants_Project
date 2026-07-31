from django import forms

from services.location_utils import US_STATE_NAMES, normalize_location

from .models import Profile

STATE_CHOICES = [("", "Select state")] + [
    (abbr, f"{abbr} — {name}") for abbr, name in US_STATE_NAMES.items()
]


class ProfileIntakeForm(forms.ModelForm):
    """First-login onboarding + Settings update form (user/ntee set in code)."""

    location_state = forms.ChoiceField(choices=STATE_CHOICES)

    class Meta:
        model = Profile
        fields = (
            "organization",
            "role_title",
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
            "organization": forms.TextInput(attrs={"placeholder": "Organization name"}),
            "role_title": forms.TextInput(attrs={"placeholder": "Your role"}),
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
            "organization",
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
