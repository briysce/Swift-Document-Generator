# Supabase (project `gdrpdiwykmnybmkadlrv`)

Shared backend for Swift Document Generator:

- **`next_bol_serial`** — shared BOL document numbers (SECURITY DEFINER RPC)
- **`customer_presets`** / **`customer_logos`** — presets and logo metadata synced across Windows/Android
- **`signatures`** — saved BOL shipper signatures (PNG metadata; bytes in Storage)
- **`shared_contacts`** / **`shared_contact_tombstones`** — PM / Received By / shipper name memory synced across Windows/Android (Document Generator–owned; **not** SLST `dropdown_roster`)
- **`shared_delivery_addresses`** — Delivery Address book shared by Shipping + BOL
- **`shared_carriers`** / **`shared_carrier_tombstones`** — Shipping "Carrier" field autocomplete; **owned by Swift Staging & Shipping Log**, Document Generator's `CarrierSync` (`mobile/lib/carrier_sync.dart`) just reads/writes the same tables (Windows/Android + Wear all share one directory)
- **`generated_documents`** + Storage bucket **`generated-documents`** — generated PDF history (cloud source of truth; local `filled/` is cache)
- **Storage bucket `customer-logos`** — customer logo image bytes
- **Storage bucket `signatures`** — shipper signature PNGs (max 2 MB, `image/png` only)

## Security posture

Same as BOL serial: the mobile app ships the **anon (publishable) key**. These tables use RLS policies that allow `anon` read/write because this is a single-company internal tool, not multi-tenant user auth. Anyone with the app can read/write shared presets, signatures, and contacts (equivalent to editing a shared spreadsheet). Do not store secrets in presets.

### `shared_contacts` table

| Column | Type | Notes |
|--------|------|-------|
| `name_key` | text PK | `lower(trim(name))` — case-insensitive identity |
| `name` | text | Display name as typed |
| `last_used_at` | timestamptz | MRU ordering |
| `updated_at` | timestamptz | Touch trigger on update |

Forget writes a row in `shared_contact_tombstones` so other devices do not re-push the name.

App flow: type or pick a name on generate → upsert shared contact → other devices pull on launch. Delete (X) removes the shared row and tombstones the key.

### `shared_carriers` table

Same shape as `shared_contacts` (`name_key` PK / `name` / `last_used_at` / `updated_at` / `created_at`), but **owned by Swift Staging & Shipping Log**, not Document Generator — `CarrierSync` only reads/writes it so the Shipping "Carrier" field shares remembered names with that app. No local cache: fetched live on focus/launch, no per-device persistence. Forget/tombstone flow mirrors `shared_contacts`.

### `signatures` table

| Column | Type | Notes |
|--------|------|-------|
| `id` | text PK | Client-generated id (used as storage filename stem) |
| `name` | text | Display name ("My signature", shipper cert name, etc.) |
| `storage_path` | text unique | e.g. `{id}.png` in bucket `signatures` |
| `updated_at` | timestamptz | Touch trigger on update |

App flow: draw signature → optional cloud save → pick from list on later BOLs → embed PNG on shipper certification signature line in flat PDF.

Migrations live in `supabase/migrations/`.
