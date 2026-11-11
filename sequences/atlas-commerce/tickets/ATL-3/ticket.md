# Least privilege for `orders-consumer-role`

The security review of the orders pipeline flagged the consumer's execution role.
`orders-consumer-role` (the role of the `orders-consumer` Lambda, which reads `orders.fifo` through
its event source mapping and writes the `orders` table) has no permissions of its own beyond basic
Lambda logging: what it needs for the queue and the table was never written down, and the review
wants the role to carry exactly that and nothing more. Please give the role its policy.

## What the role must allow

- Consuming the queue through the event source mapping: `sqs:ReceiveMessage`,
  `sqs:DeleteMessage` and `sqs:GetQueueAttributes` on `orders.fifo`.
- Storing orders: `dynamodb:PutItem` on the `orders` table (the consumer writes rows with a
  conditional put). It may also describe the table; it needs nothing else on other tables.
- Name the queue and the table by their full ARNs in the statements:
  `arn:aws:sqs:us-east-1:000000000000:orders.fifo` and
  `arn:aws:dynamodb:us-east-1:000000000000:table/orders`. No queue or table permission may be
  granted on every resource (`"Resource": "*"`), whether in the role's own policy or in a
  managed policy attached to it.

## What it must not allow

- No `sqs:ChangeMessageVisibility` on `orders.fifo`: the mapping does not need it, and the review
  wants the consumer unable to hold messages back from redelivery. This applies to every policy on
  the role (inline or attached) and to wildcards that would include it (`sqs:*`,
  `sqs:Change*`, `*`), as well as to statements on every resource (`"Resource": "*"`).
- No other queue actions the consumer does not need. And, as everywhere in this account, no role
  may be left with a policy that allows every action on every resource.

## How to ship it

- The role keeps its trust policy (`lambda.amazonaws.com` may `sts:AssumeRole`).
- Role policies in this repository live in `iam/<role-name>.json` (see `iam/README.md`). Put the
  policy in `iam/orders-consumer-role.json` and deploy it with
  `python3 deploy/deploy.py --component orders_core`. After the deploy the role must carry exactly
  one inline policy, and it must be that file's document: our checks compare the live role with
  the repository, so a policy applied by hand and not committed (or committed and not deployed)
  will be flagged.
- Leave the consumer itself as it is: `orders-consumer` keeps its Python 3.11 runtime, its handler
  `handler.lambda_handler`, an environment without AWS keys, and its SQS event source mapping on
  `orders.fifo`. Leave its code, the queues and the table unchanged in this change.
