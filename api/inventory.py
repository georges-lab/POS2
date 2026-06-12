# api/inventory.py
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from extensions import db
from models import Product, StockMovement
from api.schemas import movements_schema
 
inventory_bp = Blueprint('inventory', __name__, url_prefix='/api/v1/inventory')
 
def error(msg, code=400):
    return jsonify({'success': False, 'error': msg}), code
 
 
# ── STOCK MOVEMENT LOG ───────────────────────────────
@inventory_bp.route('/movements', methods=['GET'])
@jwt_required()
def list_movements():
    product_id = request.args.get('product_id', type=int)
    limit      = request.args.get('limit', 50, type=int)
 
    query = StockMovement.query.order_by(StockMovement.timestamp.desc())
    if product_id:
        query = query.filter_by(product_id=product_id)
 
    movements = query.limit(limit).all()
    return jsonify({
        'success': True,
        'count': len(movements),
        'movements': movements_schema.dump(movements)
    }), 200
 
 
# ── MANUAL STOCK ADJUSTMENT ──────────────────────────
# Use this for: stock counts, damage, returns, corrections
@inventory_bp.route('/adjust', methods=['POST'])
@jwt_required()
def adjust_stock():
    data = request.get_json() or {}
 
    product_id      = data.get('product_id')
    quantity_change = data.get('quantity_change')  # positive=add, negative=remove
    reason          = data.get('reason', 'adjustment')
 
    if product_id is None or quantity_change is None:
        return error('product_id and quantity_change are required')
 
    valid_reasons = ('adjustment', 'return', 'damage', 'purchase', 'reversal')
    if reason not in valid_reasons:
        return error(f'reason must be one of: {valid_reasons}')
 
    product = Product.query.get_or_404(product_id)
    new_qty = product.quantity + int(quantity_change)
 
    if new_qty < 0:
        return error(f'Adjustment would result in negative stock ({new_qty}). Current: {product.quantity}')
 
    product.quantity = new_qty
 
    movement = StockMovement(
        product_id      = product.id,
        quantity_change = int(quantity_change),
        reason          = reason,
        reference_type  = 'manual',
    )
    db.session.add(movement)
    db.session.commit()
 
    return jsonify({
        'success': True,
        'message': f'Stock adjusted. New quantity: {product.quantity}',
        'new_quantity': product.quantity
    }), 200
