# dbo.usp_standardize_patient_contact

**Needs human review.** Partly converted: 1 step(s) need manual conversion.

- Source: `samples/tsql/06_usp_standardize_patient_contact.sql`
- Findings: 5 auto, 3 with notes, 1 needing review
- Generated: `dbt/macros/procedures/usp_standardize_patient_contact.sql`, `notebooks/usp_standardize_patient_contact.py`

## Needs human review

| Line | Rule | Why | T-SQL |
|---|---|---|---|
| 17 | EX013 | STUFF(): no Databricks equivalent; sqlglot passes the call through unchanged, so the generated SQL would fail. Needs a manual rewrite. | `WITH cleaned AS ( SELECT patient_id, LTRIM(RTRIM(first_name)) AS first_name, LTRIM(RTRIM(last_nam...` |

## Converted, with review notes

| Line | Rule | Note | Fix applied |
|---|---|---|---|
| 17 | EX009 | ISNULL becomes COALESCE. ISNULL returns the type of its first argument (and can truncate the fallback value); COALESCE uses type precedence. | - |
| 17 | EX010 | '+' concatenates strings in SQL Server but is numeric addition in Databricks (sqlglot leaves it unchanged, which yields NULL or an error); rewritten to CONCAT. NULL handling is the same: any NULL gives NULL. | plus_to_concat |
| 17 | EX012 | LEN ignores trailing spaces; Databricks LENGTH counts them. Equal only when values are already right-trimmed. | - |

## Converted automatically

| Rule | Construct | Lines | How |
|---|---|---|---|
| DM006 | truncate | 15 | Full reload: becomes a table materialization (dbt) and DELETE FROM before the INSERT (notebook; open-source Delta has no TRUNCATE). |
| EX001 | cte | 17 | CTEs are supported natively. |
| EX003 | case_expression | 17 | CASE and IIF translate directly (IIF becomes IF). |
| EX011 | string_function | 17 | String functions map directly (CHARINDEX becomes LOCATE, LTRIM/RTRIM stay). |
| ST006 | session_option | 13 | SET NOCOUNT is a SQL Server session setting with no Databricks equivalent; dropped. |

## Conversion plan

| # | Step | T-SQL line | Kind | Fixes applied |
|---|---|---|---|---|
| 1 | truncate dbo.patient_contact_clean | 15 | write | - |
| 2 | insert statement | 17 | manual | - |

### Step 2: manual conversion (T-SQL line 17)

- EX013: STUFF(): no Databricks equivalent; sqlglot passes the call through unchanged, so the generated SQL would fail. Needs a manual rewrite.

```sql
WITH cleaned AS (
        SELECT
            patient_id,
            LTRIM(RTRIM(first_name)) AS first_name,
            LTRIM(RTRIM(last_name))  AS last_name,
            REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                ISNULL(phone_raw, ''), '(', ''), ')', ''), '-', ''), ' ', ''), '.', '') AS phone_digits,
            LOWER(LTRIM(RTRIM(email_raw)))  AS email_normalized,
            LEFT(LTRIM(RTRIM(postal_code)), 5) AS postal_code5,
            UPPER(state_code) AS state_code
        FROM dbo.patients
    )
    INSERT INTO dbo.patient_contact_clean (
        patient_id, full_name, phone_digits, phone_formatted, phone_masked,
        email_normalized, email_domain, postal_code5, state_code, contact_quality
    )
    SELECT
        patient_id,
        UPPER(LEFT(first_name, 1)) + LOWER(SUBSTRING(first_name, 2, LEN(first_name)))
            + ' ' + UPPER(last_name),
        phone_digits,
        CASE WHEN LEN(phone_digits) = 10
             THEN '(' + LEFT(phone_digits, 3) + ') ' + SUBSTRING(phone_digits, 4, 3)
                  + '-' + RIGHT(phone_digits, 4)
        END,
        CASE WHEN LEN(phone_digits) = 10 THEN STUFF(phone_digits, 4, 3, '***') END,
        email_normalized,
        CASE WHEN CHARINDEX('@', email_normalized) > 1
             THEN SUBSTRING(email_normalized, CHARINDEX('@', email_normalized) + 1, LEN(email_normalized))
        END,
        RIGHT('00000' + postal_code5, 5),
        state_code,
        CASE
            WHEN LEN(phone_digits) = 10 AND CHARINDEX('@', email_normalized) > 1 THEN 'COMPLETE'
            WHEN LEN(phone_digits) = 10 OR CHARINDEX('@', email_normalized) > 1  THEN 'PARTIAL'
            ELSE 'MISSING'
        END
    FROM cleaned
```

## Dropped

Statements with no Databricks equivalent needed:

- line 13: session option with no Databricks equivalent
