from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import redirect, render

from .forms import ChangeRequestForm, ComplaintForm, RegisterForm, TempRequestForm
from .guide import GUIDE_STEPS
from .models import (
    Candidate,
    ChangeRequest,
    Complaint,
    Constituency,
    TempVotingRequest,
    Vote,
    VoteLog,
    Voter,
)
from .services import get_active_election, voting_district_for


def voter_required(view):
    """Login required AND the account must be linked to a voter record."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        voter = getattr(request.user, "voter", None)
        if voter is None:
            if request.user.is_staff:
                return redirect("control_room")
            messages.info(request, "This account is not linked to a voter record.")
            return redirect("home")
        request.voter = voter
        return view(request, *args, **kwargs)

    return login_required(wrapper)


def _require_approved(request):
    """Requests are allowed only after the administrator has verified the voter (age 18+)."""
    if request.voter.registration_status != "approved":
        messages.warning(
            request,
            "Your registration is not approved yet. You can use this after the administrator verifies your age proof.",
        )
        return redirect("dashboard")
    return None


def home(request):
    return render(request, "voting/home.html", {"election": get_active_election()})


def how_to_vote(request):
    return render(request, "voting/how_to_vote.html", {"steps": GUIDE_STEPS})


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = RegisterForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        voter = form.save()
        login(request, voter.user)
        messages.success(
            request,
            "Registration received. The administrator will verify your age proof (18+), address proof and photo and then map your RFID card.",
        )
        return redirect("dashboard")
    return render(
        request,
        "voting/form_page.html",
        {"form": form, "title": "Voter registration", "button": "Register", "multipart": True,
         "help": "You must be 18 or older on the election date. Upload an age proof, an address proof (it decides your constituency) and a passport size photo. Aadhaar is optional."},
    )


@voter_required
def dashboard(request):
    voter = request.voter
    election = get_active_election()
    voting_place = voting_district_for(voter, election) if election else voter.home_district
    voted = bool(election and VoteLog.objects.filter(voter=voter, election=election).exists())
    context = {
        "voter": voter,
        "election": election,
        "voting_place": voting_place,
        "voted": voted,
        "change_requests": voter.change_requests.all()[:10],
        "complaints": voter.complaints.all()[:10],
        "temp_requests": voter.temp_requests.select_related("destination_district", "booth")[:10],
        "notifications": voter.notifications.all()[:10],
    }
    return render(request, "voting/dashboard.html", context)


@voter_required
def change_request_new(request):
    blocked = _require_approved(request)
    if blocked:
        return blocked
    form = ChangeRequestForm(request.POST or None, request.FILES or None, voter=request.voter)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.voter = request.voter
        obj.save()
        messages.success(request, "Request sent to the administrator.")
        return redirect("dashboard")
    return render(request, "voting/form_page.html", {"form": form, "title": "Name / address / constituency change", "button": "Send request", "multipart": True})


@voter_required
def complaint_new(request):
    form = ComplaintForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.voter = request.voter
        obj.save()
        messages.success(request, "Complaint registered.")
        return redirect("dashboard")
    return render(request, "voting/form_page.html", {"form": form, "title": "Register a complaint", "button": "Submit complaint"})


@voter_required
def temp_request_new(request):
    blocked = _require_approved(request)
    if blocked:
        return blocked
    form = TempRequestForm(request.POST or None, request.FILES or None, voter=request.voter)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Outstation voting request sent. You will be notified after review.")
        return redirect("dashboard")
    return render(
        request,
        "voting/form_page.html",
        {
            "form": form,
            "title": "Outstation (temporary) voting request",
            "button": "Apply",
            "multipart": True,
            "help": f"Apply at least {settings.TEMP_REQUEST_MIN_DAYS} days before the election. "
                    f"Maximum {settings.TEMP_ROUTE_QUOTA} voters from one district can be approved for each destination district.",
        },
    )


@staff_member_required
def control_room(request):
    """Admin dashboard: candidate-wise, constituency-wise and local / other-place counts."""
    election = get_active_election()
    rows = []
    totals = {"votes": 0, "local": 0, "outstation": 0}
    for const in Constituency.objects.select_related("district"):
        candidates = list(
            const.candidates.annotate(
                votes=Count("vote", filter=Q(vote__election=election)) if election else Count("vote", filter=Q(pk__isnull=True))
            ).order_by("number")
        )
        votes_qs = Vote.objects.filter(election=election, constituency=const) if election else Vote.objects.none()
        total = votes_qs.count()
        outstation = votes_qs.filter(outstation=True).count()
        for c in candidates:
            c.pct = round(c.votes * 100 / total) if total else 0
        registered = const.voters.count()
        rows.append(
            {
                "const": const,
                "candidates": candidates,
                "total": total,
                "local": total - outstation,
                "outstation": outstation,
                "registered": registered,
                "turnout": round(total * 100 / registered) if registered else 0,
            }
        )
        totals["votes"] += total
        totals["outstation"] += outstation
    totals["local"] = totals["votes"] - totals["outstation"]

    context = {
        "election": election,
        "rows": rows,
        "totals": totals,
        "pending_changes": ChangeRequest.objects.filter(status="pending").select_related("voter"),
        "pending_temp": TempVotingRequest.objects.filter(status="pending").select_related("voter", "destination_district"),
        "approved_slots": TempVotingRequest.objects.filter(status="approved").select_related("voter", "booth", "destination_district").order_by("booth", "slot_time"),
        "open_complaints": Complaint.objects.filter(status="open").select_related("voter"),
        "unmapped_voters": Voter.objects.filter(rfid_uid__isnull=True).count(),
        "pending_registrations": Voter.objects.filter(registration_status="pending").select_related("constituency"),
    }
    return render(request, "voting/control_room.html", context)
