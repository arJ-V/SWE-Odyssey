# Bulk fulfilment is broken in production

The bulk fulfilment pipeline (component `fulfillment`, code in `services/fulfillment/`) went live
last week and has not fulfilled a single order since. Warehouse partners submit batches of orders
to `POST /orders` on the `OrdersAPI` REST API (stage `prod`); each batch is supposed to be archived
in S3 and fanned out, one order at a time, into the `FulfillmentTable` DynamoDB table (partition
key `order_id`, a string). The intended path is:

`OrdersAPI` `POST /orders` -> `IngestionFunction` -> `raw-orders-bucket` -> `raw-orders-queue`
-> `SplitterFunction` -> `fulfillment-queue.fifo` -> `ProcessorFunction` -> `FulfillmentTable`

What we know so far:

- The warehouses cannot work from `FulfillmentTable`: the rows that do show up carry little more
  than an order id, and nothing of what the partner actually ordered.
- When a partner resubmits a batch, its orders show up with inflated counts, as if they had been
  processed more than once.
- Some partners say they could not parse our API's responses during a short window when it
  partially worked.
- There are messages in `raw-orders-dlq`. Nobody knows whether they come from the current
  failures or are left over from the pre-launch load test.
- The team suspects the recent encryption rollout broke permissions somewhere, but the problem
  could be at any layer: API configuration, the Lambda code, the queues, the notifications or IAM.

## What we need

- `POST /orders` on `OrdersAPI`, stage `prod`, accepts a JSON array of order objects (each has at
  least an `order_id`) and answers HTTP 201 with `Content-Type: application/json` and the body
  `{"status": "accepted"}` exactly.
- `OrdersAPI` stays the front door of this pipeline: there is exactly one REST API named
  `OrdersAPI`, and its `POST /orders` keeps invoking `IngestionFunction` (a Lambda integration).
  Do not replace it with a direct service integration or move partners to another API; the
  ingestion function is where batches are validated and archived.
- Every accepted batch is archived in `raw-orders-bucket` as a JSON object whose `"orders"` field
  holds the orders exactly as submitted, and it reaches `FulfillmentTable` through the path above:
  object-created notifications from `raw-orders-bucket` to `raw-orders-queue`, and enabled event
  source mappings `raw-orders-queue` -> `SplitterFunction` and `fulfillment-queue.fifo` ->
  `ProcessorFunction`.
- Within 60 seconds of the `POST`, every order in the batch has its row in `FulfillmentTable`. The
  row holds the order's fields exactly as submitted, with their types (strings stay strings,
  numbers stay numbers, `true`/`false` stay booleans, lists stay lists, nested objects stay maps),
  plus `processing_count` = 1 as a number, and nothing else.
- Submitting the identical batch again (partners retry on timeouts) changes nothing: no duplicate
  rows, and no order's `processing_count` goes above 1.
- `raw-orders-queue` and `fulfillment-queue.fifo` stay encrypted with the project's dedicated key
  (`alias/order-pipeline-key`), not the AWS-managed `alias/aws/sqs` key.
- Each stage keeps its own role, scoped to its job: `IngestionFunction` and `ProcessorFunction`
  use different roles; neither the ingestion role nor the splitter role grants any DynamoDB
  action; writes to `FulfillmentTable` belong to the processor, whose role grants that write
  (`dynamodb:PutItem` or `dynamodb:UpdateItem`). No role in the account may carry blanket
  permissions (an `Allow` on every action). No AWS access keys go into any function's
  environment.
- Ship the fix the way this repository ships code: the three handlers live in
  `services/fulfillment/` (`ingestion.py`, `splitter.py`, `processor.py`), each function is
  packaged from its own module there by `deploy/components/fulfillment.py`, and the change goes
  out with `python3 deploy/deploy.py --component fulfillment`. We compare the code running in
  each of the three functions with what that command packages from the repository, so code
  uploaded by hand will be flagged.

You deploy as `ProvisioningOperator`, which cannot write `FulfillmentTable` directly; orders reach
it only through the pipeline. Our checks submit new batches with order ids nobody has used before.
