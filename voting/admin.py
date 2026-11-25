from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    Booth,
    Candidate,
    ChangeRequest,
    Complaint,
    Constituency,
    District,
    Election,
    Notification,
    TempVotingRequest,
    Vote,
    VoteLog,
    Voter,
)
from .services import get_active_election, notify

admin.site.site_header = "Smart Electoral Management - Administrator"
admin.site.site_title = "Smart Electoral"
admin.site.index_title = "Review requests, map RFID cards, manage booths"


@admin.register(District)
class DistrictAdmin(admin.ModelAdmin):
    search_fields = ["name"]


@admin.register(Constituency)
class ConstituencyAdmin(admin.ModelAdmin):
    list_display = ["name", "district"]
    list_filter = ["district"]
    search_fields = ["name"]


@admin.register(Candidate)
class CandidateAdmin(admin.ModelAdmin):
    list_display = ["number", "name", "party", "constituency"]
    list_filter = ["constituency"]


@admin.register(Election)
class ElectionAdmin(admin.ModelAdmin):
    list_display = ["name", "vote_date", "is_active"]


@admin.register(Booth)
class BoothAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "district", "constituency", "device_key", "start_time", "slot_minutes"]
    list_filter = ["district", "constituency"]
    readonly_fields = []


@admin.register(Voter)
class VoterAdmin(admin.ModelAdmin):
    list_display = [
        "voter_id", "name", "phone", "constituency", "booth", "date_of_birth", "age_on_election",
        "age_proof_link", "address_proof_link", "photo_link", "aadhaar_link", "registration_status", "rfid_uid",
    ]
    list_filter = ["registration_status", "constituency__district", "constituency"]
    search_fields = ["voter_id", "name", "phone", "rfid_uid"]
    actions = ["approve_registrations", "reject_registrations"]

    @admin.display(description="Age on election day")
    def age_on_election(self, obj):
        election = get_active_election()
        day = election.vote_date if election else timezone.localdate()
        age = obj.age_on(day)
        return "-" if age is None else age

    @admin.display(description="Aadhaar")
    def aadhaar_link(self, obj):
        if obj.aadhaar_proof:
            return format_html('<a href="{}" target="_blank">View Aadhaar</a>', obj.aadhaar_proof.url)
        return "-"

    @admin.display(description="Age proof")
    def age_proof_link(self, obj):
        if obj.age_proof:
            return format_html('<a href="{}" target="_blank">{}</a>', obj.age_proof.url, obj.get_age_proof_type_display() or "View")
        return "-"

    @admin.display(description="Address proof")
    def address_proof_link(self, obj):
        if obj.address_proof:
            return format_html('<a href="{}" target="_blank">{}</a>', obj.address_proof.url, obj.get_address_proof_type_display() or "View")
        return "-"

    @admin.display(description="Photo")
    def photo_link(self, obj):
        if obj.photo:
            return format_html('<a href="{}" target="_blank">View photo</a>', obj.photo.url)
        return "-"

    @admin.action(description="Approve registration (age proof checked, 18+)")
    def approve_registrations(self, request, queryset):
        done = 0
        for voter in queryset.exclude(registration_status="approved"):
            try:
                voter.approve_registration()
                done += 1
            except ValueError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
        if done:
            self.message_user(request, f"{done} registration(s) approved.", level=messages.SUCCESS)

    @admin.action(description="Reject registration")
    def reject_registrations(self, request, queryset):
        done = 0
        for voter in queryset.filter(registration_status="pending"):
            voter.reject_registration("Age proof could not be verified.")
            done += 1
        self.message_user(request, f"{done} registration(s) rejected.", level=messages.WARNING)


class _DecisionAdmin(admin.ModelAdmin):
    """Shared approve / reject actions."""

    actions = ["approve_selected", "reject_selected"]

    @admin.action(description="Approve selected pending requests")
    def approve_selected(self, request, queryset):
        done = 0
        for obj in queryset.filter(status="pending"):
            try:
                obj.approve()
                done += 1
            except ValueError as exc:
                self.message_user(request, f"{obj}: {exc}", level=messages.ERROR)
        if done:
            self.message_user(request, f"{done} request(s) approved.", level=messages.SUCCESS)

    @admin.action(description="Reject selected pending requests")
    def reject_selected(self, request, queryset):
        done = 0
        for obj in queryset.filter(status="pending"):
            obj.reject("Please contact the administrator.")
            done += 1
        self.message_user(request, f"{done} request(s) rejected.", level=messages.WARNING)


@admin.register(ChangeRequest)
class ChangeRequestAdmin(_DecisionAdmin):
    list_display = ["voter", "kind", "new_value", "new_constituency", "new_booth", "proof_link", "status", "created_at"]
    list_filter = ["status", "kind"]
    search_fields = ["voter__voter_id", "voter__name"]
    readonly_fields = ["voter", "kind", "new_value", "new_constituency", "new_booth", "proof", "created_at", "decided_at"]

    @admin.display(description="Proof")
    def proof_link(self, obj):
        if obj.proof:
            return format_html('<a href="{}" target="_blank">View proof</a>', obj.proof.url)
        return "-"


@admin.register(TempVotingRequest)
class TempVotingRequestAdmin(_DecisionAdmin):
    list_display = ["voter", "home", "destination_district", "quota", "proof_link", "status", "booth", "slot_time"]
    list_filter = ["status", "destination_district", "election"]
    search_fields = ["voter__voter_id", "voter__name"]
    readonly_fields = ["voter", "election", "destination_district", "temp_address", "proof", "booth", "slot_time", "created_at", "decided_at"]

    @admin.display(description="Home district")
    def home(self, obj):
        return obj.voter.home_district

    @admin.display(description="Route quota")
    def quota(self, obj):
        from django.conf import settings

        used = TempVotingRequest.route_count(obj.election, obj.voter.home_district, obj.destination_district)
        return f"{used}/{settings.TEMP_ROUTE_QUOTA} approved"

    @admin.display(description="Proof")
    def proof_link(self, obj):
        if obj.proof:
            return format_html('<a href="{}" target="_blank">View proof</a>', obj.proof.url)
        return "-"


@admin.register(Complaint)
class ComplaintAdmin(admin.ModelAdmin):
    list_display = ["voter", "subject", "status", "created_at"]
    list_filter = ["status"]
    readonly_fields = ["voter", "subject", "message", "created_at"]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if change and "reply" in form.changed_data and obj.reply:
            notify(obj.voter, f"Reply to your complaint '{obj.subject}': {obj.reply}")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["voter", "message", "sms_sent", "created_at"]
    list_filter = ["sms_sent"]


@admin.register(VoteLog)
class VoteLogAdmin(admin.ModelAdmin):
    """Who voted (not for whom)."""

    list_display = ["transaction_id", "voter", "election", "booth", "cast_at"]
    list_filter = ["election", "booth"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Vote)
class VoteAdmin(admin.ModelAdmin):
    """Anonymous ballots. Read-only."""

    list_display = ["id", "election", "constituency", "candidate", "booth", "outstation", "cast_at"]
    list_filter = ["election", "constituency", "outstation"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
