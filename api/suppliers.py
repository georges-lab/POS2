# api/suppliers.py
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from extensions import db
from models import Supplier
from api.schemas import supplier_schema, suppliers_schema
 
suppliers_bp = Blueprint('suppliers', __name__, url_prefix='/api/v1/suppliers')
 
def error(msg, code=400):
    return jsonify({'success': False, 'error': msg}), code
 
 
@suppliers_bp.route('', methods=['GET'])
@jwt_required()
def list_suppliers():
    suppliers = Supplier.query.order_by(Supplier.name).all()
    return jsonify({'success': True, 'suppliers': suppliers_schema.dump(suppliers)}), 200
 
 
@suppliers_bp.route('', methods=['POST'])
@jwt_required()
def create_supplier():
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    if not name:
        return error('Supplier name is required')
    if Supplier.query.filter_by(name=name).first():
        return error(f'Supplier "{name}" already exists', 409)
    supplier = Supplier(
        name           = name,
        phone          = data.get('phone', ''),
        email          = data.get('email', ''),
        address        = data.get('address', ''),
        items_supplied = data.get('items_supplied', ''),
    )
    db.session.add(supplier)
    db.session.commit()
    return jsonify({'success': True, 'supplier': supplier_schema.dump(supplier)}), 201
