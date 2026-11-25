"""Business rules shared by the web pages and the booth (ESP32) API."""
import secrets
import textwrap

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import (
    Candidate,
    Election,
    Notification,
    TempVotingRequest,
    Vote,
    VoteLog,
    Voter,
)


class VoteDenied(Exception):
    """Raised when a card/vote must be refused. lcd1/lcd2 are 20-char lines for the 20x4 LCD."""

    def __init__(self, code, message, lcd1="", lcd2=""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.lcd1 = lcd1[:20]
        self.lcd2 = lcd2[:20]


# ---------------------------------------------------------------- notifications
def notify(voter, message):
    """Save a notification and (after the DB commit) send it to the voter's phone."""
    note = Notification.objects.create(voter=voter, message=message[:300])
    transaction.on_commit(lambda: _send_sms(note.pk, voter.phone, message))
    email = voter.user.email if voter.user_id else ""
    if email:
        transaction.on_commit(lambda: _send_email(note.pk, email, message))
    return note


def _send_email(note_id, email, message):
    """Email copy of the notification. Errors must never break voting."""
    from django.core.mail import send_mail

    try:
        send_mail("Smart Electoral Management System", message, settings.DEFAULT_FROM_EMAIL, [email])
        Notification.objects.filter(pk=note_id).update(sms_sent=True)
    except Exception as exc:
        print(f"[EMAIL ERROR] {exc}")


def _send_sms(note_id, phone, message):
    backend = getattr(settings, "SMS_BACKEND", "console")
    sent = False
    if backend == "fast2sms":
        try:
            import requests

            resp = requests.post(
                "https://www.fast2sms.com/dev/bulkV2",
                headers={"authorization": settings.FAST2SMS_API_KEY},
                data={"route": "q", "message": message, "numbers": phone},
                timeout=10,
            )
            sent = resp.ok
        except Exception as exc:  # network / key problems must never break voting
            print(f"[SMS ERROR] {exc}")
    else:
        print(f"[SMS to {phone}] {message}")
        sent = True
    if sent:
        Notification.objects.filter(pk=note_id).update(sms_sent=True)


# ---------------------------------------------------------------- election rules
def get_active_election():
    return Election.objects.filter(is_active=True).order_by("-vote_date").first()


def voting_district_for(voter, election):
    """Where may this voter vote? Destination district if a request is approved, else home."""
    approved = TempVotingRequest.objects.filter(
        voter=voter, election=election, status="approved"
    ).select_related("destination_district", "booth").first()
    if approved:
        return approved.destination_district
    return voter.home_district


def check_eligibility(uid, booth):
    """Validate a scanned card at a booth. Returns (election, voter) or raises VoteDenied."""
    election = get_active_election()
    if election is None:
        raise VoteDenied("NO_ELECTION", "No active election", "No election", "is active")
    if getattr(settings, "ENFORCE_ELECTION_DATE", False) and election.vote_date != timezone.localdate():
        raise VoteDenied("WRONG_DATE", "Not the election date", "Not election day", election.vote_date.strftime("%d-%m-%Y"))

    uid = (uid or "").replace(" ", "").upper()
    voter = Voter.objects.select_related("constituency__district", "booth").filter(rfid_uid=uid).first()
    if voter is None:
        raise VoteDenied("UNKNOWN_CARD", "Card not registered", "Card not found", "See the admin")
    if voter.registration_status != "approved":
        raise VoteDenied("NOT_APPROVED", "Registration not approved", "Not verified", "See the admin")
    age = voter.age_on(election.vote_date)
    if age is not None and age < 18:
        raise VoteDenied("UNDER_AGE", "Voter is under 18", "Not eligible", "Under 18")
    if VoteLog.objects.filter(voter=voter, election=election).exists():
        raise VoteDenied("ALREADY_VOTED", "Already voted", "Already voted", "Thank you")

    allowed = voting_district_for(voter, election)
    if booth.district_id != allowed.id:
        raise VoteDenied(
            "WRONG_BOOTH",
            f"This voter must vote in {allowed.name}",
            "Wrong district",
            f"Go: {allowed.name}",
        )
    # A local voter (no approved outstation request) at a constituency booth must belong to that
    # constituency and, when a booth is assigned, use exactly that booth.
    # District-level booths (no constituency) serve every voter of the district.
    if allowed.id == voter.constituency.district_id and booth.constituency_id:
        if booth.constituency_id != voter.constituency_id:
            raise VoteDenied(
                "WRONG_BOOTH", "This booth is for another constituency",
                "Wrong constituency", voter.constituency.name,
            )
        if voter.booth_id and voter.booth_id != booth.id:
            raise VoteDenied(
                "WRONG_BOOTH", f"Your booth is {voter.booth.name}", "Wrong booth", voter.booth.name,
            )
    return election, voter


def candidates_for(voter):
    return list(Candidate.objects.filter(constituency=voter.constituency).order_by("number")[:9])


def make_slip_lines(log, voter, booth):
    """Staff-side record. It never shows who the voter chose (secret ballot)."""
    outstation = booth.district_id != voter.home_district.id
    raw = [
        "SMART ELECTORAL",
        "VOTE RECORD (STAFF COPY)",
        "--------------------------------",
        f"Txn ID: {log.transaction_id}",
        f"Constituency: {voter.constituency.name}",
        f"Booth: {booth.name}",
        f"District: {booth.district.name}",
        f"Type: {'Outstation from ' + voter.home_district.name if outstation else 'Local'}",
        timezone.localtime(log.cast_at).strftime("%d-%m-%Y %I:%M %p"),
    ]
    lines = []
    for line in raw:
        lines.extend(textwrap.wrap(line, 32) or [""])
    return lines


@transaction.atomic
def cast_vote(uid, booth, candidate_no):
    """Re-check everything, store the secret ballot, log the voter, notify the phone."""
    election, voter = check_eligibility(uid, booth)
    try:
        number = int(candidate_no)
    except (TypeError, ValueError):
        raise VoteDenied("BAD_CANDIDATE", "Invalid candidate", "Invalid choice", "Try again")
    candidate = Candidate.objects.filter(constituency=voter.constituency, number=number).first()
    if candidate is None:
        raise VoteDenied("BAD_CANDIDATE", "Invalid candidate", "Invalid choice", "Try again")

    outstation = booth.district_id != voter.home_district.id
    txn = f"TXN{election.pk}-{secrets.token_hex(4).upper()}"

    Vote.objects.create(
        election=election, candidate=candidate, constituency=voter.constituency,
        booth=booth, outstation=outstation,
    )
    log = VoteLog.objects.create(voter=voter, election=election, booth=booth, transaction_id=txn)
    when = timezone.localtime(log.cast_at).strftime("%d-%m-%Y %I:%M %p")
    notify(voter, f"Your vote was recorded at {booth.name} on {when}. Transaction ID: {txn}. Thank you for voting.")
    return log, voter
