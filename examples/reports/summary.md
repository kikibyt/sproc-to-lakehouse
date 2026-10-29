# Conversion summary

12 procedures: **6 fully converted**, 6 partly converted (9 manual steps in total).

Verdicts: 0 auto, 6 auto with notes, 6 needing review. Target: both. Tool 0.1.0, sqlglot 30.25.0.

## Procedures

| Procedure | Verdict | Conversion | Auto | Notes | Review | Blocking rules |
|---|---|---|---|---|---|---|
| [usp_load_daily_dispense_summary](usp_load_daily_dispense_summary.md) | note | full | 5 | 3 | 0 | - |
| [usp_build_monthly_inventory_snapshot](usp_build_monthly_inventory_snapshot.md) | note | full | 11 | 3 | 0 | - |
| [usp_upsert_patient_enrollment](usp_upsert_patient_enrollment.md) | note | full | 5 | 7 | 0 | - |
| [usp_calc_refill_gaps](usp_calc_refill_gaps.md) | note | full | 7 | 4 | 0 | - |
| [usp_classify_payer_claims](usp_classify_payer_claims.md) | note | full | 3 | 2 | 0 | - |
| [usp_standardize_patient_contact](usp_standardize_patient_contact.md) | review | partial (1 manual) | 5 | 3 | 1 | EX013 |
| [usp_shipment_sla_report](usp_shipment_sla_report.md) | note | full | 7 | 5 | 0 | - |
| [usp_update_shipment_status](usp_update_shipment_status.md) | review | partial (3 manual) | 4 | 5 | 3 | ST005 |
| [usp_rebuild_prescriber_rollup](usp_rebuild_prescriber_rollup.md) | review | partial (2 manual) | 9 | 2 | 1 | ST001 |
| [usp_recalc_reorder_points_cursor](usp_recalc_reorder_points_cursor.md) | review | partial (1 manual) | 7 | 9 | 4 | CF001, CF002, CF008, ST005 |
| [usp_export_dispenses_by_region_dynamic](usp_export_dispenses_by_region_dynamic.md) | review | partial (1 manual) | 3 | 4 | 2 | CF005, EX013 |
| [usp_close_month_end](usp_close_month_end.md) | review | partial (1 manual) | 6 | 6 | 12 | CF002, CF003, CF004, CF007, CF008, DM003, DM004, ST005, ST007 |

## Findings by rule

| Rule | Construct | Findings | Procedures | Severity |
|---|---|---|---|---|
| CF001 | Row-by-row cursor processing | 1 | 1 | review |
| CF002 | WHILE loop | 2 | 2 | review |
| CF003 | TRY/CATCH error handling | 1 | 1 | review |
| CF004 | Explicit multi-statement transaction | 1 | 1 | review |
| CF005 | Dynamic SQL execution | 1 | 1 | review |
| CF007 | THROW / RAISERROR | 2 | 1 | review |
| CF008 | IF / ELSE branching | 4 | 3 | auto, review |
| DM001 | MERGE upsert | 1 | 1 | note |
| DM002 | UPDATE/DELETE with FROM ... JOIN | 5 | 3 | note |
| DM003 | UPDATE/DELETE TOP (n) | 1 | 1 | review |
| DM004 | OUTPUT clause on DML | 1 | 1 | review |
| DM006 | TRUNCATE TABLE | 2 | 2 | auto |
| DM007 | SELECT returning rows to the caller | 1 | 1 | note |
| EX001 | Common table expressions (WITH) | 6 | 6 | auto |
| EX002 | Window functions (OVER ...) | 6 | 5 | auto |
| EX003 | CASE / IIF | 11 | 9 | auto |
| EX004 | Date arithmetic (DATEADD, EOMONTH, DATEFROMPARTS, ...) | 16 | 7 | auto |
| EX005 | DATEDIFF | 2 | 2 | note |
| EX006 | DATEPART week / weekday | 1 | 1 | note |
| EX007 | GETDATE / SYSDATETIME | 14 | 8 | note |
| EX008 | CONVERT / TRY_CONVERT | 3 | 2 | note |
| EX009 | ISNULL | 8 | 7 | note |
| EX010 | String concatenation with + | 2 | 2 | note |
| EX011 | String functions | 3 | 3 | auto |
| EX012 | LEN | 1 | 1 | note |
| EX013 | Function without a Databricks equivalent | 2 | 2 | review |
| EX014 | STRING_AGG | 1 | 1 | note |
| EX015 | BIT column used as 0/1 | 9 | 7 | note |
| EX016 | 'yyyymmdd' string used as a date | 1 | 1 | note |
| ST001 | Temp table (#name) used as intermediate storage | 2 | 2 | auto, review |
| ST002 | Table variable (@name TABLE) used as intermediate storage | 1 | 1 | auto |
| ST003 | Scalar variable assigned from a query | 3 | 2 | note |
| ST004 | Procedure parameters | 11 | 11 | auto |
| ST005 | @@ system variables | 6 | 3 | review |
| ST006 | SET session options | 13 | 12 | auto, note |
| ST007 | SCOPE_IDENTITY / @@IDENTITY | 1 | 1 | review |
| ST008 | Temp table cleanup (DROP TABLE #t) | 2 | 2 | auto |

## Fixes applied by the generator

| Fix | What it does | Procedures |
|---|---|---|
| bit_to_boolean | BIT columns are BOOLEAN on Databricks: 0/1 comparisons become FALSE/TRUE and values written into them are cast to BOOLEAN (needs DDL). | usp_upsert_patient_enrollment, usp_calc_refill_gaps, usp_classify_payer_claims, usp_shipment_sla_report |
| compact_date_literal | 'yyyymmdd' strings compared with dates become DATE literals; Spark cannot cast them implicitly (needs DDL). | usp_upsert_patient_enrollment |
| iso_week_extract | EXTRACT(WEEKISO ...) becomes EXTRACT(WEEK ...), the Databricks ISO 8601 field. | usp_shipment_sla_report |
| plus_to_concat | String '+' becomes CONCAT; sqlglot leaves '+', which Databricks treats as numeric addition. | - |
| transient_table_to_cte | Temp tables / table variables populated without later mutation become CTEs (or intermediate models when read more than once). | usp_build_monthly_inventory_snapshot, usp_shipment_sla_report |
| try_convert_safe_parse | TRY_CONVERT with a style becomes CAST(TRY_TO_TIMESTAMP(...)); sqlglot emits TO_DATE/TO_TIMESTAMP, which raise on bad input. | usp_upsert_patient_enrollment |
| update_from_to_merge | UPDATE/DELETE ... FROM ... JOIN becomes MERGE INTO, because Delta UPDATE/DELETE has no FROM clause. | usp_build_monthly_inventory_snapshot, usp_update_shipment_status |
