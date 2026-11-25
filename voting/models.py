import secrets
from datetime import datetime, timedelta

from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

STATUS_CHOICES = [
    ("pending", "Pending"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
]


class District(models.Model):
    name = models.CharField(max_length=60, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Constituency(models.Model):
    name = models.CharField(max_length=80, unique=True)
    district = models.ForeignKey(District, on_delete=models.PROTECT, related_name="constituencies")

    class Meta:
        ordering = ["district__name", "name"]
        verbose_name_plural = "constituencies"

    def __str__(self):
        return f"{self.name} ({self.district.name})"


class Candidate(models.Model):
    """`number` is the key the voter presses on the 4x3 keypad (1-9)."""

    constituency = models.ForeignKey(Constituency, on_delete=models.CASCADE, related_name="candidates")
    number = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(9)])
    name = models.CharField(max_length=60)
    party = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ["constituency", "number"]
        unique_together = [("constituency", "number")]

    def __str__(self):
        return f"{self.number}. {self.name} - {self.constituency.name}"


class Election(models.Model):
    name = models.CharField(max_length=100)
    vote_date = models.DateField()
    is_active = models.BooleanField(default=False, help_text="Only one election should be active.")

    class Meta:
        ordering = ["-vote_date"]

    def __str__(self):
        return f"{self.name} ({self.vote_date})"


def _new_device_key():
    return secrets.token_hex(8)


class Booth(models.Model):
    """One polling booth = one ESP32 device. The device sends booth id + key."""

    name = models.CharField(max_length=80)
    district = models.ForeignKey(District, on_delete=models.PROTECT, related_name="booths")
    constituency = models.ForeignKey(
        "Constituency", on_delete=models.PROTECT, null=True, blank=True, related_name="booths",
        help_text="A constituency has many booths. Leave empty for a district-level booth that serves "
                  "every constituency of the district.",
    )
    device_key = models.CharField(max_length=32, default=_new_device_key)
    start_time = models.TimeField(default=datetime.strptime("09:00", "%H:%M").time())
    slot_minutes = models.PositiveSmallIntegerField(default=10)

    class Meta:
        ordering = ["district__name", "name"]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.constituency_id and self.district_id and self.constituency.district_id != self.district_id:
            raise ValidationError("The constituency does not belong to the selected district.")

    def save(self, *args, **kwargs):
        if self.constituency_id:
            self.district_id = self.constituency.district_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} - {self.district.name}"


GENDER_CHOICES = [("M", "Male"), ("F", "Female"), ("O", "Other")]
RELATION_CHOICES = [("father", "Father"), ("mother", "Mother"), ("spouse", "Spouse"), ("guardian", "Guardian")]
AGE_PROOF_CHOICES = [
    ("marksheet", "10th / 12th mark sheet"), ("birth", "Birth certificate"), ("aadhaar", "Aadhaar"),
    ("pan", "PAN card"), ("passport", "Passport"), ("licence", "Driving licence"),
]
ADDRESS_PROOF_CHOICES = [
    ("bill", "EB / water / gas bill"), ("aadhaar", "Aadhaar"), ("passbook", "Bank / post office passbook"),
    ("passport", "Passport"), ("rent", "Registered rent agreement"), ("sale", "Registered sale deed"),
]


class Voter(models.Model):
    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True)
    voter_id = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=80)
    phone = models.CharField(max_length=15, help_text="Mobile number for vote notifications")
    address = models.TextField()
    constituency = models.ForeignKey(Constituency, on_delete=models.PROTECT, related_name="voters")
    rfid_uid = models.CharField(
        max_length=20, unique=True, null=True, blank=True,
        help_text="RFID card UID, e.g. 04A1B2C3. The administrator maps the card after the voter registers.",
    )
    date_of_birth = models.DateField(null=True, blank=True)
    age_proof = models.FileField(
        upload_to="age_proofs/", blank=True,
        help_text="Birth certificate / school certificate / other age proof",
    )
    registration_status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="approved",
        help_text="Portal registrations start as Pending until the administrator verifies age 18+. "
                  "Voters added by the administrator are approved.",
    )
    registration_note = models.CharField(max_length=200, blank=True)
    aadhaar_proof = models.FileField(
        upload_to="aadhaar_proofs/", blank=True,
        help_text="Aadhaar card copy (identity / address proof). The 10th mark sheet is stored in 'age proof'.",
    )
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, blank=True)
    relation_type = models.CharField(max_length=10, choices=RELATION_CHOICES, blank=True)
    relative_name = models.CharField(max_length=80, blank=True, help_text="Father / mother / spouse name")
    age_proof_type = models.CharField(max_length=20, choices=AGE_PROOF_CHOICES, blank=True)
    address_proof = models.FileField(upload_to="address_proofs/", blank=True)
    address_proof_type = models.CharField(max_length=20, choices=ADDRESS_PROOF_CHOICES, blank=True)
    photo = models.ImageField(upload_to="voter_photos/", blank=True, help_text="Passport size photo")
    booth = models.ForeignKey(
        Booth, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_voters",
        help_text="Polling booth of the voter (inside the voter's constituency). Changed by a booth change request.",
    )

    class Meta:
        ordering = ["voter_id"]

    def save(self, *args, **kwargs):
        self.rfid_uid = (self.rfid_uid or "").replace(" ", "").upper() or None
        super().save(*args, **kwargs)

    @property
    def home_district(self):
        return self.constituency.district

    def age_on(self, day):
        """Completed years of age on the given date (None if the date of birth is unknown)."""
        dob = self.date_of_birth
        if dob is None:
            return None
        return day.year - dob.year - ((day.month, day.day) < (dob.month, dob.day))

    def approve_registration(self, note=""):
        """Administrator verifies the age proof. The voter must be 18+ on the election date."""
        from .services import get_active_election, notify

        election = get_active_election()
        day = election.vote_date if election else timezone.localdate()
        age = self.age_on(day)
        if age is None:
            raise ValueError(f"{self.name}: date of birth is missing.")
        if age < 18:
            raise ValueError(f"{self.name} will be only {age} on {day:%d-%m-%Y}. A voter must be 18 or older.")
        self.registration_status = "approved"
        self.registration_note = note
        if self.booth_id is None:
            self.booth = Booth.objects.filter(constituency=self.constituency).order_by("name").first()
        self.save()
        notify(self, "Your registration is approved. You are verified as an eligible voter (18+).")

    def reject_registration(self, note=""):
        from .services import notify

        self.registration_status = "rejected"
        self.registration_note = note
        self.save()
        notify(self, f"Your registration was rejected. {note}".strip())

    def __str__(self):
        return f"{self.name} ({self.voter_id})"


class ChangeRequest(models.Model):
    KIND_CHOICES = [
        ("name", "Name change"),
        ("address", "Address change"),
        ("constituency", "Constituency change"),
        ("booth", "Booth change"),
    ]
    voter = models.ForeignKey(Voter, on_delete=models.CASCADE, related_name="change_requests")
    kind = models.CharField(max_length=15, choices=KIND_CHOICES)
    new_value = models.TextField(blank=True, help_text="New name, new address or the reason for a booth change")
    new_constituency = models.ForeignKey(Constituency, on_delete=models.PROTECT, null=True, blank=True)
    new_booth = models.ForeignKey(Booth, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    proof = models.FileField(
        upload_to="change_proofs/", blank=True,
        help_text="Address proof (rent agreement / utility bill / Aadhaar). Needed for address and constituency change.",
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    admin_note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.voter.voter_id} - {self.get_kind_display()} ({self.status})"

    def approve(self, note=""):
        from .services import notify

        voter = self.voter
        if self.kind == "name":
            voter.name = self.new_value
        elif self.kind == "address":
            voter.address = self.new_value
        elif self.kind == "constituency":
            if self.new_constituency is None:
                raise ValueError("No new constituency selected.")
            voter.constituency = self.new_constituency
            # the old booth belongs to the old constituency: give the first booth of the new one
            voter.booth = Booth.objects.filter(constituency=self.new_constituency).order_by("name").first()
        elif self.kind == "booth":
            if self.new_booth is None:
                raise ValueError("No new booth selected.")
            if self.new_booth.constituency_id != voter.constituency_id:
                raise ValueError("The new booth must be inside the voter's own constituency.")
            voter.booth = self.new_booth
        voter.save()
        self.status = "approved"
        self.admin_note = note
        self.decided_at = timezone.now()
        self.save()
        notify(voter, f"Your {self.get_kind_display().lower()} request was approved and updated.")

    def reject(self, note=""):
        from .services import notify

        self.status = "rejected"
        self.admin_note = note
        self.decided_at = timezone.now()
        self.save()
        notify(self.voter, f"Your {self.get_kind_display().lower()} request was rejected. {note}".strip())


class Complaint(models.Model):
    STATUS = [("open", "Open"), ("resolved", "Resolved")]
    voter = models.ForeignKey(Voter, on_delete=models.CASCADE, related_name="complaints")
    subject = models.CharField(max_length=120)
    message = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS, default="open")
    reply = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.voter.voter_id} - {self.subject}"


class TempVotingRequest(models.Model):
    """A voter asks to vote in another district for one election."""

    voter = models.ForeignKey(Voter, on_delete=models.CASCADE, related_name="temp_requests")
    election = models.ForeignKey(Election, on_delete=models.CASCADE)
    destination_district = models.ForeignKey(District, on_delete=models.PROTECT)
    temp_address = models.TextField(help_text="Temporary address in the destination district")
    proof = models.FileField(upload_to="proofs/", help_text="Bonafide / hostel letter / rent agreement / utility bill")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    admin_note = models.CharField(max_length=200, blank=True)
    booth = models.ForeignKey(Booth, on_delete=models.SET_NULL, null=True, blank=True)
    slot_time = models.TimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.voter.voter_id} -> {self.destination_district.name} ({self.status})"

    @classmethod
    def route_count(cls, election, home_district, destination_district, exclude_pk=None):
        """How many voters are already approved on this route (home district -> destination)."""
        qs = cls.objects.filter(
            election=election,
            status="approved",
            voter__constituency__district=home_district,
            destination_district=destination_district,
        )
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)
        return qs.count()

    def approve(self, note=""):
        """Admin approval: check the route quota, allot a booth and the next free time slot."""
        from django.conf import settings

        from .services import notify

        quota = getattr(settings, "TEMP_ROUTE_QUOTA", 10)
        used = self.route_count(self.election, self.voter.home_district, self.destination_district, self.pk)
        if used >= quota:
            raise ValueError(
                f"Quota full: {used}/{quota} voters from {self.voter.home_district.name} "
                f"are already approved for {self.destination_district.name}."
            )
        booth = Booth.objects.filter(district=self.destination_district).order_by("id").first()
        if booth is None:
            raise ValueError(f"No booth exists in {self.destination_district.name}. Add a booth first.")
        taken = TempVotingRequest.objects.filter(
            booth=booth, election=self.election, status="approved"
        ).exclude(pk=self.pk).count()
        start = datetime.combine(self.election.vote_date, booth.start_time)
        slot = start + timedelta(minutes=booth.slot_minutes * taken)
        self.booth = booth
        self.slot_time = slot.time()
        self.status = "approved"
        self.admin_note = note
        self.decided_at = timezone.now()
        self.save()
        notify(
            self.voter,
            f"Your request to vote in {self.destination_district.name} is approved. "
            f"Booth: {booth.name}, date: {self.election.vote_date:%d-%m-%Y}, time: {self.slot_time:%I:%M %p}. "
            "Carry your RFID card.",
        )

    def reject(self, note=""):
        from .services import notify

        self.status = "rejected"
        self.admin_note = note
        self.decided_at = timezone.now()
        self.save()
        notify(self.voter, f"Your request to vote in {self.destination_district.name} was rejected. {note}".strip())


class Vote(models.Model):
    """The ballot itself. It deliberately has NO link to the voter (secret ballot)."""

    election = models.ForeignKey(Election, on_delete=models.CASCADE)
    candidate = models.ForeignKey(Candidate, on_delete=models.PROTECT)
    constituency = models.ForeignKey(Constituency, on_delete=models.PROTECT)
    booth = models.ForeignKey(Booth, on_delete=models.PROTECT)
    outstation = models.BooleanField(default=False, help_text="True = voter voted outside their home district")
    cast_at = models.DateTimeField(auto_now_add=True)


class VoteLog(models.Model):
    """Records THAT a voter voted (prevents double voting), not WHO they voted for."""

    voter = models.ForeignKey(Voter, on_delete=models.CASCADE)
    election = models.ForeignKey(Election, on_delete=models.CASCADE)
    booth = models.ForeignKey(Booth, on_delete=models.PROTECT)
    transaction_id = models.CharField(max_length=30, unique=True)
    cast_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("voter", "election")]
        ordering = ["-cast_at"]


class Notification(models.Model):
    voter = models.ForeignKey(Voter, on_delete=models.CASCADE, related_name="notifications")
    message = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)
    sms_sent = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.voter.voter_id}: {self.message[:40]}"
