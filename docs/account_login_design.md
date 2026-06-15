# Account login → auto-provision license key + profile

> Design for the "log in once, everything fills in" feature. Replaces manual
> license-key pasting: the user signs in with their account email + password and
> the app provisions both the **license key** and the **profile**
> (name / honorific / DOB / location) from their account.
> Date: 2026-06-15.

## What already exists (so we build less)

The account backend is on Supabase and most of the data layer is done:

- **Auth:** Supabase **email + password** (`signInWithPassword` / `signUp`,
  `product-site/components/account/AuthForm.tsx`). Email confirmation gates the
  session.
- **Licenses:** `licenses` table keyed by `license_key`, linked to a Supabase
  `user_id` (`lib/account.ts` → `getAccountLicenses(userId)`, `claimLicenseByKey`).
- **Profile + sync:** the desktop app **already pushes** a profile snapshot
  `{name, honorific, date_of_birth, location, work_email, personal_email}` to
  `POST /api/proxy/sync` (keyed by `X-License-Key`). It's stored in the
  **`account_sync`** table and read back by `getLatestAccountSync(userId)`; the
  `/account` page already renders it (read-only).
- **Settings pull:** `GET /api/proxy/device-settings` already pulls
  web-controlled settings down by license key — the pattern we mirror for profile.

So the **only genuinely missing capability is provisioning the license key from a
login**. The profile model, storage, push-sync, and display all exist.

## Design

### PR A — `product-site` (this PR)

1. **`getSupabaseAnon()`** (`lib/supabase.ts`) — an anon-key client (created per
   call, no shared session) so we can validate a password with
   `signInWithPassword`. The existing admin client uses the service-role key and
   bypasses auth, so it can't verify passwords.
2. **`loginAndProvision(email, password)`** (`lib/account.ts`) —
   `signInWithPassword` → on success, `getAccountLicenses(user.id)` (pick the
   entitled license, else most recent) + `getLatestAccountSync(user.id)?.profile`
   → returns `{ licenseKey, status, planLabel, profile }`. Typed result;
   distinguishes bad-credentials, unconfirmed-email, and no-license.
3. **`POST /api/account/app-login`** — `{email, password}` → `loginAndProvision`
   → `{ license_key, status, plan, profile, has_license }`. The password is used
   immediately for `signInWithPassword` and **never stored or logged**; Supabase
   Auth rate-limits sign-ins.
4. **`GET /api/proxy/profile`** (`X-License-Key`) + `getProfileByLicenseKey()` —
   lets the app re-pull the profile at launch without re-login (mirrors
   `device-settings`).

### PR B — the app (follow-up)

- A **"Log in to account"** form in onboarding **and** under User Settings
  (email + password) → `POST /api/account/app-login` → write `LICENSE_KEY` to
  `~/Library/Application Support/VALET/.env` and populate Name / Honorific / DOB /
  Location from the returned `profile`.
- **Launch-time re-pull** of the profile via `GET /api/proxy/profile`, applied
  the same way `_fetch_and_apply_device_settings` applies settings.
- **Keep manual license-key entry** as a fallback (offline / no account).
- `has_license: false` → prompt the user to purchase / claim.

## Auth & privacy

- The password transits app → proxy → Supabase over TLS only, exactly like the
  license key does today; it is never persisted or logged on our side.
- The **license key remains the bearer** for every other proxy call — login only
  *provisions* it; it doesn't change the per-request auth model.
- Profile fields (incl. DOB / location) already live in `account_sync`; the
  login response returns them to the authenticated caller over TLS — no new PII
  surface beyond what the sync already stores.

## Edge cases

| Case | Handling |
|---|---|
| Wrong email/password | `401`, "Incorrect email or password." |
| Email not confirmed | `403`, "Please confirm your email, then try again." |
| No license on the account | `200` `has_license:false` → app prompts to purchase/claim |
| Multiple licenses | pick `active`/`trialing`, else most recent (list is `created_at desc`) |
| Profile never synced | `profile: null` → app keeps whatever's already in `.env` |

## Deferred (optional, later)

A **web profile editor** on `/account` (today the desktop app is the source of
truth via sync-up; editing on the web is a convenience, not required for this
flow). Not in PR A or B.
