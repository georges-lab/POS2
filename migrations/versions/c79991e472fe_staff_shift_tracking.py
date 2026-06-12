"""staff shift tracking

Revision ID: c79991e472fe
Revises: 8dc619c61b7b
Create Date: 2026-06-10 01:23:17.582433

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import reflection

# revision identifiers, used by Alembic.
revision = 'c79991e472fe'
down_revision = '8dc619c61b7b'
branch_labels = None
depends_on = None


def upgrade():
    # Get a list of tables and columns that already exist in the database
    conn = op.get_bind()
    inspect_obj = reflection.Inspector.from_engine(conn)
    existing_tables = inspect_obj.get_table_names()

    # 1. client_credit_payment
    if 'client_credit_payment' not in existing_tables:
        op.create_table('client_credit_payment',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('client_id', sa.Integer(), nullable=False),
            sa.Column('amount', sa.Float(), nullable=False),
            sa.Column('method', sa.String(length=20), nullable=True),
            sa.Column('reference', sa.String(length=120), nullable=True),
            sa.Column('recorded_by', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
            sa.ForeignKeyConstraint(['recorded_by'], ['user.id'], ),
            sa.PrimaryKeyConstraint('id')
        )

    # 2. eod_report
    if 'eod_report' not in existing_tables:
        op.create_table('eod_report',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('report_date', sa.Date(), nullable=False),
            sa.Column('generated_by', sa.Integer(), nullable=True),
            sa.Column('generated_at', sa.DateTime(), nullable=True),
            sa.Column('total_revenue', sa.Float(), nullable=True),
            sa.Column('total_cash', sa.Float(), nullable=True),
            sa.Column('total_mpesa', sa.Float(), nullable=True),
            sa.Column('total_credit_sales', sa.Float(), nullable=True),
            sa.Column('total_discounts', sa.Float(), nullable=True),
            sa.Column('total_vat', sa.Float(), nullable=True),
            sa.Column('transaction_count', sa.Integer(), nullable=True),
            sa.Column('units_sold', sa.Integer(), nullable=True),
            sa.Column('total_expenses', sa.Float(), nullable=True),
            sa.Column('gross_profit', sa.Float(), nullable=True),
            sa.Column('net_profit', sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(['generated_by'], ['user.id'], ),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('report_date')
        )

    # 3. loyalty_transaction
    if 'loyalty_transaction' not in existing_tables:
        op.create_table('loyalty_transaction',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('client_id', sa.Integer(), nullable=False),
            sa.Column('points', sa.Integer(), nullable=False),
            sa.Column('reason', sa.String(length=100), nullable=True),
            sa.Column('reference_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
            sa.PrimaryKeyConstraint('id')
        )

    # 4. purchase_order
    if 'purchase_order' not in existing_tables:
        op.create_table('purchase_order',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('supplier_id', sa.Integer(), nullable=False),
            sa.Column('created_by', sa.Integer(), nullable=True),
            sa.Column('po_number', sa.String(length=40), nullable=True),
            sa.Column('status', sa.String(length=30), nullable=True),
            sa.Column('notes', sa.String(length=500), nullable=True),
            sa.Column('expected_date', sa.Date(), nullable=True),
            sa.Column('total_amount', sa.Float(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('sent_at', sa.DateTime(), nullable=True),
            sa.Column('received_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['created_by'], ['user.id'], ),
            sa.ForeignKeyConstraint(['supplier_id'], ['supplier.id'], ),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('po_number')
        )

    # 5. shift
    if 'shift' not in existing_tables:
        op.create_table('shift',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('opened_by_id', sa.Integer(), nullable=True),
            sa.Column('opened_at', sa.DateTime(), nullable=True),
            sa.Column('closed_at', sa.DateTime(), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=True),
            sa.Column('opening_float', sa.Float(), nullable=True),
            sa.Column('expected_cash', sa.Float(), nullable=True),
            sa.Column('actual_cash', sa.Float(), nullable=True),
            sa.Column('cash_variance', sa.Float(), nullable=True),
            sa.Column('closing_notes', sa.String(length=500), nullable=True),
            sa.Column('total_sales', sa.Float(), nullable=True),
            sa.Column('total_cash', sa.Float(), nullable=True),
            sa.Column('total_mpesa', sa.Float(), nullable=True),
            sa.Column('total_discounts', sa.Float(), nullable=True),
            sa.Column('transaction_count', sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(['opened_by_id'], ['user.id'], ),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
            sa.PrimaryKeyConstraint('id')
        )

    # 6. purchase_order_item
    if 'purchase_order_item' not in existing_tables:
        op.create_table('purchase_order_item',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('purchase_order_id', sa.Integer(), nullable=False),
            sa.Column('product_id', sa.Integer(), nullable=False),
            sa.Column('quantity_ordered', sa.Integer(), nullable=False),
            sa.Column('quantity_received', sa.Integer(), nullable=True),
            sa.Column('unit_cost', sa.Float(), nullable=False),
            sa.Column('line_total', sa.Float(), nullable=False),
            sa.ForeignKeyConstraint(['product_id'], ['product.id'], ),
            sa.ForeignKeyConstraint(['purchase_order_id'], ['purchase_order.id'], ),
            sa.PrimaryKeyConstraint('id')
        )

    # 7. return
    if 'return' not in existing_tables:
        op.create_table('return',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('receipt_id', sa.Integer(), nullable=False),
            sa.Column('processed_by', sa.Integer(), nullable=True),
            sa.Column('reason', sa.String(length=255), nullable=True),
            sa.Column('refund_method', sa.String(length=20), nullable=True),
            sa.Column('refund_amount', sa.Float(), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['processed_by'], ['user.id'], ),
            sa.ForeignKeyConstraint(['receipt_id'], ['receipt.id'], ),
            sa.PrimaryKeyConstraint('id')
        )

    # 8. return_item
    if 'return_item' not in existing_tables:
        op.create_table('return_item',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('return_id', sa.Integer(), nullable=False),
            sa.Column('sale_id', sa.Integer(), nullable=False),
            sa.Column('product_id', sa.Integer(), nullable=True),
            sa.Column('qty_returned', sa.Integer(), nullable=False),
            sa.Column('unit_price', sa.Float(), nullable=False),
            sa.Column('refund_amount', sa.Float(), nullable=False),
            sa.Column('restock', sa.Boolean(), nullable=True),
            sa.ForeignKeyConstraint(['product_id'], ['product.id'], ),
            sa.ForeignKeyConstraint(['return_id'], ['return.id'], ),
            sa.ForeignKeyConstraint(['sale_id'], ['sale.id'], ),
            sa.PrimaryKeyConstraint('id')
        )

    # 9. Alter Client columns safely
    client_cols = [c['name'] for c in inspect_obj.get_columns('client')]
    with op.batch_alter_table('client', schema=None) as batch_op:
        if 'loyalty_points' not in client_cols:
            batch_op.add_column(sa.Column('loyalty_points', sa.Integer(), nullable=True))
        if 'total_spent' not in client_cols:
            batch_op.add_column(sa.Column('total_spent', sa.Float(), nullable=True))
        if 'credit_limit' not in client_cols:
            batch_op.add_column(sa.Column('credit_limit', sa.Float(), nullable=True))
        if 'credit_balance' not in client_cols:
            batch_op.add_column(sa.Column('credit_balance', sa.Float(), nullable=True))
        if 'credit_enabled' not in client_cols:
            batch_op.add_column(sa.Column('credit_enabled', sa.Boolean(), nullable=True))

    # 10. Alter Product columns safely
    product_cols = [c['name'] for c in inspect_obj.get_columns('product')]
    with op.batch_alter_table('product', schema=None) as batch_op:
        if 'reorder_point' not in product_cols:
            batch_op.add_column(sa.Column('reorder_point', sa.Integer(), nullable=True))
        if 'unit_type' not in product_cols:
            batch_op.add_column(sa.Column('unit_type', sa.String(length=20), nullable=True))
        if 'bulk_size' not in product_cols:
            batch_op.add_column(sa.Column('bulk_size', sa.Integer(), nullable=True))
        if 'bulk_buying_price' not in product_cols:
            batch_op.add_column(sa.Column('bulk_buying_price', sa.Float(), nullable=True))
        if 'preferred_supplier_id' not in product_cols:
            batch_op.add_column(sa.Column('preferred_supplier_id', sa.Integer(), nullable=True))
            # Safe constraint name given here explicitly for SQLite batch compatibility
            batch_op.create_foreign_key('fk_product_preferred_supplier', 'supplier', ['preferred_supplier_id'], ['id'])

    # 11. Alter User columns safely
    user_cols = [c['name'] for c in inspect_obj.get_columns('user')]
    with op.batch_alter_table('user', schema=None) as batch_op:
        if 'full_name' not in user_cols:
            batch_op.add_column(sa.Column('full_name', sa.String(length=150), nullable=True))
        if 'phone' not in user_cols:
            batch_op.add_column(sa.Column('phone', sa.String(length=30), nullable=True))
        if 'id_number' not in user_cols:
            batch_op.add_column(sa.Column('id_number', sa.String(length=30), nullable=True))
        if 'hire_date' not in user_cols:
            batch_op.add_column(sa.Column('hire_date', sa.Date(), nullable=True))
        if 'salary' not in user_cols:
            batch_op.add_column(sa.Column('salary', sa.Float(), nullable=True))
        if 'is_active' not in user_cols:
            batch_op.add_column(sa.Column('is_active', sa.Boolean(), nullable=True))
        if 'created_at' not in user_cols:
            batch_op.add_column(sa.Column('created_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('created_at')
        batch_op.drop_column('is_active')
        batch_op.drop_column('salary')
        batch_op.drop_column('hire_date')
        batch_op.drop_column('id_number')
        batch_op.drop_column('phone')
        batch_op.drop_column('full_name')

    with op.batch_alter_table('product', schema=None) as batch_op:
        batch_op.drop_constraint('fk_product_preferred_supplier', type_='foreignkey')
        batch_op.drop_column('preferred_supplier_id')
        batch_op.drop_column('bulk_buying_price')
        batch_op.drop_column('bulk_size')
        batch_op.drop_column('unit_type')
        batch_op.drop_column('reorder_point')

    with op.batch_alter_table('client', schema=None) as batch_op:
        batch_op.drop_column('credit_enabled')
        batch_op.drop_column('credit_balance')
        batch_op.drop_column('credit_limit')
        batch_op.drop_column('total_spent')
        batch_op.drop_column('loyalty_points')

    op.drop_table('return_item')
    op.drop_table('return')
    op.drop_table('purchase_order_item')
    op.drop_table('shift')
    op.drop_table('purchase_order')
    op.drop_table('loyalty_transaction')
    op.drop_table('eod_report')
    op.drop_table('client_credit_payment')