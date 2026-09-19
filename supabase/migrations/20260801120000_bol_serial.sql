-- Shared BOL document numbers (SW-####) for all Windows/Android clients.
-- SECURITY DEFINER RPC so anon can allocate without direct counter writes.
-- Reconstructed for the briysce "Swift Document Generator" project (old shared
-- project gdrpdiwykmnybmkadlrv is gone); matches BolDocumentNumber.allocate().

CREATE TABLE IF NOT EXISTS public.bol_serial_counter (
  id text PRIMARY KEY DEFAULT 'default',
  last_value integer NOT NULL DEFAULT 0 CHECK (last_value >= 0),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.bol_serial_log (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_number text NOT NULL,
  serial_value integer NOT NULL,
  source text,
  client_note text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS bol_serial_log_created_idx
  ON public.bol_serial_log (created_at DESC);

ALTER TABLE public.bol_serial_counter ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bol_serial_log ENABLE ROW LEVEL SECURITY;

-- Counter/log are mutated only via next_bol_serial (SECURITY DEFINER).
-- Anon may read the log for support; direct counter writes stay locked down.
DROP POLICY IF EXISTS "anon_select_bol_serial_log" ON public.bol_serial_log;
CREATE POLICY "anon_select_bol_serial_log"
  ON public.bol_serial_log FOR SELECT TO anon
  USING (true);

DROP POLICY IF EXISTS "anon_select_bol_serial_counter" ON public.bol_serial_counter;
CREATE POLICY "anon_select_bol_serial_counter"
  ON public.bol_serial_counter FOR SELECT TO anon
  USING (true);

GRANT SELECT ON public.bol_serial_log TO anon;
GRANT SELECT ON public.bol_serial_counter TO anon;

INSERT INTO public.bol_serial_counter (id, last_value)
VALUES ('default', 0)
ON CONFLICT (id) DO NOTHING;

CREATE OR REPLACE FUNCTION public.next_bol_serial(
  p_source text DEFAULT NULL,
  p_client_note text DEFAULT NULL
)
RETURNS TABLE(document_number text, serial_value integer)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  next_val integer;
  doc_no text;
BEGIN
  INSERT INTO public.bol_serial_counter (id, last_value)
  VALUES ('default', 0)
  ON CONFLICT (id) DO NOTHING;

  UPDATE public.bol_serial_counter
  SET
    last_value = last_value + 1,
    updated_at = now()
  WHERE id = 'default'
  RETURNING bol_serial_counter.last_value INTO next_val;

  doc_no := 'SW-' || lpad(next_val::text, 4, '0');

  INSERT INTO public.bol_serial_log (
    document_number,
    serial_value,
    source,
    client_note
  ) VALUES (
    doc_no,
    next_val,
    p_source,
    p_client_note
  );

  document_number := doc_no;
  serial_value := next_val;
  RETURN NEXT;
END;
$$;

REVOKE ALL ON FUNCTION public.next_bol_serial(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.next_bol_serial(text, text) TO anon, authenticated;
