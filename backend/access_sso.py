"""Optional single sign-on through Cloudflare Access.

When Gamendar sits behind a Cloudflare Access application, Cloudflare adds a
signed JWT to every request it lets through (the Cf-Access-Jwt-Assertion
header). If both settings below are present we verify that token against the
team's public keys and sign in the active user whose email matches.

    team domain  e.g. yourteam.cloudflareaccess.com
    AUD tag      the application's "Application Audience (AUD) Tag"

Admins set these in Admin → Single sign-on (stored in the settings table).
The CF_ACCESS_TEAM_DOMAIN / CF_ACCESS_AUD environment variables, if set,
take precedence and lock the admin fields. Leave either value empty and this
is switched off. Password sign-in always keeps working (for example on the
LAN address, which bypasses Cloudflare).

Only the verified JWT is trusted. The plain Cf-Access-Authenticated-User-Email
header is never used, because anyone who can reach the container port directly
can set it.
"""
import os
import re

import jwt
from flask import has_app_context
from jwt import PyJWKClient

from database import get_db

# Cloudflare's key endpoint is fetched inline while handling a request, and the
# container runs a single worker, so a slow endpoint would stall every other
# request too — including the password sign-in that is meant to be the fallback
# when Cloudflare is having a bad day. Give up quickly instead of hanging.
JWKS_TIMEOUT_SECONDS = 5

_TEAM_RE = re.compile(r'^[a-z0-9][a-z0-9-]*\.cloudflareaccess\.com$')
_AUD_RE = re.compile(r'^[0-9a-f]{32,128}$')
_jwks_clients = {}


def clean_team(value):
    """Normalise "yourteam", "https://yourteam.cloudflareaccess.com/" etc. Raises ValueError if invalid."""
    team = re.sub(r'^https?://', '', str(value or '').strip().lower()).split('/')[0]
    if team and '.' not in team:
        team += '.cloudflareaccess.com'
    if team and not _TEAM_RE.match(team):
        raise ValueError('Team domain should look like yourteam.cloudflareaccess.com')
    return team


def clean_aud(value):
    aud = str(value or '').strip().lower()
    if aud and not _AUD_RE.match(aud):
        raise ValueError("The AUD tag is the long hex string on the Access application's overview page")
    return aud


def env_locked():
    """True when the environment variables decide the settings (the admin fields are read-only)."""
    return bool(os.environ.get('CF_ACCESS_TEAM_DOMAIN', '').strip() or os.environ.get('CF_ACCESS_AUD', '').strip())


def raw_settings():
    """(team, aud) as configured, possibly empty, from the environment or the settings table."""
    if env_locked():
        team, aud = os.environ.get('CF_ACCESS_TEAM_DOMAIN', ''), os.environ.get('CF_ACCESS_AUD', '')
    elif has_app_context():
        rows = dict(get_db().execute(
            "SELECT key, value FROM settings WHERE key IN ('cf_access_team_domain', 'cf_access_aud')"
        ).fetchall())
        team, aud = rows.get('cf_access_team_domain', ''), rows.get('cf_access_aud', '')
    else:
        team, aud = '', ''
    return team, aud


def settings():
    """(team_domain, aud) when SSO is configured correctly, otherwise None."""
    team, aud = raw_settings()
    try:
        team, aud = clean_team(team), clean_aud(aud)
    except ValueError as e:
        print(f'WARNING: Cloudflare Access settings are invalid ({e}); single sign-on is off.')
        return None
    if not team or not aud:
        return None
    return team, aud


def check_team_keys(team):
    """Fetch the team's signing keys once, so a typo in the team domain is caught when saving."""
    PyJWKClient(f'https://{team}/cdn-cgi/access/certs',
                timeout=JWKS_TIMEOUT_SECONDS).get_signing_keys()


def enabled():
    return settings() is not None


def warm_keys():
    """Populate the key cache at startup, so no sign-in pays for the first fetch.

    Bounded by JWKS_TIMEOUT_SECONDS, and failure is not fatal: the keys are
    fetched on demand instead.
    """
    cfg = settings()
    if not cfg:
        return
    try:
        _jwks(cfg[0]).get_signing_keys()
    except Exception as e:
        print(f'WARNING: could not pre-fetch Cloudflare Access keys ({e}); '
              'the first sign-in will fetch them instead.')


def _jwks(team):
    client = _jwks_clients.get(team)
    if client is None:
        # PyJWKClient caches the key set and refetches when it sees an unknown key id (key rotation).
        client = PyJWKClient(f'https://{team}/cdn-cgi/access/certs', cache_keys=True,
                             lifespan=12 * 3600, timeout=JWKS_TIMEOUT_SECONDS)
        _jwks_clients[team] = client
    return client


def verified_email(request):
    """The email in a valid Cloudflare Access token on this request, lowercased, or None."""
    cfg = settings()
    token = request.headers.get('Cf-Access-Jwt-Assertion', '')
    if not cfg or not token:
        return None
    team, aud = cfg
    try:
        key = _jwks(team).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token, key,
            algorithms=['RS256'],
            audience=aud,
            issuer=f'https://{team}',
            leeway=30,
            options={'require': ['exp', 'iss', 'aud']},
        )
    except Exception as e:
        print(f'Cloudflare Access token rejected: {e}')
        return None
    email = (claims.get('email') or '').strip().lower()  # service tokens carry no email
    return email or None
