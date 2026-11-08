# Memo: Three fixes worth ~Rs 9 lakh a month across 12 Chennai dark stores

**To:** City Ops Lead, Chennai · **From:** S. Nakul Raj · **Data:** 1 to 28 Oct 2026, 12 stores, 314,682 orders, 1.65M order lines (simulated, see README)

## Bottom line

Our 10-minute promise breaks in three places, for three different reasons. Each one needs a different fix:

| Store | Symptom | Root cause | Fix | Cost | Monthly upside |
|---|---|---|---|---|---|
| Velachery | Late orders rise 9x at 7 to 9 PM (0.9% to 8.3% over 15 min) | Rider roster is flat at 27 riders every hour | Reshape the roster to follow demand | **Saves 13 rider-hours/day** | ~Rs 1.25 L repeat GMV |
| Sholinganallur | 43% of orders over 15 min, all day; rating 3.41 vs 4.58 at the best store | 27% of orders are over 3 km away; 91% of those arrive late | Open a second store in the outer catchment (more riders won't help) | Capex | ~Rs 5.6 L repeat GMV |
| Anna Nagar | Morning dairy/bread fill rate 63% vs 98% at other stores | Dairy inbound lands after the 7 to 9 AM rush | Move the dairy vendor slot to 05:00 | ~Zero | ~Rs 2.2 L direct GMV |

**Why lateness costs money:** customers whose order took more than 15 min reordered within 14 days **40.0%** of the time, vs **51.9%** after an on-time order. That is a 12-point drop. Every late order is a likely lost repeat order.

## How I found it

1. **Scorecard first** (query 01). I ranked stores by % delivered in 10 min. Sholinganallur was last, but Velachery sat mid-table, so the store average hid its problem.
2. **Split delivery time into legs** (query 02): pick, rider wait, travel. Picking was flat at ~2.5 min everywhere, so the store floor is not the problem. Variation came from rider wait and travel.
3. **Peak vs off-peak** (query 03). A store that is slow all day has a structural problem. A store that is slow only at peak has a capacity problem. Velachery was the only store worse at peak: 9.2x, with rider wait tripling to 3.0 min.
4. **Joined demand to the rider roster** (queries 04 and 05). Late orders stay low until load passes ~2.5 orders per rider-hour, then jump to 21% above 3.0. Velachery runs 27 riders at all hours. It needs ~45 at 8 PM and only ~9 at 1 AM.
5. **Distance tail** (query 07). Sholinganallur's 90th-percentile distance is 4.4 km, vs 1.8 to 2.6 km elsewhere. Longer rides also keep riders out longer, so rider wait rises too. Adding riders treats the symptom; the catchment is the cause.
6. **Fill rate by store x category x daypart** (query 06). One cell stood out: Anna Nagar Dairy & Bread, 06:00 to 11:00, at 63.9%. Every other cell in the network was above 95.9%.

## Also seen

- **Rain days** (7 of 28): orders per store +13%, while orders over 15 min jump from 4.1% to 17.3%. Rosters are planned on the dry-day forecast. *Recommendation:* a rain-trigger roster using the IMD forecast the evening before, with +20% riders from 17:00 and a surge incentive.
- **Outer-ring stores** (Kattankulathur, Tambaram, Porur, Perungudi) run 6 to 10% over 15 min. That is milder, but it is the same distance pattern as Sholinganallur. They are worth watching as density grows.

## What I'd do this week

1. **Velachery:** publish the reshaped roster (sheet `Velachery_Roster`) for next week. Track hourly % over 15 min and orders per rider-hour daily. Success = peak-hour breach under 2% within 2 weeks.
2. **Anna Nagar:** call the dairy vendor and the warehouse dispatch lead to move the slot to 05:00. Go to the store at 07:00 for two mornings to confirm shelves are full. Success = morning dairy fill rate above 97%.
3. **Sholinganallur:** run a catchment study. Map order density over 3 km to shortlist a second site, and model its break-even in orders per day. In the meantime, test a smaller serviceable radius at peak, and measure the trade-off between cancellations and lateness.

## Caveats

- The data is simulated. The method, queries and workbook are what carry over to real data.
- The retention gap is correlational. A late order and a lower reorder rate could share a cause, such as a far-away customer. Confirm with an A/B holdout before committing capex.
- The upside counts one avoided lost reorder per fixed late order. That is conservative, because it ignores lifetime value.
