-- =============================================================
-- Part 2: Database Schema for StockFlow
-- =============================================================
-- Inventory Management System — B2B SaaS
-- Designed for: multi-warehouse, multi-supplier, product bundles
-- =============================================================


-- -------------------------------------------------
-- 1. Companies  (tenants / business accounts)
-- -------------------------------------------------
CREATE TABLE companies (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    email           VARCHAR(255),
    phone           VARCHAR(50),
    address         TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


-- -------------------------------------------------
-- 2. Warehouses  (each belongs to one company)
-- -------------------------------------------------
CREATE TABLE warehouses (
    id              SERIAL PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name            VARCHAR(255) NOT NULL,
    location        TEXT,                   -- address or geo-coordinates
    is_active       BOOLEAN DEFAULT TRUE,   -- soft-disable a warehouse
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_warehouses_company ON warehouses(company_id);


-- -------------------------------------------------
-- 3. Product Categories  (optional grouping)
-- -------------------------------------------------
CREATE TABLE product_categories (
    id              SERIAL PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name            VARCHAR(255) NOT NULL,
    description     TEXT
);


-- -------------------------------------------------
-- 4. Products
-- -------------------------------------------------
-- NOTE: Products belong to a company, NOT to a warehouse.
-- The warehouse relationship is in the inventory table.
CREATE TABLE products (
    id                  SERIAL PRIMARY KEY,
    company_id          INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    category_id         INTEGER REFERENCES product_categories(id) ON DELETE SET NULL,
    name                VARCHAR(255) NOT NULL,
    sku                 VARCHAR(100) NOT NULL,
    description         TEXT,
    price               NUMERIC(12, 2) NOT NULL CHECK (price >= 0),
    cost_price          NUMERIC(12, 2) CHECK (cost_price >= 0),    -- what we pay the supplier
    product_type        VARCHAR(50) DEFAULT 'standard',            -- 'standard', 'bundle', 'perishable'
    low_stock_threshold INTEGER DEFAULT 10 CHECK (low_stock_threshold >= 0),
    unit                VARCHAR(50) DEFAULT 'pieces',              -- pieces, kg, litres, etc.
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_sku_per_company UNIQUE (company_id, sku)
);

CREATE INDEX idx_products_company ON products(company_id);
CREATE INDEX idx_products_sku ON products(sku);


-- -------------------------------------------------
-- 5. Product Bundles  (self-referencing many-to-many)
-- -------------------------------------------------
-- A "bundle" product contains other products.
-- e.g., "Starter Kit" = 2× Widget A + 1× Widget B
CREATE TABLE product_bundles (
    id              SERIAL PRIMARY KEY,
    bundle_id       INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    component_id    INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    quantity        INTEGER NOT NULL CHECK (quantity > 0),

    -- prevent adding the same component twice
    CONSTRAINT uq_bundle_component UNIQUE (bundle_id, component_id),
    -- prevent a product from being a component of itself
    CONSTRAINT chk_no_self_bundle CHECK (bundle_id <> component_id)
);


-- -------------------------------------------------
-- 6. Suppliers
-- -------------------------------------------------
CREATE TABLE suppliers (
    id              SERIAL PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name            VARCHAR(255) NOT NULL,
    contact_email   VARCHAR(255),
    contact_phone   VARCHAR(50),
    address         TEXT,
    lead_time_days  INTEGER DEFAULT 7,      -- how long they take to deliver
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_suppliers_company ON suppliers(company_id);


-- -------------------------------------------------
-- 7. Product–Supplier relationship  (many-to-many)
-- -------------------------------------------------
-- A product can be sourced from multiple suppliers.
-- Each supplier may have a different price or lead time.
CREATE TABLE product_suppliers (
    id              SERIAL PRIMARY KEY,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    supplier_id     INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    supply_price    NUMERIC(12, 2),         -- price from this supplier
    lead_time_days  INTEGER,                -- override supplier-level default
    is_preferred    BOOLEAN DEFAULT FALSE,  -- mark the go-to supplier

    CONSTRAINT uq_product_supplier UNIQUE (product_id, supplier_id)
);


-- -------------------------------------------------
-- 8. Inventory  (product × warehouse stock levels)
-- -------------------------------------------------
CREATE TABLE inventory (
    id              SERIAL PRIMARY KEY,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    warehouse_id    INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE CASCADE,
    quantity        INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    reserved_qty    INTEGER NOT NULL DEFAULT 0 CHECK (reserved_qty >= 0),
    last_counted_at TIMESTAMP,              -- manual stock-take date
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_product_warehouse UNIQUE (product_id, warehouse_id)
);

CREATE INDEX idx_inventory_product ON inventory(product_id);
CREATE INDEX idx_inventory_warehouse ON inventory(warehouse_id);
-- Useful for the low-stock alert query:
CREATE INDEX idx_inventory_low_stock ON inventory(warehouse_id, quantity);


-- -------------------------------------------------
-- 9. Inventory Transactions  (audit trail)
-- -------------------------------------------------
-- Every stock change is recorded here (incoming, outgoing, adjustments).
CREATE TABLE inventory_transactions (
    id              SERIAL PRIMARY KEY,
    inventory_id    INTEGER NOT NULL REFERENCES inventory(id) ON DELETE CASCADE,
    product_id      INTEGER NOT NULL REFERENCES products(id),
    warehouse_id    INTEGER NOT NULL REFERENCES warehouses(id),
    transaction_type VARCHAR(50) NOT NULL,  -- 'purchase', 'sale', 'adjustment', 'transfer_in', 'transfer_out', 'return'
    quantity_change INTEGER NOT NULL,        -- positive = stock in, negative = stock out
    reference_id    VARCHAR(100),            -- e.g., order number or PO number
    notes           TEXT,
    performed_by    VARCHAR(255),            -- user who made the change
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_inv_txn_inventory ON inventory_transactions(inventory_id);
CREATE INDEX idx_inv_txn_product ON inventory_transactions(product_id);
CREATE INDEX idx_inv_txn_created ON inventory_transactions(created_at);
-- For the "recent sales" check in the low-stock alert:
CREATE INDEX idx_inv_txn_type_date ON inventory_transactions(transaction_type, created_at);


-- -------------------------------------------------
-- 10. Purchase Orders  (ordering from suppliers)
-- -------------------------------------------------
CREATE TABLE purchase_orders (
    id              SERIAL PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    supplier_id     INTEGER NOT NULL REFERENCES suppliers(id),
    warehouse_id    INTEGER NOT NULL REFERENCES warehouses(id),
    status          VARCHAR(50) DEFAULT 'draft',  -- draft, submitted, received, cancelled
    total_amount    NUMERIC(12, 2),
    order_date      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expected_date   TIMESTAMP,
    received_date   TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE purchase_order_items (
    id              SERIAL PRIMARY KEY,
    po_id           INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
    product_id      INTEGER NOT NULL REFERENCES products(id),
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    unit_price      NUMERIC(12, 2) NOT NULL
);
