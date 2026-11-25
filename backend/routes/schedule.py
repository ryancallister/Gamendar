"""Each person's recurring blocked-out time, and applying it to a week.

Blocks are private: every query here is scoped to the signed-in user, so a
label like "Therapy" is never visible to anyone else. What the rest of the
group sees is only the availability it produces.
"""
from flask import Blueprint, request, jsonify
from datetime import date, timedelta

from database import get_db
from auth_utils import token_required
import schedule_blocks as sb
from signal_service import check_and_notify_all_available
from signal_service import record_change as signal_record_change
from discord_service import check_and_notify_all_available as discord_check_all_available
import push_triggers

schedule_bp = Blueprint('schedule', __name__)


def _as_block(row):
    return {
        'id': row['id'],
        'label': row['label'],
        'days': sb.parse_days(row['days']),
        'start': sb.parse_hhmm(row['start_time']),
        'end': sb.parse_hhmm(row['end_time']),
    }


def _my_blocks(db, user_id):
    rows = db.execute(
        'SELECT * FROM schedule_blocks WHERE user_id = ? ORDER BY start_time, id', (user_id,)
    ).fetchall()
    return [_as_block(r) for r in rows]


def _public(block):
    return {
        'id': block['id'],
        'label': block['label'],
        'days': block['days'],
        'start_time': sb.format_hhmm(block['start']),
        'end_time': sb.format_hhmm(block['end']),
        'summary': sb.describe(block),
    }


@schedule_bp.route('/', methods=['GET'])
@token_required
def list_blocks(current_user):
    return jsonify([_public(b) for b in _my_blocks(get_db(), current_user['id'])])


@schedule_bp.route('/', methods=['POST'])
@token_required
def create_block(current_user):
    data = request.get_json(silent=True) or {}
    try:
        block = sb.clean(data.get('label'), data.get('days'),
                         data.get('start_time'), data.get('end_time'))
    except sb.BadBlock as e:
        return jsonify({'error': str(e)}), 400
    db = get_db()
    cursor = db.execute(
        'INSERT INTO schedule_blocks (user_id, label, days, start_time, end_time) '
        'VALUES (?, ?, ?, ?, ?)',
        (current_user['id'], block['label'], ','.join(str(d) for d in block['days']),
         sb.format_hhmm(block['start']), sb.format_hhmm(block['end']))
    )
    db.commit()
    block['id'] = cursor.lastrowid
    return jsonify(_public(block)), 201


@schedule_bp.route('/<int:block_id>', methods=['PUT'])
@token_required
def update_block(current_user, block_id):
    db = get_db()
    existing = db.execute(
        'SELECT * FROM schedule_blocks WHERE id = ? AND user_id = ?',
        (block_id, current_user['id'])
    ).fetchone()
    if not existing:
        return jsonify({'error': 'Not found'}), 404

    data = request.get_json(silent=True) or {}
    try:
        block = sb.clean(
            data.get('label', existing['label']),
            data.get('days', existing['days']),
            data.get('start_time', existing['start_time']),
            data.get('end_time', existing['end_time']),
        )
    except sb.BadBlock as e:
        return jsonify({'error': str(e)}), 400

    db.execute(
        'UPDATE schedule_blocks SET label = ?, days = ?, start_time = ?, end_time = ? '
        'WHERE id = ? AND user_id = ?',
        (block['label'], ','.join(str(d) for d in block['days']),
         sb.format_hhmm(block['start']), sb.format_hhmm(block['end']),
         block_id, current_user['id'])
    )
    db.commit()
    block['id'] = block_id
    return jsonify(_public(block))


@schedule_bp.route('/<int:block_id>', methods=['DELETE'])
@token_required
def delete_block(current_user, block_id):
    db = get_db()
    cursor = db.execute('DELETE FROM schedule_blocks WHERE id = ? AND user_id = ?',
                        (block_id, current_user['id']))
    db.commit()
    if not cursor.rowcount:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'message': 'Removed'})


@schedule_bp.route('/preview/<int:event_id>', methods=['GET'])
@token_required
def preview(current_user, event_id):
    """What applying would set, without setting it."""
    db = get_db()
    event = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
    if not event:
        return jsonify({'error': 'Event not found'}), 404
    blocks = _my_blocks(db, current_user['id'])
    return jsonify({'blocks': len(blocks), 'days': _plan(event, blocks)})


def _plan(event, blocks):
    start = date.fromisoformat(event['week_start'])
    end = date.fromisoformat(event['week_end'])
    days = []
    day = start
    while day <= end:
        status, from_time, to_time = sb.availability_for(blocks, day.weekday())
        days.append({'date': day.isoformat(), 'status': status,
                     'start_time': from_time, 'end_time': to_time})
        day += timedelta(days=1)
    return days


@schedule_bp.route('/busy/<int:event_id>', methods=['GET'])
@token_required
def busy(current_user, event_id):
    """Your own blocked-out spans for each day of this event's week.

    Only ever your own: the chart draws these on your row, and somebody else's
    labels are none of your business.
    """
    db = get_db()
    event = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
    if not event:
        return jsonify({'error': 'Event not found'}), 404

    blocks = _my_blocks(db, current_user['id'])
    out = {}
    day = date.fromisoformat(event['week_start'])
    last = date.fromisoformat(event['week_end'])
    while day <= last:
        weekday = day.weekday()
        window = sb.preferred_free_window(blocks, weekday)
        out[day.isoformat()] = {
            'spans': sb.labelled_spans_on(blocks, weekday),
            # The gap the chart centres on when nobody has given a time yet.
            'free': None if window is None else {'start': window[0], 'end': window[1]},
        }
        day += timedelta(days=1)
    return jsonify(out)


@schedule_bp.route('/apply/<int:event_id>', methods=['POST'])
@token_required
def apply(current_user, event_id):
    db = get_db()
    event = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
    if not event:
        return jsonify({'error': 'Event not found'}), 404

    blocks = _my_blocks(db, current_user['id'])
    if not blocks:
        return jsonify({'error': 'You have no blocked-out time saved yet'}), 400

    plan = _plan(event, blocks)
    changes = []
    for entry in plan:
        prior = db.execute(
            'SELECT status FROM availability WHERE user_id = ? AND event_id = ? AND date = ?',
            (current_user['id'], event_id, entry['date'])
        ).fetchone()
        changes.append((entry['date'], prior['status'] if prior else None, entry['status']))
        # Notes are the person's own words about that day, so leave them be.
        db.execute('''
            INSERT INTO availability (user_id, event_id, date, status, note, start_time, end_time, updated_at)
            VALUES (?, ?, ?, ?, '', ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, event_id, date) DO UPDATE SET
                status = excluded.status,
                start_time = excluded.start_time,
                end_time = excluded.end_time,
                updated_at = CURRENT_TIMESTAMP
        ''', (current_user['id'], event_id, entry['date'], entry['status'],
              entry['start_time'] or None, entry['end_time'] or None))
    db.commit()

    # Same follow-ups as any other availability change.
    for day, was, now in changes:
        try:
            signal_record_change(db, event_id, current_user['id'], day, was, now)
        except Exception as e:
            print(f'Signal change-record error: {e}')
    for day in {c[0] for c in changes}:
        for check, name in ((discord_check_all_available, 'Discord'),
                            (check_and_notify_all_available, 'Signal'),
                            (push_triggers.all_available, 'Push')):
            try:
                check(db, event_id, day)
            except Exception as e:
                print(f'{name} all-available check error: {e}')

    return jsonify({'message': f'Applied your schedule to {len(plan)} days',
                    'days': plan})
