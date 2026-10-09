-- ==============================================================================
-- Invenergy Contract Intelligence Platform - Initial ERP Portfolio Projects Seed
-- Dataset: contract_intelligence (or configured BQ_DATASET_ID)
--
-- Inserts baseline renewable energy development assets into the projects table.
-- ==============================================================================

MERGE `contract_intelligence.projects` T
USING (
    SELECT 
        'prj_cedar_lantern_wind' AS project_id,
        'Cedar Lantern Wind Energy Center' AS project_name,
        'ONSHORE_WIND' AS energy_technology,
        'ERP-WND-001' AS erp_project_code,
        'IL' AS state_province,
        'McLean' AS county,
        250.0 AS target_capacity_mw,
        0 AS landowner_count,
        0 AS document_count,
        0 AS special_conditions_count,
        0 AS flagged_node_count,
        CURRENT_TIMESTAMP() AS updated_at
    UNION ALL SELECT 
        'prj_sun_ridge_solar',
        'Sun Ridge Solar & Agrivoltaics',
        'SOLAR',
        'ERP-SOL-002',
        'OH',
        'Hardin',
        180.0,
        0, 0, 0, 0,
        CURRENT_TIMESTAMP()
    UNION ALL SELECT 
        'prj_prairie_vault_storage',
        'Prairie Vault BESS Storage',
        'STORAGE',
        'ERP-STR-003',
        'TX',
        'Pecos',
        150.0,
        0, 0, 0, 0,
        CURRENT_TIMESTAMP()
    UNION ALL SELECT 
        'prj_blue_meridian_tx',
        'Blue Meridian HVDC Transmission',
        'TRANSMISSION',
        'ERP-TRN-004',
        'KS',
        'Finney',
        800.0,
        0, 0, 0, 0,
        CURRENT_TIMESTAMP()
    UNION ALL SELECT 
        'prj_caldera_geothermal',
        'Caldera Basin Geothermal',
        'GEOTHERMAL',
        'ERP-GEO-005',
        'NV',
        'Churchill',
        95.0,
        0, 0, 0, 0,
        CURRENT_TIMESTAMP()
) S
ON T.project_id = S.project_id
WHEN NOT MATCHED THEN
    INSERT (
        project_id, project_name, energy_technology, erp_project_code,
        state_province, county, target_capacity_mw, landowner_count,
        document_count, special_conditions_count, flagged_node_count, updated_at
    )
    VALUES (
        S.project_id, S.project_name, S.energy_technology, S.erp_project_code,
        S.state_province, S.county, S.target_capacity_mw, S.landowner_count,
        S.document_count, S.special_conditions_count, S.flagged_node_count, S.updated_at
    );
