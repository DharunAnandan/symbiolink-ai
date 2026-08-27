"""
Configuration for SymbioLink AI.

Local development still defaults to MySQL (via PyMySQL -- see
requirements.txt), in separate databases ("symbiolink_dev" vs "symbiolink")
so the two environments can never collide. Production, when deployed to a
host like Render, instead reads a single DATABASE_URL the platform injects
for you -- Render's managed database is Postgres, and this project's only
DB access is plain SQLAlchemy ORM (models.py) with no raw/dialect-specific
SQL anywhere, so pointing it at Postgres instead of MySQL needed no query
changes, just this file plus requirements.txt's psycopg2-binary. The
automated test suite is the one exception and stays on in-memory SQLite --
see TestingConfig below for why.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _normalize_db_url(url):
    """Render (and several other hosts, following Heroku's old convention)
    hand out a DATABASE_URL that starts "postgres://", but SQLAlchemy 1.4+
    only recognizes the "postgresql://" scheme and raises
    NoSuchModuleError on the old one. Rewriting just the scheme prefix is
    the standard fix -- the rest of the URL (user/pass/host/db) is untouched.
    A None url (nothing set yet) passes through unchanged."""
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


class Config:
    """Base configuration."""
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'symbiolink-ai-change-in-production'
    
    # Database configuration
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # pool_pre_ping makes SQLAlchemy test a pooled connection with a cheap
    # "SELECT 1" before handing it to a request, and transparently reconnect
    # if MySQL had silently closed it (e.g. after the machine slept, or the
    # MySQL service restarted) -- without this, that request fails outright
    # instead of just reconnecting, which looks like random slow/broken page
    # loads rather than a clean error.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
    }

    # Twilio configuration
    TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
    TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
    TWILIO_PHONE_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER')
    
    # Razorpay configuration
    RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID')
    RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET')
    
    # Google Maps configuration
    GOOGLE_MAPS_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY')

    # Email (SMTP) configuration -- same "real if configured, simulated
    # console-log otherwise" pattern as Twilio/Razorpay above (see
    # email_service.py). Any standard SMTP provider works (Gmail app
    # password, SendGrid, Mailgun, your college's SMTP relay, etc.).
    SMTP_HOST = os.environ.get('SMTP_HOST')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', '587') or 587)
    SMTP_USERNAME = os.environ.get('SMTP_USERNAME')
    SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD')
    SMTP_FROM_ADDRESS = os.environ.get('SMTP_FROM_ADDRESS') or os.environ.get('SMTP_USERNAME')

    # Transport cost configuration
    TRANSPORT_RS_PER_KM_SOLO = 45
    TRANSPORT_RS_PER_KM_POOLED = 18
    
    # Matching configuration
    MAX_RADIUS_KM = 3.0
    MAX_HOPS = 3
    LOW_VOLUME_THRESHOLD_KG = 100
    POOL_RADIUS_KM = 1.8


class DevelopmentConfig(Config):
    """Development configuration -- MySQL by default now (previously SQLite).

    Points at a separate "symbiolink_dev" database (not "symbiolink", which
    ProductionConfig uses) so day-to-day development can never accidentally
    read/write production data even if both configs somehow pointed at the
    same MySQL server. Override with DEV_DATABASE_URL in .env if your local
    MySQL user/password/host differ from the placeholder below -- see
    .env.example.
    """
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = _normalize_db_url(
        os.environ.get('DEV_DATABASE_URL')
        or 'mysql+pymysql://root:password@localhost/symbiolink_dev'
    )


class ProductionConfig(Config):
    """Production configuration.

    Reads DATABASE_URL from the environment -- this is the one variable a
    host like Render sets for you automatically once you attach its managed
    Postgres database, no manual copy-pasting of host/user/password needed.
    Falls back to a local MySQL connection string (matching
    DevelopmentConfig's default) only so this class still has a sane value
    if FLASK_ENV=production is set without DATABASE_URL configured -- e.g.
    running production settings locally against your own MySQL instance,
    the way this project worked before a real host was added. Either a
    Postgres or a MySQL URL works fine here; see this file's module
    docstring for why the ORM doesn't care which.
    """
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = _normalize_db_url(
        os.environ.get('DATABASE_URL')
        or 'mysql+pymysql://root:password@localhost/symbiolink'
    )


class TestingConfig(Config):
    """Testing configuration -- deliberately still in-memory SQLite, not
    MySQL, even though dev/production now are. This is a common, intentional
    split: the test suite (tests/conftest.py) needs a database that exists
    instantly with no setup, is wiped clean automatically between runs, and
    never requires a MySQL server to be installed/running just to `pytest`.
    None of the app's code is SQLite- or MySQL-specific either way (see this
    file's module docstring), so the tests are exercising the same ORM code
    path regardless of which database backs it."""
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}