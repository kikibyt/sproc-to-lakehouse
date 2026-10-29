# dbo.usp_classify_payer_claims

**Converts automatically, with review notes.** Fully converted: the generated code runs without manual steps.

- Source: `samples/tsql/05_usp_classify_payer_claims.sql`
- Findings: 3 auto, 2 with notes, 0 needing review
- Parameters: `@from_date DATE`, `@to_date DATE`
- Generated: `dbt/models/usp_classify_payer_claims/claim_classification.sql`, `notebooks/usp_classify_payer_claims.py`

## Converted, with review notes

| Line | Rule | Note | Fix applied |
|---|---|---|---|
| 20 | EX007 | Current time: SQL Server returns the server's local time; Databricks returns the session time zone (UTC unless configured). | - |
| 20 | EX015 | BIT column compared with or assigned 0/1. On Databricks BIT is BOOLEAN and ANSI mode rejects BOOLEAN = INT; rewritten to TRUE/FALSE and CAST(... AS BOOLEAN). | bit_to_boolean |

## Converted automatically

| Rule | Construct | Lines | How |
|---|---|---|---|
| EX003 | case_expression | 20 | CASE and IIF translate directly (IIF becomes IF). |
| ST004 | parameters | 10 | 2 input parameter(s) become dbt vars and notebook widgets. |
| ST006 | session_option | 15 | SET NOCOUNT is a SQL Server session setting with no Databricks equivalent; dropped. |

## Conversion plan

| # | Step | T-SQL line | Kind | Fixes applied |
|---|---|---|---|---|
| 1 | delete dbo.claim_classification | 17 | write | - |
| 2 | insert dbo.claim_classification | 20 | write | bit_to_boolean |

## Dropped

Statements with no Databricks equivalent needed:

- line 15: session option with no Databricks equivalent
