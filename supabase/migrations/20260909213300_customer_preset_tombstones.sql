-- Tombstones for customer_presets so a delete on one device/install is not
-- silently re-uploaded by another device that still has a stale local copy.
-- customer_presets previously had no tombstone table at all (unlike
-- shared_contacts / shared_delivery_addresses / shared_carriers), so a
-- deleted preset could resurrect: PresetSync.syncOnLaunch()'s
-- _pushLocalOnlyPresets() would see the name missing from remote and
-- re-INSERT it from any device/install whose local presets.json still had
-- it cached, generating a brand-new row (fresh id/created_at) with no way
-- to tell it apart from a legitimate re-creation. Root cause of the "K"
-- BOL preset repeatedly reappearing after deletion.

CREATE TABLE IF NOT EXISTS public.customer_preset_tombstones (
  kind text NOT NULL CHECK (kind IN ('shipping', 'receiving', 'bol')),
  name text NOT NULL,
  deleted_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (kind, name)
);

ALTER TABLE public.customer_preset_tombstones ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_all_customer_preset_tombstones"
  ON public.customer_preset_tombstones;
CREATE POLICY "anon_all_customer_preset_tombstones"
  ON public.customer_preset_tombstones FOR ALL TO anon
  USING (true) WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE, DELETE ON public.customer_preset_tombstones TO anon;
