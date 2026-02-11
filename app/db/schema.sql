PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS warehouses (
  code TEXT PRIMARY KEY,
  title TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  brand TEXT NOT NULL,
  model TEXT NOT NULL,
  name TEXT NOT NULL,
  wh_price REAL NOT NULL,
  UNIQUE(brand, model)
);

CREATE TABLE IF NOT EXISTS stock (
  warehouse_code TEXT NOT NULL,
  product_id INTEGER NOT NULL,
  qty REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (warehouse_code, product_id),
  FOREIGN KEY (warehouse_code) REFERENCES warehouses(code) ON DELETE CASCADE,
  FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

-- carts
CREATE TABLE IF NOT EXISTS carts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  status TEXT NOT NULL DEFAULT 'OPEN', -- OPEN / CLOSED
  FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cart_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cart_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  qty REAL NOT NULL,
  price_mode TEXT NOT NULL, -- wh / wh10 / custom
  unit_price REAL NOT NULL,
  total REAL NOT NULL,
  FOREIGN KEY (cart_id) REFERENCES carts(id) ON DELETE CASCADE,
  FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

-- invoices
CREATE TABLE IF NOT EXISTS invoices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cart_id INTEGER NOT NULL UNIQUE,
  number INTEGER NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  currency TEXT NOT NULL DEFAULT 'USD',
  total REAL NOT NULL,
  FOREIGN KEY (cart_id) REFERENCES carts(id) ON DELETE CASCADE
);

-- Brands master data
CREATE TABLE IF NOT EXISTS brands (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Stock operations journal (for audit & reporting)
CREATE TABLE IF NOT EXISTS stock_ops (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  op_type TEXT NOT NULL,                 -- e.g. 'RECEIVE'
  source TEXT NOT NULL,                  -- 'CHINA' | 'DEALER'
  warehouse_code TEXT NOT NULL,          -- e.g. 'TM_DEPO' | '1416_SHOP'
  product_id INTEGER NOT NULL,
  qty REAL NOT NULL,
  FOREIGN KEY(product_id) REFERENCES products(id)
);

CREATE INDEX IF NOT EXISTS idx_stock_ops_created_at ON stock_ops(created_at);
CREATE INDEX IF NOT EXISTS idx_stock_ops_product_id ON stock_ops(product_id);
CREATE INDEX IF NOT EXISTS idx_stock_ops_warehouse_code ON stock_ops(warehouse_code);