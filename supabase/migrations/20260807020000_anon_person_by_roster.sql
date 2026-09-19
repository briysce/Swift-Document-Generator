-- Allow Swift Document Generator (anon key) to autocomplete Swift Contact
-- from SLST employee roster names only.
--
-- On a fresh Document Generator project the SLST-owned `dropdown_roster`
-- table may not exist yet. Create a compatible stub so this policy applies;
-- SLST (or a later seed) can populate `person_by` rows. App UI now prefers
-- `shared_contacts` via ContactSync; this remains for legacy tools/tests.

CREATE TABLE IF NOT EXISTS public.dropdown_roster (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  roster_type text NOT NULL,
  value text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS dropdown_roster_type_value_idx
  ON public.dropdown_roster (roster_type, value);

ALTER TABLE public.dropdown_roster ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_person_by_roster" ON public.dropdown_roster;
CREATE POLICY "anon_select_person_by_roster"
  ON public.dropdown_roster
  FOR SELECT
  TO anon
  USING (roster_type = 'person_by');

GRANT SELECT ON public.dropdown_roster TO anon;
