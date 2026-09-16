"""Resolve the JWT signing secret.

An explicit SECRET_KEY always wins. Without one the key is generated once and
kept in the data volume, so an appliance-style install (Unraid's "Add
Container", or docker compose with no .env) comes up working without handing
every deployment the same hard-coded secret — and sessions still survive a
restart, which a per-boot random key would not.
"""

import os
import secrets
import stat
import sys

# Values that mean "nobody actually chose a key"
PLACEHOLDERS = {'', 'change-me-in-production', 'your-secret-key-here', 'changeme'}

KEY_FILENAME = 'secret_key'


def _read_key(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except (FileNotFoundError, IsADirectoryError):
        return ''
    except OSError as e:
        raise RuntimeError(f'Cannot read {path}: {e}') from e


def _write_key(path, key):
    """Write the key 0600, atomically, so a crash can't leave a partial key."""
    tmp = f'{path}.{os.getpid()}.tmp'
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'w') as f:
                f.write(key)
        except BaseException:
            os.unlink(tmp)
            raise
        os.replace(tmp, path)
    except OSError as e:
        raise RuntimeError(f'Cannot write {path}: {e}') from e


def load_or_create(key_path):
    """Return the persisted key, generating and storing one if absent."""
    existing = _read_key(key_path)
    if existing:
        return existing

    parent = os.path.dirname(key_path) or '.'
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as e:
        raise RuntimeError(f'Cannot create {parent}: {e}') from e

    _write_key(key_path, secrets.token_hex(32))
    # Re-read so concurrent starts converge on whichever key landed on disk.
    key = _read_key(key_path)
    if not key:
        raise RuntimeError(f'Wrote {key_path} but read it back empty')
    return key


def resolve(env=None, database_path=None):
    """Work out the signing key. Returns (key, source) or exits on failure."""
    env = os.environ if env is None else env

    explicit = (env.get('SECRET_KEY') or '').strip()
    if explicit and explicit not in PLACEHOLDERS:
        return explicit, 'environment'

    key_path = (env.get('SECRET_KEY_FILE') or '').strip()
    if not key_path:
        db = database_path or env.get('DATABASE_PATH') or '/data/calendar.db'
        key_path = os.path.join(os.path.dirname(db) or '.', KEY_FILENAME)

    try:
        key = load_or_create(key_path)
    except RuntimeError as e:
        print(
            f'FATAL: no SECRET_KEY set and the generated one could not be stored.\n'
            f'       {e}\n'
            f'       Make the data volume writable, or set SECRET_KEY explicitly.\n'
            f'       (A key that cannot be persisted would log everyone out on every restart.)',
            file=sys.stderr,
        )
        sys.exit(1)

    return key, key_path


def describe(source):
    if source == 'environment':
        return 'SECRET_KEY: using the value from the environment'
    return f'SECRET_KEY: not set — using the generated key at {source}'


def key_is_private(key_path):
    """True when the key file is not readable by group or others."""
    try:
        mode = os.stat(key_path).st_mode
    except OSError:
        return False
    return not mode & (stat.S_IRWXG | stat.S_IRWXO)
