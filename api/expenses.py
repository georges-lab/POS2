# api/expenses.py
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from extensions import db
from models import Expense
from api.schemas import expense_schema, expenses_schema
from datetime import date
 
expenses_bp = Blueprint('expenses', __name__, url_prefix='/api/v1/expenses')
 
def error(msg, code=400):
    return jsonify({'success': False, 'error': msg}), code
 
 
@expenses_bp.route('', methods=['GET'])
@jwt_required()
def list_expenses():
    category = request.args.get('category')
    query    = Expense.query.order_by(Expense.date.desc())
    if category:
        query = query.filter_by(category=category)
    expenses = query.all()
    return jsonify({'success': True, 'expenses': expenses_schema.dump(expenses)}), 200
 
 
@expenses_bp.route('', methods=['POST'])
@jwt_required()
def create_expense():
    data = request.get_json() or {}
    desc   = data.get('description', '').strip()
    amount = data.get('amount')
    if not desc or amount is None:
        return error('description and amount are required')
    if float(amount) <= 0:
        return error('amount must be greater than 0')
    expense = Expense(
        description = desc,
        amount      = float(amount),
        category    = data.get('category', 'General'),
        date        = date.today(),
    )
    db.session.add(expense)
    db.session.commit()
    return jsonify({'success': True, 'expense': expense_schema.dump(expense)}), 201
