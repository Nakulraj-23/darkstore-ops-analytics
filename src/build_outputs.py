"""Builds the Excel workbook, charts and impact numbers from data/darkstore.db."""
import json
import os
import sqlite3

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule, CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "results")
CH = os.path.join(OUT, "charts")
os.makedirs(CH, exist_ok=True)
con = sqlite3.connect(os.path.join(ROOT, "data", "darkstore.db"))
q = lambda sql: pd.read_sql_query(sql, con)

YELLOW = "F8CB46"   # quick-commerce yellow / green palette for charts
GREEN = "0C831F"
HDR = PatternFill("solid", fgColor="1F2937")
INPUT = PatternFill("solid", fgColor="FFF4C2")
thin = Side(style="thin", color="D1D5DB")
BOX = Border(left=thin, right=thin, top=thin, bottom=thin)

# ---------------------------------------------------------------- data pulls
score = q(open(os.path.join(ROOT, "sql", "analysis.sql")).read()
          .split("-- name: 01_store_scorecard")[1].split("-- name:")[0])
heat = q("""SELECT s.store_name, order_hour, ROUND(100.0*AVG(delivery_min>15),1) AS pct
            FROM orders o JOIN stores s USING(store_id)
            WHERE status='delivered' AND is_rain_day=0 GROUP BY 1,2""")
heat_p = heat.pivot(index="store_name", columns="order_hour", values="pct")
hour_order = [h for h in range(6, 24)] + [0, 1]
heat_p = heat_p[hour_order].loc[score["store_name"]]

vel = q("""SELECT o.order_hour AS hour, AVG(n) AS avg_orders, AVG(r.riders_planned) AS riders
           FROM (SELECT store_id, ops_date, order_hour, COUNT(*) n FROM orders
                 WHERE store_id='S01' AND status='delivered' AND is_rain_day=0
                 GROUP BY 1,2,3) o
           JOIN rider_shifts r ON r.store_id=o.store_id AND r.ops_date=o.ops_date AND r.hour=o.order_hour
           GROUP BY o.order_hour""")
vel["sort"] = (vel["hour"] + 18) % 24
vel = vel.sort_values("sort").drop(columns="sort").reset_index(drop=True)

fill = q("""SELECT s.store_name, k.category, ROUND(100.0*AVG(i.fulfilled),1) AS fill
            FROM order_items i JOIN orders o USING(order_id) JOIN skus k USING(sku_id)
            JOIN stores s ON s.store_id=o.store_id
            WHERE o.order_hour BETWEEN 6 AND 10 GROUP BY 1,2""")
fill_p = fill.pivot(index="store_name", columns="category", values="fill").loc[score["store_name"]]

ret = q("""WITH seq AS (SELECT customer_id, order_ts, delivery_min,
             LEAD(order_ts) OVER (PARTITION BY customer_id ORDER BY order_ts) next_ts
             FROM orders WHERE status='delivered')
           SELECT CASE WHEN delivery_min<=15 THEN 'On time (<= 15 min)' ELSE 'Late (> 15 min)' END AS bucket,
                  COUNT(*) AS orders,
                  ROUND(100.0*AVG(next_ts IS NOT NULL AND julianday(next_ts)-julianday(order_ts)<=14),1) AS reorder_pct
           FROM seq WHERE order_ts < '2026-10-15' GROUP BY 1 ORDER BY 1 DESC""")

dist = q("""SELECT s.store_name, distance_km FROM orders o JOIN stores s USING(store_id)
            WHERE status='delivered' AND store_id IN ('S03','S05','S01')""")

facts = {
    "orders": int(q("SELECT COUNT(*) n FROM orders")["n"][0]),
    "lines": int(q("SELECT COUNT(*) n FROM order_items")["n"][0]),
    "aov": float(q("SELECT AVG(gmv) a FROM orders WHERE status='delivered'")["a"][0]),
    "vel_late_peak_orders": int(q("""SELECT COUNT(*) n FROM orders WHERE store_id='S01'
                                     AND order_hour BETWEEN 18 AND 22 AND delivery_min>15""")["n"][0]),
    "sho_late_orders": int(q("SELECT COUNT(*) n FROM orders WHERE store_id='S03' AND delivery_min>15")["n"][0]),
    "sho_over3_pct": float(q("SELECT 100.0*AVG(distance_km>3) p FROM orders WHERE store_id='S03' AND status='delivered'")["p"][0]),
    "an_lost_gmv": float(q("""SELECT SUM(i.price*i.qty) v FROM order_items i JOIN orders o USING(order_id)
                              JOIN skus k USING(sku_id) WHERE o.store_id='S02' AND k.category='Dairy & Bread'
                              AND o.order_hour BETWEEN 6 AND 10 AND i.fulfilled=0""")["v"][0]),
    "an_dairy_fill": float(fill_p.loc["Anna Nagar", "Dairy & Bread"]),
    "net_dairy_fill": float(fill_p.drop(index="Anna Nagar")["Dairy & Bread"].mean()),
    "reorder_on_time": float(ret.iloc[0]["reorder_pct"]),
    "reorder_late": float(ret.iloc[1]["reorder_pct"]),
    "vel_rider_hours_now": float(vel["riders"].sum()),
}
TARGET = 2.2
vel["needed"] = (vel["avg_orders"] / TARGET).apply(lambda x: int(-(-x // 1)))
facts["vel_rider_hours_needed"] = float(vel["needed"].sum())
facts["vel_peak_gap"] = int((vel["needed"] - vel["riders"]).max())
drop = (facts["reorder_on_time"] - facts["reorder_late"]) / 100
facts["reorder_drop_pts"] = round(drop * 100, 1)
facts["vel_repeat_gmv_at_risk"] = facts["vel_late_peak_orders"] * drop * facts["aov"]
facts["sho_repeat_gmv_at_risk"] = facts["sho_late_orders"] * drop * facts["aov"]
json.dump(facts, open(os.path.join(OUT, "facts.json"), "w"), indent=2)

# ---------------------------------------------------------------- charts
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})

fig, ax = plt.subplots(figsize=(10, 4.8))
im = ax.imshow(heat_p.values, aspect="auto", cmap="YlOrRd", vmin=0, vmax=40)
ax.set_xticks(range(len(hour_order)), ["%02d" % h for h in hour_order])
ax.set_yticks(range(len(heat_p)), heat_p.index)
ax.set_xlabel("Hour of day (dry days)")
ax.set_title("% of orders delivered in more than 15 min, by store and hour")
fig.colorbar(im, ax=ax, label="% > 15 min")
fig.tight_layout(); fig.savefig(os.path.join(CH, "01_breach_heatmap.png"), dpi=160); plt.close(fig)

fig, ax = plt.subplots(figsize=(10, 4.2))
x = range(len(vel))
ax.bar(x, vel["needed"], color="#" + YELLOW, label="Riders needed (%.1f orders/rider-hr)" % TARGET)
ax.plot(x, vel["riders"], color="#" + GREEN, lw=2.5, label="Riders rostered today (flat)")
ax.set_xticks(list(x), ["%02d" % h for h in vel["hour"]])
ax.set_xlabel("Hour"); ax.set_ylabel("Riders")
ax.set_title("Velachery: flat roster vs demand-shaped need")
ax.legend(frameon=False)
fig.tight_layout(); fig.savefig(os.path.join(CH, "02_velachery_roster.png"), dpi=160); plt.close(fig)

fig, ax = plt.subplots(figsize=(8, 4))
for name, col in [("T Nagar", "#9CA3AF"), ("Velachery", "#" + YELLOW), ("Sholinganallur", "#DC2626")]:
    ax.hist(dist[dist.store_name == name]["distance_km"], bins=60, range=(0, 7), histtype="step",
            lw=2, color=col, label=name, density=True)
ax.axvline(3, ls="--", color="#374151"); ax.text(3.05, ax.get_ylim()[1] * 0.9, "3 km", fontsize=9)
ax.set_xlabel("Delivery distance (km)"); ax.set_ylabel("Share of orders")
ax.set_title("Sholinganallur's catchment has a long tail")
ax.legend(frameon=False)
fig.tight_layout(); fig.savefig(os.path.join(CH, "03_distance_tail.png"), dpi=160); plt.close(fig)

fig, ax = plt.subplots(figsize=(6, 3.6))
ax.bar(ret["bucket"], ret["reorder_pct"], color=["#" + GREEN, "#DC2626"])
for i, v in enumerate(ret["reorder_pct"]):
    ax.text(i, v + 1, "%.1f%%" % v, ha="center", fontweight="bold")
ax.set_ylim(0, 65); ax.set_ylabel("Reordered within 14 days")
ax.set_title("Late delivery costs repeat orders")
fig.tight_layout(); fig.savefig(os.path.join(CH, "04_retention.png"), dpi=160); plt.close(fig)

# ---------------------------------------------------------------- Excel
wb = Workbook()


def header(ws, row, cols, start=1):
    for j, c in enumerate(cols):
        cell = ws.cell(row=row, column=start + j, value=c)
        cell.font = Font(bold=True, color="FFFFFF"); cell.fill = HDR
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BOX


def widths(ws, w):
    for i, v in enumerate(w, 1):
        ws.column_dimensions[get_column_letter(i)].width = v


ws = wb.active; ws.title = "README"
lines = [
    ("Dark-Store Ops Analytics: Chennai network, Oct 2026 (simulated data)", True),
    ("", False),
    ("What this is", True),
    ("28 days, 12 dark stores, %s orders, %s order lines. Synthetic data calibrated to public quick-commerce metrics." %
     (format(facts["orders"], ","), format(facts["lines"], ",")), False),
    ("Yellow cells are inputs: change them and every dependent number recalculates.", False),
    ("", False),
    ("Sheets", True),
    ("Scorecard: one row per store, KPIs with conditional formatting and a live RANK.", False),
    ("Breach_Heatmap: % of orders > 15 min, store x hour (dry days).", False),
    ("Velachery_Roster: what-if roster model. Change the target orders per rider-hour.", False),
    ("Morning_Fill: line fill rate 06:00-10:59 by store x category.", False),
    ("Impact: rupee sizing of each fix, all formulas, assumptions in yellow.", False),
    ("Orders_Sample: 5,000 raw orders for pivots / SUMIFS / XLOOKUP practice.", False),
]
for i, (t, b) in enumerate(lines, 1):
    ws.cell(row=i, column=1, value=t).font = Font(bold=b, size=14 if i == 1 else 11)
ws.column_dimensions["A"].width = 110

# Scorecard
ws = wb.create_sheet("Scorecard")
cols = ["Store", "Orders", "GMV (Rs lakh)", "AOV (Rs)", "% in 10 min", "% > 15 min",
        "Avg delivery (min)", "Line fill %", "% cancelled OOS", "Avg rating", "On-time rank"]
header(ws, 1, cols)
n = len(score)
for i, r in score.iterrows():
    row = i + 2
    vals = [r.store_name, int(r.orders), r.gmv_lakh, r.aov, r.pct_in_10_min, r.pct_over_15_min,
            r.avg_delivery_min, r.line_fill_rate, r.pct_cancelled_oos, r.avg_rating]
    for j, v in enumerate(vals, 1):
        ws.cell(row=row, column=j, value=v).border = BOX
    ws.cell(row=row, column=11, value="=RANK(E%d,$E$2:$E$%d,0)" % (row, n + 1)).border = BOX
tot = n + 2
ws.cell(row=tot, column=1, value="Network").font = Font(bold=True)
ws.cell(row=tot, column=2, value="=SUM(B2:B%d)" % (n + 1))
ws.cell(row=tot, column=3, value="=SUM(C2:C%d)" % (n + 1))
ws.cell(row=tot, column=4, value="=ROUND(C%d*100000/B%d,0)" % (tot, tot))
for c in "EFGHIJ":
    ws["%s%d" % (c, tot)] = "=ROUND(SUMPRODUCT(%s2:%s%d,$B$2:$B$%d)/$B$%d,2)" % (c, c, n + 1, n + 1, tot)
for c in range(1, 12):
    ws.cell(row=tot, column=c).font = Font(bold=True)
rng = lambda c: "%s2:%s%d" % (c, c, n + 1)
ws.conditional_formatting.add(rng("E"), ColorScaleRule(start_type="min", start_color="F8696B",
                              mid_type="percentile", mid_value=50, mid_color="FFEB84", end_type="max", end_color="63BE7B"))
ws.conditional_formatting.add(rng("F"), ColorScaleRule(start_type="min", start_color="63BE7B",
                              mid_type="percentile", mid_value=50, mid_color="FFEB84", end_type="max", end_color="F8696B"))
ws.conditional_formatting.add(rng("H"), CellIsRule(operator="lessThan", formula=["97"], fill=PatternFill("solid", fgColor="F8696B")))
widths(ws, [16, 10, 12, 10, 11, 11, 12, 11, 12, 10, 10])
ws.freeze_panes = "B2"

# Heatmap
ws = wb.create_sheet("Breach_Heatmap")
header(ws, 1, ["Store \\ Hour"] + ["%02d:00" % h for h in hour_order])
for i, (store, r) in enumerate(heat_p.iterrows(), 2):
    ws.cell(row=i, column=1, value=store).font = Font(bold=True)
    for j, h in enumerate(hour_order, 2):
        ws.cell(row=i, column=j, value=float(r[h])).border = BOX
last = get_column_letter(len(hour_order) + 1)
ws.conditional_formatting.add("B2:%s%d" % (last, len(heat_p) + 1),
                              ColorScaleRule(start_type="num", start_value=0, start_color="FFFFFF",
                                             mid_type="num", mid_value=10, mid_color="FFD966",
                                             end_type="num", end_value=40, end_color="C00000"))
widths(ws, [16] + [7] * len(hour_order))

# Velachery roster what-if
ws = wb.create_sheet("Velachery_Roster")
ws["A1"] = "Target orders per rider-hour"; ws["A1"].font = Font(bold=True)
ws["B1"] = TARGET; ws["B1"].fill = INPUT; ws["B1"].border = BOX
ws["A2"] = "Rider cost per hour (Rs, assumption)"; ws["A2"].font = Font(bold=True)
ws["B2"] = 110; ws["B2"].fill = INPUT; ws["B2"].border = BOX
header(ws, 4, ["Hour", "Avg orders (dry day)", "Riders rostered", "Riders needed", "Gap (+ short / - surplus)", "Orders per rider now"])
for i, r in vel.iterrows():
    row = i + 5
    ws.cell(row=row, column=1, value="%02d:00" % int(r.hour))
    ws.cell(row=row, column=2, value=round(float(r.avg_orders), 1))
    ws.cell(row=row, column=3, value=int(round(r.riders)))
    ws.cell(row=row, column=4, value="=ROUNDUP(B%d/$B$1,0)" % row)
    ws.cell(row=row, column=5, value="=D%d-C%d" % (row, row))
    ws.cell(row=row, column=6, value="=ROUND(B%d/C%d,2)" % (row, row))
    for c in range(1, 7):
        ws.cell(row=row, column=c).border = BOX
end = 4 + len(vel)
t = end + 1
ws.cell(row=t, column=1, value="Total rider-hours / day").font = Font(bold=True)
for c in "BCD":
    ws["%s%d" % (c, t)] = "=SUM(%s5:%s%d)" % (c, c, end)
    ws["%s%d" % (c, t)].font = Font(bold=True)
ws["A%d" % (t + 2)] = "Peak shortfall (max gap)"; ws["B%d" % (t + 2)] = "=MAX(E5:E%d)" % end
ws["A%d" % (t + 3)] = "Surplus rider-hours to move"; ws["B%d" % (t + 3)] = '=-SUMIF(E5:E%d,"<0")' % end
ws["A%d" % (t + 4)] = "Extra rider-hours needed"; ws["B%d" % (t + 4)] = '=SUMIF(E5:E%d,">0")' % end
ws["A%d" % (t + 5)] = "Net daily cost change (Rs)"; ws["B%d" % (t + 5)] = "=(D%d-C%d)*B2" % (t, t)
for k in range(2, 6):
    ws["A%d" % (t + k)].font = Font(bold=True)
ws.conditional_formatting.add("E5:E%d" % end, CellIsRule(operator="greaterThan", formula=["0"], fill=PatternFill("solid", fgColor="F8B4B4")))
ws.conditional_formatting.add("E5:E%d" % end, CellIsRule(operator="lessThan", formula=["0"], fill=PatternFill("solid", fgColor="BBF7D0")))
widths(ws, [34, 18, 15, 14, 22, 18])

# Morning fill
ws = wb.create_sheet("Morning_Fill")
cats = list(fill_p.columns)
header(ws, 1, ["Store \\ Category (06:00-10:59)"] + cats)
for i, (store, r) in enumerate(fill_p.iterrows(), 2):
    ws.cell(row=i, column=1, value=store).font = Font(bold=True)
    for j, c in enumerate(cats, 2):
        ws.cell(row=i, column=j, value=float(r[c])).border = BOX
ws.conditional_formatting.add("B2:%s%d" % (get_column_letter(len(cats) + 1), len(fill_p) + 1),
                              ColorScaleRule(start_type="num", start_value=60, start_color="C00000",
                                             mid_type="num", mid_value=95, mid_color="FFD966",
                                             end_type="num", end_value=98, end_color="FFFFFF"))
widths(ws, [30] + [14] * len(cats))

# Impact
ws = wb.create_sheet("Impact")
ws["A1"] = "Monthly impact sizing (28-day data, all formulas)"; ws["A1"].font = Font(bold=True, size=13)
rows = [
    ("Assumptions", None, None),
    ("AOV (Rs)", round(facts["aov"], 0), "input"),
    ("14-day reorder rate, on-time orders (%)", facts["reorder_on_time"], "input"),
    ("14-day reorder rate, late orders (%)", facts["reorder_late"], "input"),
    ("Reorder drop from a late order (pts)", "=B4-B5", None),
    ("Share of Sholinganallur late orders a 2nd store would fix (%)", 70, "input"),
    ("Share of Velachery peak late orders a re-roster would fix (%)", 85, "input"),
    ("Share of Anna Nagar lost dairy GMV recovered by 05:00 inbound (%)", 80, "input"),
    ("", None, None),
    ("Inputs from SQL", None, None),
    ("Velachery 18-23h orders > 15 min", facts["vel_late_peak_orders"], None),
    ("Sholinganallur orders > 15 min", facts["sho_late_orders"], None),
    ("Anna Nagar morning dairy/bread lost GMV (Rs)", round(facts["an_lost_gmv"], 0), None),
    ("", None, None),
    ("Monthly upside (Rs)", None, None),
    ("Velachery re-roster: repeat GMV protected", "=B12*B8/100*B6/100*B3", None),
    ("Sholinganallur 2nd store: repeat GMV protected", "=B13*B7/100*B6/100*B3", None),
    ("Anna Nagar inbound fix: direct GMV recovered", "=B14*B9/100", None),
    ("Total", "=SUM(B17:B19)", None),
]
for i, (label, val, kind) in enumerate(rows, 2):
    a = ws.cell(row=i, column=1, value=label)
    if val is None and label:
        a.font = Font(bold=True, size=11)
        continue
    b = ws.cell(row=i, column=2, value=val)
    b.border = BOX
    if kind == "input":
        b.fill = INPUT
    if i >= 17:
        b.number_format = '"Rs "#,##0'
ws["A20"].font = Font(bold=True); ws["B20"].font = Font(bold=True)
ws["A22"] = ("Repeat GMV protected = late orders x share fixed x reorder drop x AOV. It counts one avoided lost "
             "reorder per fixed late order, a deliberately conservative first-order estimate.")
ws["A22"].alignment = Alignment(wrap_text=True)
widths(ws, [62, 16])

# Raw sample
ws = wb.create_sheet("Orders_Sample")
sample = q("""SELECT o.order_id, s.store_name, o.ops_date, o.order_hour, o.is_rain_day, o.distance_km,
                     o.items_requested, o.items_fulfilled, o.gmv, o.lost_gmv, o.pick_min,
                     o.rider_wait_min, o.travel_min, o.delivery_min, o.status, o.rating
              FROM orders o JOIN stores s USING(store_id) ORDER BY RANDOM() LIMIT 5000""")
header(ws, 1, list(sample.columns))
for r in sample.itertuples(index=False):
    ws.append(list(r))
ws.freeze_panes = "A2"; ws.auto_filter.ref = ws.dimensions
widths(ws, [11, 15, 11, 8, 8, 9, 9, 9, 9, 9, 8, 9, 9, 9, 13, 7])

wb.save(os.path.join(OUT, "DarkStore_Ops_Analysis.xlsx"))
print(json.dumps(facts, indent=2))
