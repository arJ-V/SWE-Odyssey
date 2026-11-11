# Public order intake: an `orders-api` that writes straight to `orders.fifo`

Merchants' storefronts need one HTTP endpoint to submit orders to. Today orders only reach
`orders.fifo` from internal producers; the consumer behind it (`orders-consumer`, which stores
each order in the `orders` table) is already running. We want a public REST endpoint in front of
the queue, without a Lambda in between: API Gateway should call SQS itself, so an order is
accepted only once it is in the queue and there is no extra function to run, scale or pay for.

## What we need

- A REST API named `orders-api` with a `POST /orders` method, deployed to a stage named `dev`.
  Clients call it without authorization, with a JSON body such as
  `{"order_id": "o-123", "merchant_id": "m-9", "amount": 199}`. On this platform the stage is
  reached at `$AWS_ENDPOINT_URL/restapis/<api-id>/dev/_user_request_/orders`.
- The method uses a **direct AWS service integration to SQS** (integration type `AWS`, not
  `AWS_PROXY` and not a Lambda), whose URI targets the FIFO queue `orders.fifo` by its queue path
  (the URI ends in `/orders.fifo`).
- API Gateway calls SQS with an IAM execution role of your choice, set as the integration's
  credentials (the role's ARN). That role trusts `apigateway.amazonaws.com` (`sts:AssumeRole`) and
  is allowed `sqs:SendMessage` on the `orders.fifo` queue ARN. Keep it scoped to that queue: none of
  its SQS permissions may be granted on every resource (`"Resource": "*"`).
- The integration makes a form-encoded SQS `SendMessage` call:
  - it overrides the integration request's `Content-Type` header with the literal
    `'application/x-www-form-urlencoded'` (a request parameter mapping);
  - its request template (for `application/json` requests) contains `Action=SendMessage`, and the
    message body is the **whole** request body, URL-encoded: `MessageBody=$util.urlEncode(...)` of
    `$input.body`, `$input.json('$')` or `$util.toJson($input.path('$'))` (not a single field);
  - it sets `MessageGroupId=...` and `MessageDeduplicationId=...` explicitly, each taken from
    the request body with a template expression, not a fixed value. `orders.fifo` has
    content-based deduplication turned off and must keep it off, so a send without a
    deduplication id is rejected.
- The live `dev` stage serves this final configuration. Our checks call the deployed stage, not
  the API's draft configuration.

## Behaviour we will check against `dev`

- A valid `POST /orders` returns HTTP 200, and within about 30 seconds the `orders` table has
  exactly that order: `order_id`, `merchant_id` and `amount` as posted (an amount of 199 is
  stored as 199). Nothing lands in `orders-dlq.fifo`.
- Storefronts retry a POST when the response is slow. Posting the same body again (within a few
  minutes) returns 200 and leaves the stored row exactly as it was (its `created_at` stays the
  first one), and nothing lands in the DLQ: a retry must not be delivered to the consumer as a
  second order.
- Two different orders for one merchant, posted one after the other, are stored in that order:
  the first has a `created_at` no later than the second's.

## Constraints

- Leave `orders.fifo`, `orders-dlq.fifo` (and the redrive between them), the `orders` table,
  `orders-consumer`, its event source mapping and its role as they are. Production data paths stay
  protected: your `ProvisioningOperator` user cannot send to `orders.fifo` directly, and that is
  intended.
- No IAM role in the account may be left with a policy that allows every action on every
  resource.
- Put the API in the repository like the rest of the platform: a component under
  `deploy/components/` that `python3 deploy/deploy.py --all` brings up (see `README.md`), then
  deploy it with `deploy/deploy.py`.
