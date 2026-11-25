"""Settings for the Smart Electoral Management System (academic demo)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
DEBUG = os.environ.get("DEBUG", "1") == "1"
# The ESP32 reaches the server over the local network, so allow any host in the demo.
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "voting",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "electoral.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "electoral.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Demo project: no password rules so demo accounts are easy to type.
AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "home"

# ---- Project settings -------------------------------------------------------
# A temporary-district voting request must be made at least this many days
# before the election date ("one month before").
TEMP_REQUEST_MIN_DAYS = 30

# Outstation quota from the synopsis: at most this many voters from ONE home district
# can be approved for EACH destination district (e.g. 10 Chennai -> Madurai).
TEMP_ROUTE_QUOTA = 10

# False = the booth accepts votes on any day (needed for a classroom demo).
# True  = the booth accepts votes only on the election date.
ENFORCE_ELECTION_DATE = False

# "console" prints notification SMS in the server window.
# "fast2sms" sends a real SMS (needs FAST2SMS_API_KEY and the requests package).
SMS_BACKEND = os.environ.get("SMS_BACKEND", "console")
FAST2SMS_API_KEY = os.environ.get("FAST2SMS_API_KEY", "")

# ---- Email notifications
# Default: emails are printed in the server window.
# For real emails set these environment variables (Gmail needs an "App password"):
#   EMAIL_HOST_USER=yourname@gmail.com   EMAIL_HOST_PASSWORD=<16-letter app password>
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
if EMAIL_HOST_USER:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = "smtp.gmail.com"
    EMAIL_PORT = 587
    EMAIL_USE_TLS = True
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
EMAIL_TIMEOUT = 5   # never let a slow mail server hold up the booth machine
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER or "electoral@localhost"
