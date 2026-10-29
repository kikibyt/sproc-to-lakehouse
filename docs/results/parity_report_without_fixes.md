# Parity report

The original T-SQL procedure runs on SQL Server and the generated code runs on Spark + Delta Lake, each against the same synthetic data. The tables each procedure writes (and any result set it returns) are then compared: row counts, per-column checksums and row-level differences by primary key.

- Reference: Microsoft SQL Server 2022 (RTM-CU27-GDR) (KB5126934) - 16.0.4305.5 (X64)
- Candidate: Apache Spark 4.0.1 with Delta Lake
- Data: 3576 synthetic rows across 16 tables (seed 20240315)
- **Ablation run: the tool's targeted fixes were switched off** (plain sqlglot output inside the same pipeline).
- Result: **2 match**, 0 mismatch, 4 error, 6 not run

| Procedure | Status | Compared | Rows (SQL Server / Spark) | Columns matching | Changed by the procedure | Note |
|---|---|---|---|---|---|---|
| usp_load_daily_dispense_summary | MATCH | dbo.daily_dispense_summary | 5 / 5 | 10/10 | +1 ~2 -0 |  |
| usp_build_monthly_inventory_snapshot | ERROR | - | - | - | - | [PARSE_SYNTAX_ERROR] Syntax error at or near 'FROM'. SQLSTATE: 42601 (line 16, pos 0) |
| usp_upsert_patient_enrollment | ERROR | - | - | - | - | [CANNOT_PARSE_TIMESTAMP] Text '13/45/2024' could not be parsed: Invalid value for MonthOfYear (valid values 1 - 12): 13. Use `try_to_timestamp` to tolerate invalid input string and return NULL instead. SQLSTATE: 22007 |
| usp_calc_refill_gaps | ERROR | - | - | - | - | Table does not support truncates: spark_catalog.dbo.refill_gap_events. |
| usp_classify_payer_claims | MATCH | dbo.claim_classification | 404 / 404 | 10/10 | +384 ~13 -0 |  |
| usp_shipment_sla_report | ERROR | - | - | - | - | [INVALID_EXTRACT_FIELD] Cannot extract `WEEKISO` from "CAST(2024-03-04 AS DATE)". SQLSTATE: 42601; line 21 pos 2 |
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
