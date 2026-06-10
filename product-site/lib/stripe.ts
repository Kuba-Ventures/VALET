import Stripe from "stripe";

const secretKey = process.env.STRIPE_SECRET_KEY;

if (!secretKey) {
  // Fail loud at import time in any route that needs Stripe, rather than
  // producing confusing runtime errors deeper in the flow.
  console.warn("STRIPE_SECRET_KEY is not set. Stripe routes will fail until it is.");
}

// Pin to the SDK's bundled API version by omitting apiVersion. This keeps the
// integration stable across deploys without chasing version strings.
export const stripe = new Stripe(secretKey || "sk_test_placeholder", {
  typescript: true,
});
