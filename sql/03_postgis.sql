-- PostGIS extensions. Requires the postgis extension (docker image postgis/postgis).
-- NOT executed by the plain-PostgreSQL test suite in this repository; run it in the compose stack.

CREATE EXTENSION IF NOT EXISTS postgis;

-- Real geometry column derived from the WKT that the pipeline stores (UTM 40N, metres).
ALTER TABLE core.plot
  ADD COLUMN IF NOT EXISTS geom geometry(Polygon, 32640)
  GENERATED ALWAYS AS (ST_GeomFromText(geom_wkt, 32640)) STORED;
CREATE INDEX IF NOT EXISTS plot_geom_gix ON core.plot USING gist (geom);

-- Plot polygons as GeoJSON for map panels / QGIS.
CREATE OR REPLACE VIEW mart.v_plot_geojson AS
SELECT plot_id, block, treatment, ST_AsGeoJSON(ST_Transform(geom, 4326))::jsonb AS geometry
FROM core.plot;

-- SQL twin of the Python rule soil.gps_in_plot: field GPS fix must fall within 10 m of the plot polygon.
CREATE OR REPLACE VIEW audit.v_soil_gps_check AS
SELECT s.sample_key, s.plot_id,
       ST_Distance(ST_Transform(ST_SetSRID(ST_MakePoint(s.gps_lon, s.gps_lat), 4326), 32640), p.geom) AS dist_to_plot_m,
       ST_Distance(ST_Transform(ST_SetSRID(ST_MakePoint(s.gps_lon, s.gps_lat), 4326), 32640), p.geom) > 10 AS outside_tolerance
FROM core.soil_sample s JOIN core.plot p USING (plot_id)
WHERE s.status = 'current' AND s.gps_lon IS NOT NULL;

-- Overlap guard for the design itself: plots must not intersect.
CREATE OR REPLACE VIEW audit.v_plot_overlaps AS
SELECT a.plot_id AS plot_a, b.plot_id AS plot_b
FROM core.plot a JOIN core.plot b ON a.plot_id < b.plot_id AND ST_Intersects(a.geom, b.geom);

-- Zonal NDVI: pixels from a raster/vector table can be aggregated to plots with ST_Intersection;
-- the Python pipeline loads plot-mean NDVI already, so this is the extension point for pixel-level data.
