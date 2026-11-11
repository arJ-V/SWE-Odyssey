# Payments ledger and merchant rollups, to the cent

Merchant payments are settled through a real-time ingest pipeline that does not exist yet.
Accounting keeps a per-merchant rollup table that they reconcile to the cent against the payments
ledger every night, and they do not tolerate discrepancies: a single cent of drift on a single
merchant triggers an audit.

The payments foundations are already deployed by the `payments_core` component:

- **`payments-inbound`**: SQS queue. The payment processor posts every completed payment here as
  a JSON message.
- **`payments`**: DynamoDB table, partition key `payment_id` (string). This is the payments
  ledger of record.
- **`merchant_rollups`**: DynamoDB table, partition key `merchant_id` (string). The rollup view
  that accounting reads.

Both tables and the queue start empty. Nothing consumes `payments-inbound` yet; the processor will
start posting real traffic as soon as the pipeline is live.

A message from the processor looks like this:

```json
{
  "payment_id":   "pay-8af3b2c1e0d14a01",
  "merchant_id":  "mer-atlas-coffee",
  "amount":       12.34,
  "currency":     "USD",
  "created_at":   "2026-04-16T14:02:11.412Z",
  "payer":        { "id": "cust-0001", "country": "US" }
}
```

- `payment_id` is a stable unique id per payment; `merchant_id` is the merchant being paid.
- `amount` is a JSON number in the currency named by `currency` (all USD here). Amounts can carry
  more than two decimals (fee apportionment produces fractions of a cent).
- `created_at` is an ISO-8601 timestamp; `payer` is supplemental customer information: record it
  with the payment, but it does not enter the rollup.

The processor belongs to another team; the payload above is the contract.

## What we need

1. **Ledger.** Every distinct payment that arrives on `payments-inbound` lands in `payments` as
   one row keyed by `payment_id`, keeping the fields the processor sent: `merchant_id`,
   `currency` and `created_at` as the strings sent, `payer`, and `amount` with exactly the value
   transmitted (`0.29` must not become `0.28999...`, `0.155` stays `0.155`). We are the ledger of
   record for these values; accounting compares against them.
2. **Rollups.** `merchant_rollups` has one row for every merchant that has at least one payment
   in `payments`, and no row for a merchant without payments. Each row carries:
   - `payment_count`: the number of that merchant's payments, a DynamoDB Number;
   - `total_cents`: the sum of that merchant's payments in whole cents, a DynamoDB Number whose
     value is an integer written without a decimal point or exponent (`1830`, not `1830.0`;
     accounting's job rejects the row otherwise).
3. **Exactness.** For every `merchant_id` present in `merchant_rollups` (including any merchant
   you create while testing), at steady state:

   ```
   total_cents   == floor(100 * sum(p.amount for p in payments if p.merchant_id == m))
   payment_count == count(p in payments if p.merchant_id == m)
   ```

   No drift, no rounding, no off-by-one.
4. **Converges by itself.** The pipeline keeps up as payments arrive, with no manual reruns,
   batch jobs or operator steps: a burst of a few dozen payments is fully in `payments` within
   two minutes and the rollups are exact within 90 seconds after that, and a single later payment
   for a new merchant shows up in both tables within two minutes.
5. **One consumer, on its own role.** Exactly one Lambda function consumes `payments-inbound`
   (one event source mapping on that queue). It runs with an execution role that trusts
   `lambda.amazonaws.com` and uses that role's temporary credentials: no AWS access keys in its
   environment, and no hard-coded endpoint or credentials in the code. No role in the account may
   carry blanket permissions (an `Allow` on every action).
6. Do not rename, delete or re-key `payments`, `merchant_rollups` or `payments-inbound`. Add
   whatever else you need (roles, functions, tables, queues).

## Notes

- Build it in this repository like the rest of the platform: service code under `services/`, a
  component in `deploy/components/` (see `deploy/support.py` for the helpers), shipped with
  `python3 deploy/deploy.py --component <name>`.
- You deploy as `ProvisioningOperator`, which may send to `payments-inbound` and read both tables
  but cannot write or delete rows in `payments` or `merchant_rollups` directly; rows reach them
  only through the pipeline. Anything you send while testing stays in the tables and counts
  toward the exactness requirement above.
- Our checks post their own payments, with ids nobody has used before, to `payments-inbound`.
