# Parity report

The original T-SQL procedure runs on SQL Server and the generated code runs on Spark + Delta Lake, each against the same synthetic data. The tables each procedure writes (and any result set it returns) are then compared: row counts, per-column checksums and row-level differences by primary key.

- Reference: Microsoft SQL Server 2022 (RTM-CU27-GDR) (KB5126934) - 16.0.4305.5 (X64)
- Candidate: Apache Spark 4.0.1 with Delta Lake
- Data: 3576 synthetic rows across 16 tables (seed 20240315)
- Result: **6 match**, 0 mismatch, 0 error, 6 not run

| Procedure | Status | Compared | Rows (SQL Server / Spark) | Columns matching | Changed by the procedure | Note |
|---|---|---|---|---|---|---|
| usp_load_daily_dispense_summary | MATCH | dbo.daily_dispense_summary | 5 / 5 | 10/10 | +1 ~2 -0 |  |
| usp_build_monthly_inventory_snapshot | MATCH | dbo.inventory_snapshots | 288 / 288 | 9/9 | +72 ~0 -0 |  |
| usp_upsert_patient_enrollment | MATCH | dbo.dim_patient_enrollment | 68 / 68 | 8/8 | +28 ~13 -0 |  |
| usp_calc_refill_gaps | MATCH | dbo.refill_gap_events | 514 / 514 | 9/9 | +513 ~1 -4 |  |
| usp_classify_payer_claims | MATCH | dbo.claim_classification | 404 / 404 | 10/10 | +384 ~13 -0 |  |
| usp_shipment_sla_report | MATCH | result set 1 | 6 / 6 | 9/9 | - |  |
| usp_standardize_patient_contact | not run | - | - | - | - | 1 manual step(s) must be converted by a person first |
| usp_update_shipment_status | not run | - | - | - | - | 3 manual step(s) must be converted by a person first |
| usp_rebuild_prescriber_rollup | not run | - | - | - | - | 2 manual step(s) must be converted by a person first |
| usp_recalc_reorder_points_cursor | not run | - | - | - | - | 1 manual step(s) must be converted by a person first |
| usp_export_dispenses_by_region_dynamic | not run | - | - | - | - | 1 manual step(s) must be converted by a person first |
| usp_close_month_end | not run | - | - | - | - | 1 manual step(s) must be converted by a person first |

Changed by the procedure: rows inserted (+), updated (~) and deleted (-) on SQL Server compared with the data loaded before the run. A match on a table the procedure changed is real evidence; a match on an unchanged table would prove nothing.

Normalisation applied before comparing:

- decimals compared by value, rounded to the column's scale when known
- trailing spaces ignored (CHAR padding)
- dates/timestamps compared as ISO-8601 text; BIT and BOOLEAN as true/false

## usp_load_daily_dispense_summary: MATCH

Parameters: run_date=2024-03-15.

### dbo.daily_dispense_summary

Rows: 5 on SQL Server, 5 on Spark. Matched by: summary_date, therapy_area. Ignored (set from the current time): loaded_at.

| Column | SQL Server checksum | Spark checksum | Match |
|---|---|---|---|
| summary_date | `18354f6198ab` | `18354f6198ab` | yes |
| summary_date_key | `1912b42ebc63` | `1912b42ebc63` | yes |
| therapy_area | `d486ac3be1e3` | `d486ac3be1e3` | yes |
| dispense_count | `1020a83a1f66` | `1020a83a1f66` | yes |
| patient_count | `10ee8ab0c67f` | `10ee8ab0c67f` | yes |
| total_quantity | `31212b929540` | `31212b929540` | yes |
| total_days_supply | `eba14a0c2684` | `eba14a0c2684` | yes |
| total_billed | `8f4a0cb55443` | `8f4a0cb55443` | yes |
| total_paid | `ce3a68511fd5` | `ce3a68511fd5` | yes |
| paid_to_billed_pct | `78618be0f697` | `78618be0f697` | yes |

## usp_build_monthly_inventory_snapshot: MATCH

Parameters: snapshot_month=2024-03-01.

### dbo.inventory_snapshots

Rows: 288 on SQL Server, 288 on Spark. Matched by: snapshot_month, site_id, product_id. Ignored (set from the current time): loaded_at.

| Column | SQL Server checksum | Spark checksum | Match |
|---|---|---|---|
| snapshot_month | `cd4c5ed48546` | `cd4c5ed48546` | yes |
| site_id | `197872113c5a` | `197872113c5a` | yes |
| product_id | `0bddd1094371` | `0bddd1094371` | yes |
| opening_qty | `d892d826cceb` | `d892d826cceb` | yes |
| received_qty | `781525d9fb86` | `781525d9fb86` | yes |
| dispensed_qty | `f99eb7bcacf6` | `f99eb7bcacf6` | yes |
| adjusted_qty | `b30199c469e7` | `b30199c469e7` | yes |
| closing_qty | `4f2fc86f4dcc` | `4f2fc86f4dcc` | yes |
| avg_dispensed_3m | `b7b9f1704153` | `b7b9f1704153` | yes |

## usp_upsert_patient_enrollment: MATCH

Parameters: load_batch_id=1.

### dbo.dim_patient_enrollment

Rows: 68 on SQL Server, 68 on Spark. Matched by: patient_id, program_code. Ignored (set from the current time): created_at, updated_at.

| Column | SQL Server checksum | Spark checksum | Match |
|---|---|---|---|
| patient_id | `9e8f9eb86774` | `9e8f9eb86774` | yes |
| program_code | `b4d40cb618a3` | `b4d40cb618a3` | yes |
| enrollment_status | `92f48ffa09d9` | `92f48ffa09d9` | yes |
| enrolled_date | `ebe810d91add` | `ebe810d91add` | yes |
| disenrolled_date | `6f81b6c45c49` | `6f81b6c45c49` | yes |
| payer_id | `deb2171cc7f9` | `deb2171cc7f9` | yes |
| case_manager_email | `2ed44cf02b41` | `2ed44cf02b41` | yes |
| is_active | `0cfec3bbb11a` | `0cfec3bbb11a` | yes |

## usp_calc_refill_gaps: MATCH

Parameters: as_of_date=2024-03-31, gap_threshold_days=7.

### dbo.refill_gap_events

Rows: 514 on SQL Server, 514 on Spark. Matched by: patient_id, product_id, fill_seq. Ignored (set from the current time): calculated_at.

| Column | SQL Server checksum | Spark checksum | Match |
|---|---|---|---|
| patient_id | `93b848085d97` | `93b848085d97` | yes |
| product_id | `f24c5c6634da` | `f24c5c6634da` | yes |
| fill_seq | `5a20f3414d5a` | `5a20f3414d5a` | yes |
| dispense_date | `f5266b6e8298` | `f5266b6e8298` | yes |
| supply_end_date | `f5492144baf2` | `f5492144baf2` | yes |
| next_dispense_date | `f2e01fa1885b` | `f2e01fa1885b` | yes |
| gap_days | `837f4ab27ce1` | `837f4ab27ce1` | yes |
| gap_category | `e5fa3145ca00` | `e5fa3145ca00` | yes |
| is_open_gap | `e36448567f3e` | `e36448567f3e` | yes |

## usp_classify_payer_claims: MATCH

Parameters: from_date=2024-03-01, to_date=2024-03-31.

### dbo.claim_classification

Rows: 404 on SQL Server, 404 on Spark. Matched by: claim_id. Ignored (set from the current time): classified_at.

| Column | SQL Server checksum | Spark checksum | Match |
|---|---|---|---|
| claim_id | `5d0244a56880` | `5d0244a56880` | yes |
| dispense_id | `453c5545c7f2` | `453c5545c7f2` | yes |
| submitted_date | `1ab48d7ca81b` | `1ab48d7ca81b` | yes |
| payer_type | `558665f54721` | `558665f54721` | yes |
| coverage_segment | `b47622a98054` | `b47622a98054` | yes |
| claim_outcome | `04a88a97a50c` | `04a88a97a50c` | yes |
| copay_tier | `4851d72b989d` | `4851d72b989d` | yes |
| paid_ratio | `31d29f252680` | `31d29f252680` | yes |
| follow_up_priority | `3b1a1ac468e6` | `3b1a1ac468e6` | yes |
| high_oop_flag | `606460057e16` | `606460057e16` | yes |

## usp_shipment_sla_report: MATCH

Parameters: week_start=2024-03-04.

### result set 1

Rows: 6 on SQL Server, 6 on Spark. Matched by: full row (no key).

| Column | SQL Server checksum | Spark checksum | Match |
|---|---|---|---|
| iso_week | `5161d6c49db4` | `5161d6c49db4` | yes |
| region_code | `5b824b6ecdd9` | `5b824b6ecdd9` | yes |
| ship_method | `e121cd57dfb7` | `e121cd57dfb7` | yes |
| shipped_count | `7549d8e68249` | `7549d8e68249` | yes |
| on_time_count | `1ea31770a9a8` | `1ea31770a9a8` | yes |
| late_1_2_days | `49bfc8eb7f3b` | `49bfc8eb7f3b` | yes |
| late_3_plus_days | `fb8bd06ba551` | `fb8bd06ba551` | yes |
| cold_chain_late | `27c4b7bcc7f6` | `27c4b7bcc7f6` | yes |
| on_time_pct | `78c7bc4c251f` | `78c7bc4c251f` | yes |
