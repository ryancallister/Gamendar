"""Web Push notifications to installed PWAs.

Messages reuse the Signal templates so a group only writes its wording once;
the first line of a rendered template becomes the notification title and the
rest becomes its body.

Opting in is per device, not per account: subscribing is something a browser
does, so someone with a phone and a laptop has one subscription each and can
turn either off on its own. Having a subscription IS the opt-in, which keeps
the toggle and the stored state from drifting apart.

Sends happen on a background thread. The container runs a single worker, so
posting to several push endpoints inline would stall every other request.
"""
import base64
import json
import os
import queue
import threading

from py_vapid import Vapid01
from cryptography.hazmat.primitives import serialization
from pywebpush import WebPushException, webpush

from signal_service import TEMPLATE_DEFAULTS, get_setting, render_template

KEY_FILENAME = 'vapid_key.pem'
SEND_TIMEOUT_SECONDS = 10
# How long a push service should hold a notification for a device that is
# offline. The default of 0 means "deliver right now or throw it away", which
# loses every notification sent while a phone is asleep. These are about
# today's plans, so holding them for a few hours is right and a day is not.
TTL_SECONDS = 6 * 3600
QUEUE_MAX = 1000
DEFAULT_CONTACT = 'mailto:gamendar@localhost'

_vapid_lock = threading.Lock()
_vapid = None
_queue = queue.Queue(maxsize=QUEUE_MAX)
_worker_started = False
_worker_lock = threading.Lock()


# ── Keys ──────────────────────────────────────────────────────────

def key_path():
    db = os.environ.get('DATABASE_PATH') or '/data/calendar.db'
    return os.environ.get('VAPID_KEY_FILE') or os.path.join(os.path.dirname(db) or '.', KEY_FILENAME)


def _load_vapid():
    """The server's VAPID identity, generated once and kept beside the database.

    Regenerating it would silently invalidate every existing subscription, so
    it is written atomically and never overwritten.
    """
    global _vapid
    with _vapid_lock:
        if _vapid is not None:
            return _vapid
        path = key_path()
        if not os.path.isfile(path):
            os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
            fresh = Vapid01()
            fresh.generate_keys()
            tmp = f'{path}.{os.getpid()}.tmp'
            fresh.save_key(tmp)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
            print(f'Push: generated a VAPID key at {path}')
        _vapid = Vapid01.from_file(path)
        return _vapid


def public_key():
    """The applicationServerKey the browser needs, base64url with no padding."""
    try:
        raw = _load_vapid().public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    except Exception as e:
        print(f'Push: cannot load the VAPID key ({e}); push is unavailable.')
        return None
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def available():
    return public_key() is not None


def contact(db=None):
    value = (get_setting(db, 'push_contact') if db is not None else None) or DEFAULT_CONTACT
    return value if str(value).startswith(('mailto:', 'https:')) else DEFAULT_CONTACT


# ── Subscriptions ─────────────────────────────────────────────────

def save_subscription(db, user_id, subscription, user_agent=''):
    """Store (or re-point) one browser's subscription. Returns False if malformed."""
    endpoint = (subscription or {}).get('endpoint')
    keys = (subscription or {}).get('keys') or {}
    p256dh, auth = keys.get('p256dh'), keys.get('auth')
    if not endpoint or not p256dh or not auth:
        return False
    db.execute(
        'INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, user_agent) '
        'VALUES (?, ?, ?, ?, ?) '
        'ON CONFLICT(endpoint) DO UPDATE SET user_id = excluded.user_id, '
        'p256dh = excluded.p256dh, auth = excluded.auth, user_agent = excluded.user_agent',
        (user_id, str(endpoint)[:500], str(p256dh)[:200], str(auth)[:200], str(user_agent or '')[:200])
    )
    db.commit()
    return True


def delete_subscription(db, endpoint):
    db.execute('DELETE FROM push_subscriptions WHERE endpoint = ?', (str(endpoint or '')[:500],))
    db.commit()


def subscription_count(db, user_id=None):
    if user_id is None:
        return db.execute('SELECT COUNT(*) FROM push_subscriptions').fetchone()[0]
    return db.execute(
        'SELECT COUNT(*) FROM push_subscriptions WHERE user_id = ?', (user_id,)).fetchone()[0]


def anyone_subscribed(db):
    return subscription_count(db) > 0


# ── Messages ──────────────────────────────────────────────────────

def split_message(text):
    """A rendered template becomes (title, body) at its first line break."""
    text = (text or '').strip()
    if not text:
        return 'Gamendar', ''
    head, _, rest = text.partition('\n')
    return head.strip()[:120], rest.strip()[:600]


def render(db, template_key, **values):
    template = get_setting(db, template_key) or TEMPLATE_DEFAULTS.get(template_key, '')
    return split_message(render_template(template, **values))


# ── Sending ───────────────────────────────────────────────────────

def _enqueue(item):
    try:
        _queue.put_nowait(item)
    except queue.Full:
        print('Push: send queue is full; dropping a notification.')


def notify_all(db, title, body, url='/'):
    """Everyone with a subscription. Rendering happens here; sending does not."""
    _enqueue({'user_ids': None, 'title': title, 'body': body, 'url': url,
              'contact': contact(db)})


def notify_users(db, user_ids, title, body, url='/'):
    ids = [int(u) for u in user_ids]
    if not ids:
        return
    _enqueue({'user_ids': ids, 'title': title, 'body': body, 'url': url,
              'contact': contact(db)})


def notify_all_from_template(db, template_key, url='/', **values):
    title, body = render(db, template_key, **values)
    notify_all(db, title, body, url)


def notify_users_from_template(db, user_ids, template_key, url='/', **values):
    title, body = render(db, template_key, **values)
    notify_users(db, user_ids, title, body, url)


def _targets(db, user_ids):
    sql = ('SELECT s.* FROM push_subscriptions s '
           'JOIN users u ON s.user_id = u.id WHERE u.is_active = 1')
    params = []
    if user_ids is not None:
        sql += f" AND s.user_id IN ({','.join('?' * len(user_ids))})"
        params = list(user_ids)
    return db.execute(sql, params).fetchall()


def send_now(db, item):
    """Deliver one queued item. Returns (sent, failed). Prunes dead subscriptions."""
    try:
        # Make sure the key exists: it is created lazily, and a send that
        # quietly did nothing because nothing had asked for the public key
        # yet would be invisible.
        _load_vapid()
        private_key = key_path()
    except Exception as e:
        print(f'Push: no VAPID key, cannot send ({e}).')
        return 0, 0

    payload = json.dumps({'title': item['title'], 'body': item['body'], 'url': item.get('url', '/')})
    sent = failed = 0
    for row in _targets(db, item['user_ids']):
        try:
            webpush(
                subscription_info={
                    'endpoint': row['endpoint'],
                    'keys': {'p256dh': row['p256dh'], 'auth': row['auth']},
                },
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={'sub': item['contact']},
                ttl=TTL_SECONDS,
                timeout=SEND_TIMEOUT_SECONDS,
            )
            sent += 1
        except WebPushException as e:
            status = getattr(e.response, 'status_code', None)
            if status in (404, 410):
                # The browser threw the subscription away; stop trying forever.
                delete_subscription(db, row['endpoint'])
                print(f'Push: dropped an expired subscription ({status}).')
            else:
                failed += 1
                print(f'Push: send failed ({status or e}).')
        except Exception as e:
            failed += 1
            print(f'Push: send error ({e}).')
    return sent, failed


def start_push_worker(app):
    """Drain the queue on a daemon thread, so no request waits on a push send."""
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True

    def run():
        while True:
            item = _queue.get()
            try:
                with app.app_context():
                    from database import get_db
                    send_now(get_db(), item)
            except Exception as e:
                print(f'Push worker error: {e}')
            finally:
                _queue.task_done()

    threading.Thread(target=run, daemon=True).start()
