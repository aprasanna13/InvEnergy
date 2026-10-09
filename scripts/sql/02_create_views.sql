-- ==============================================================================
-- Invenergy Contract Intelligence Platform - BigQuery Deduplication Views
-- Dataset: contract_intelligence (or configured BQ_DATASET_ID)
--
-- The platform uses an Append-Only Lakehouse ingestion model.
-- These views encapsulate the `QUALIFY ROW_NUMBER() OVER (...) = 1` deduplication
-- logic, providing a clean active-state interface for BI tools, Looker, and
-- Gemini in BigQuery Data Analytics Agents.
-- ==============================================================================

-- 1. Active Projects View
CREATE OR REPLACE VIEW `contract_intelligence.v_active_projects` AS
SELECT *
FROM `contract_intelligence.projects`
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY project_id 
    ORDER BY updated_at DESC
) = 1;

-- 2. Active Landowners View
CREATE OR REPLACE VIEW `contract_intelligence.v_active_landowners` AS
SELECT *
FROM `contract_intelligence.landowners`
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY project_id, landowner_id 
    ORDER BY updated_at DESC
) = 1;

-- 3. Active Documents View
CREATE OR REPLACE VIEW `contract_intelligence.v_active_documents` AS
SELECT *
FROM `contract_intelligence.documents`
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY document_id 
    ORDER BY updated_at DESC
) = 1;

-- 4. Active Contract Clauses View
CREATE OR REPLACE VIEW `contract_intelligence.v_active_clauses` AS
SELECT *
FROM `contract_intelligence.clauses`
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY document_id, node_id 
    ORDER BY updated_at DESC
) = 1;

-- 5. Active Defined Terms View
CREATE OR REPLACE VIEW `contract_intelligence.v_active_defined_terms` AS
SELECT *
FROM `contract_intelligence.defined_terms`
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY document_id, term_name 
    ORDER BY updated_at DESC
) = 1;

-- 6. Active Exhibits Catalog View
CREATE OR REPLACE VIEW `contract_intelligence.v_active_exhibits_catalog` AS
SELECT *
FROM `contract_intelligence.exhibits_catalog`
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY document_id, exhibit_id 
    ORDER BY updated_at DESC
) = 1;

-- 7. Active Special Conditions & Constraints View
CREATE OR REPLACE VIEW `contract_intelligence.v_active_special_conditions` AS
SELECT *
FROM `contract_intelligence.special_conditions`
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY document_id, condition_id 
    ORDER BY updated_at DESC
) = 1;

-- 8. Active Subcontractor DND Signoffs View
CREATE OR REPLACE VIEW `contract_intelligence.v_active_dnd_signoffs` AS
SELECT *
FROM `contract_intelligence.dnd_signoffs`
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY document_id, signoff_id 
    ORDER BY signed_at DESC
) = 1;

-- 9. Comprehensive Portfolio Intelligence Mart View
CREATE OR REPLACE VIEW `contract_intelligence.v_contract_intelligence_mart` AS
SELECT 
    p.project_id,
    p.project_name,
    p.energy_technology,
    p.erp_project_code,
    p.state_province,
    p.county,
    l.landowner_id,
    l.landowner_name,
    l.parcel_summary,
    d.document_id,
    d.filename,
    d.gcs_pdf_uri,
    d.page_count,
    d.effective_date,
    d.ingestion_status,
    sc.condition_id,
    sc.constraint_category,
    sc.target_asset_or_area,
    sc.actionable_obligation_summary,
    sc.quantitative_metric,
    sc.temporal_restriction,
    sc.penalty_or_consequence,
    sc.hitl_status AS condition_hitl_status
FROM `contract_intelligence.v_active_projects` p
LEFT JOIN `contract_intelligence.v_active_landowners` l 
    ON p.project_id = l.project_id
LEFT JOIN `contract_intelligence.v_active_documents` d 
    ON l.landowner_id = d.landowner_id
LEFT JOIN `contract_intelligence.v_active_special_conditions` sc 
    ON d.document_id = sc.document_id;
