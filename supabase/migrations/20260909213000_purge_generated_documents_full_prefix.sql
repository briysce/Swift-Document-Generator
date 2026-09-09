-- Fix: purge_generated_documents_older_than() only ever deleted the exact
-- `storage_path` (the PDF itself), leaving sibling `form.json` / `logo_N.png`
-- objects uploaded under the same `{kind}/{id}/` prefix (see
-- DocumentHistorySync.upload / _saveSnapshot) permanently orphaned in the
-- `generated-documents` Storage bucket once the row was purged. The Dart
-- in-app delete path (DocumentHistorySync.deleteRecord) already does this
-- correctly (lists + deletes the whole prefix) — this brings the server-side
-- 90-day cron purge up to the same standard.
--
-- No app code depends on the function signature; this only redefines the
-- body. Verified live: the pg_cron job `purge-generated-documents-90d`
-- (schedule '15 4 * * *') is already active and calls this function by name,
-- so it picks up the fix automatically on its next run.

CREATE OR REPLACE FUNCTION public.purge_generated_documents_older_than(
  retention_days integer DEFAULT 90
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, storage
AS $$
DECLARE
  rec record;
  n integer := 0;
BEGIN
  IF retention_days < 1 THEN
    retention_days := 90;
  END IF;

  FOR rec IN
    SELECT id, kind, storage_path
    FROM public.generated_documents
    WHERE created_at < (now() - make_interval(days => retention_days))
  LOOP
    -- Delete every object under kind/id/ (PDF + form.json + logo_N.png),
    -- not just the single row's own storage_path.
    DELETE FROM storage.objects
    WHERE bucket_id = 'generated-documents'
      AND name LIKE (rec.kind || '/' || rec.id || '/%');

    DELETE FROM public.generated_documents
    WHERE id = rec.id;
    n := n + 1;
  END LOOP;

  RETURN n;
END;
$$;

REVOKE ALL ON FUNCTION public.purge_generated_documents_older_than(integer)
  FROM PUBLIC;
REVOKE ALL ON FUNCTION public.purge_generated_documents_older_than(integer)
  FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.purge_generated_documents_older_than(integer)
  TO postgres;
