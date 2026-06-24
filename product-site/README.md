# VALET marketing + checkout site

The public front door for VALET: a marketing landing page, Stripe
subscription checkout with a 7 day free trial, license issuance into Supabase,
and a license validation endpoint the local app calls to gate access.

This site is intentionally separate from the local app so it survives the
upcoming app re-scaffold without changes.

## Stack

- Next.js (App Router) + TypeScript
- Tailwind CSS, with all design tokens centralized in `styles/tokens.css`
- Stripe Checkout (subscription mode, TEST mode for now)
- Supabase (Postgres) as the license store
- Deploy target: Vercel

## Local setup

1. Install dependencies:

   ```
   npm install
   ```

2. Copy the env template and fill it in:

   ```
   cp .env.example .env.local
   ```

   | Variable | What it is |
   | --- | --- |
   | `STRIPE_SECRET_KEY` | Stripe secret key (test mode) |
   | `STRIPE_WEBHOOK_SECRET` | Signing secret for your webhook endpoint |
   | `STRIPE_PRICE_ID` | Price ID for the $20 per month plan |
   | `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY` | Stripe publishable key |
   | `SUPABASE_URL` | Supabase project URL |
   | `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key (server only) |
   | `NEXT_PUBLIC_SITE_URL` | Base URL, e.g. `http://localhost:3000` |

3. Create the database table. In the Supabase SQL editor, run
   `supabase/migration.sql`.

4. In Stripe (test mode), create a recurring Price at $20 per month and put its
   ID in `STRIPE_PRICE_ID`.

5. Run the dev server:

   ```
   npm run dev
   ```

## Stripe webhook (local)

Use the Stripe CLI to forward events to the local webhook route:

```
stripe listen --forward-to localhost:3000/api/stripe/webhook
```

Copy the `whsec_...` it prints into `STRIPE_WEBHOOK_SECRET`.

## API contracts

| Route | Method | Purpose |
| --- | --- | --- |
| `/api/checkout` | POST | Creates a subscription Checkout Session, returns `{ url }` |
| `/api/stripe/webhook` | POST | Verifies signature, upserts license rows on subscription events |
| `/api/license/validate` | POST | `{ license_key }` -> `{ status, plan, current_period_end }` |
| `/api/download` | GET | Validates the license, returns the placeholder artifact |

`status` is one of: `active`, `trialing`, `past_due`, `canceled`, `invalid`.

## Deploy (Vercel)

1. Import the repo into Vercel.
2. Add every variable from `.env.example` in the Vercel project settings.
3. In the Stripe dashboard, add a webhook endpoint at
   `https://www.valet-voice.com/api/stripe/webhook` subscribed to
   `checkout.session.completed`, `customer.subscription.created`,
   `customer.subscription.updated`, and `customer.subscription.deleted`. Put its
   signing secret in `STRIPE_WEBHOOK_SECRET`. Use the canonical `www` host: the
   apex `valet-voice.com` 308-redirects and Stripe does **not** follow redirects,
   so registering the bare apex (or any stale deployment alias) silently fails
   every delivery.
4. Set `NEXT_PUBLIC_SITE_URL` to the production URL (`https://www.valet-voice.com`).

## Swapping in the real installer

The download route serves a placeholder. When the signed app build exists, set
`DOWNLOAD_SOURCE` at the top of `app/api/download/route.ts` to its URL or storage
key. That one line flips the route from placeholder to real installer.

## Open questions (surfaced, not blocking)

- Final public product name and domain (everything uses `VALET`).
- Bundled key cost model: confirms whether "everything included, no API key"
  copy is accurate at launch.
- Real download artifact format (signed DMG, Tauri/Electron wrapper, installer).
- Whether to email the license key on purchase (currently shown on screen only).
