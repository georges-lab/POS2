# api/clients.py
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from extensions import db
from models import Client
from api.schemas import client_schema, clients_schema
 
clients_bp = Blueprint('clients', __name__, url_prefix='/api/v1/clients')
 
def error(msg, code=400):
    return jsonify({'success': False, 'error': msg}), code
 
 
@clients_bp.route('', methods=['GET'])
@jwt_required()
def list_clients():
    search = request.args.get('search', '')
    query  = Client.query.order_by(Client.name)
    if search:
        query = query.filter(Client.name.ilike(f'%{search}%'))
    clients = query.all()
    return jsonify({'success': True, 'clients': clients_schema.dump(clients)}), 200
 
 
@clients_bp.route('/<int:client_id>', methods=['GET'])
@jwt_required()
def get_client(client_id):
    client = Client.query.get_or_404(client_id)
    return jsonify({'success': True, 'client': client_schema.dump(client)}), 200
 
 
@clients_bp.route('', methods=['POST'])
@jwt_required()
def create_client():
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    if not name:
        return error('Client name is required')
    client = Client(
        name      = name,
        phone     = data.get('phone', ''),
        email     = data.get('email', ''),
        is_walk_in = data.get('is_walk_in', False),
    )
    db.session.add(client)
    db.session.commit()
    return jsonify({'success': True, 'client': client_schema.dump(client)}), 201
 
 
@clients_bp.route('/<int:client_id>', methods=['PUT'])
@jwt_required()
def update_client(client_id):
    client = Client.query.get_or_404(client_id)
    data   = request.get_json() or {}
    if 'name' in data:  client.name  = data['name']
    if 'phone' in data: client.phone = data['phone']
    if 'email' in data: client.email = data['email']
    db.session.commit()
    return jsonify({'success': True, 'client': client_schema.dump(client)}), 200
