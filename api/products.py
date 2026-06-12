# api/products.py
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from extensions import db
from models import Product, StockMovement
from api.schemas import product_schema, products_schema
from api.auth import admin_required
 
products_bp = Blueprint('products', __name__, url_prefix='/api/v1/products')
 
def error(msg, code=400):
    return jsonify({'success': False, 'error': msg}), code
 
 
# ── LIST ALL PRODUCTS ────────────────────────────────
@products_bp.route('', methods=['GET'])
@jwt_required()
def list_products():
    query = Product.query
 
    # Optional filters via query string: /api/v1/products?category=Food&search=milk
    category = request.args.get('category')
    search   = request.args.get('search')
 
    if category:
        query = query.filter_by(category=category)
    if search:
        query = query.filter(Product.name.ilike(f'%{search}%'))
 
    products = query.order_by(Product.name).all()
    return jsonify({'success': True, 'count': len(products), 'products': products_schema.dump(products)}), 200
 
 
# ── GET SINGLE PRODUCT ───────────────────────────────
@products_bp.route('/<int:product_id>', methods=['GET'])
@jwt_required()
def get_product(product_id):
    product = Product.query.get_or_404(product_id)
    return jsonify({'success': True, 'product': product_schema.dump(product)}), 200
 
 
# ── CREATE PRODUCT ───────────────────────────────────
@products_bp.route('', methods=['POST'])
@jwt_required()
def create_product():
    # Force JSON parsing regardless of strict browser headers
    data = request.get_json(force=True)
    if not data:
        return error('Request body must be JSON')
 
    # Validate required string fields
    name = data.get('name', '').strip()
    if not name:
        return error('Product name is required')
 
    # Wrap type casting in a try/except safety net to block alphabetic text in math fields
    try:
        buying_price  = float(data.get('buying_price', 0))
        selling_price = float(data.get('selling_price', 0))
        quantity      = int(data.get('quantity', 0))
        min_stock_level = int(data.get('min_stock_level', 5))
    except (ValueError, TypeError):
        return error('Validation error: Prices must be numeric values and quantities must be whole integers.')
 
    if selling_price < 0 or buying_price < 0:
        return error('Prices cannot be negative')
 
    # Check barcode uniqueness if provided
    barcode = data.get('barcode')
    if barcode and Product.query.filter_by(barcode=barcode).first():
        return error(f'Barcode {barcode} already exists', 409)
 
    product = Product(
        name          = name,
        buying_price  = buying_price,
        selling_price = selling_price,
        quantity      = quantity,
        min_stock_level = min_stock_level,
        barcode       = barcode,
        category      = data.get('category', 'General'),
    )
    db.session.add(product)
    db.session.flush()  # get product.id before commit
 
    # Record initial stock as a movement
    if product.quantity > 0:
        movement = StockMovement(
            product_id     = product.id,
            quantity_change = product.quantity,
            reason         = 'purchase',
            reference_type = 'manual',
        )
        db.session.add(movement)
 
    db.session.commit()
    return jsonify({'success': True, 'product': product_schema.dump(product)}), 201
 
 
# ── UPDATE PRODUCT ───────────────────────────────────
@products_bp.route('/<int:product_id>', methods=['PUT'])
@jwt_required()
def update_product(product_id):
    product = Product.query.get_or_404(product_id)
    data    = request.get_json(force=True) or {}
 
    # Safe data-type conversion validation for partial updates
    try:
        if 'name' in data:            product.name          = data['name'].strip()
        if 'buying_price' in data:   product.buying_price  = float(data['buying_price'])
        if 'selling_price' in data:  product.selling_price = float(data['selling_price'])
        if 'category' in data:       product.category      = data['category']
        if 'min_stock_level' in data: product.min_stock_level = int(data['min_stock_level'])
    except (ValueError, TypeError):
        return error('Validation error: Provided prices must be numeric values and quantities must be integers.')
 
    db.session.commit()
    return jsonify({'success': True, 'product': product_schema.dump(product)}), 200
 
 
# ── DELETE PRODUCT (admin only) ───────────────────────
@products_bp.route('/<int:product_id>', methods=['DELETE'])
@admin_required
def delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    return jsonify({'success': True, 'message': f'Product {product_id} deleted'}), 200
 
 
# ── LOW STOCK ALERT ──────────────────────────────────
@products_bp.route('/low-stock', methods=['GET'])
@jwt_required()
def low_stock():
    products = Product.query.filter(
        Product.quantity <= Product.min_stock_level
    ).all()
    return jsonify({'success': True, 'count': len(products), 'products': products_schema.dump(products)}), 200
 
 
# ── BARCODE LOOKUP ───────────────────────────────────
@products_bp.route('/by-barcode', methods=['GET'])
@jwt_required()
def by_barcode():
    code = request.args.get('code', '').strip()
    if not code:
        return error('Barcode ?code= parameter is required')
    product = Product.query.filter_by(barcode=code).first()
    if not product:
        return error('Product not found', 404)
    return jsonify({'success': True, 'product': product_schema.dump(product)}), 200