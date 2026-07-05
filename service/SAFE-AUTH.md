# Filming behind a login wall — the safe pattern (L5)

Many demos live behind the customer's own auth. The unsafe way is to have our tool hold the
customer's real password and log in for them. **We never do that.** This is the policy +
mechanism so the hosted service can film authenticated flows without ever holding a real
credential.

## The rule
The service **never stores, sees, or types a customer's real production password.** Full stop.
This is a security promise we make in the ToS and enforce in code.

## Three supported ways to film behind auth (customer picks)
1. **Demo / test account (default, safest).** The customer makes a throwaway account on their
   own app with dummy data and gives Captur'd *those* credentials. Nothing real is exposed.
   This is what we recommend and what the templates assume (`login-flow` uses fixture creds).
2. **Bring-your-own session.** The customer logs in themselves and hands the service a
   short-lived **session cookie / token** (not a password). The render worker injects it into
   the browser context for that one job, uses it, and **discards it** — never persisted to disk
   or logs. Scoped, expiring, revocable by the customer.
3. **Customer-driven capture.** For the most sensitive apps, the customer runs the *local*
   (free/OSS) engine on their own machine against their own logged-in browser — nothing leaves
   their box. The hosted service only does the enrichment/branding on the resulting file.

## What the code enforces
- Session tokens live **in memory for the duration of one job only**; never written to disk,
  never in logs, never in the stored artifact's metadata.
- The typed-value capture (paid_boot) and any credential field are **redaction-aware** — a
  password field is never rendered as readable text in the export (already true: the
  `login-flow` template treats a legible secret as burned footage).
- No credential is ever echoed back to the caller or included in a job result.

## The one owner decision (2-minute check, not a build blocker)
Confirm we ship with **option 1 as the default and option 2 opt-in**, and that the ToS states
the no-real-password promise. That's the whole policy call — everything else above is
mechanism I can build without it.
