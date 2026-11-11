# atlas-commerce

Order intake, fulfilment and merchant payments for the atlas-commerce platform. Everything runs in
one AWS account (`us-east-1`); this repository holds the service code and the scripts that deploy it.

## Layout

| path | what it is |
|---|---|
| `services/orders_consumer/` | `orders-consumer` Lambda (Python 3.11): reads `orders.fifo`, writes the `orders` table |
| `services/fulfillment/` | the bulk fulfilment Lambdas (Python 3.9): `ingestion.py`, `splitter.py`, `processor.py` |
| `deploy/deploy.py` | deploys components from this repository |
| `deploy/components/` | one module per component; each creates what is missing and updates what exists |
| `deploy/support.py` | shared helpers (clients, Lambda packaging, create-or-update calls) |
| `iam/` | IAM policy documents applied by the components (see `iam/README.md`) |

## Components

| component | resources |
|---|---|
| `orders_core` | FIFO queue `orders.fifo` (redrive to `orders-dlq.fifo` after 3 receives), DynamoDB table `orders` (key `order_id`), role `orders-consumer-role`, Lambda `orders-consumer`, event source mapping `orders.fifo` -> `orders-consumer` |
| `fulfillment` | REST API `OrdersAPI` (stage `prod`, `POST /orders`) -> `IngestionFunction` -> S3 bucket `raw-orders-bucket` -> queue `raw-orders-queue` (dead letters to `raw-orders-dlq`) -> `SplitterFunction` -> `fulfillment-queue.fifo` -> `ProcessorFunction` -> table `FulfillmentTable`; KMS aliases `alias/order-pipeline-key` and `alias/order-archive-key`; one execution role per function |
| `payments_core` | queue `payments-inbound`, tables `payments` (key `payment_id`) and `merchant_rollups` (key `merchant_id`) |

## Deploying

```sh
python3 deploy/deploy.py --list                    # components, in deploy order
python3 deploy/deploy.py --component orders_core   # one component
python3 deploy/deploy.py --all                     # everything
```

Lambda code is packaged from the repository by `deploy/support.py` (`package_dir` / `package_files`),
so what runs is exactly what is committed here. Deploy through `deploy/deploy.py`, not by uploading
code by hand.

## Workstation

The AWS endpoint, region and credentials are already configured (`AWS_ENDPOINT_URL`,
`AWS_DEFAULT_REGION`, `AWS_SHARED_CREDENTIALS_FILE`); `aws`, `awslocal`, Python 3 with boto3, `jq`,
`zip` and `curl` are installed. You deploy as the `ProvisioningOperator` user. Production data
paths are protected: that user cannot write the production tables directly, send to
`orders.fifo` or invoke the consumers by hand; data reaches them only through the pipelines.
