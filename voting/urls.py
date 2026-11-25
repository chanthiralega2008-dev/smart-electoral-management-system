from django.contrib.auth import views as auth_views
from django.urls import path

from . import api, views
from .forms import VoterLoginForm

urlpatterns = [
    path("", views.home, name="home"),
    path("how-to-vote/", views.how_to_vote, name="how_to_vote"),
    path("register/", views.register, name="register"),
    path("login/", auth_views.LoginView.as_view(template_name="voting/login.html", authentication_form=VoterLoginForm), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("request/change/", views.change_request_new, name="change_request_new"),
    path("request/complaint/", views.complaint_new, name="complaint_new"),
    path("request/outstation/", views.temp_request_new, name="temp_request_new"),
    path("control-room/", views.control_room, name="control_room"),
    path("api/scan/", api.scan, name="api_scan"),
    path("api/vote/", api.vote, name="api_vote"),
]
