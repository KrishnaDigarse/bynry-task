"""
Part 3: Low-Stock Alerts API Endpoint
=======================================
GET /api/companies/<company_id>/alerts/low-stock

Returns products that are below their low-stock threshold and have
had at least one sale in the last 30 days (i.e., actively selling).

Assumptions documented inline.
"""

from flask import Flask, request, jsonify
from datetime import datetime, timedelta
from sqlalchemy import text
import math
import logging

logger = logging.getLogger(__name__)

# ---- Flask app setup (for demonstration) ----
app = Flask(__name__)

# In a real project, this would come from config / environment
# app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://...'


# ==============================================================
# LOW-STOCK ALERTS ENDPOINT
# ==============================================================

@app.route('/api/companies/<int:company_id>/alerts/low-stock', methods=['GET'])
def get_low_stock_alerts(company_id):
    """
    Return a list of products whose current stock is below their
    low_stock_threshold, filtered to only include products with
    recent sales activity.

    Query params (all optional):
        - warehouse_id : filter to a single warehouse
        - days         : how far back to check for "recent" sales (default 30)
        - page         : page number for pagination (default 1)
        - per_page     : results per page (default 50)

    Assumptions:
        1. Each product has a `low_stock_threshold` column (set per product).
        2. "Recent sales activity" = at least one 'sale' transaction in the last N days.
        3. We pick the *preferred* supplier for each product; if none is marked
           preferred, we fall back to the first supplier alphabetically.
        4. `days_until_stockout` is estimated by:
           current_stock / (total units sold in last N days / N).
           If there were no sales somehow (shouldn't happen because of the filter,
           but just in case), we return null.
        5. We use the schema from Part 2 (products, inventory, inventory_transactions,
           product_suppliers, suppliers tables).
    """

    # ---- 1. Validate company exists ----
    company = db.session.execute(
        text("SELECT id FROM companies WHERE id = :cid"),
        {"cid": company_id}
    ).fetchone()

    if not company:
        return jsonify({"error": "Company not found"}), 404

    # ---- 2. Read optional query parameters ----
    warehouse_filter = request.args.get('warehouse_id', type=int)
    lookback_days = request.args.get('days', default=30, type=int)
    page = request.args.get('page', default=1, type=int)
    per_page = request.args.get('per_page', default=50, type=int)

    # Basic sanity checks
    if lookback_days < 1:
        lookback_days = 30
    if page < 1:
        page = 1
    if per_page < 1 or per_page > 100:
        per_page = 50

    cutoff_date = datetime.utcnow() - timedelta(days=lookback_days)

    # ---- 3. Build the main query ----
    #
    # This query does the heavy lifting:
    #   - JOINs inventory with products and warehouses
    #   - Filters: quantity < threshold, warehouse belongs to company
    #   - Sub-selects for: total recent sales, preferred supplier info
    #
    # I'm using raw SQL here for clarity. In a real project I'd likely
    # use SQLAlchemy ORM or query builder, but for an assignment the raw
    # SQL makes the logic easier to follow.

    query = text("""
        WITH recent_sales AS (
            -- Sum up total units sold per product per warehouse in the lookback window
            SELECT
                product_id,
                warehouse_id,
                ABS(SUM(quantity_change)) AS total_sold
            FROM inventory_transactions
            WHERE transaction_type = 'sale'
              AND created_at >= :cutoff
            GROUP BY product_id, warehouse_id
        ),
        preferred_supplier AS (
            -- Pick the preferred supplier for each product.
            -- If multiple are marked preferred, pick the one with shortest lead time.
            -- If none is preferred, pick the first one alphabetically (fallback).
            SELECT DISTINCT ON (ps.product_id)
                ps.product_id,
                s.id   AS supplier_id,
                s.name AS supplier_name,
                s.contact_email
            FROM product_suppliers ps
            JOIN suppliers s ON s.id = ps.supplier_id AND s.is_active = TRUE
            ORDER BY ps.product_id,
                     ps.is_preferred DESC,
                     s.lead_time_days ASC NULLS LAST,
                     s.name ASC
        )
        SELECT
            p.id            AS product_id,
            p.name          AS product_name,
            p.sku,
            w.id            AS warehouse_id,
            w.name          AS warehouse_name,
            i.quantity       AS current_stock,
            p.low_stock_threshold AS threshold,
            rs.total_sold,
            ps.supplier_id,
            ps.supplier_name,
            ps.contact_email AS supplier_email
        FROM inventory i
        JOIN products p   ON p.id = i.product_id
        JOIN warehouses w ON w.id = i.warehouse_id
        -- only products with recent sales
        JOIN recent_sales rs
            ON rs.product_id = i.product_id
            AND rs.warehouse_id = i.warehouse_id
        -- supplier info (LEFT JOIN because a product might not have a supplier yet)
        LEFT JOIN preferred_supplier ps
            ON ps.product_id = p.id
        WHERE w.company_id = :company_id
          AND p.is_active = TRUE
          AND i.quantity < p.low_stock_threshold
          AND (:wh_filter IS NULL OR w.id = :wh_filter)
        ORDER BY
            -- most urgent first: lowest ratio of stock-to-threshold
            (i.quantity::FLOAT / NULLIF(p.low_stock_threshold, 0)) ASC,
            p.name ASC
        LIMIT :limit
        OFFSET :offset
    """)

    params = {
        "company_id": company_id,
        "cutoff": cutoff_date,
        "wh_filter": warehouse_filter,
        "limit": per_page,
        "offset": (page - 1) * per_page,
    }

    # ---- 4. Execute query ----
    try:
        rows = db.session.execute(query, params).fetchall()
    except Exception as e:
        logger.exception("Error fetching low-stock alerts")
        return jsonify({"error": "Internal server error"}), 500

    # ---- 5. Build response ----
    alerts = []
    for row in rows:
        # Calculate estimated days until stock runs out
        days_until_stockout = None
        if row.total_sold and row.total_sold > 0 and lookback_days > 0:
            daily_sales_rate = row.total_sold / lookback_days
            if daily_sales_rate > 0:
                days_until_stockout = math.ceil(row.current_stock / daily_sales_rate)

        alert = {
            "product_id": row.product_id,
            "product_name": row.product_name,
            "sku": row.sku,
            "warehouse_id": row.warehouse_id,
            "warehouse_name": row.warehouse_name,
            "current_stock": row.current_stock,
            "threshold": row.threshold,
            "days_until_stockout": days_until_stockout,
            "supplier": None
        }

        # Attach supplier info if available
        if row.supplier_id:
            alert["supplier"] = {
                "id": row.supplier_id,
                "name": row.supplier_name,
                "contact_email": row.supplier_email
            }

        alerts.append(alert)

    # ---- 6. Get total count for pagination metadata ----
    count_query = text("""
        SELECT COUNT(*) AS cnt
        FROM inventory i
        JOIN products p   ON p.id = i.product_id
        JOIN warehouses w ON w.id = i.warehouse_id
        JOIN (
            SELECT product_id, warehouse_id
            FROM inventory_transactions
            WHERE transaction_type = 'sale'
              AND created_at >= :cutoff
            GROUP BY product_id, warehouse_id
        ) rs ON rs.product_id = i.product_id
            AND rs.warehouse_id = i.warehouse_id
        WHERE w.company_id = :company_id
          AND p.is_active = TRUE
          AND i.quantity < p.low_stock_threshold
          AND (:wh_filter IS NULL OR w.id = :wh_filter)
    """)

    try:
        total_alerts = db.session.execute(count_query, {
            "company_id": company_id,
            "cutoff": cutoff_date,
            "wh_filter": warehouse_filter,
        }).scalar()
    except Exception:
        total_alerts = len(alerts)

    return jsonify({
        "alerts": alerts,
        "total_alerts": total_alerts,
        "page": page,
        "per_page": per_page,
        "total_pages": math.ceil(total_alerts / per_page) if total_alerts > 0 else 0
    }), 200


# ==============================================================
# Run the app (development only)
# ==============================================================
if __name__ == '__main__':
    app.run(debug=True, port=5000)
