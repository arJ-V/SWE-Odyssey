# Orders consumer: one bad message retries the batch, resubmissions overwrite orders, amounts lose cents

The orders consumer (`orders-consumer`, code in `services/orders_consumer/`) takes order messages
off `orders.fifo` and stores them in the `orders` table. Last week's incident review found three
problems with it:

1. **One bad message sends the whole batch back.** When a single message in a batch cannot be
   processed, every message in that batch is delivered again. The `orders.fifo` ->
   `orders-consumer` event source mapping is not set up for partial batch responses, and the
   handler does not answer in the format Lambda expects for them either (it returns its own
   `{"failed": [...]}` list).
2. **Resubmitted orders overwrite the original.** Clients resubmit an order when their request
   times out. The consumer then writes the row again, including a new `created_at`. We order a
   merchant's orders by `created_at`, so it has to stay the time the order was first stored.
3. **Amounts lose their cents.** An order sent with `"amount": 19.99` is stored as 19.

## What we need

- `orders-consumer` reports failures per message with Lambda's partial batch response for SQS:
  `{"batchItemFailures": [{"itemIdentifier": "<messageId>"}, ...]}`, listing exactly the messages
  it could not process (an empty list when there are none). A message it cannot process (for
  example, a body that is not valid JSON) is reported that way; the handler does not raise for
  it, and the other messages in the same batch are still stored.
- The `orders.fifo` -> `orders-consumer` event source mapping has per-record failure reporting
  turned on (`ReportBatchItemFailures`).
- The first message for an `order_id` creates its row. Any later message with the same
  `order_id` (a redelivery or a resubmission, same body) is acknowledged as processed: it is not
  reported as a failure, nothing goes to `orders-dlq.fifo`, and the stored row stays exactly as
  it was, `created_at` included.
- Rows keep today's shape: `order_id` and `merchant_id` as strings, `amount` as a number equal to
  the amount in the message (19.99 stays 19.99), and `created_at` as a number: Unix epoch seconds
  when the order was first stored. Two orders for one merchant, sent one after the other, keep
  that order in their `created_at` values.
- A valid message sent to `orders.fifo` has its row in `orders` within about 30 seconds and never
  ends up in `orders-dlq.fifo`.
- The function keeps its runtime (Python 3.11), handler name (`handler.lambda_handler`) and the
  rest of its configuration; do not put AWS keys in its environment.
- Ship it the way this repository ships code: the change lives in `services/orders_consumer/`
  (and `deploy/` if the deployment needs to change) and goes out with
  `python3 deploy/deploy.py --component orders_core`. Our checks compare the code running in
  `orders-consumer` with what the repository packages, so code uploaded by hand will be flagged.

Leave the queues, the table and the consumer's IAM role as they are; the role is being reviewed
separately.
