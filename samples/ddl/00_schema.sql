/*
    Synthetic schema for a fictional specialty pharmacy.

    Every table, column, code value and name in this repository is invented for
    demonstration purposes. Nothing here describes a real company, client, product,
    payer, prescriber or patient.

    Target platform of this file: Microsoft SQL Server 2017+ (T-SQL).
*/

-------------------------------------------------------------------------------
-- Reference data
-------------------------------------------------------------------------------

CREATE TABLE dbo.sites (
    site_id         INT           NOT NULL PRIMARY KEY,
    site_name       VARCHAR(50)   NOT NULL,          -- e.g. 'Site 01'
    region_code     VARCHAR(10)   NOT NULL,          -- e.g. 'NORTH', 'SOUTH'
    state_code      CHAR(2)       NOT NULL,
    is_active       BIT           NOT NULL DEFAULT 1
);

CREATE TABLE dbo.products (
    product_id      INT           NOT NULL PRIMARY KEY,
    product_code    VARCHAR(20)   NOT NULL,          -- e.g. 'PRD-0001'
    product_name    VARCHAR(100)  NOT NULL,          -- e.g. 'Product A 50mg'
    therapy_area    VARCHAR(30)   NOT NULL,          -- e.g. 'THERAPY_AREA_1'
    unit_cost       DECIMAL(12,2) NOT NULL,
    lead_time_days  INT           NOT NULL,
    is_cold_chain   BIT           NOT NULL DEFAULT 0,
    is_active       BIT           NOT NULL DEFAULT 1
);

CREATE TABLE dbo.payers (
    payer_id        INT           NOT NULL PRIMARY KEY,
    payer_name      VARCHAR(100)  NOT NULL,          -- e.g. 'Payer 001'
    payer_type      VARCHAR(30)   NOT NULL           -- COMMERCIAL | GOVERNMENT | MANUFACTURER_ASSIST | CASH
);

CREATE TABLE dbo.prescribers (
    prescriber_id   INT           NOT NULL PRIMARY KEY,
    prescriber_name VARCHAR(100)  NOT NULL,          -- e.g. 'Prescriber 0042'
    specialty       VARCHAR(50)   NOT NULL,
    state_code      CHAR(2)       NOT NULL,
    region_code     VARCHAR(10)   NOT NULL
);

CREATE TABLE dbo.patients (
    patient_id      VARCHAR(10)   NOT NULL PRIMARY KEY,  -- e.g. 'PT000123'
    first_name      VARCHAR(50)   NULL,                  -- generated, e.g. 'patient'
    last_name       VARCHAR(50)   NULL,                  -- generated, e.g. '0123'
    date_of_birth   DATE          NULL,
    phone_raw       VARCHAR(30)   NULL,                  -- free text as captured at intake
    email_raw       VARCHAR(120)  NULL,
    postal_code     VARCHAR(10)   NULL,
    state_code      CHAR(2)       NULL,
    created_at      DATETIME2(0)  NOT NULL
);

-------------------------------------------------------------------------------
-- Transactional data
-------------------------------------------------------------------------------

CREATE TABLE dbo.dispenses (
    dispense_id      BIGINT        NOT NULL PRIMARY KEY,
    patient_id       VARCHAR(10)   NOT NULL,
    prescriber_id    INT           NOT NULL,
    product_id       INT           NOT NULL,
    payer_id         INT           NOT NULL,
    site_id          INT           NOT NULL,
    dispense_date    DATE          NOT NULL,
    quantity         DECIMAL(12,2) NOT NULL,
    days_supply      INT           NOT NULL,
    fill_number      INT           NOT NULL,
    billed_amount    DECIMAL(12,2) NOT NULL,
    paid_amount      DECIMAL(12,2) NOT NULL,
    copay_amount     DECIMAL(12,2) NOT NULL,
    status           VARCHAR(20)   NOT NULL,         -- DISPENSED | REVERSED | CANCELLED
    is_period_locked BIT           NOT NULL DEFAULT 0
);

CREATE TABLE dbo.inventory_movements (
    movement_id     BIGINT        NOT NULL PRIMARY KEY,
    site_id         INT           NOT NULL,
    product_id      INT           NOT NULL,
    movement_date   DATE          NOT NULL,
    movement_type   VARCHAR(20)   NOT NULL,          -- RECEIPT | DISPENSE | ADJUSTMENT | RETURN
    quantity        DECIMAL(12,2) NOT NULL           -- signed for ADJUSTMENT, positive otherwise
);

CREATE TABLE dbo.stg_patient_enrollments (
    stg_row_id          BIGINT        NOT NULL IDENTITY(1,1) PRIMARY KEY,
    load_batch_id       INT           NOT NULL,
    patient_id          VARCHAR(10)   NOT NULL,
    program_code        VARCHAR(20)   NULL,          -- raw: may carry stray spaces / lower case
    enrollment_status   VARCHAR(20)   NULL,
    enrolled_on         VARCHAR(10)   NULL,          -- raw text, MM/DD/YYYY
    disenrolled_on      VARCHAR(10)   NULL,          -- raw text, MM/DD/YYYY, may be invalid
    payer_id            INT           NULL,
    case_manager_email  VARCHAR(120)  NULL,
    loaded_at           DATETIME2(0)  NOT NULL
);

CREATE TABLE dbo.shipments (
    shipment_id            BIGINT       NOT NULL PRIMARY KEY,
    dispense_id            BIGINT       NOT NULL,
    site_id                INT          NOT NULL,
    carrier_code           VARCHAR(10)  NOT NULL,   -- e.g. 'CARRIER_A'
    ship_method            VARCHAR(20)  NOT NULL,   -- OVERNIGHT | TWO_DAY | GROUND
    shipped_at             DATETIME2(0) NOT NULL,
    promised_delivery_date DATE         NOT NULL,
    delivered_at           DATETIME2(0) NULL,
    status                 VARCHAR(20)  NOT NULL,   -- SHIPPED | IN_TRANSIT | DELAYED | EXCEPTION | DELIVERED
    requires_cold_chain    BIT          NOT NULL,
    last_status_update     DATETIME2(0) NOT NULL
);

CREATE TABLE dbo.carrier_tracking_events (
    event_id        BIGINT       NOT NULL PRIMARY KEY,
    shipment_id     BIGINT       NOT NULL,
    event_code      VARCHAR(5)   NOT NULL,          -- PU pickup | IT in transit | OD out for delivery | DL delivered | EX exception
    event_ts        DATETIME2(0) NOT NULL,
    location_state  CHAR(2)      NULL
);

CREATE TABLE dbo.claims (
    claim_id            BIGINT        NOT NULL PRIMARY KEY,
    dispense_id         BIGINT        NOT NULL,
    payer_id            INT           NOT NULL,
    plan_type           VARCHAR(20)   NOT NULL,     -- e.g. STANDARD | HDHP | PART_D | MANAGED_GOV
    submitted_at        DATETIME2(0)  NOT NULL,
    response_code       CHAR(1)       NOT NULL,     -- P paid | R rejected | D duplicate
    reject_code         VARCHAR(5)    NULL,
    billed_amount       DECIMAL(12,2) NOT NULL,
    paid_amount         DECIMAL(12,2) NOT NULL,
    patient_pay_amount  DECIMAL(12,2) NOT NULL
);

-------------------------------------------------------------------------------
-- Targets written by the stored procedures
-------------------------------------------------------------------------------

CREATE TABLE dbo.daily_dispense_summary (
    summary_date        DATE          NOT NULL,
    summary_date_key    CHAR(8)       NOT NULL,     -- yyyymmdd
    therapy_area        VARCHAR(30)   NOT NULL,
    dispense_count      INT           NOT NULL,
    patient_count       INT           NOT NULL,
    total_quantity      DECIMAL(14,2) NOT NULL,
    total_days_supply   INT           NOT NULL,
    total_billed        DECIMAL(14,2) NOT NULL,
    total_paid          DECIMAL(14,2) NOT NULL,
    paid_to_billed_pct  DECIMAL(7,2)  NULL,
    loaded_at           DATETIME2(0)  NOT NULL,
    PRIMARY KEY (summary_date, therapy_area)
);

CREATE TABLE dbo.inventory_snapshots (
    snapshot_month      DATE          NOT NULL,     -- last day of the month
    site_id             INT           NOT NULL,
    product_id          INT           NOT NULL,
    opening_qty         DECIMAL(14,2) NOT NULL,
    received_qty        DECIMAL(14,2) NOT NULL,
    dispensed_qty       DECIMAL(14,2) NOT NULL,
    adjusted_qty        DECIMAL(14,2) NOT NULL,
    closing_qty         DECIMAL(14,2) NOT NULL,
    avg_dispensed_3m    DECIMAL(14,2) NULL,
    loaded_at           DATETIME2(0)  NOT NULL,
    PRIMARY KEY (snapshot_month, site_id, product_id)
);

CREATE TABLE dbo.dim_patient_enrollment (
    patient_id          VARCHAR(10)   NOT NULL,
    program_code        VARCHAR(20)   NOT NULL,
    enrollment_status   VARCHAR(20)   NOT NULL,
    enrolled_date       DATE          NULL,
    disenrolled_date    DATE          NULL,
    payer_id            INT           NOT NULL,
    case_manager_email  VARCHAR(120)  NULL,
    is_active           BIT           NOT NULL,
    created_at          DATETIME2(0)  NOT NULL,
    updated_at          DATETIME2(0)  NOT NULL,
    PRIMARY KEY (patient_id, program_code)
);

CREATE TABLE dbo.refill_gap_events (
    patient_id          VARCHAR(10)   NOT NULL,
    product_id          INT           NOT NULL,
    fill_seq            INT           NOT NULL,
    dispense_date       DATE          NOT NULL,
    supply_end_date     DATE          NOT NULL,
    next_dispense_date  DATE          NULL,
    gap_days            INT           NOT NULL,
    gap_category        VARCHAR(20)   NOT NULL,
    is_open_gap         BIT           NOT NULL,
    calculated_at       DATETIME2(0)  NOT NULL,
    PRIMARY KEY (patient_id, product_id, fill_seq)
);

CREATE TABLE dbo.claim_classification (
    claim_id            BIGINT        NOT NULL PRIMARY KEY,
    dispense_id         BIGINT        NOT NULL,
    submitted_date      DATE          NOT NULL,
    payer_type          VARCHAR(30)   NOT NULL,
    coverage_segment    VARCHAR(30)   NOT NULL,
    claim_outcome       VARCHAR(30)   NOT NULL,
    copay_tier          VARCHAR(10)   NOT NULL,
    paid_ratio          DECIMAL(9,4)  NULL,
    follow_up_priority  VARCHAR(10)   NOT NULL,
    high_oop_flag       BIT           NOT NULL,
    classified_at       DATETIME2(0)  NOT NULL
);

CREATE TABLE dbo.patient_contact_clean (
    patient_id          VARCHAR(10)   NOT NULL PRIMARY KEY,
    full_name           VARCHAR(120)  NULL,
    phone_digits        VARCHAR(30)   NULL,
    phone_formatted     VARCHAR(20)   NULL,
    phone_masked        VARCHAR(30)   NULL,
    email_normalized    VARCHAR(120)  NULL,
    email_domain        VARCHAR(120)  NULL,
    postal_code5        CHAR(5)       NULL,
    state_code          CHAR(2)       NULL,
    contact_quality     VARCHAR(10)   NOT NULL
);

CREATE TABLE dbo.prescriber_monthly_rollup (
    period_month        DATE          NOT NULL,
    prescriber_id       INT           NOT NULL,
    region_code         VARCHAR(10)   NOT NULL,
    patient_count       INT           NOT NULL,
    new_patient_count   INT           NOT NULL,
    dispense_count      INT           NOT NULL,
    total_days_supply   INT           NOT NULL,
    total_paid          DECIMAL(14,2) NOT NULL,
    rank_in_region      INT           NOT NULL,
    prescriber_tier     VARCHAR(10)   NOT NULL,
    PRIMARY KEY (period_month, prescriber_id)
);

CREATE TABLE dbo.product_reorder_points (
    product_id          INT           NOT NULL PRIMARY KEY,
    avg_daily_units     DECIMAL(12,4) NOT NULL,
    reorder_point       DECIMAL(12,2) NOT NULL,
    on_hand_qty         DECIMAL(12,2) NOT NULL,
    needs_reorder       BIT           NOT NULL,
    calculated_at       DATETIME2(0)  NOT NULL
);

CREATE TABLE dbo.period_close (
    period_month        DATE          NOT NULL PRIMARY KEY,
    close_status        VARCHAR(20)   NOT NULL,
    dispense_count      INT           NOT NULL,
    total_paid          DECIMAL(16,2) NULL,
    closed_at           DATETIME2(0)  NOT NULL
);

CREATE TABLE dbo.dispenses_archive (
    dispense_id         BIGINT        NOT NULL PRIMARY KEY,
    patient_id          VARCHAR(10)   NOT NULL,
    product_id          INT           NOT NULL,
    dispense_date       DATE          NOT NULL,
    quantity            DECIMAL(12,2) NOT NULL,
    paid_amount         DECIMAL(12,2) NOT NULL,
    status              VARCHAR(20)   NOT NULL,
    archived_at         DATETIME2(0)  NOT NULL DEFAULT SYSDATETIME()
);

CREATE TABLE dbo.etl_run_log (
    run_id              INT           NOT NULL IDENTITY(1,1) PRIMARY KEY,
    proc_name           VARCHAR(128)  NOT NULL,
    step_name           VARCHAR(100)  NOT NULL,
    status              VARCHAR(20)   NOT NULL,
    rows_affected       INT           NULL,
    started_at          DATETIME2(3)  NOT NULL,
    finished_at         DATETIME2(3)  NULL,
    error_message       NVARCHAR(4000) NULL
);
