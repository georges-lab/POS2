# api/reports.py
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from extensions import db
from models import Sale, Receipt, Expense, Product
from sqlalchemy import func
from datetime import date, timedelta
 
reports_bp = Blueprint('reports', __name__, url_prefix='/api/v1/reports')
 
 
@reports_bp.route('/summary', methods=['GET'])
@jwt_required()
def summary():
    # Optional: ?days=7 or ?days=30
    days  = request.args.get('days', 30, type=int)
    since = date.today() - timedelta(days=days)
 
    total_revenue = db.session.query(
        func.sum(Sale.total_price)).filter(Sale.timestamp >= since).scalar() or 0
 
    total_cost = db.session.query(
        func.sum(Sale.quantity_sold * Product.buying_price)
    ).join(Product, Sale.product_id == Product.id
    ).filter(Sale.timestamp >= since).scalar() or 0
 
    total_expenses = db.session.query(
        func.sum(Expense.amount)).filter(Expense.date >= since).scalar() or 0
 
    gross_profit = round(total_revenue - total_cost, 2)
    net_profit   = round(gross_profit - total_expenses, 2)
 
    open_invoices = Receipt.query.filter_by(status='Open').count()
    low_stock_count = Product.query.filter(
        Product.quantity <= Product.min_stock_level).count()
 
    return jsonify({
        'success': True,
        'period_days': days,
        'total_revenue':   round(total_revenue, 2),
        'total_cogs':      round(total_cost, 2),
        'total_expenses':  round(total_expenses, 2),
        'gross_profit':    gross_profit,
        'net_profit':      net_profit,
        'open_invoices':   open_invoices,
        'low_stock_count': low_stock_count,
    }), 200
 
 
@reports_bp.route('/fast-moving', methods=['GET'])
@jwt_required()
def fast_moving():
    limit = request.args.get('limit', 10, type=int)
    rows  = (
        db.session.query(
            Sale.product_name,
            func.sum(Sale.quantity_sold).label('total_sold'),
            func.sum(Sale.total_price).label('total_revenue'),
        )
        .group_by(Sale.product_name)
        .order_by(func.sum(Sale.quantity_sold).desc())
        .limit(limit).all()
    )
    data = [{'product': r[0], 'total_sold': r[1], 'total_revenue': round(r[2], 2)} for r in rows]
    return jsonify({'success': True, 'products': data}), 200
