-- Shared customer presets + logos for Swift Document Generator (all devices).
-- Security: anon read/write — same posture as publishable key in mobile app (shared org tool, not multi-tenant).

CREATE TABLE IF NOT EXISTS public.customer_presets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind text NOT NULL CHECK (kind IN ('shipping', 'receiving', 'bol')),
  name text NOT NULL,
  fields jsonb NOT NULL DEFAULT '{}'::jsonb,
  logo_refs text[] NOT NULL DEFAULT '{}',
  updated_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (kind, name)
);

CREATE TABLE IF NOT EXISTS public.customer_logos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  storage_path text NOT NULL UNIQUE,
  file_name text NOT NULL,
  content_type text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.customer_presets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.customer_logos ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_all_customer_presets" ON public.customer_presets;
CREATE POLICY "anon_all_customer_presets"
  ON public.customer_presets FOR ALL TO anon
  USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anon_all_customer_logos" ON public.customer_logos;
CREATE POLICY "anon_all_customer_logos"
  ON public.customer_logos FOR ALL TO anon
  USING (true) WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE, DELETE ON public.customer_presets TO anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.customer_logos TO anon;

CREATE OR REPLACE FUNCTION public.touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS customer_presets_touch_updated ON public.customer_presets;
CREATE TRIGGER customer_presets_touch_updated
  BEFORE UPDATE ON public.customer_presets
  FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

DROP TRIGGER IF EXISTS customer_logos_touch_updated ON public.customer_logos;
CREATE TRIGGER customer_logos_touch_updated
  BEFORE UPDATE ON public.customer_logos
  FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
  'customer-logos',
  'customer-logos',
  true,
  5242880,
  ARRAY['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/bmp']
)
ON CONFLICT (id) DO UPDATE SET
  public = EXCLUDED.public,
  file_size_limit = EXCLUDED.file_size_limit,
  allowed_mime_types = EXCLUDED.allowed_mime_types;

DROP POLICY IF EXISTS "anon_select_customer_logos_storage" ON storage.objects;
CREATE POLICY "anon_select_customer_logos_storage"
  ON storage.objects FOR SELECT TO anon
  USING (bucket_id = 'customer-logos');

DROP POLICY IF EXISTS "anon_insert_customer_logos_storage" ON storage.objects;
CREATE POLICY "anon_insert_customer_logos_storage"
  ON storage.objects FOR INSERT TO anon
  WITH CHECK (bucket_id = 'customer-logos');

DROP POLICY IF EXISTS "anon_update_customer_logos_storage" ON storage.objects;
CREATE POLICY "anon_update_customer_logos_storage"
  ON storage.objects FOR UPDATE TO anon
  USING (bucket_id = 'customer-logos')
  WITH CHECK (bucket_id = 'customer-logos');

DROP POLICY IF EXISTS "anon_delete_customer_logos_storage" ON storage.objects;
CREATE POLICY "anon_delete_customer_logos_storage"
  ON storage.objects FOR DELETE TO anon
  USING (bucket_id = 'customer-logos');
