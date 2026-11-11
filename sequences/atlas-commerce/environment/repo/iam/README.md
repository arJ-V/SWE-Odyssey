# IAM policy documents

Inline policies for the platform's execution roles live here as `<role-name>.json` (a standard IAM
policy document). A component that owns the role applies the file when it deploys, if the file
exists: for example `deploy/components/orders_core.py` applies `iam/orders-consumer-role.json` to
`orders-consumer-role` as the inline policy `OrdersConsumerPolicy`. Roles without a file here keep
only the managed policies their component attaches.
