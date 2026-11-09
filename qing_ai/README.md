# qing_ai

Provides Qing AI agents, conversations, skills, tools, and provider-neutral assistant orchestration.

## Provider calls, limits and housekeeping

- **Daily limit** (`qing_ai.daily_request_limit`, default 100) counts real model calls per user and
  per calendar day in the user's time zone. Answers, every business-planning round and planner retries
  each count as one call; failed calls count too. Administrators can review calls and exact provider
  token counters under *Qing AI → Daily Usage*.
- **Maximum output tokens** (`qing_ai.max_output_tokens`, default 4096, 256–8192) bounds one answer.
  Answers that stop at the limit are marked as cut off; an empty answer caused by the limit (common with
  reasoning models) is reported as such instead of "no text".
- **Provider errors** are mapped to actionable messages (key, model/endpoint, rate limit or quota, timeout)
  with the provider's short error code when available. Connection failures and HTTP 429/5xx are retried at
  most twice with a short back-off (honouring `Retry-After`); read timeouts and quota exhaustion are never
  retried. Logs record status, error code and request id, never the response body or prompt.
- **Housekeeping** runs in Odoo's daily auto-vacuum: unconfirmed previews (core and sales) are deleted one
  day after they expire, and abandoned planner runs one day after their deadline. Confirmed operations are kept.
