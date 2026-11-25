from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.utils import timezone

from .models import ADDRESS_PROOF_CHOICES, AGE_PROOF_CHOICES, GENDER_CHOICES, RELATION_CHOICES, Booth, ChangeRequest, Complaint, Constituency, District, TempVotingRequest, Voter
from .services import get_active_election


class BootstrapMixin:
    """Give every widget a Bootstrap class."""

    def _style(self):
        for field in self.fields.values():
            widget = field.widget
            css = "form-select" if isinstance(widget, forms.Select) else "form-control"
            widget.attrs["class"] = (widget.attrs.get("class", "") + " " + css).strip()


class VoterLoginForm(BootstrapMixin, AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Voter ID"
        self._style()


class RegisterForm(BootstrapMixin, forms.Form):
    voter_id = forms.CharField(max_length=30, label="Voter ID")
    name = forms.CharField(max_length=80, label="Full name")
    date_of_birth = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="Date of birth")
    gender = forms.ChoiceField(choices=GENDER_CHOICES, label="Gender")
    relation_type = forms.ChoiceField(choices=RELATION_CHOICES, label="Relative (father / mother / spouse)")
    relative_name = forms.CharField(max_length=80, label="Relative's name")
    age_proof_type = forms.ChoiceField(choices=AGE_PROOF_CHOICES, label="Age proof type")
    age_proof = forms.FileField(label="Age proof (date-of-birth document)")
    address_proof_type = forms.ChoiceField(choices=ADDRESS_PROOF_CHOICES, label="Address proof type")
    address_proof = forms.FileField(label="Address proof (should match your address and constituency)")
    photo = forms.ImageField(label="Passport size photo")
    aadhaar_proof = forms.FileField(required=False, label="Aadhaar card (optional)")
    phone = forms.RegexField(regex=r"^\d{10}$", label="Mobile number (10 digits)",
                             error_messages={"invalid": "Enter a 10-digit mobile number."})
    email = forms.EmailField(label="Email (notifications are sent here)")
    address = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    constituency = forms.ModelChoiceField(queryset=Constituency.objects.select_related("district"))
    declaration = forms.BooleanField(
        label="I declare that I am an Indian citizen, the details are true, and I am not registered as a voter anywhere else."
    )
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()
        self.fields["declaration"].widget.attrs["class"] = "form-check-input"

    def clean_date_of_birth(self):
        dob = self.cleaned_data["date_of_birth"]
        election = get_active_election()
        day = election.vote_date if election else timezone.localdate()
        if dob > timezone.localdate():
            raise forms.ValidationError("Date of birth cannot be in the future.")
        age = day.year - dob.year - ((day.month, day.day) < (dob.month, dob.day))
        if age < 18:
            raise forms.ValidationError(
                f"You must be 18 or older on the election date ({day:%d-%m-%Y}). You will be {age}."
            )
        return dob

    def clean_voter_id(self):
        value = self.cleaned_data["voter_id"].strip().upper()
        if User.objects.filter(username__iexact=value).exists() or Voter.objects.filter(voter_id__iexact=value).exists():
            raise forms.ValidationError("This Voter ID is already registered.")
        return value

    def clean(self):
        data = super().clean()
        if data.get("password1") and data.get("password1") != data.get("password2"):
            self.add_error("password2", "Passwords do not match.")
        if data.get("password1"):
            try:
                password_validation.validate_password(data["password1"])
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return data

    def save(self):
        d = self.cleaned_data
        user = User.objects.create_user(username=d["voter_id"], email=d["email"], password=d["password1"])
        return Voter.objects.create(
            user=user, voter_id=d["voter_id"], name=d["name"], phone=d["phone"],
            address=d["address"], constituency=d["constituency"],
            date_of_birth=d["date_of_birth"], age_proof=d["age_proof"], aadhaar_proof=d.get("aadhaar_proof") or "",
            gender=d["gender"], relation_type=d["relation_type"], relative_name=d["relative_name"],
            age_proof_type=d["age_proof_type"], address_proof=d["address_proof"],
            address_proof_type=d["address_proof_type"], photo=d["photo"],
            registration_status="pending",
        )


class ChangeRequestForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ChangeRequest
        fields = ["kind", "new_value", "new_booth", "proof"]
        labels = {
            "kind": "What do you want to change?",
            "new_value": "New name / new address / reason for booth change",
            "new_booth": "New booth (for booth change, inside your constituency)",
            "proof": "Proof (required for address change; optional for booth change)",
        }
        widgets = {"new_value": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        self.voter = kwargs.pop("voter")
        super().__init__(*args, **kwargs)
        # Constituency change is not offered. Voters ask for a nearer booth inside their own constituency.
        self.fields["kind"].choices = [c for c in ChangeRequest.KIND_CHOICES if c[0] != "constituency"]
        self.fields["new_booth"].queryset = (
            Booth.objects.filter(constituency=self.voter.constituency).exclude(pk=self.voter.booth_id)
        )
        self._style()

    def clean(self):
        data = super().clean()
        kind = data.get("kind")
        if kind in ("name", "address") and not (data.get("new_value") or "").strip():
            self.add_error("new_value", "Enter the new value.")
        if kind == "booth":
            if not data.get("new_booth"):
                self.add_error("new_booth", "Select the new booth.")
            if not (data.get("new_value") or "").strip():
                self.add_error("new_value", "Enter the reason (for example health or distance).")
        if kind == "address" and not data.get("proof"):
            self.add_error("proof", "Upload an address proof (rent agreement / utility bill / Aadhaar).")
        if ChangeRequest.objects.filter(voter=self.voter, kind=kind, status="pending").exists():
            raise forms.ValidationError("You already have a pending request of this type.")
        return data


class ComplaintForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Complaint
        fields = ["subject", "message"]
        widgets = {"message": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()


class TempRequestForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = TempVotingRequest
        fields = ["destination_district", "temp_address", "proof"]
        labels = {"destination_district": "District where you want to vote"}
        widgets = {"temp_address": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        self.voter = kwargs.pop("voter")
        self.election = get_active_election()
        super().__init__(*args, **kwargs)
        self.fields["destination_district"].queryset = District.objects.exclude(pk=self.voter.home_district.pk)
        self._style()

    def clean(self):
        data = super().clean()
        if self.election is None:
            raise forms.ValidationError("There is no active election right now.")
        min_days = getattr(settings, "TEMP_REQUEST_MIN_DAYS", 30)
        last_day = self.election.vote_date - timedelta(days=min_days)
        if timezone.localdate() > last_day:
            raise forms.ValidationError(
                f"Outstation requests must be made at least {min_days} days before the election "
                f"({self.election.vote_date:%d-%m-%Y}). The last day to apply was {last_day:%d-%m-%Y}."
            )
        if TempVotingRequest.objects.filter(
            voter=self.voter, election=self.election, status__in=["pending", "approved"]
        ).exists():
            raise forms.ValidationError("You already have a pending or approved outstation request for this election.")
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.voter = self.voter
        obj.election = self.election
        if commit:
            obj.save()
        return obj
