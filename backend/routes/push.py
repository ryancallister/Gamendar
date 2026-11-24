"""Per-device opt-in for OS notifications."""
from flask import Blueprint, request, jsonify
from database import get_db
from auth_utils import token_required
import push_service

push_bp = Blueprint('push', __name__)


@push_bp.route('/config', methods=['GET'])
@token_required
def config(current_user):
    """What the browser needs to subscribe, and whether this account has any."""
    key = push_service.public_key()
    return jsonify({
        'available': key is not None,
        'public_key': key,
        'devices': push_service.subscription_count(get_db(), current_user['id']),
    })


@push_bp.route('/subscribe', methods=['POST'])
@token_required
def subscribe(current_user):
    data = request.get_json(silent=True) or {}
    subscription = data.get('subscription') or data
    if not push_service.save_subscription(
            get_db(), current_user['id'], subscription,
            request.headers.get('User-Agent', '')):
        return jsonify({'error': 'That subscription is missing its endpoint or keys'}), 400
    return jsonify({'message': 'Notifications are on for this device'})


@push_bp.route('/unsubscribe', methods=['POST'])
@token_required
def unsubscribe(current_user):
    data = request.get_json(silent=True) or {}
    endpoint = data.get('endpoint')
    if not endpoint:
        return jsonify({'error': 'endpoint required'}), 400
    push_service.delete_subscription(get_db(), endpoint)
    return jsonify({'message': 'Notifications are off for this device'})


@push_bp.route('/test', methods=['POST'])
@token_required
def test(current_user):
    db = get_db()
    if not push_service.subscription_count(db, current_user['id']):
        return jsonify({'error': 'Turn notifications on for this device first'}), 400
    push_service.notify_users(
        db, [current_user['id']],
        '🔔 Gamendar notifications are on',
        'This is what an update will look like.',
    )
    return jsonify({'message': 'Test notification queued'})
