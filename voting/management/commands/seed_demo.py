"""python manage.py seed_demo  -> creates demo districts, candidates, voters, booths and an admin."""
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from voting.models import Booth, Candidate, Constituency, District, Election, Voter

DATA = {
    "Madurai": ["Madurai Central", "Madurai East"],
    "Chennai": ["T Nagar", "Egmore"],
    "Coimbatore": ["Coimbatore North", "Coimbatore South"],
    "Virudhunagar": ["Virudhunagar", "Sivakasi"],
}
# Fictional names and parties for the demo only.
CANDIDATES = [("Arun Kumar", "Party Alpha"), ("Meena Devi", "Party Beta"), ("Ravi Shankar", "Independent")]

VOTERS = [
    # voter_id, name, phone, constituency, rfid uid (replace with your real card UIDs)
    ("DEMO001", "Priya S", "9876500001", "Madurai Central", "AA11BB01"),
    ("DEMO002", "Kavya R", "9876500002", "Madurai East", "AA11BB02"),
    ("DEMO003", "Divya M", "9876500003", "T Nagar", "AA11BB03"),
    ("DEMO004", "Lakshmi K", "9876500004", "Egmore", "AA11BB04"),
    ("DEMO005", "Meena T", "9876500005", "Coimbatore North", "AA11BB05"),
]


class Command(BaseCommand):
    help = "Create demo data for the Smart Electoral Management System"

    def handle(self, *args, **options):
        admin_user, created = User.objects.get_or_create(username="admin", defaults={"is_staff": True, "is_superuser": True})
        if created:
            admin_user.set_password("admin123")
            admin_user.save()

        for dist_name, consts in DATA.items():
            dist, _ = District.objects.get_or_create(name=dist_name)
            for cname in consts:
                const, _ = Constituency.objects.get_or_create(name=cname, defaults={"district": dist})
                for i, (cand, party) in enumerate(CANDIDATES, start=1):
                    Candidate.objects.get_or_create(
                        constituency=const, number=i, defaults={"name": f"{cand} ({cname[:3].upper()})", "party": party}
                    )
            Booth.objects.get_or_create(name=f"Booth {dist_name[:3].upper()}-01", district=dist)
            # two booths per constituency (one ESP32 machine = one booth)
            for cname in consts:
                const = Constituency.objects.get(name=cname)
                for n in (1, 2):
                    Booth.objects.get_or_create(name=f"{cname} B{n}", district=dist, constituency=const)

        Election.objects.update_or_create(
            name="Demo Election 2026",
            defaults={"vote_date": timezone.localdate() + timedelta(days=45), "is_active": True},
        )

        for vid, name, phone, cname, uid in VOTERS:
            const = Constituency.objects.get(name=cname)
            user, made = User.objects.get_or_create(username=vid)
            if made:
                user.set_password("demo123")
                user.save()
            voter, _ = Voter.objects.get_or_create(
                voter_id=vid,
                defaults=dict(user=user, name=name, phone=phone, address=f"Demo address, {const.district.name}",
                              constituency=const, rfid_uid=uid, date_of_birth=date(2000, 1, 1)),
            )
            if voter.booth_id is None:   # home booth = first booth of the voter's constituency
                voter.booth = Booth.objects.filter(constituency=voter.constituency).order_by("name").first()
                voter.save()

        self.stdout.write(self.style.SUCCESS("Demo data ready.\n"))
        self.stdout.write("Admin login : admin / admin123   (open /admin/ and /control-room/)")
        self.stdout.write("Voter logins: DEMO001..DEMO005 / demo123")
        self.stdout.write("\nBooths (put these two values in the ESP32 code):")
        for b in Booth.objects.all():
            self.stdout.write(f"  BOOTH_ID = {b.id:<3} BOOTH_KEY = \"{b.device_key}\"   {b.name} ({b.district.name})")
