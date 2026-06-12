# api/receipts.py
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from extensions import db
from models import Receipt, Sale, Payment, Product, StockMovement, Client
from api.schemas import receipt_schema, receipts_schema
from datetime import datetime
 
receipts_bp = Blueprint('receipts', __name__, url_prefix='/api/v1/receipts')
 
def error(msg, code=400):
    return jsonify({'success': False, 'error': msg}), code
 
 
# ── LIST RECEIPTS ────────────────────────────────────
@receipts_bp.route('', methods=['GET'])
@jwt_required()
def list_receipts():
    status = request.args.get('status')          # ?status=Open
    limit  = request.args.get('limit', 20, type=int)
 
    query = Receipt.query.order_by(Receipt.timestamp.desc())
    if status:
        query = query.filter_by(status=status)
 
    receipts = query.limit(limit).all()
    return jsonify({'success': True, 'receipts': receipts_schema.dump(receipts)}), 200
 
 
# ── GET SINGLE RECEIPT ───────────────────────────────
@receipts_bp.route('/<int:receipt_id>', methods=['GET'])
@jwt_required()
def get_receipt(receipt_id):
    receipt = Receipt.query.get_or_404(receipt_id)
    return jsonify({'success': True, 'receipt': receipt_schema.dump(receipt)}), 200
 
 
# ── CREATE RECEIPT (full sale) ────────────────────────
@receipts_bp.route('', methods=['POST'])
@jwt_required()
def create_receipt():
    data = request.get_json(force=True) or {}
 
    # --- VALIDATE INCOMING DATA ---
    items = data.get('items', [])
    if not items:
        return error('items list is required and cannot be empty')
 
    # Expected request format:
    # {
    #   'customer_name': 'John Doe',
    #   'client_id': 5,          (optional)
    #   'payment_method': 'Cash', (Cash or Mpesa)
    #   'amount_paid': 1500.0,
    #   'items': [
    #     {'product_id': 3, 'quantity': 2, 'discount': 0},
    #     {'product_id': 7, 'quantity': 1, 'discount': 50}
    #   ]
    # }
 
    try:
        receipt = Receipt(
            customer_name = data.get('customer_name', 'Walk-in'),
            client_id     = data.get('client_id'),
            status        = 'Open',
        )
        db.session.add(receipt)
        db.session.flush()  # get receipt.id
 
        total_amount = 0.0
 
        for item in items:
            product_id = item.get('product_id')
            quantity   = int(item.get('quantity', 1))
            discount   = float(item.get('discount', 0))
 
            product = Product.query.get(product_id)
            if not product:
                db.session.rollback()
                return error(f'Product {product_id} not found', 404)
 
            if product.quantity < quantity:
                db.session.rollback()
                return error(f'Insufficient stock for {product.name}. Available: {product.quantity}', 400)
 
            unit_price = float(product.selling_price)
            base_total = round(unit_price * quantity, 2)
            line_total = round(base_total - discount, 2)
 
            sale = Sale(
                product_name  = product.name,
                product_id    = product.id,
                quantity_sold = quantity,
                unit_price    = unit_price,
                total_price   = base_total,
                discount      = discount,
                total_amount  = line_total,
                receipt_id    = receipt.id,
                client_id     = data.get('client_id'),
                tax_rate      = 0.16,
            )
            db.session.add(sale)
 
            # Deduct stock
            product.quantity -= quantity
            db.session.add(StockMovement(
                product_id      = product.id,
                quantity_change = -quantity,
                reason          = 'sale',
                reference_type  = 'receipt',
                reference_id    = receipt.id,
            ))
 
            total_amount += line_total
 
        receipt.total_amount = round(total_amount, 2)
 
        # Record payment if provided
        amount_paid    = float(data.get('amount_paid', 0))
        payment_method = data.get('payment_method', 'Cash')
 
        if amount_paid > 0:
            payment = Payment(
                receipt_id = receipt.id,
                client_id  = data.get('client_id'),
                method     = payment_method,
                amount     = amount_paid,
                status     = 'Success' if payment_method == 'Cash' else 'Pending',
            )
            db.session.add(payment)
            db.session.flush()
            receipt.recompute_balance()
        else:
            receipt.balance_due = receipt.total_amount
 
        db.session.commit()
        return jsonify({'success': True, 'receipt': receipt_schema.dump(receipt)}), 201
 
    except Exception as e:
        db.session.rollback()
        return error(f'Failed to create receipt: {str(e)}', 500)
 
 
# ── RECORD A PAYMENT ─────────────────────────────────
@receipts_bp.route('/<int:receipt_id>/pay', methods=['POST'])
@jwt_required()
def pay_receipt(receipt_id):
    receipt = Receipt.query.get_or_404(receipt_id)
 
    if receipt.status in ('Paid', 'Cancelled'):
        return error(f'Cannot add payment to a {receipt.status} receipt')
 
    data   = request.get_json() or {}
    amount = float(data.get('amount', 0))
    method = data.get('method', 'Cash')
 
    if amount <= 0:
 
        payment = Payment(
        receipt_id = receipt.id,
        client_id  = receipt.client_id,
        method     = method,
        amount     = amount,
        status     = 'Success' if method == 'Cash' else 'Pending',
        notes      = data.get('notes', ''),
        reference  = data.get('reference', ''),
    )
    db.session.add(payment)
    db.session.flush()
    receipt.recompute_balance()
    db.session.commit()
 
    return jsonify({'success': True, 'receipt': receipt_schema.dump(receipt)}), 200
 
 
# ── CANCEL RECEIPT ────────────────────────────────────
@receipts_bp.route('/<int:receipt_id>/cancel', methods=['POST'])
@jwt_required()
def cancel_receipt(receipt_id):
    receipt = Receipt.query.get_or_404(receipt_id)
    if receipt.status == 'Cancelled':
        return error('Receipt already cancelled')
 
    # Return stock for all sale items
    for sale in receipt.sales:
        product = Product.query.get(sale.product_id)
        if product:
            product.quantity += sale.quantity_sold
            db.session.add(StockMovement(
                product_id      = product.id,
                quantity_change = sale.quantity_sold,
                reason          = 'reversal',
                reference_type  = 'receipt',
                reference_id    = receipt.id,
            ))
 
    receipt.status = 'Cancelled'
    db.session.commit()
    return jsonify({'success': True, 'message': f'Receipt {receipt_id} cancelled and stock restored'}), 200
