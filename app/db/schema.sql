PRAGMA foreign_keys = ON;

-- Клиенты
CREATE TABLE IF NOT EXISTS clients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Товары (brand + model = уникально)
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  brand TEXT NOT NULL,
  model TEXT NOT NULL,
  name TEXT NOT NULL,
  wholesale_price REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(brand, model)
);

-- Остатки по складам
CREATE TABLE IF NOT EXISTS stock (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  warehouse TEXT NOT NULL,              -- CHINA_DEPOT / WAREHOUSE / SHOP
  product_id INTEGER NOT NULL,
  qty INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(warehouse, product_id),
  FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
);

-- Движения (перемещения)
CREATE TABLE IF NOT EXISTS movements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  from_wh TEXT NOT NULL,
  to_wh TEXT NOT NULL,
  product_id INTEGER NOT NULL,
  qty INTEGER NOT NULL,
  note TEXT,
  FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
);

-- Инвойсы
CREATE TABLE IF NOT EXISTS invoices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  number INTEGER NOT NULL UNIQUE,
  client_id INTEGER NOT NULL,
  date TEXT NOT NULL DEFAULT (date('now')),
  currency TEXT NOT NULL DEFAULT 'USD',
  total REAL NOT NULL DEFAULT 0,
  is_debt INTEGER NOT NULL DEFAULT 1, -- 1=долг, 0=оплачено
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS invoice_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  invoice_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  qty INTEGER NOT NULL,
  price REAL NOT NULL,
  price_type TEXT NOT NULL,            -- wh / wh10 / custom
  line_total REAL NOT NULL,
  FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
  FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE RESTRICT
);
