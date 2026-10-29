# Spark check

Every generated statement checked against Apache Spark 4.0.1 with empty tables built from the DDL. Executed: the statement ran. Analyzed: a Delta-only statement (MERGE/UPDATE/DELETE) was parsed by Spark and its expressions resolved. Skipped: a manual step. This proves the SQL is valid and resolves; parity testing proves results.

- Notebook steps: 8 executed, 11 analyzed, 0 failed, 9 skipped (manual)
- dbt model builds: 10 passed, 0 failed

| Procedure | Step | T-SQL line | Level | Result |
|---|---|---|---|---|
| usp_load_daily_dispense_summary | load @run_date | 15 | executed | ok |
| usp_load_daily_dispense_summary | delete dbo.daily_dispense_summary | 19 | analyzed | ok |
| usp_load_daily_dispense_summary | insert dbo.daily_dispense_summary | 22 | executed | ok |
| usp_build_monthly_inventory_snapshot | delete dbo.inventory_snapshots | 37 | analyzed | ok |
| usp_build_monthly_inventory_snapshot | insert dbo.inventory_snapshots | 40 | executed | ok |
| usp_build_monthly_inventory_snapshot | update dbo.inventory_snapshots | 66 | analyzed | ok |
| usp_upsert_patient_enrollment | merge dbo.dim_patient_enrollment | 16 | analyzed | ok |
| usp_calc_refill_gaps | truncate dbo.refill_gap_events | 16 | analyzed | ok |
| usp_calc_refill_gaps | insert dbo.refill_gap_events | 18 | executed | ok |
| usp_classify_payer_claims | delete dbo.claim_classification | 17 | analyzed | ok |
| usp_classify_payer_claims | insert dbo.claim_classification | 20 | executed | ok |
| usp_standardize_patient_contact | truncate dbo.patient_contact_clean | 15 | analyzed | ok |
| usp_standardize_patient_contact | insert statement | 17 | skipped | ok |
| usp_shipment_sla_report | result set | 38 | executed | ok |
| usp_update_shipment_status | load @as_of | 20 | executed | ok |
| usp_update_shipment_status | update dbo.shipments | 23 | analyzed | ok |
| usp_update_shipment_status | set_variable statement | 43 | skipped | ok |
| usp_update_shipment_status | update dbo.shipments | 46 | analyzed | ok |
| usp_update_shipment_status | set_variable statement | 56 | skipped | ok |
| usp_update_shipment_status | update dbo.shipments | 59 | analyzed | ok |
| usp_update_shipment_status | manual block | 65 | skipped | ok |
| usp_rebuild_prescriber_rollup | manual block | 19 | skipped | ok |
| usp_rebuild_prescriber_rollup | delete dbo.prescriber_monthly_rollup | 89 | analyzed | ok |
| usp_rebuild_prescriber_rollup | insert statement | 92 | skipped | ok |
| usp_recalc_reorder_points_cursor | manual block | 24 | skipped | ok |
| usp_export_dispenses_by_region_dynamic | manual block | 20 | skipped | ok |
| usp_close_month_end | insert dbo.etl_run_log | 27 | executed | ok |
| usp_close_month_end | manual block | 30 | skipped | ok |
| usp_load_daily_dispense_summary | dbt model daily_dispense_summary | 22 | executed | ok |
| usp_load_daily_dispense_summary | dbt model daily_dispense_summary (incremental) | 22 | executed | ok |
| usp_build_monthly_inventory_snapshot | dbt model inventory_snapshots | 40 | executed | ok |
| usp_build_monthly_inventory_snapshot | dbt model inventory_snapshots (incremental) | 40 | executed | ok |
| usp_upsert_patient_enrollment | dbt model dim_patient_enrollment | 16 | executed | ok |
| usp_upsert_patient_enrollment | dbt model dim_patient_enrollment (incremental) | 16 | executed | ok |
| usp_calc_refill_gaps | dbt model refill_gap_events | 18 | executed | ok |
| usp_classify_payer_claims | dbt model claim_classification | 20 | executed | ok |
| usp_classify_payer_claims | dbt model claim_classification (incremental) | 20 | executed | ok |
| usp_shipment_sla_report | dbt model shipment_sla_report | 38 | executed | ok |
