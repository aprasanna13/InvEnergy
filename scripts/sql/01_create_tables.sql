-- ==============================================================================
-- Invenergy Contract Intelligence Platform - BigQuery Table DDL
-- Dataset: contract_intelligence (or configured BQ_DATASET_ID)
--
-- Tables:
--   1. projects
--   2. landowners
--   3. documents
--   4. clauses
--   5. defined_terms
--   6. exhibits_catalog
--   7. special_conditions
--   8. dnd_signoffs
-- ==============================================================================

-- 1. Projects Table (Portfolio Root)
CREATE TABLE IF NOT EXISTS `contract_intelligence.projects` (
    project_id STRING NOT NULL,
    project_name STRING NOT NULL,
    energy_technology STRING NOT NULL,
    erp_project_code STRING NOT NULL,
    state_province STRING,
    county STRING,
    target_capacity_mw FLOAT64,
    landowner_count INT64 NOT NULL,
    document_count INT64 NOT NULL,
    special_conditions_count INT64 NOT NULL,
    flagged_node_count INT64 NOT NULL,
    updated_at TIMESTAMP NOT NULL
)
CLUSTER BY project_id;

-- 2. Landowners Table (Portfolio Tier 2)
CREATE TABLE IF NOT EXISTS `contract_intelligence.landowners` (
    landowner_id STRING NOT NULL,
    project_id STRING NOT NULL,
    landowner_name STRING NOT NULL,
    qrm_party_id STRING,
    grantee_entity_name STRING,
    parcel_summary STRING,
    is_multi_parcel BOOL NOT NULL,
    contract_count INT64 NOT NULL,
    special_conditions_count INT64 NOT NULL,
    updated_at TIMESTAMP NOT NULL
)
CLUSTER BY project_id, landowner_id;

-- 3. Documents Registry (Portfolio Tier 3 & Contract Metadata)
CREATE TABLE IF NOT EXISTS `contract_intelligence.documents` (
    document_id STRING NOT NULL,
    filename STRING NOT NULL,
    gcs_pdf_uri STRING NOT NULL,
    gcs_export_prefix STRING NOT NULL,
    page_count INT64 NOT NULL,
    contracting_parties_json JSON,
    effective_date STRING,
    flagged_node_count INT64 NOT NULL,
    special_conditions_count INT64,
    ingestion_status STRING NOT NULL,
    error_message STRING,
    ingested_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    project_id STRING,
    landowner_id STRING,
    energy_technology STRING,
    grantor_landowner_name STRING,
    grantee_entity_name STRING
)
PARTITION BY DATE(ingested_at)
CLUSTER BY project_id, document_id;

-- 4. Contract Clauses (Hierarchical AST Nodes)
CREATE TABLE IF NOT EXISTS `contract_intelligence.clauses` (
    document_id STRING NOT NULL,
    gcs_pdf_uri STRING NOT NULL,
    node_id STRING NOT NULL,
    parent_node_id STRING,
    sibling_order INT64 NOT NULL,
    document_zone STRING NOT NULL,
    canonical_path STRING NOT NULL,
    depth INT64 NOT NULL,
    clause_label STRING NOT NULL,
    numbering_scheme STRING NOT NULL,
    level_1_label STRING,
    level_2_label STRING,
    level_3_label STRING,
    level_4_label STRING,
    level_5_plus_path STRING,
    clause_title STRING,
    is_inline_clause BOOL NOT NULL,
    preamble_text STRING,
    verbatim_text STRING NOT NULL,
    postamble_text STRING,
    reconstructed_context_text STRING NOT NULL,
    defined_terms_used STRING NOT NULL,
    cross_references STRING NOT NULL,
    page_start INT64 NOT NULL,
    page_end INT64 NOT NULL,
    has_special_condition BOOL,
    special_condition_count INT64,
    hitl_status STRING NOT NULL,
    hitl_flag_reasons STRING NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    reviewed_by STRING,
    review_notes STRING
)
CLUSTER BY document_id, node_id;

-- 5. Defined Terms Glossary
CREATE TABLE IF NOT EXISTS `contract_intelligence.defined_terms` (
    document_id STRING NOT NULL,
    term_name STRING NOT NULL,
    defined_in_node_id STRING NOT NULL,
    definition_type STRING NOT NULL,
    verbatim_definition STRING NOT NULL,
    referenced_in_nodes STRING NOT NULL,
    page_number INT64 NOT NULL,
    updated_at TIMESTAMP NOT NULL
)
CLUSTER BY document_id, term_name;

-- 6. Exhibits & Schedules Catalog
CREATE TABLE IF NOT EXISTS `contract_intelligence.exhibits_catalog` (
    document_id STRING NOT NULL,
    exhibit_id STRING NOT NULL,
    exhibit_title STRING NOT NULL,
    exhibit_modality STRING NOT NULL,
    page_start INT64 NOT NULL,
    page_end INT64 NOT NULL,
    referenced_by_nodes STRING NOT NULL,
    structured_entities_json JSON,
    has_unresolved_external_dep BOOL NOT NULL,
    updated_at TIMESTAMP NOT NULL
)
CLUSTER BY document_id, exhibit_id;

-- 7. Special Landowner & Construction Conditions
CREATE TABLE IF NOT EXISTS `contract_intelligence.special_conditions` (
    condition_id STRING NOT NULL,
    document_id STRING NOT NULL,
    node_id STRING NOT NULL,
    canonical_path STRING NOT NULL,
    constraint_category STRING NOT NULL,
    target_asset_or_area STRING NOT NULL,
    quantitative_metric STRING,
    temporal_restriction STRING,
    penalty_or_consequence STRING,
    actionable_obligation_summary STRING NOT NULL,
    verbatim_excerpt STRING NOT NULL,
    page_number INT64 NOT NULL,
    extraction_confidence FLOAT64 NOT NULL,
    hitl_status STRING NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    reviewed_by STRING,
    project_id STRING,
    landowner_id STRING
)
CLUSTER BY document_id, condition_id;

-- 8. Subcontractor Do-Not-Disturb (DND) Signoffs
CREATE TABLE IF NOT EXISTS `contract_intelligence.dnd_signoffs` (
    signoff_id STRING NOT NULL,
    document_id STRING NOT NULL,
    project_id STRING NOT NULL,
    landowner_id STRING NOT NULL,
    subcontractor_company STRING NOT NULL,
    foreman_name STRING NOT NULL,
    construction_trade STRING NOT NULL,
    acknowledged_condition_ids STRING NOT NULL,
    acknowledged_count INT64 NOT NULL,
    dispatch_readiness_at_signoff STRING NOT NULL,
    briefing_notes STRING,
    signed_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(signed_at)
CLUSTER BY project_id, document_id;
