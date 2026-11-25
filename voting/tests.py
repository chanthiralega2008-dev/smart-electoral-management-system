import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import ChangeRequestForm, TempRequestForm
from .models import (
    Booth, Candidate, ChangeRequest, Constituency, District, Election,
    Notification, TempVotingRequest, Vote, VoteLog, Voter,
)


def proof():
    return SimpleUploadedFile("proof.pdf", b"%PDF-1.4 test", content_type="application/pdf")


def photo():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(buf, "PNG")
    return SimpleUploadedFile("photo.png", buf.getvalue(), content_type="image/png")


class BaseCase(TestCase):
    def setUp(self):
        self.madurai = District.objects.create(name="Madurai")
        self.chennai = District.objects.create(name="Chennai")
        self.mad_c = Constituency.objects.create(name="Madurai Central", district=self.madurai)
        self.che_c = Constituency.objects.create(name="T Nagar", district=self.chennai)
        for const in (self.mad_c, self.che_c):
            for i, n in enumerate(["A", "B", "C"], start=1):
                Candidate.objects.create(constituency=const, number=i, name=f"{n}-{const.pk}", party="P")
        self.mad_booth = Booth.objects.create(name="Mad Booth", district=self.madurai)
        self.che_booth = Booth.objects.create(name="Che Booth", district=self.chennai)
        self.election = Election.objects.create(
            name="Test", vote_date=timezone.localdate() + timedelta(days=45), is_active=True
        )
        self.priya = self.make_voter("V1", self.che_c, "AA01")    # Chennai voter
        self.kavya = self.make_voter("V2", self.mad_c, "AA02")    # Madurai voter

    def make_voter(self, vid, const, uid):
        user = User.objects.create_user(vid, password="pw12345")
        return Voter.objects.create(
            user=user, voter_id=vid, name=f"Voter {vid}", phone="9876543210",
            address="addr", constituency=const, rfid_uid=uid,
        )

    def post(self, url_name, booth, **extra):
        payload = {"booth_id": booth.id, "key": booth.device_key, **extra}
        return self.client.post(reverse(url_name), json.dumps(payload), content_type="application/json")


class BoothApiTests(BaseCase):
    def test_local_vote_full_flow(self):
        r = self.post("api_scan", self.mad_booth, uid="aa02").json()
        self.assertTrue(r["ok"])
        self.assertEqual([c["no"] for c in r["candidates"]], [1, 2, 3])
        # only the voter's own constituency candidates
        self.assertTrue(all(str(self.mad_c.pk) in c["name"] for c in r["candidates"]))

        r = self.post("api_vote", self.mad_booth, uid="AA02", candidate_no=2).json()
        self.assertTrue(r["ok"])
        self.assertTrue(r["transaction_id"].startswith("TXN"))
        self.assertTrue(any("Txn ID" in line for line in r["slip"]))
        self.assertFalse(Vote.objects.get().outstation)
        self.assertEqual(VoteLog.objects.count(), 1)
        self.assertTrue(Notification.objects.filter(voter=self.kavya, message__contains="vote was recorded").exists())

        # second attempt is refused
        r = self.post("api_scan", self.mad_booth, uid="AA02").json()
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "ALREADY_VOTED")

    def test_ballot_is_not_linked_to_voter(self):
        self.post("api_vote", self.mad_booth, uid="AA02", candidate_no=1)
        field_names = {f.name for f in Vote._meta.get_fields()}
        self.assertNotIn("voter", field_names)

    def test_unknown_card_and_wrong_booth(self):
        r = self.post("api_scan", self.mad_booth, uid="DEADBEEF").json()
        self.assertEqual(r["code"], "UNKNOWN_CARD")
        r = self.post("api_scan", self.mad_booth, uid="AA01").json()   # Chennai voter at Madurai booth
        self.assertEqual(r["code"], "WRONG_BOOTH")

    def test_invalid_candidate_rejected(self):
        r = self.post("api_vote", self.mad_booth, uid="AA02", candidate_no=9).json()
        self.assertEqual(r["code"], "BAD_CANDIDATE")
        self.assertEqual(Vote.objects.count(), 0)

    def test_bad_device_key(self):
        resp = self.client.post(
            reverse("api_scan"),
            json.dumps({"booth_id": self.mad_booth.id, "key": "wrong", "uid": "AA02"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_outstation_voting(self):
        req = TempVotingRequest.objects.create(
            voter=self.priya, election=self.election, destination_district=self.madurai,
            temp_address="hostel", proof=proof(),
        )
        # not approved yet -> cannot vote in Madurai
        self.assertEqual(self.post("api_scan", self.mad_booth, uid="AA01").json()["code"], "WRONG_BOOTH")

        req.approve()
        req.refresh_from_db()
        self.assertEqual(req.booth, self.mad_booth)
        self.assertIsNotNone(req.slot_time)

        scan = self.post("api_scan", self.mad_booth, uid="AA01").json()
        self.assertTrue(scan["ok"])
        # candidates are still the voter's OWN (Chennai) constituency
        self.assertTrue(all(str(self.che_c.pk) in c["name"] for c in scan["candidates"]))
        self.assertTrue(self.post("api_vote", self.mad_booth, uid="AA01", candidate_no=1).json()["ok"])
        self.assertTrue(Vote.objects.get().outstation)
        # and the home booth is now refused
        self.assertEqual(self.post("api_scan", self.che_booth, uid="AA01").json()["code"], "ALREADY_VOTED")


class QuotaAndRulesTests(BaseCase):
    def test_route_quota_of_ten(self):
        voters = [self.make_voter(f"Q{i}", self.che_c, f"QQ{i:02d}") for i in range(11)]
        reqs = [
            TempVotingRequest.objects.create(
                voter=v, election=self.election, destination_district=self.madurai,
                temp_address="x", proof=proof(),
            )
            for v in voters
        ]
        for r in reqs[:10]:
            r.approve()
        with self.assertRaises(ValueError):
            reqs[10].approve()
        # slots are unique and increasing
        slots = [r.slot_time for r in TempVotingRequest.objects.filter(status="approved").order_by("slot_time")]
        self.assertEqual(len(set(slots)), 10)

    def test_thirty_day_rule(self):
        self.election.vote_date = timezone.localdate() + timedelta(days=10)
        self.election.save()
        form = TempRequestForm(
            {"destination_district": self.madurai.pk, "temp_address": "x"},
            {"proof": proof()}, voter=self.priya,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("at least 30 days", str(form.errors))

    def test_valid_request_accepted(self):
        form = TempRequestForm(
            {"destination_district": self.madurai.pk, "temp_address": "hostel"},
            {"proof": proof()}, voter=self.priya,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_change_request_approval_updates_voter(self):
        cr = ChangeRequest.objects.create(voter=self.kavya, kind="name", new_value="Kavya Rani")
        cr.approve()
        self.kavya.refresh_from_db()
        self.assertEqual(self.kavya.name, "Kavya Rani")
        cr2 = ChangeRequest.objects.create(voter=self.kavya, kind="constituency", new_constituency=self.che_c)
        cr2.approve()
        self.kavya.refresh_from_db()
        self.assertEqual(self.kavya.constituency, self.che_c)

    def test_constituency_change_is_not_offered(self):
        form = ChangeRequestForm({"kind": "constituency"}, voter=self.kavya)
        self.assertFalse(form.is_valid())
        self.assertIn("kind", form.errors)
        self.assertNotIn("new_constituency", form.fields)

    def test_address_change_needs_proof(self):
        form = ChangeRequestForm({"kind": "address", "new_value": "New street"}, voter=self.kavya)
        self.assertFalse(form.is_valid())
        self.assertIn("proof", form.errors)
        form = ChangeRequestForm(
            {"kind": "address", "new_value": "New street"}, {"proof": proof()}, voter=self.kavya
        )
        self.assertTrue(form.is_valid(), form.errors)
        # a name change needs no proof
        form = ChangeRequestForm({"kind": "name", "new_value": "Kavya R"}, voter=self.kavya)
        self.assertTrue(form.is_valid(), form.errors)


class PageTests(BaseCase):
    def test_public_pages(self):
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)
        resp = self.client.get(reverse("how_to_vote"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "தமிழ்")
        self.assertContains(resp, "Confirm with #")

    def test_register_and_dashboard(self):
        resp = self.client.post(reverse("register"), {
            "voter_id": "newv1", "name": "New Voter", "phone": "9000000000", "email": "new1@example.com", "address": "somewhere",
            "constituency": self.mad_c.pk, "password1": "pw12345", "password2": "pw12345",
            "date_of_birth": "2000-01-01", "age_proof": proof(),
            "gender": "F", "relation_type": "father", "relative_name": "R", "age_proof_type": "marksheet",
            "address_proof_type": "bill", "address_proof": proof(), "photo": photo(), "declaration": "on",
        })
        self.assertRedirects(resp, reverse("dashboard"))
        self.assertTrue(Voter.objects.filter(voter_id="NEWV1", rfid_uid__isnull=True).exists())
        self.assertContains(self.client.get(reverse("dashboard")), "Not mapped yet")
        self.assertContains(self.client.get(reverse("dashboard")), "Registration pending")

    def test_voter_pages_and_staff_control_room(self):
        self.client.login(username="V1", password="pw12345")
        for name in ("dashboard", "change_request_new", "complaint_new", "temp_request_new"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertNotEqual(self.client.get(reverse("control_room")).status_code, 200)   # not staff
        staff = User.objects.create_user("boss", password="pw", is_staff=True)
        self.client.force_login(staff)
        self.post("api_vote", self.mad_booth, uid="AA02", candidate_no=1)
        resp = self.client.get(reverse("control_room"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Other-place")


class AgeVerificationTests(BaseCase):
    def reg_data(self, dob):
        return {
            "voter_id": "NEWV2", "name": "N", "phone": "9000000001", "email": "n2@example.com", "address": "a",
            "constituency": self.mad_c.pk, "date_of_birth": dob,
            "password1": "pw12345", "password2": "pw12345", "age_proof": proof(),
            "gender": "F", "relation_type": "father", "relative_name": "R", "age_proof_type": "marksheet",
            "address_proof_type": "bill", "address_proof": proof(), "photo": photo(), "declaration": "on",
        }

    def test_approval_sends_email(self):
        from django.core import mail

        dob = (timezone.localdate() - timedelta(days=365 * 25)).isoformat()
        self.client.post(reverse("register"), self.reg_data(dob))
        voter = Voter.objects.get(voter_id="NEWV2")
        self.assertEqual(voter.user.email, "n2@example.com")
        with self.captureOnCommitCallbacks(execute=True):
            voter.approve_registration()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["n2@example.com"])
        self.assertIn("approved", mail.outbox[0].body)

    def test_under_18_registration_rejected(self):
        dob = (timezone.localdate() - timedelta(days=365 * 16)).isoformat()
        resp = self.client.post(reverse("register"), self.reg_data(dob))
        self.assertEqual(resp.status_code, 200)           # form shown again with an error
        self.assertFalse(Voter.objects.filter(voter_id="NEWV2").exists())

    def test_pending_voter_denied_at_booth_until_admin_approves(self):
        dob = (timezone.localdate() - timedelta(days=365 * 25)).isoformat()
        resp = self.client.post(reverse("register"), self.reg_data(dob))
        self.assertRedirects(resp, reverse("dashboard"))
        voter = Voter.objects.get(voter_id="NEWV2")
        self.assertEqual(voter.registration_status, "pending")
        voter.rfid_uid = "NN01"
        voter.save()
        self.assertEqual(self.post("api_scan", self.mad_booth, uid="NN01").json()["code"], "NOT_APPROVED")
        # a pending voter cannot send outstation requests either
        self.assertRedirects(self.client.get(reverse("temp_request_new")), reverse("dashboard"))
        voter.approve_registration()
        self.assertTrue(self.post("api_scan", self.mad_booth, uid="NN01").json()["ok"])

    def test_turning_18_on_election_day_is_allowed(self):
        d = self.election.vote_date
        voter = self.make_voter("T18", self.mad_c, "T18X")
        voter.date_of_birth = d.replace(year=d.year - 18)
        voter.registration_status = "pending"
        voter.save()
        voter.approve_registration()
        self.assertEqual(voter.registration_status, "approved")

    def test_one_day_short_of_18_cannot_be_approved(self):
        d = self.election.vote_date
        voter = self.make_voter("T17", self.mad_c, "T17X")
        voter.date_of_birth = d.replace(year=d.year - 18) + timedelta(days=1)
        voter.registration_status = "pending"
        voter.save()
        with self.assertRaises(ValueError):
            voter.approve_registration()
        voter.refresh_from_db()
        self.assertEqual(voter.registration_status, "pending")


class RegistrationDocumentTests(BaseCase):
    def reg_data(self, **extra):
        data = {
            "voter_id": "NEWV3", "name": "N", "phone": "9000000002", "email": "n3@example.com",
            "address": "a", "constituency": self.mad_c.pk, "date_of_birth": "2000-01-01",
            "password1": "pw12345", "password2": "pw12345",
            "age_proof": proof(),
            "gender": "F", "relation_type": "father", "relative_name": "R", "age_proof_type": "marksheet",
            "address_proof_type": "bill", "address_proof": proof(), "photo": photo(), "declaration": "on",
        }
        data.update(extra)
        return data

    def test_registration_needs_address_proof_and_photo(self):
        for missing in ("address_proof", "photo", "declaration"):
            data = self.reg_data()
            del data[missing]
            resp = self.client.post(reverse("register"), data)
            self.assertEqual(resp.status_code, 200, missing)
            self.assertFalse(Voter.objects.filter(voter_id="NEWV3").exists(), missing)
        data = self.reg_data()
        del data["age_proof"]
        resp = self.client.post(reverse("register"), data)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Voter.objects.filter(voter_id="NEWV3").exists())

    def test_documents_and_photo_are_stored(self):
        resp = self.client.post(reverse("register"), self.reg_data())
        self.assertRedirects(resp, reverse("dashboard"))
        voter = Voter.objects.get(voter_id="NEWV3")
        self.assertTrue(voter.age_proof)
        self.assertTrue(voter.address_proof)
        self.assertTrue(voter.photo)
        self.assertFalse(voter.aadhaar_proof)   # Aadhaar is optional


class BoothPerConstituencyTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.mad_east = Constituency.objects.create(name="Madurai East", district=self.madurai)
        self.b1 = Booth.objects.create(name="MC B1", district=self.madurai, constituency=self.mad_c)
        self.b2 = Booth.objects.create(name="MC B2", district=self.madurai, constituency=self.mad_c)
        self.e1 = Booth.objects.create(name="ME B1", district=self.madurai, constituency=self.mad_east)
        for const in (self.mad_east,):
            for i, n in enumerate(["A", "B", "C"], start=1):
                Candidate.objects.create(constituency=const, number=i, name=f"{n}-{const.pk}", party="P")
        self.kavya.booth = self.b1
        self.kavya.save()

    def test_booth_save_syncs_district(self):
        self.assertEqual(self.b1.district, self.madurai)

    def test_voter_must_use_own_constituency_and_assigned_booth(self):
        self.assertTrue(self.post("api_scan", self.b1, uid="AA02").json()["ok"])
        # the district-level booth (no constituency) still serves every voter of the district
        self.assertTrue(self.post("api_scan", self.mad_booth, uid="AA02").json()["ok"])
        r = self.post("api_scan", self.b2, uid="AA02").json()          # same constituency, other booth
        self.assertEqual(r["code"], "WRONG_BOOTH")
        self.assertEqual(r["lcd1"], "Wrong booth")
        r = self.post("api_scan", self.e1, uid="AA02").json()          # other constituency
        self.assertEqual(r["code"], "WRONG_BOOTH")
        self.assertEqual(r["lcd1"], "Wrong constituency")

    def test_voter_without_assigned_booth_can_use_any_booth_of_own_constituency(self):
        self.kavya.booth = None
        self.kavya.save()
        self.assertTrue(self.post("api_scan", self.b2, uid="AA02").json()["ok"])
        self.assertEqual(self.post("api_scan", self.e1, uid="AA02").json()["code"], "WRONG_BOOTH")

    def test_outstation_voter_can_use_any_booth_of_destination_district(self):
        req = TempVotingRequest.objects.create(
            voter=self.priya, election=self.election, destination_district=self.madurai,
            temp_address="hostel", proof=proof(),
        )
        req.approve()
        for booth in (self.b1, self.b2, self.e1):
            self.assertTrue(self.post("api_scan", booth, uid="AA01").json()["ok"], booth.name)

    def test_booth_change_request(self):
        # the form lists only the other booths of the voter's own constituency
        form = ChangeRequestForm(voter=self.kavya)
        self.assertEqual(set(form.fields["new_booth"].queryset), {self.b2})
        # a reason is required
        bad = ChangeRequestForm({"kind": "booth", "new_booth": self.b2.pk}, voter=self.kavya)
        self.assertFalse(bad.is_valid())
        self.assertIn("new_value", bad.errors)
        ok = ChangeRequestForm(
            {"kind": "booth", "new_booth": self.b2.pk, "new_value": "Ill health, B2 is nearer"}, voter=self.kavya
        )
        self.assertTrue(ok.is_valid(), ok.errors)
        # after approval only the new booth works
        cr = ChangeRequest.objects.create(
            voter=self.kavya, kind="booth", new_booth=self.b2, new_value="Ill health"
        )
        cr.approve()
        self.kavya.refresh_from_db()
        self.assertEqual(self.kavya.booth, self.b2)
        self.assertTrue(self.post("api_scan", self.b2, uid="AA02").json()["ok"])
        self.assertEqual(self.post("api_scan", self.b1, uid="AA02").json()["code"], "WRONG_BOOTH")

    def test_booth_of_another_constituency_cannot_be_approved(self):
        cr = ChangeRequest.objects.create(voter=self.kavya, kind="booth", new_booth=self.e1, new_value="x")
        with self.assertRaises(ValueError):
            cr.approve()

    def test_constituency_change_gives_booth_of_new_constituency(self):
        cr = ChangeRequest.objects.create(voter=self.kavya, kind="constituency", new_constituency=self.mad_east)
        cr.approve()
        self.kavya.refresh_from_db()
        self.assertEqual(self.kavya.booth, self.e1)

    def test_registration_approval_assigns_first_booth(self):
        user = User.objects.create_user("NEWV4", password="pw12345")
        voter = Voter.objects.create(
            user=user, voter_id="NEWV4", name="N", phone="9000000003", address="a",
            constituency=self.mad_c, date_of_birth=timezone.localdate() - timedelta(days=365 * 25),
            registration_status="pending",
        )
        voter.approve_registration()
        voter.refresh_from_db()
        self.assertEqual(voter.booth, self.b1)
