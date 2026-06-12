# api/auth.py
from flask import Blueprint, request, jsonify
from flask_jwt_extended import (
    create_access_token, jwt_required, get_jwt_identity, get_jwt
)
from extensions import db
from models import User
from api.schemas import user_schema
 
auth_bp = Blueprint('auth', __name__, url_prefix='/api/v1/auth')
 
# ── HELPER: standard error response ─────────────────
def error(msg, code=400):
    return jsonify({'success': False, 'error': msg}), code
 
 
# ── LOGIN ────────────────────────────────────────────
@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    if not data:
        return error('Request body must be JSON')
 
    username = data.get('username', '').strip()
    password = data.get('password', '')
 
    if not username or not password:
        return error('username and password are required')
 
    user = User.query.filter_by(username=username).first()
    if not user or not user.check_password(password):
        return error('Invalid username or password', 401)
 
    # Create token — identity stores user id
    # Additional claims store role so you can check it in routes
    token = create_access_token(
        identity=str(user.id),
        additional_claims={'role': user.role}
    )
 
    return jsonify({
        'success': True,
        'token': token,
        'user': user_schema.dump(user)
    }), 200
 
 
# ── CURRENT USER INFO ────────────────────────────────
@auth_bp.route('/me', methods=['GET'])
@jwt_required()
def me():
    user_id = int(get_jwt_identity())
    user = User.query.get_or_404(user_id)
    return jsonify({'success': True, 'user': user_schema.dump(user)}), 200
 
 
# ── ADMIN GUARD DECORATOR ────────────────────────────
# Import and use this in any route that only admins can access
from functools import wraps
from flask_jwt_extended import verify_jwt_in_request
 
def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        claims = get_jwt()
        if claims.get('role') != 'admin':
            return jsonify({'success': False, 'error': 'Admin access required'}), 403
        return fn(*args, **kwargs)
    return wrapper
