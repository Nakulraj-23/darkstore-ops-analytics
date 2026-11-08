CREATE TABLE stores (
    store_id    TEXT PRIMARY KEY,
    store_name  TEXT NOT NULL,
    zone        TEXT NOT NULL,
    sq_ft       INTEGER,
    avg_catchment_km REAL
);

CREATE TABLE skus (
    sku_id     TEXT PRIMARY KEY,
    sku_name   TEXT NOT NULL,
    category   TEXT NOT NULL,
    price      REAL NOT NULL,
    unit_cost  REAL NOT NULL
);

CREATE TABLE orders (
    order_id        TEXT PRIMARY KEY,
    store_id        TEXT NOT NULL REFERENCES stores(store_id),
    customer_id     TEXT NOT NULL,
    order_ts        TEXT NOT NULL,
    ops_date        TEXT NOT NULL,      -- operating day (orders after midnight roll back to it)
    order_hour      INTEGER NOT NULL,
    is_rain_day     INTEGER NOT NULL,
    distance_km     REAL,
    items_requested INTEGER NOT NULL,
    items_fulfilled INTEGER NOT NULL,
    gmv             REAL NOT NULL,      -- value of what was actually delivered
    lost_gmv        REAL NOT NULL,      -- value of out-of-stock lines
    pick_min        REAL,
    rider_wait_min  REAL,
    travel_min      REAL,
    delivery_min    REAL,               -- order placed -> handed to customer
    status          TEXT NOT NULL,      -- delivered | cancelled_oos
    rating          INTEGER
);

CREATE TABLE order_items (
    order_id   TEXT NOT NULL REFERENCES orders(order_id),
    sku_id     TEXT NOT NULL REFERENCES skus(sku_id),
    qty        INTEGER NOT NULL,
    price      REAL NOT NULL,
    fulfilled  INTEGER NOT NULL         -- 0 = out of stock
);

CREATE TABLE rider_shifts (
    store_id       TEXT NOT NULL,
    ops_date       TEXT NOT NULL,
    hour           INTEGER NOT NULL,
    riders_planned INTEGER NOT NULL
);

CREATE INDEX ix_orders_store_hour ON orders(store_id, ops_date, order_hour);
CREATE INDEX ix_items_order ON order_items(order_id);
CREATE INDEX ix_items_sku ON order_items(sku_id);
