# Merchant document index on CloudFormation (`pipeline-stack`)

Merchants upload their documents (invoices, settlement statements, onboarding files) to an S3
bucket, and support needs to look any of them up by its key: which bucket it is in, how large it
is and when it arrived. We want a small index for that: every object created in the bucket is
recorded in a DynamoDB table by a Lambda function. Compliance requires the index table to be
encrypted with a key we own, and the platform team wants this piece managed as one CloudFormation
stack so it can be reviewed, updated and torn down as a unit.

## What we need

- A CloudFormation stack named **`pipeline-stack`**, in `CREATE_COMPLETE` or `UPDATE_COMPLETE`,
  whose resources include the S3 bucket `pipeline-source`, the DynamoDB table `pipeline-events`
  and the Lambda function `pipeline-processor`. Creating these three imperatively is not enough;
  the stack must own them. You may split the work across more stacks or add custom resources if
  you need to, and the function code can be inline (`Code.ZipFile`) or come from S3.
- **`pipeline-source`**: the S3 bucket merchants upload to.
- **`pipeline-events`**: a DynamoDB table with a single string partition key `object_key`.
- **`pipeline-processor`**: a Lambda function on runtime `python3.11`, invoked whenever an object
  is created in `pipeline-source`: the bucket's notification configuration sends `s3:ObjectCreated`
  events to this function, and the function's resource policy allows `s3.amazonaws.com` to
  invoke it (`lambda:InvokeFunction`) for events from `pipeline-source`. It reaches AWS through
  `http://localstack:4566` (set `AWS_ENDPOINT_URL` in its environment, like the platform's other
  functions); no AWS keys in its environment.
- For each new object the function writes one item to `pipeline-events`:
  - `object_key` (S): the object's key, decoded. S3 event notifications URL-encode the key
    (a space arrives as `+`); decode it exactly once, so that an upload named
    `docs/red flower.bin` is stored under exactly `docs/red flower.bin`.
  - `bucket` (S): the bucket name.
  - `size` (N): the object's size in bytes.
  - `event_time` (S): the event's timestamp, never empty.
- Any fresh upload, including a key never seen before, has its item within a minute.
- **Encryption**: `pipeline-events` is encrypted at rest with a customer-managed KMS key that the
  `pipeline-stack` stack itself creates (not the AWS-managed `alias/aws/dynamodb` key).
- **The function's execution role** (all of its inline and attached policies taken together):
  - DynamoDB: exactly the `dynamodb:PutItem` capability on the exact ARN of `pipeline-events`;
    no other DynamoDB actions, no `Resource: "*"`, no other table.
  - KMS: `kms:Decrypt` and `kms:GenerateDataKey` on the exact ARN of the stack's key. The rest of
    the `kms:GenerateDataKey*` family and the read-only `kms:DescribeKey` are acceptable too;
    nothing else in KMS, no `Resource: "*"`, no other key. Without these the function's writes to
    the encrypted table fail in production, even with `dynamodb:PutItem` granted.
  - Action names are case-insensitive and IAM wildcard spellings such as
    `kms:GenerateDataKey*` are fine; how the statements are split or combined does not matter,
    and unrelated permissions (for example for logs) may sit beside them.
- No role in the account may keep a policy that allows every action on every resource.

## How to ship it

You deploy as `ProvisioningOperator`. Like the rest of the platform, this has to be reproducible
from the repository: keep the template in the repository and add a component under
`deploy/components/` that creates or updates the stack, then run
`python3 deploy/deploy.py --component <name>`. The production data path is protected: you cannot
write to `pipeline-events` or invoke `pipeline-processor` directly, so check the pipeline by
uploading to `pipeline-source`. Leave the existing components and their resources as they are.
