-- Dark-store operations analysis. Each block starts with "-- name:" so the runner
-- can execute them one at a time. SQLite 3.25+ (window functions).

-- name: 01_store_scorecard
-- Q: Which stores are healthy and which are not? One row per store, ranked by on-time %.
SELECT
    s.store_name,
    COUNT(*)                                                         AS orders,
    ROUND(SUM(o.gmv) / 100000.0, 1)                                  AS gmv_lakh,
    ROUND(AVG(CASE WHEN o.status = 'delivered' THEN o.gmv END), 0)   AS aov,
    ROUND(100.0 * AVG(o.delivery_min <= 10), 1)                      AS pct_in_10_min,
    ROUND(100.0 * AVG(o.delivery_min > 15), 1)                       AS pct_over_15_min,
    ROUND(AVG(o.delivery_min), 1)                                    AS avg_delivery_min,
    ROUND(100.0 * SUM(o.items_fulfilled) / SUM(o.items_requested), 1) AS line_fill_rate,
    ROUND(100.0 * AVG(o.status = 'cancelled_oos'), 2)                AS pct_cancelled_oos,
    ROUND(AVG(o.rating), 2)                                          AS avg_rating,
    RANK() OVER (ORDER BY AVG(o.delivery_min <= 10) DESC)            AS on_time_rank
FROM orders o
JOIN stores s USING (store_id)
WHERE o.status = 'delivered' OR o.status = 'cancelled_oos'
GROUP BY s.store_name
ORDER BY on_time_rank;

-- name: 02_where_the_minutes_go
-- Q: For each store, which leg of the delivery is slow: picking, waiting for a rider, or the ride?
SELECT
    s.store_name,
    ROUND(AVG(pick_min), 2)       AS avg_pick,
    ROUND(AVG(rider_wait_min), 2) AS avg_rider_wait,
    ROUND(AVG(travel_min), 2)     AS avg_travel,
    ROUND(AVG(delivery_min), 2)   AS avg_total,
    CASE
        WHEN AVG(rider_wait_min) >= MAX(AVG(pick_min), AVG(travel_min)) THEN 'rider wait'
        WHEN AVG(travel_min) >= AVG(pick_min) THEN 'travel'
        ELSE 'picking'
    END                           AS biggest_leg
FROM orders o
JOIN stores s USING (store_id)
WHERE status = 'delivered'
GROUP BY s.store_name
ORDER BY avg_total DESC;

-- name: 03_peak_vs_offpeak
-- Q: Which stores fall apart specifically at the evening peak (18:00-22:59)?
--    A store slow all day has a structural issue; a store slow only at peak has a capacity issue.
SELECT s.store_name,
       ROUND(100.0 * AVG(CASE WHEN order_hour NOT BETWEEN 18 AND 22 THEN delivery_min > 15 END), 1) AS offpeak_pct_over_15,
       ROUND(100.0 * AVG(CASE WHEN order_hour BETWEEN 18 AND 22 THEN delivery_min > 15 END), 1)     AS peak_pct_over_15,
       ROUND(AVG(CASE WHEN order_hour BETWEEN 18 AND 22 THEN rider_wait_min END), 2)                AS peak_rider_wait,
       ROUND(AVG(CASE WHEN order_hour BETWEEN 18 AND 22 THEN delivery_min > 15 END)
           / AVG(CASE WHEN order_hour NOT BETWEEN 18 AND 22 THEN delivery_min > 15 END), 1)         AS peak_multiple
FROM orders o JOIN stores s USING (store_id)
WHERE status = 'delivered' AND is_rain_day = 0
GROUP BY s.store_name
ORDER BY peak_multiple DESC;

-- name: 04_rider_load_vs_breach
-- Q: Is late delivery explained by orders-per-rider? Join the roster to actual demand.
WITH load AS (
    SELECT o.store_id, o.ops_date, o.order_hour,
           COUNT(*)                         AS orders,
           r.riders_planned,
           1.0 * COUNT(*) / r.riders_planned AS orders_per_rider,
           AVG(o.delivery_min > 15)          AS breach
    FROM orders o
    JOIN rider_shifts r
      ON r.store_id = o.store_id AND r.ops_date = o.ops_date AND r.hour = o.order_hour
    WHERE o.status = 'delivered'
    GROUP BY o.store_id, o.ops_date, o.order_hour
)
SELECT
    CASE WHEN orders_per_rider < 1.5 THEN 'a) < 1.5'
         WHEN orders_per_rider < 2.0 THEN 'b) 1.5 - 2.0'
         WHEN orders_per_rider < 2.5 THEN 'c) 2.0 - 2.5'
         WHEN orders_per_rider < 3.0 THEN 'd) 2.5 - 3.0'
         ELSE 'e) 3.0+' END              AS orders_per_rider_hour,
    COUNT(*)                             AS store_hours,
    SUM(orders)                          AS orders,
    ROUND(100.0 * SUM(breach * orders) / SUM(orders), 1) AS pct_over_15_min
FROM load
GROUP BY 1
ORDER BY 1;

-- name: 05_velachery_roster_gap
-- Q: Velachery's roster vs demand by hour (avg across days). How many riders short at peak?
--    Target: 2.2 orders per rider-hour (where breach stays low in query 04).
WITH d AS (
    SELECT o.order_hour, o.ops_date, COUNT(*) AS orders, r.riders_planned
    FROM orders o
    JOIN rider_shifts r ON r.store_id = o.store_id AND r.ops_date = o.ops_date AND r.hour = o.order_hour
    WHERE o.store_id = 'S01' AND o.status = 'delivered'
    GROUP BY o.order_hour, o.ops_date
)
SELECT printf('%02d:00', order_hour)               AS hour,
       ROUND(AVG(orders), 0)                       AS avg_orders,
       ROUND(AVG(riders_planned), 0)               AS riders_planned,
       ROUND(AVG(orders) / 2.2, 0)                 AS riders_needed,
       ROUND(AVG(orders) / 2.2 - AVG(riders_planned), 0) AS gap
FROM d
GROUP BY order_hour
ORDER BY (order_hour + 18) % 24;   -- 06:00 first

-- name: 06_stockouts_by_daypart
-- Q: Where do stockouts concentrate? Fill rate by store x category x daypart, worst 10 cells.
SELECT s.store_name, k.category,
       CASE WHEN o.order_hour BETWEEN 6 AND 10 THEN 'morning 06-11'
            WHEN o.order_hour BETWEEN 11 AND 16 THEN 'afternoon 11-17'
            ELSE 'evening/night' END            AS daypart,
       COUNT(*)                                 AS lines,
       ROUND(100.0 * AVG(i.fulfilled), 1)       AS fill_rate,
       ROUND(SUM(CASE WHEN i.fulfilled = 0 THEN i.price * i.qty END) / 1000.0, 1) AS lost_gmv_k
FROM order_items i
JOIN orders o USING (order_id)
JOIN skus k   USING (sku_id)
JOIN stores s ON s.store_id = o.store_id
GROUP BY 1, 2, 3
HAVING lines > 500
ORDER BY fill_rate ASC
LIMIT 10;

-- name: 07_distance_tail
-- Q: Which store's catchment is too big? Share of orders > 3 km and the 90th-percentile distance.
WITH ranked AS (
    SELECT store_id, distance_km, delivery_min, rider_wait_min,
           ROW_NUMBER() OVER (PARTITION BY store_id ORDER BY distance_km) AS rn,
           COUNT(*)     OVER (PARTITION BY store_id)                      AS n
    FROM orders WHERE status = 'delivered'
)
SELECT s.store_name,
       ROUND(AVG(distance_km), 2)                                    AS avg_km,
       MAX(CASE WHEN rn = CAST(0.9 * n AS INT) THEN distance_km END) AS p90_km,
       ROUND(100.0 * AVG(distance_km > 3), 1)                        AS pct_over_3km,
       ROUND(100.0 * AVG(CASE WHEN distance_km > 3 THEN delivery_min > 15 END), 1) AS breach_when_over_3km,
       ROUND(AVG(CASE WHEN distance_km > 3 THEN rider_wait_min END), 2)           AS wait_when_over_3km
FROM ranked JOIN stores s USING (store_id)
GROUP BY s.store_name
ORDER BY pct_over_3km DESC;

-- name: 08_rain_effect
-- Q: What does rain do to the network? Per-store-day averages, rain vs dry.
WITH sd AS (
    SELECT store_id, ops_date, is_rain_day,
           COUNT(*) AS orders, AVG(delivery_min) AS avg_min,
           AVG(delivery_min > 15) AS breach,
           1.0 * SUM(items_fulfilled) / SUM(items_requested) AS fill
    FROM orders GROUP BY store_id, ops_date, is_rain_day
)
SELECT CASE is_rain_day WHEN 1 THEN 'rain' ELSE 'dry' END AS day_type,
       COUNT(DISTINCT ops_date)        AS days,
       ROUND(AVG(orders), 0)           AS orders_per_store_day,
       ROUND(AVG(avg_min), 2)          AS avg_delivery_min,
       ROUND(100.0 * AVG(breach), 1)   AS pct_over_15_min,
       ROUND(100.0 * AVG(fill), 1)     AS line_fill_rate
FROM sd GROUP BY is_rain_day;

-- name: 09_late_delivery_retention
-- Q: Does a late delivery cost us the customer? 14-day reorder rate by delivery-time bucket.
--    Only orders up to 14 Oct so every order has a full 14-day window.
WITH seq AS (
    SELECT customer_id, order_ts, delivery_min,
           LEAD(order_ts) OVER (PARTITION BY customer_id ORDER BY order_ts) AS next_ts
    FROM orders WHERE status = 'delivered'
)
SELECT CASE WHEN delivery_min <= 10 THEN 'a) <= 10 min'
            WHEN delivery_min <= 15 THEN 'b) 10 - 15 min'
            WHEN delivery_min <= 20 THEN 'c) 15 - 20 min'
            ELSE 'd) > 20 min' END                       AS delivery_bucket,
       COUNT(*)                                          AS orders,
       ROUND(100.0 * AVG(next_ts IS NOT NULL
             AND julianday(next_ts) - julianday(order_ts) <= 14), 1) AS reorder_14d_pct
FROM seq
WHERE order_ts < '2026-10-15'
GROUP BY 1 ORDER BY 1;

-- name: 10_daily_trend_7dma
-- Q: Network daily orders and on-time %, with a 7-day moving average to read through rain spikes.
WITH day AS (
    SELECT ops_date, MAX(is_rain_day) AS rain, COUNT(*) AS orders,
           100.0 * AVG(delivery_min <= 10) AS pct_in_10
    FROM orders WHERE status = 'delivered' GROUP BY ops_date
)
SELECT ops_date, CASE rain WHEN 1 THEN 'rain' ELSE '' END AS rain, orders,
       ROUND(AVG(orders) OVER (ORDER BY ops_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 0) AS orders_7dma,
       ROUND(pct_in_10, 1) AS pct_in_10_min,
       ROUND(AVG(pct_in_10) OVER (ORDER BY ops_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 1) AS pct_in_10_7dma
FROM day ORDER BY ops_date;

-- name: 11_lost_gmv_by_issue
-- Q: Size each problem in rupees per month (28 days in data).
SELECT 'Anna Nagar morning dairy/bread stockouts' AS issue,
       ROUND(SUM(i.price * i.qty) / 100000.0, 2) AS lost_gmv_lakh
FROM order_items i JOIN orders o USING (order_id) JOIN skus k USING (sku_id)
WHERE o.store_id = 'S02' AND k.category = 'Dairy & Bread'
  AND o.order_hour BETWEEN 6 AND 10 AND i.fulfilled = 0
UNION ALL
SELECT 'All other stockouts (network baseline)',
       ROUND(SUM(i.price * i.qty) / 100000.0, 2)
FROM order_items i JOIN orders o USING (order_id) JOIN skus k USING (sku_id)
WHERE i.fulfilled = 0
  AND NOT (o.store_id = 'S02' AND k.category = 'Dairy & Bread' AND o.order_hour BETWEEN 6 AND 10)
UNION ALL
SELECT 'Velachery 18:00-22:59 GMV delivered > 15 min (exposed to the reorder drop in query 09)',
       ROUND(SUM(gmv) / 100000.0, 2)
FROM orders WHERE store_id = 'S01' AND order_hour BETWEEN 18 AND 22 AND delivery_min > 15
UNION ALL
SELECT 'Sholinganallur GMV delivered > 15 min (exposed to the reorder drop)',
       ROUND(SUM(gmv) / 100000.0, 2)
FROM orders WHERE store_id = 'S03' AND delivery_min > 15;

-- name: 12_stockout_windows
-- Q: Gaps-and-islands. When is a whole category unavailable for 2+ hours in a row, and how much does it cost?
--    An hour is flagged when >= 20% of a store-category's order lines are out of stock (network baseline ~2.5%).
--    Consecutive flagged hours share the same (hour_seq - ROW_NUMBER()) value, which labels each island.
WITH cat_hour AS (
    SELECT o.store_id, k.category, o.ops_date, o.order_hour,
           CAST(julianday(o.ops_date) - julianday('2026-10-01') AS INT) * 24
             + (o.order_hour + 18) % 24                     AS hour_seq,
           COUNT(*)                                         AS lines,
           1.0 - AVG(i.fulfilled)                           AS oos_share,
           SUM(CASE WHEN i.fulfilled = 0 THEN i.price * i.qty ELSE 0 END) AS lost_gmv
    FROM order_items i
    JOIN orders o USING (order_id)
    JOIN skus k   USING (sku_id)
    GROUP BY o.store_id, k.category, o.ops_date, o.order_hour
    HAVING COUNT(*) >= 20
),
flagged AS (
    SELECT *, hour_seq - ROW_NUMBER() OVER (PARTITION BY store_id, category ORDER BY hour_seq) AS island
    FROM cat_hour
    WHERE oos_share >= 0.20
),
windows AS (
    SELECT store_id, category, island,
           MIN(ops_date) AS ops_date, MIN(hour_seq) AS start_seq, MAX(hour_seq) AS end_seq,
           COUNT(*) AS hours, SUM(lost_gmv) AS lost_gmv
    FROM flagged
    GROUP BY store_id, category, island
)
SELECT s.store_name, w.category,
       COUNT(*) AS windows,
       ROUND(AVG(w.hours), 1) AS avg_hours,
       printf('%02d:00', MIN((w.start_seq % 24 + 6) % 24)) AS earliest_start,
       printf('%02d:59', MAX((w.end_seq % 24 + 6) % 24))   AS latest_end,
       ROUND(SUM(w.lost_gmv) / 1000.0, 1) AS lost_gmv_k
FROM windows w JOIN stores s USING (store_id)
WHERE w.hours >= 2
GROUP BY s.store_name, w.category
ORDER BY lost_gmv_k DESC;

-- name: 13_store_contribution
-- Q: Which stores make money? Contribution per order, fixed cost, break-even orders/day and daily profit.
--    Costs are assumptions in the params CTE, calibrated so contribution is ~4-5% of AOV (low single digits, as in public q-commerce disclosures).
WITH params AS (            -- assumptions (Rs); change here, everything below follows
    SELECT 135.0 AS rider_cost_per_hour,      -- payout + incentives + insurance
           15.0  AS pick_pack_cost_per_order,  -- bags + variable picker time
           0.02  AS payment_fee_pct,
           0.04  AS discount_pct,
           0.015 AS wastage_pct,               -- expiry, damage, shrinkage
           0.03  AS ad_income_pct,             -- brand ads and supplier funding
           75.0  AS rent_per_sqft_month,
           12000.0 AS staff_power_per_day      -- manager, security, base crew, power
),
sales AS (
    SELECT o.store_id,
           COUNT(DISTINCT o.order_id)                AS orders,
           SUM(i.price * i.qty)                      AS gmv,
           SUM((i.price - k.unit_cost) * i.qty)      AS product_margin
    FROM orders o
    JOIN order_items i USING (order_id)
    JOIN skus k USING (sku_id)
    WHERE o.status = 'delivered' AND i.fulfilled = 1
    GROUP BY o.store_id
),
riders AS (
    SELECT store_id, SUM(riders_planned) AS rider_hours FROM rider_shifts GROUP BY store_id
),
per_store AS (
    SELECT s.store_id, st.store_name, st.sq_ft,
           s.orders / 28.0 AS orders_day,
           s.gmv / s.orders AS aov,
           ( s.product_margin
           + s.gmv * (p.ad_income_pct - p.payment_fee_pct - p.discount_pct - p.wastage_pct)
           - s.orders * p.pick_pack_cost_per_order
           - r.rider_hours * p.rider_cost_per_hour ) / s.orders AS contribution_per_order,
           st.sq_ft * p.rent_per_sqft_month * 12 / 365.0 + p.staff_power_per_day AS fixed_cost_day
    FROM sales s
    JOIN riders r USING (store_id)
    JOIN stores st USING (store_id)
    CROSS JOIN params p
)
SELECT store_name,
       ROUND(orders_day, 0)                                    AS orders_day,
       ROUND(aov, 0)                                           AS aov,
       ROUND(contribution_per_order, 1)                        AS contrib_per_order,
       ROUND(100.0 * contribution_per_order / aov, 1)          AS contrib_pct_aov,
       ROUND(fixed_cost_day, 0)                                AS fixed_cost_day,
       ROUND(fixed_cost_day / contribution_per_order, 0)       AS breakeven_orders_day,
       ROUND(orders_day * contribution_per_order - fixed_cost_day, 0) AS store_profit_day,
       RANK() OVER (ORDER BY orders_day * contribution_per_order - fixed_cost_day DESC) AS profit_rank
FROM per_store
ORDER BY profit_rank;
