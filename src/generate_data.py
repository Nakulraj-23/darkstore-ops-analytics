"""
Simulates 28 days of quick-commerce operations for 12 Chennai dark stores.

The data is SYNTHETIC. Volumes, basket sizes and order values are calibrated to
public Eternal/Blinkit disclosures (store throughput, AOV range), and three
operational problems are deliberately hidden in the data so the analysis has
something real to find, the same way they hide in production data:

  1. Rider shift plan at one store is flat across the day, so evening peaks
     run out of riders and rider-wait explodes.
  2. One store's dairy/bread replenishment lands late morning, so the morning
     milk-and-bread rush hits empty shelves.
  3. One store's catchment is too large, so delivery distances (and times)
     have a long tail, i.e. it needs a new store, not more riders.

Plus a real-world effect: rain days lift demand and slow riders, and customers
who get a late delivery reorder less over the next two weeks.

Output: data/darkstore.db (SQLite) + CSVs.
"""
import csv
import math
import os
import random
import sqlite3
from datetime import date, datetime, timedelta

random.seed(42)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
os.makedirs(DATA, exist_ok=True)

START = date(2026, 10, 1)
DAYS = 28
RAIN_DAYS = {date(2026, 10, d) for d in (6, 7, 14, 15, 16, 22, 27)}  # NE monsoon onset

# store_id, name, zone, base orders/day, mean distance km, sq ft
STORES = [
    ("S01", "Velachery", "South", 1050, 1.5, 4200),
    ("S02", "Anna Nagar", "West", 1000, 1.4, 4000),
    ("S03", "Sholinganallur", "OMR", 880, 2.5, 3800),
    ("S04", "Adyar", "South", 950, 1.4, 3600),
    ("S05", "T Nagar", "Central", 1100, 1.2, 4400),
    ("S06", "Mylapore", "Central", 820, 1.3, 3200),
    ("S07", "Porur", "West", 700, 1.6, 3400),
    ("S08", "Tambaram", "South", 640, 1.6, 3500),
    ("S09", "Kattankulathur", "South", 520, 1.7, 3000),
    ("S10", "Guindy", "Central", 760, 1.5, 3300),
    ("S11", "Perungudi", "OMR", 800, 1.6, 3500),
    ("S12", "Kilpauk", "Central", 720, 1.3, 3100),
]
FLAT_RIDER_STORE = "S01"
LATE_DAIRY_STORE = "S02"
BIG_CATCHMENT_STORE = "S03"

# Share of daily orders by hour (store open 06:00 to 01:59)
HOUR_WEIGHTS = {
    6: 2.5, 7: 5.0, 8: 6.5, 9: 6.0, 10: 5.0, 11: 4.5, 12: 4.5, 13: 4.5,
    14: 3.8, 15: 3.6, 16: 4.0, 17: 5.0, 18: 6.5, 19: 8.0, 20: 8.5,
    21: 8.0, 22: 6.0, 23: 4.0, 0: 2.5, 1: 1.6,
}
_w = sum(HOUR_WEIGHTS.values())
HOUR_SHARE = {h: v / _w for h, v in HOUR_WEIGHTS.items()}
MORNING = {6, 7, 8, 9, 10}

# category, gross margin, [ (name, price) ... ], basket weight
CATALOG = [
    ("Dairy & Bread", 0.10, [("Toned Milk 500ml", 28), ("Full Cream Milk 1L", 68), ("Curd 400g", 45),
                             ("Paneer 200g", 95), ("Butter 100g", 58), ("Brown Bread", 50),
                             ("White Bread", 40), ("Eggs (6)", 54)], 2.6),
    ("Fruits & Vegetables", 0.28, [("Onion 1kg", 42), ("Tomato 1kg", 38), ("Potato 1kg", 36),
                                   ("Banana (6)", 48), ("Coriander", 12), ("Green Chilli 100g", 10),
                                   ("Apple 4pc", 160), ("Lemon 250g", 30)], 2.2),
    ("Snacks", 0.24, [("Potato Chips", 20), ("Namkeen 200g", 55), ("Biscuits Pack", 30),
                      ("Chocolate Bar", 50), ("Cookies 150g", 65)], 1.4),
    ("Beverages", 0.22, [("Cola 750ml", 40), ("Packaged Water 1L", 20), ("Cold Coffee", 45),
                         ("Juice 1L", 110), ("Tea 250g", 140)], 1.0),
    ("Staples", 0.12, [("Rice 5kg", 420), ("Atta 5kg", 260), ("Toor Dal 1kg", 165),
                       ("Sunflower Oil 1L", 155), ("Sugar 1kg", 48), ("Salt 1kg", 25)], 0.8),
    ("Instant Food", 0.20, [("Instant Noodles", 14), ("Ready-to-eat Meal", 99),
                            ("Pasta 500g", 85), ("Idli Batter 1kg", 75)], 1.0),
    ("Personal Care", 0.32, [("Toothpaste", 99), ("Shampoo 180ml", 185), ("Soap (4)", 160),
                             ("Face Wash", 210)], 0.5),
    ("Household", 0.30, [("Dishwash Liquid", 115), ("Detergent 1kg", 145), ("Garbage Bags", 99),
                         ("Floor Cleaner", 175)], 0.5),
    ("Ice Cream", 0.30, [("Ice Cream Tub", 199), ("Cone (4)", 160)], 0.4),
]

SKUS = []  # (sku_id, name, category, price, unit_cost, weight)
for cat, margin, items, cw in CATALOG:
    for name, price in items:
        sid = "K%03d" % (len(SKUS) + 1)
        SKUS.append((sid, name, cat, price, round(price * (1 - margin), 2), cw / len(items)))
SKU_W = [s[5] for s in SKUS]


def lognorm(mean, sigma):
    mu = math.log(mean) - sigma ** 2 / 2
    return random.lognormvariate(mu, sigma)


def poisson(lam):
    # Knuth; fine for small/medium lam, normal approx for large
    if lam > 50:
        return max(0, int(random.gauss(lam, math.sqrt(lam))))
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= random.random()
        if p <= L:
            return k
        k += 1


def main():
    db_path = os.path.join(DATA, "darkstore.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.executescript(open(os.path.join(ROOT, "sql", "schema.sql")).read())

    cur.executemany("INSERT INTO stores VALUES (?,?,?,?,?)",
                    [(s[0], s[1], s[2], s[5], s[4]) for s in STORES])
    cur.executemany("INSERT INTO skus VALUES (?,?,?,?,?)", [s[:5] for s in SKUS])

    order_rows, item_rows, shift_rows = [], [], []
    oid = 0
    for store_id, name, zone, base, mean_dist, sqft in STORES:
        n_customers = base * 22
        last_bad = {}  # customer -> date of last late (>15 min) delivery
        for d in range(DAYS):
            day = START + timedelta(days=d)
            rain = day in RAIN_DAYS
            weekend = day.weekday() >= 5
            day_mult = (1.18 if rain else 1.0) * (1.12 if weekend else 1.0) * (1 + 0.004 * d)
            expected_day = base * day_mult

            for hour, share in HOUR_SHARE.items():
                exp_orders = expected_day * share
                n_orders = poisson(exp_orders)

                # Rider plan: planned from the *non-rain* forecast for the hour,
                # except the flat-template store which spreads riders evenly.
                plan_base = base * (1.12 if weekend else 1.0)
                if store_id == FLAT_RIDER_STORE:
                    plan_orders = plan_base / len(HOUR_SHARE) * 1.15
                else:
                    plan_orders = plan_base * share
                riders = max(3, math.ceil(plan_orders / 2.6 * 1.1))
                shift_rows.append((store_id, day.isoformat(), hour, riders))

                speed = 16.5 if rain else 20.0  # km/h
                trips_per_rider = 60.0 / (2 * (mean_dist / speed * 60) + 12)  # ride out + back + handover/queue
                capacity = riders * trips_per_rider
                util = n_orders / capacity if capacity else 9
                base_wait = 0.4 + max(0.0, util - 0.85) * 9.0

                for _ in range(n_orders):
                    # pick a customer; recently-burned customers are less likely to come back
                    cust = None
                    for _try in range(4):
                        c = random.randint(1, n_customers)
                        lb = last_bad.get(c)
                        if lb is not None and (day - lb).days <= 14 and random.random() < 0.33:
                            continue
                        cust = c
                        break
                    if cust is None:
                        continue  # demand lost to churn
                    oid += 1
                    order_id = "O%07d" % oid
                    minute = random.randint(0, 59)
                    order_ts = datetime(day.year, day.month, day.day, hour, minute, random.randint(0, 59))
                    if hour < 6:  # after-midnight orders belong to the previous ops day but next calendar day
                        order_ts += timedelta(days=1)

                    n_lines = max(1, min(14, poisson(4.6) + 1))
                    lines = random.choices(SKUS, weights=SKU_W, k=n_lines)
                    seen, fulfilled_any = set(), False
                    gmv = lost = 0.0
                    req = ful = 0
                    for sku in lines:
                        if sku[0] in seen:
                            continue
                        seen.add(sku[0])
                        u = random.random()
                        qty = 1 if u < 0.68 else 2 if u < 0.93 else 3
                        p_oos = 0.022
                        if store_id == LATE_DAIRY_STORE and sku[2] == "Dairy & Bread" and hour in MORNING:
                            p_oos = 0.36
                        if rain and sku[2] == "Fruits & Vegetables":
                            p_oos = 0.07
                        ok = random.random() >= p_oos
                        req += 1
                        if ok:
                            ful += 1
                            fulfilled_any = True
                            gmv += sku[3] * qty
                        else:
                            lost += sku[3] * qty
                        item_rows.append((order_id, sku[0], qty, sku[3], int(ok)))

                    if not fulfilled_any:
                        status = "cancelled_oos"
                        pick = wait = travel = total = None
                        dist = None
                        rating = None
                    else:
                        if store_id == BIG_CATCHMENT_STORE:
                            dist = lognorm(mean_dist, 0.55)
                        else:
                            dist = lognorm(mean_dist, 0.38)
                        dist = round(min(dist, 9.0), 2)
                        pick = max(0.8, random.gauss(1.4 + 0.22 * ful, 0.35))
                        wait = max(0.0, random.gauss(base_wait, 0.4 + base_wait * 0.25))
                        wait = min(wait, 18.0)
                        travel = dist / speed * 60 + max(0.3, random.gauss(1.0, 0.3))
                        total = round(pick + wait + travel, 2)
                        status = "delivered"
                        if total > 10:
                            last_bad[cust] = day if total > 15 else last_bad.get(cust)
                            if last_bad[cust] is None:
                                del last_bad[cust]
                        r = 5 if total <= 10 else 4 if total <= 15 else 3 if total <= 20 else 2
                        if ful < req:
                            r -= 1
                        r = int(min(5, max(1, r + random.choice([0, 0, 0, 0, -1, 1]))))
                        rating = r
                        pick, wait, travel = round(pick, 2), round(wait, 2), round(travel, 2)

                    order_rows.append((
                        order_id, store_id, "C%s%06d" % (store_id[1:], cust),
                        order_ts.isoformat(sep=" "), day.isoformat(), hour, int(rain),
                        dist, req, ful, round(gmv, 2), round(lost, 2),
                        pick, wait, travel, total, status, rating,
                    ))

    cur.executemany("INSERT INTO orders VALUES (%s)" % ",".join("?" * 18), order_rows)
    cur.executemany("INSERT INTO order_items VALUES (?,?,?,?,?)", item_rows)
    cur.executemany("INSERT INTO rider_shifts VALUES (?,?,?,?)", shift_rows)
    con.commit()

    for table in ("stores", "skus", "orders", "rider_shifts"):
        rows = cur.execute("SELECT * FROM %s" % table)
        cols = [c[0] for c in rows.description]
        with open(os.path.join(DATA, table + ".csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(rows)
    con.close()
    print("orders:", len(order_rows), "order_items:", len(item_rows), "shift rows:", len(shift_rows))


if __name__ == "__main__":
    main()
