from django import forms

from .models import Project


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        exclude = ("user", "ntee_code")
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Project title"}),
            "description": forms.Textarea(
                attrs={"rows": 5, "placeholder": "What the project does"}
            ),
            "location_city": forms.TextInput(attrs={"placeholder": "City"}),
            "location_state": forms.TextInput(
                attrs={"placeholder": "ST", "maxlength": 2}
            ),
            "budget_requested": forms.NumberInput(
                attrs={"step": "0.01", "min": "0", "placeholder": "0.00"}
            ),
            "eligibility_notes": forms.Textarea(
                attrs={"rows": 3, "placeholder": "Optional eligibility notes"}
            ),
        }
