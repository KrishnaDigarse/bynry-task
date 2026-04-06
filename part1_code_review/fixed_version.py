"""
Part 1 - Fixed Version: Create Product API Endpoint
====================================================
Fixed all identified issues with proper error handling,
validation, atomicity, and idempotency.
"""

from flask import request, jsonify
from decimal import Decimal, InvalidOperation
from sqlalchemy.exc import IntegrityError
import logging

logger = logging.getLogger(__name__)


@app.route('/api/products', methods=['POST'])
def create_product():
    """
    Create a new product and optionally set initial inventory in a warehouse.
    
    Expected JSON body:
    {
        "name": "Widget A",
        "sku": "WID-001",
        "price": "29.99",
        "warehouse_id": 5,
        "initial_quantity": 100   // optional, defaults to 0
    }
    """

    # ---- Step 1: Parse & validate input ----
    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    # Check required fields exist
    required_fields = ['name', 'sku', 'price', 'warehouse_id']
    missing = [f for f in required_fields if f not in data]
    if missing:
        return jsonify({
            "error": f"Missing required fields: {', '.join(missing)}"
        }), 400

    # Validate name is not empty/blank
    name = str(data['name']).strip()
    if not name:
        return jsonify({"error": "Product name cannot be empty"}), 400

    # Validate SKU format (basic check — non-empty string)
    sku = str(data['sku']).strip().upper()
    if not sku:
        return jsonify({"error": "SKU cannot be empty"}), 400

    # Validate price is a positive decimal
    try:
        price = Decimal(str(data['price']))
        if price <= 0:
            return jsonify({"error": "Price must be a positive number"}), 400
    except (InvalidOperation, ValueError):
        return jsonify({"error": "Price must be a valid number"}), 400

    # Validate warehouse_id is a positive integer
    try:
        warehouse_id = int(data['warehouse_id'])
        if warehouse_id <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"error": "warehouse_id must be a positive integer"}), 400

    # Validate initial_quantity (optional, defaults to 0)
    initial_quantity = data.get('initial_quantity', 0)
    try:
        initial_quantity = int(initial_quantity)
        if initial_quantity < 0:
            return jsonify({"error": "initial_quantity cannot be negative"}), 400
    except (ValueError, TypeError):
        return jsonify({"error": "initial_quantity must be a valid integer"}), 400

    # ---- Step 2: Check warehouse actually exists ----
    warehouse = Warehouse.query.get(warehouse_id)
    if not warehouse:
        return jsonify({"error": f"Warehouse {warehouse_id} not found"}), 404

    # ---- Step 3: Check for duplicate SKU ----
    existing = Product.query.filter_by(sku=sku).first()
    if existing:
        return jsonify({
            "error": f"A product with SKU '{sku}' already exists"
        }), 409  # 409 Conflict

    # ---- Step 4: Create product + inventory in ONE transaction ----
    try:
        product = Product(
            name=name,
            sku=sku,
            price=price
            # NOTE: warehouse_id removed from Product — a product is NOT
            # tied to a single warehouse. The relationship lives in Inventory.
        )
        db.session.add(product)
        db.session.flush()  # get product.id without committing yet

        inventory = Inventory(
            product_id=product.id,
            warehouse_id=warehouse_id,
            quantity=initial_quantity
        )
        db.session.add(inventory)

        # Single commit — both rows succeed or both roll back
        db.session.commit()

        logger.info(f"Product created: id={product.id}, sku={sku}")

        return jsonify({
            "message": "Product created successfully",
            "product_id": product.id,
            "sku": sku,
            "inventory": {
                "warehouse_id": warehouse_id,
                "quantity": initial_quantity
            }
        }), 201  # 201 Created

    except IntegrityError:
        db.session.rollback()
        logger.error(f"Integrity error creating product sku={sku}")
        return jsonify({
            "error": "Could not create product due to a data conflict"
        }), 409

    except Exception as e:
        db.session.rollback()
        logger.exception("Unexpected error creating product")
        return jsonify({"error": "Internal server error"}), 500
