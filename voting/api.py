"""JSON API used by the ESP32 booth machine.

POST /api/scan/  {booth_id, key, uid}                -> eligibility + candidate list
POST /api/vote/  {booth_id, key, uid, candidate_no}  -> stores the vote, returns the staff slip
"""
import hmac
import json

from django.db import IntegrityError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import services
from .models import Booth


def _body(request):
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
        return data if isinstance(data, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None


def _booth(data):
    try:
        booth = Booth.objects.select_related("district").get(pk=int(data.get("booth_id")))
    except (TypeError, ValueError, Booth.DoesNotExist):
        return None
    if not hmac.compare_digest(str(data.get("key", "")), booth.device_key):
        return None
    return booth


def _denied(exc):
    return JsonResponse(
        {"ok": False, "code": exc.code, "message": exc.message, "lcd1": exc.lcd1, "lcd2": exc.lcd2}
    )


@csrf_exempt
@require_POST
def scan(request):
    data = _body(request)
    if data is None:
        return JsonResponse({"ok": False, "code": "BAD_JSON", "message": "Invalid JSON"}, status=400)
    booth = _booth(data)
    if booth is None:
        return JsonResponse({"ok": False, "code": "BAD_DEVICE", "message": "Unknown booth or key"}, status=403)
    try:
        election, voter = services.check_eligibility(data.get("uid"), booth)
    except services.VoteDenied as exc:
        return _denied(exc)
    candidates = [
        {"no": c.number, "name": c.name, "party": c.party} for c in services.candidates_for(voter)
    ]
    if not candidates:
        return _denied(services.VoteDenied("NO_CANDIDATES", "No candidates", "No candidates", "for constituency"))
    return JsonResponse(
        {
            "ok": True,
            "voter_name": voter.name,
            "constituency": voter.constituency.name,
            "candidates": candidates,
        }
    )


@csrf_exempt
@require_POST
def vote(request):
    data = _body(request)
    if data is None:
        return JsonResponse({"ok": False, "code": "BAD_JSON", "message": "Invalid JSON"}, status=400)
    booth = _booth(data)
    if booth is None:
        return JsonResponse({"ok": False, "code": "BAD_DEVICE", "message": "Unknown booth or key"}, status=403)
    try:
        log, voter = services.cast_vote(data.get("uid"), booth, data.get("candidate_no"))
    except services.VoteDenied as exc:
        return _denied(exc)
    except IntegrityError:
        return _denied(services.VoteDenied("ALREADY_VOTED", "Already voted", "Already voted", "Thank you"))
    return JsonResponse(
        {
            "ok": True,
            "transaction_id": log.transaction_id,
            "slip": services.make_slip_lines(log, voter, booth),
        }
    )
