# StockFlow — Inventory Management System

**Case Study Solution**
**Candidate:** Krishna Digarse

---

## Table of Contents

1. [Part 1: Code Review & Debugging](#part-1-code-review--debugging)
2. [Part 2: Database Design](#part-2-database-design)
3. [Part 3: API Implementation](#part-3-api-implementation)
4. [Assumptions & Open Questions](#assumptions--open-questions)

---

## Part 1: Code Review & Debugging

> Full code: `part1_code_review/buggy_original.py` (original) and `part1_code_review/fixed_version.py` (fixed)

### Issues Found

I went through the code line-by-line and found **7 issues** — some are bugs that would break things in production, others are missing best practices that would eventually cause trouble.

---

#### Issue 1: No Input Validation At All

**What's wrong:** The code does `data['name']`, `data['price']`, etc. with zero validation. If any field is missing, it throws a `KeyError` which Flask turns into a generic 500 error. The user sees a confusing server error instead of "hey, you forgot the name field."

**What could go wrong in production:**
- Missing fields → 500 errors and confused API consumers
- Negative price values get saved (a product priced at -$50? Free money!)
- Empty strings become product names
- Non-numeric values for price cause unhandled exceptions

**Fix:** Validate every field before using it. Check for presence, correct types, sensible ranges. Return clear 400 errors with messages telling the caller exactly what's wrong.

---

#### Issue 2: Two Separate Commits (No Atomicity)

**What's wrong:** The code does `db.session.commit()` twice — once after creating the Product, once after creating the Inventory record. These should be a single transaction.

**What could go wrong in production:** If the first commit succeeds but the second one fails (say the database connection drops for a moment), you end up with a product that exists but has no inventory record. Now you have "ghost" products floating around the system with no stock information. Every reporting query would need to handle this inconsistency. Nightmare to debug at 2 AM.

**Fix:** Use a single `commit()` at the end. Use `db.session.flush()` after adding the product to get its ID (needed for the inventory foreign key) without actually committing. If anything fails, both records roll back together.

---

#### Issue 3: `warehouse_id` on the Product Model

**What's wrong:** The code sets `warehouse_id` on the Product. But the requirements say "products can exist in multiple warehouses." If a product has a single `warehouse_id` column, it can only belong to one warehouse.

**What could go wrong in production:** A small business has two warehouses (say, New York and Chicago). They stock Widget A in both. With this schema, they can only associate Widget A with *one* warehouse at the product level. The whole multi-warehouse feature is broken.

**Fix:** Remove `warehouse_id` from the Product model. The Product-Warehouse relationship should live in the Inventory table (which already has both `product_id` and `warehouse_id`). That's the correct junction.

---

#### Issue 4: No Duplicate SKU Check

**What's wrong:** The requirements say "SKUs must be unique across the platform." The code doesn't check for existing SKUs before inserting. If there's a database-level UNIQUE constraint, the insert will fail with an unhandled `IntegrityError`. If there *isn't* a constraint (worse), you'll end up with duplicate SKUs.

**What could go wrong in production:** Two products with the same SKU means barcode scanning, inventory lookups, and order fulfillment all break. Someone scans "WID-001" and the system returns... which product? It's a data integrity disaster.

**Fix:** Check for an existing product with the same SKU before inserting. Also make sure the database has a UNIQUE constraint as a safety net. Handle the `IntegrityError` gracefully just in case of a race condition.

---

#### Issue 5: No Error Handling / No Rollback

**What's wrong:** There's no `try/except` around the database operations. No rollback if something fails. No logging.

**What could go wrong in production:** Any database error (constraint violation, connection timeout, deadlock) crashes the endpoint with an unhandled exception. The user gets a 500 error with potentially sensitive traceback information. The database session may be left in a broken state, which can cause cascading failures for subsequent requests.

**Fix:** Wrap database operations in `try/except`. Catch `IntegrityError` specifically (for duplicate-key situations). Catch generic `Exception` as a last resort. Always `rollback()` on error. Log the exception for debugging.

---

#### Issue 6: Wrong HTTP Status Code

**What's wrong:** The response returns a 200 OK for creating a resource. The correct status code is **201 Created**. Also, the current response body is minimal — it doesn't return enough information for the API consumer to know what was actually created.

**What could go wrong in production:** It's not a "bug" per se, but it's bad API design. Frontend developers and API consumers rely on status codes. 200 vs 201 matters for caching, tooling, and client-side logic. A well-designed API should follow HTTP conventions.

**Fix:** Return `201` status code. Include more useful data in the response (product_id, sku, initial inventory info).

---

#### Issue 7: No Warehouse Existence Check

**What's wrong:** The code takes `warehouse_id` from the request and uses it directly without checking whether that warehouse actually exists.

**What could go wrong in production:** A user sends `warehouse_id: 99999`. If there's a foreign key constraint, the database insert fails with an unhelpful error. If there's no FK constraint, you create an inventory record pointing to a non-existent warehouse — broken data.

**Fix:** Query the warehouse before using it. Return a clear 404 if it doesn't exist.

---

### Summary of Changes

| Issue | Severity | Fix |
|-------|----------|-----|
| No input validation | High | Added validation for all fields |
| Two commits (no atomicity) | High | Single commit, flush for ID |
| warehouse_id on Product | High | Removed — use Inventory table |
| No duplicate SKU check | High | Check before insert + handle IntegrityError |
| No error handling | Medium | try/except with rollback and logging |
| Wrong HTTP status | Low | Return 201 Created |
| No warehouse check | Medium | Verify warehouse exists first |

---

## Part 2: Database Design

> Full schema: `part2_database_design/schema.sql`

### Entity-Relationship Overview

```
Companies ─┬── Warehouses
            ├── Products ──── Product Categories
            ├── Suppliers
            └── Purchase Orders

Products ──┬── Inventory (per warehouse) ── Inventory Transactions
            ├── Product Suppliers (many-to-many with Suppliers)
            └── Product Bundles (self-referencing)
```

### Tables Designed

| Table | Purpose |
|-------|---------|
| `companies` | Tenant/business accounts |
| `warehouses` | Physical locations, each belongs to one company |
| `product_categories` | Optional grouping for products |
| `products` | Product catalog, belongs to a company |
| `product_bundles` | Self-referencing join table for bundle products |
| `suppliers` | Vendor companies that supply products |
| `product_suppliers` | Many-to-many: which supplier provides which product |
| `inventory` | Stock levels: product × warehouse |
| `inventory_transactions` | Audit trail for every stock change |
| `purchase_orders` | Orders placed to suppliers |
| `purchase_order_items` | Line items in a purchase order |

### Key Design Decisions

**1. SKU uniqueness is scoped to a company**
I used `UNIQUE (company_id, sku)` instead of a globally unique SKU. Reasoning: in a multi-tenant B2B SaaS, two different companies might independently use "PROD-001" as a SKU. It only needs to be unique within their own catalog. If global uniqueness is actually needed, this is an easy constraint change.

**2. `low_stock_threshold` lives on the Product**
Each product has its own threshold. A bag of screws might have a threshold of 500, while an expensive machine part might be 5. Putting it on the product (rather than a global setting) gives businesses flexibility. If they need per-warehouse thresholds later, we can move it to the `inventory` table.

**3. Inventory Transactions as an append-only audit log**
I never update old transaction records. Every stock change (sale, purchase, adjustment, transfer) is a new row. This gives a complete history and makes debugging stock discrepancies straightforward. The `quantity_change` is signed: positive for stock-in, negative for stock-out.

**4. `reserved_qty` in Inventory**
Added a `reserved_qty` column to track items that are committed to orders but not yet shipped. Available stock = `quantity - reserved_qty`. This is crucial for preventing overselling.

**5. Product Bundles as a join table**
A bundle is just a regular product with `product_type = 'bundle'`. The `product_bundles` table records which components (and how many of each) make up the bundle. The `CHECK (bundle_id <> component_id)` constraint prevents circular references at the single level. Deeply nested bundles would need application-level validation.

**6. Indexes**
I added indexes on:
- Foreign keys used in JOINs (company_id in warehouses, products, suppliers)
- Fields used in WHERE clauses (sku, transaction_type + created_at)
- A composite index on `inventory(warehouse_id, quantity)` specifically for the low-stock alert query

---

### Questions I'd Ask the Product Team

1. **Multi-currency support?** — If companies operate in different countries, do we need to store prices in multiple currencies? That would add a `currency` column and potentially an exchange rate table.

2. **Permissions / Roles?** — Who can create products vs. who can adjust inventory? We'd need a `users` table with roles and permissions. I left this out since it wasn't mentioned.

3. **Stock transfer between warehouses** — Is this a feature? If so, transfers should be a pair of linked inventory_transactions (transfer_out from warehouse A, transfer_in to warehouse B). I've accounted for this in the transaction_type enum.

4. **Expiry dates for perishable products?** — If some products expire, we might need a `batch` or `lot` concept with expiry dates. The `product_type = 'perishable'` flag is a placeholder for this.

5. **Bundle inventory model** — When someone buys a bundle, do we deduct from individual component stock, or does the bundle itself have its own stock count? This significantly changes how inventory works for bundles.

6. **Soft delete vs. hard delete?** — I used `is_active` flags for products, suppliers, and warehouses. Should deleted data be permanently removed or just hidden? For auditing purposes, soft delete is usually better.

7. **Units of measurement** — I added a `unit` column on products (pieces, kg, litres). But do we need unit conversion support (e.g., buying in pallets, selling in individual pieces)?

8. **Minimum order quantities** — When reordering from a supplier, is there a minimum order? This could go on `product_suppliers`.

---

## Part 3: API Implementation

> Full code: `part3_api_implementation/low_stock_alerts.py`

### Approach

The endpoint `GET /api/companies/{company_id}/alerts/low-stock` needs to:

1. Find all products where `current_stock < low_stock_threshold`
2. Only include products that have had recent sales (not dormant inventory)
3. Calculate an estimated "days until stockout"
4. Include supplier information for easy reordering
5. Handle pagination and optional filtering

### Query Strategy

I used a CTE-based SQL query with three parts:

```
recent_sales CTE    →  calculates total units sold per product/warehouse in the lookback window
preferred_supplier CTE  →  finds the best supplier for each product
main query          →  joins inventory + products + warehouses, filters on low stock + recent sales
```

### How `days_until_stockout` Works

Simple and practical estimation:

```
daily_sales_rate = total_units_sold / lookback_days
days_until_stockout = current_stock / daily_sales_rate
```

For example: 50 units sold in the last 30 days = ~1.67/day. Current stock is 10. Estimated stockout in 6 days.

This isn't perfect (it doesn't account for trends, seasonality, or weekends), but it's a reasonable starting point. For a production system, I'd consider using a moving average or exponential smoothing.

### Edge Cases Handled

| Edge Case | How It's Handled |
|-----------|-----------------|
| Company doesn't exist | Return 404 |
| No low-stock products | Return empty `alerts: []` with `total_alerts: 0` |
| Product has no supplier | `supplier` field is `null` in response (LEFT JOIN) |
| Zero sales rate | `days_until_stockout` returns `null` |
| Invalid query parameters | Defaults applied (page=1, per_page=50, days=30) |
| Very large result sets | Pagination with configurable page size (max 100) |
| Product exists in multiple warehouses | Each warehouse generates its own alert |
| Inactive/discontinued products | Filtered out (`is_active = TRUE`) |
| Database errors | Caught, logged, return 500 with clean message |

### Optional Query Parameters

The assignment only mentioned the basic endpoint, but I added optional parameters that make the API more useful:

- `?warehouse_id=5` — filter alerts to a specific warehouse
- `?days=14` — change the lookback window for "recent" sales (default: 30)
- `?page=2&per_page=25` — pagination

### Assumptions Made

1. **"Recent sales activity"** = at least one `sale` transaction in the `inventory_transactions` table within the last 30 days (configurable via query param).
2. **Preferred supplier** is determined by the `is_preferred` flag in `product_suppliers`. If multiple are marked preferred, we pick the one with the shortest lead time. If none is preferred, we fall back to alphabetical order.
3. **Low stock threshold is per-product**, stored in the `products.low_stock_threshold` column. Different products can have different thresholds.
4. **We're using the schema from Part 2**, specifically the `products`, `inventory`, `inventory_transactions`, `product_suppliers`, and `suppliers` tables.
5. **Alerts are sorted by urgency** — products closest to running out (lowest stock-to-threshold ratio) appear first.

---

## Assumptions & Open Questions

### Global Assumptions

- This is a **Python/Flask** application using **SQLAlchemy** as the ORM and **PostgreSQL** as the database.
- The application is **multi-tenant** — each company's data is isolated by `company_id` foreign keys (row-level multi-tenancy, not schema-per-tenant).
- Authentication and authorization are handled by middleware before the request reaches these endpoints (not shown in the code).
- All timestamps are stored in UTC.

### Questions I Would Ask Before Building

1. How are we handling **authentication**? API keys, JWT tokens, OAuth? This affects how we identify which company is making the request.
2. What's the **expected scale**? 100 companies or 10,000? This impacts whether row-level multi-tenancy is sufficient or if we need schema/database isolation.
3. Are there **webhook/notification** requirements? Should the system push alerts to Slack, email, or an external system when stock gets low?
4. How should **bundles** affect low-stock alerts? If a bundle's components are running low, should the bundle itself show up in alerts?
5. Is there a concept of **safety stock** separate from low-stock threshold? (Safety stock = minimum you want to always keep; low-stock threshold = when to start reordering.)

---

*End of submission.*
