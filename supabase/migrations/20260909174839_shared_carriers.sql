-- Shared carrier-name directory for the Shipping "Carrier" field.
--
-- NOT owned by Document Generator: this is the same `shared_carriers` /
-- `shared_carrier_tombstones` store already used by Swift Staging &
-- Shipping Log's "Carrier" field (Windows/Android + Wear) — see
-- `mobile/lib/carrier_sync.dart`. This migration (version 20260909174839)
-- was already applied directly to the shared Supabase project before this
-- file existed in this repo's tree; committing it here with the matching
-- version/name just makes the already-live schema reproducible from this
-- repo too (`IF NOT EXISTS` / `DROP ... IF EXISTS` throughout, so re-running
-- it is a no-op). Keep this in sync with the sibling repo's copy if either
-- changes the shape.

CREATE TABLE IF NOT EXISTS public.shared_carriers (
  name_key text PRIMARY KEY,
  name text NOT NULL,
  last_used_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.shared_carrier_tombstones (
  name_key text PRIMARY KEY,
  deleted_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.shared_carriers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.shared_carrier_tombstones ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_all_shared_carriers" ON public.shared_carriers;
CREATE POLICY "anon_all_shared_carriers"
  ON public.shared_carriers FOR ALL TO anon
  USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anon_all_shared_carrier_tombstones"
  ON public.shared_carrier_tombstones;
CREATE POLICY "anon_all_shared_carrier_tombstones"
  ON public.shared_carrier_tombstones FOR ALL TO anon
  USING (true) WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE, DELETE ON public.shared_carriers TO anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.shared_carrier_tombstones TO anon;

DROP TRIGGER IF EXISTS shared_carriers_touch_updated ON public.shared_carriers;
CREATE TRIGGER shared_carriers_touch_updated
  BEFORE UPDATE ON public.shared_carriers
  FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
