PRAGMA foreign_keys = ON;

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
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(brand, model)
);

CREATE TABLE IF NOT EXISTS warehouses (
  code TEXT PRIMARY KEY,
  title TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stock (
  warehouse_code TEXT NOT NULL,
  product_id INTEGER NOT NULL,
  qty REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (warehouse_code, product_id),
  FOREIGN KEY (warehouse_code) REFERENCES warehouses(code) ON DELETE CASCADE,
  FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS invoices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  invoice_no INTEGER NOT NULL UNIQUE,
  client_id INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  total REAL NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  pdf_path TEXT,
  FOREIGN KEY (client_id) REFERENCES clients(id)
);

CREATE TABLE IF NOT EXISTS invoice_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  invoice_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  qty REAL NOT NULL,
  unit_price REAL NOT NULL,
  price_mode TEXT NOT NULL,
  line_total REAL NOT NULL,
  FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
  FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  amount REAL NOT NULL,
  note TEXT,
  FOREIGN KEY (client_id) REFERENCES clients(id)
);
