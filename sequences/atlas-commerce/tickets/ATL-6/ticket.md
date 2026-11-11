# Order workflow: run every placed order through `OrderProcessor`

The storefront already publishes an `OrderPlaced` event to the account's default EventBridge bus
for every checkout (source `com.ecommerce.orders`, detail type `OrderPlaced`). Nothing consumes it
yet. We want an order workflow behind it that prices the items, takes the payment decision and a
risk check side by side, and hands the order to fulfilment or to the review team. The event's
`detail` looks like this:

```json
{"orderId": "ORD-123", "items": [{"productId": "A100", "qty": 2}, {"productId": "X999", "qty": 1}]}
```

## What we need

**Product price cache.** A DynamoDB table `ProductData` with the string partition key `productId`.
It starts with one product, `A100`, whose `price` is the number 50.00. The catalogue team also loads
prices into this table directly; a price that is already there is the price to use and must never
be overwritten.

**Messaging.** An SQS queue `FulfillmentQueue` for approved orders, an SNS topic `OrderAlerts` for
orders that need review, and an SQS queue `AlertsQueue` subscribed to `OrderAlerts`. The alerting
consumers read `AlertsQueue` and parse the standard SNS notification: each message body is the JSON
envelope SNS produces, with `"Type": "Notification"`, the `OrderAlerts` topic ARN as `TopicArn`, a
`MessageId`, and the alert itself as a JSON string in `Message`. Keep that format.

**Two Lambda functions** (Python 3.9 or later):
- `EnrichItemLambda` takes one item (`{"productId": "B200", "qty": 2}`), looks its price up in
  `ProductData`, and returns the item with the price added (`{"productId": "B200", "qty": 2,
  "price": 99.99}`). On a cache miss it uses the default price 99.99 and stores that product with
  price 99.99 in `ProductData`.
- `ProcessPaymentLambda` takes the list of priced items and adds up `qty * price`. A total up to
  and including 1000.00 returns exactly `{"paymentStatus": "APPROVED"}`; above 1000.00 exactly
  `{"paymentStatus": "REVIEW"}`.

**The state machine `OrderProcessor`**, with an execution role that Step Functions
(`states.amazonaws.com`) can assume and that lets it invoke the functions, send to the queue and
publish to the topic. Its definition:
- It is started with the whole EventBridge event as input (the rule's target has no `InputPath`
  or input transformer) and begins by taking the event's `detail` (for example a Pass state with
  `"InputPath": "$.detail"`).
- A Map state over `items` calls `EnrichItemLambda` for each item, with `MaxConcurrency` 3.
- The priced items then go into a Parallel state with two branches: one invokes
  `ProcessPaymentLambda`; the other is a risk check, a Pass state whose result is
  `{"riskScore": 0}`. Every execution runs both branches.
- A Choice state routes on `paymentStatus`. APPROVED: send to `FulfillmentQueue` with the direct
  Step Functions SQS integration; the message body is the JSON object
  `{"orderId": "<the order's id>", "status": "APPROVED"}`. REVIEW: publish to `OrderAlerts` with
  the direct SNS integration (`arn:aws:states:::sns:publish`, with the topic's full ARN as
  `TopicArn` in the task's parameters); the message is the JSON `{"orderId": "<the order's id>",
  "status": "REVIEW"}`. The original `orderId` has to survive the Map and Parallel steps to get
  there.

**The rule `NewOrderRule`** on the default bus, matching
`{"source": ["com.ecommerce.orders"], "detail-type": ["OrderPlaced"]}`, with `OrderProcessor` as
its target and one execution role for its target(s). That role can be assumed by EventBridge
(`events.amazonaws.com`) and may only start `OrderProcessor`: it must not be able to start any
other state machine or to delete state machines.

**Speed.** An order placed on the bus should have its outcome message in `FulfillmentQueue` or in
`AlertsQueue` within about 15 seconds.

## Rules of the account

- Every Lambda function in the account runs with a role that Lambda (`lambda.amazonaws.com`) can
  assume, and no function has AWS keys (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  `AWS_SESSION_TOKEN`) in its environment. Functions reach AWS through `http://localstack:4566`,
  like the platform's other functions.
- No role may keep a policy that allows every action on every resource.

## How to ship it

The workflow becomes part of the platform: put its code and definition in this repository and add
a component under `deploy/components/` (see `deploy/deploy.py --list` and the existing components),
then deploy it with `python3 deploy/deploy.py --component <name>`. Leave the existing components
and their resources as they are.
