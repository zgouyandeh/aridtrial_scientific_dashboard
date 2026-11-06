-- Additive source-lineage migration. Safe for both existing and freshly-created schemas.

ALTER TABLE raw.kobo_submission
  ADD COLUMN IF NOT EXISTS source_type text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_name text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_dataset text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS manifest_sha256 text,
  ADD COLUMN IF NOT EXISTS contract_sha256 text;

ALTER TABLE raw.file_drop
  ADD COLUMN IF NOT EXISTS source_type text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_name text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_dataset text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS manifest_sha256 text,
  ADD COLUMN IF NOT EXISTS contract_sha256 text;

ALTER TABLE audit.quarantine
  ADD COLUMN IF NOT EXISTS source_type text,
  ADD COLUMN IF NOT EXISTS source_name text,
  ADD COLUMN IF NOT EXISTS source_dataset text,
  ADD COLUMN IF NOT EXISTS source_sha256 text,
  ADD COLUMN IF NOT EXISTS manifest_sha256 text,
  ADD COLUMN IF NOT EXISTS contract_sha256 text;

ALTER TABLE core.soil_sample
  ADD COLUMN IF NOT EXISTS source_type text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_name text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_dataset text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS manifest_sha256 text,
  ADD COLUMN IF NOT EXISTS contract_sha256 text;
ALTER TABLE core.tree_obs
  ADD COLUMN IF NOT EXISTS source_type text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_name text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_dataset text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS manifest_sha256 text,
  ADD COLUMN IF NOT EXISTS contract_sha256 text;
ALTER TABLE core.sensor_reading
  ADD COLUMN IF NOT EXISTS source_type text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_name text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_dataset text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS manifest_sha256 text,
  ADD COLUMN IF NOT EXISTS contract_sha256 text;
ALTER TABLE core.weather_daily
  ADD COLUMN IF NOT EXISTS source_type text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_name text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_dataset text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS manifest_sha256 text,
  ADD COLUMN IF NOT EXISTS contract_sha256 text;
ALTER TABLE core.water_meter
  ADD COLUMN IF NOT EXISTS source_type text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_name text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_dataset text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS manifest_sha256 text,
  ADD COLUMN IF NOT EXISTS contract_sha256 text;
ALTER TABLE core.ndvi
  ADD COLUMN IF NOT EXISTS source_type text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_name text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_dataset text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS manifest_sha256 text,
  ADD COLUMN IF NOT EXISTS contract_sha256 text;
ALTER TABLE core.weather_reanalysis
  ADD COLUMN IF NOT EXISTS source_type text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_name text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_dataset text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS manifest_sha256 text,
  ADD COLUMN IF NOT EXISTS contract_sha256 text;
ALTER TABLE core.ndvi_block
  ADD COLUMN IF NOT EXISTS source_type text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_name text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_dataset text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS manifest_sha256 text,
  ADD COLUMN IF NOT EXISTS contract_sha256 text;

ALTER TABLE core.plot
  ADD COLUMN IF NOT EXISTS source_sha256 text,
  ADD COLUMN IF NOT EXISTS source_type text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_name text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS source_dataset text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS manifest_sha256 text,
  ADD COLUMN IF NOT EXISTS contract_sha256 text;

CREATE INDEX IF NOT EXISTS raw_kobo_source_lookup ON raw.kobo_submission (form_id, source_type, source_name);
CREATE INDEX IF NOT EXISTS raw_file_drop_source_lookup ON raw.file_drop (dataset, source_type, source_name);

-- Raw landing tables are append-only, so legacy source identity is derived at read time
-- rather than updating historical raw records. Only rebuildable core tables are backfilled.
UPDATE core.soil_sample c
SET source_type = CASE WHEN r.source_type = 'unknown' AND r.source LIKE 'mock:%' THEN 'synthetic' ELSE r.source_type END,
    source_name = CASE WHEN r.source_type = 'unknown' AND r.source LIKE 'mock:%' THEN 'simulate.kobo.make_submissions' ELSE r.source_name END,
    source_dataset = CASE WHEN r.source_dataset = 'unknown' THEN 'soil_samples' ELSE r.source_dataset END
FROM raw.kobo_submission r
WHERE c.payload_sha256 = r.payload_sha256 AND c.source_type = 'unknown';

UPDATE core.tree_obs c
SET source_type = CASE WHEN r.source_type = 'unknown' AND r.source LIKE 'mock:%' THEN 'synthetic' ELSE r.source_type END,
    source_name = CASE WHEN r.source_type = 'unknown' AND r.source LIKE 'mock:%' THEN 'simulate.kobo.make_submissions' ELSE r.source_name END,
    source_dataset = CASE WHEN r.source_dataset = 'unknown' THEN 'tree_observations' ELSE r.source_dataset END
FROM raw.kobo_submission r
WHERE c.payload_sha256 = r.payload_sha256 AND c.source_type = 'unknown';

UPDATE core.sensor_reading c
SET source_type = CASE WHEN r.source_type = 'unknown' THEN 'synthetic' ELSE r.source_type END,
    source_name = CASE WHEN r.source_type = 'unknown' THEN r.file_name ELSE r.source_name END,
    source_dataset = COALESCE(NULLIF(r.source_dataset, 'unknown'), 'sensor_readings')
FROM raw.file_drop r
WHERE c.source_sha256 = r.sha256 AND r.dataset = 'sensors' AND c.source_type = 'unknown';

UPDATE core.weather_daily c
SET source_type = CASE WHEN r.source_type = 'unknown' THEN 'synthetic_station' ELSE r.source_type END,
    source_name = CASE WHEN r.source_type = 'unknown' THEN r.file_name ELSE r.source_name END,
    source_dataset = COALESCE(NULLIF(r.source_dataset, 'unknown'), 'station_weather')
FROM raw.file_drop r
WHERE c.source_sha256 = r.sha256 AND r.dataset = 'weather' AND c.source_type = 'unknown';

UPDATE core.water_meter c
SET source_type = CASE WHEN r.source_type = 'unknown' THEN 'synthetic' ELSE r.source_type END,
    source_name = CASE WHEN r.source_type = 'unknown' THEN r.file_name ELSE r.source_name END,
    source_dataset = COALESCE(NULLIF(r.source_dataset, 'unknown'), 'water_meter_readings')
FROM raw.file_drop r
WHERE c.source_sha256 = r.sha256 AND r.dataset = 'meters' AND c.source_type = 'unknown';

UPDATE core.ndvi c
SET source_type = CASE WHEN r.source_type = 'unknown' THEN 'synthetic_plot' ELSE r.source_type END,
    source_name = CASE WHEN r.source_type = 'unknown' THEN r.file_name ELSE r.source_name END,
    source_dataset = COALESCE(NULLIF(r.source_dataset, 'unknown'), 'plot_level_ndvi')
FROM raw.file_drop r
WHERE c.source_sha256 = r.sha256 AND r.dataset = 'ndvi' AND c.source_type = 'unknown';

UPDATE core.weather_reanalysis c
SET source_type = CASE WHEN r.source_type = 'unknown' THEN 'real' ELSE r.source_type END,
    source_name = CASE WHEN r.source_type = 'unknown' THEN r.file_name ELSE r.source_name END,
    source_dataset = COALESCE(NULLIF(r.source_dataset, 'unknown'), 'weather_reanalysis')
FROM raw.file_drop r
WHERE c.source_sha256 = r.sha256 AND r.dataset = 'reanalysis' AND c.source_type = 'unknown';

UPDATE core.ndvi_block c
SET source_type = CASE WHEN r.source_type = 'unknown' THEN 'real' ELSE r.source_type END,
    source_name = CASE WHEN r.source_type = 'unknown' THEN r.file_name ELSE r.source_name END,
    source_dataset = COALESCE(NULLIF(r.source_dataset, 'unknown'), 'sentinel2_block_ndvi')
FROM raw.file_drop r
WHERE c.source_sha256 = r.sha256 AND r.dataset = 'ndvi_block' AND c.source_type = 'unknown';

UPDATE core.plot
SET source_type = 'synthetic', source_name = 'design.layout.make_layout', source_dataset = 'plot_layout'
WHERE source_type = 'unknown';
