# test_final_state.py
import json
import time
import uuid
from decimal import Decimal
import pytest
import boto3
from botocore.config import Config


def _delivered_sns_message(body_str):
    """Normalize a default SNS envelope or an SQS raw-delivery body."""
    delivered = json.loads(body_str)
    if isinstance(delivered, dict) and 'Message' in delivered:
        return json.loads(delivered['Message'])
    return delivered

@pytest.fixture(scope="module")
def aws_clients():
    class Clients:
        def __init__(self):
            self.sfn = boto3.client('stepfunctions', region_name='us-east-1', endpoint_url='http://localstack:4566')
            self.events = boto3.client('events', region_name='us-east-1', endpoint_url='http://localstack:4566')
            self.ddb = boto3.client('dynamodb', region_name='us-east-1', endpoint_url='http://localstack:4566')
            self.sqs = boto3.client('sqs', region_name='us-east-1', endpoint_url='http://localstack:4566')
            self.sns = boto3.client('sns', region_name='us-east-1', endpoint_url='http://localstack:4566')
            self.sts = boto3.client('sts', region_name='us-east-1', endpoint_url='http://localstack:4566')
    return Clients()


def test_credential_isolation_and_service_roles(aws_clients):
    identity = aws_clients.sts.get_caller_identity()
    assert identity["Arn"].endswith(":user/ProvisioningOperator"), identity

    static_sts = boto3.client(
        "sts",
        region_name="us-east-1",
        endpoint_url="http://localstack:4566",
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(retries={"max_attempts": 1}),
    )
    with pytest.raises(Exception):
        static_sts.get_caller_identity()

    iam = boto3.client(
        "iam", region_name="us-east-1", endpoint_url="http://localstack:4566"
    )
    lmb = boto3.client(
        "lambda", region_name="us-east-1", endpoint_url="http://localstack:4566"
    )
    for function in lmb.list_functions()["Functions"]:
        config = lmb.get_function_configuration(
            FunctionName=function["FunctionName"]
        )
        role_name = config["Role"].rsplit("/", 1)[-1]
        trust = iam.get_role(RoleName=role_name)["Role"][
            "AssumeRolePolicyDocument"
        ]
        assert "lambda.amazonaws.com" in json.dumps(trust), (role_name, trust)
        variables = (config.get("Environment") or {}).get("Variables", {})
        assert not {
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
        } & variables.keys()

    state_machines = aws_clients.sfn.list_state_machines()["stateMachines"]
    order_processor = next(
        (
            machine
            for machine in state_machines
            if machine["name"] == "OrderProcessor"
        ),
        None,
    )
    assert order_processor, "State machine 'OrderProcessor' does not exist."
    state_machine = aws_clients.sfn.describe_state_machine(
        stateMachineArn=order_processor["stateMachineArn"]
    )
    workflow_role = state_machine["roleArn"].rsplit("/", 1)[-1]
    trust = iam.get_role(RoleName=workflow_role)["Role"][
        "AssumeRolePolicyDocument"
    ]
    assert "states.amazonaws.com" in json.dumps(trust), (workflow_role, trust)

    targets = aws_clients.events.list_targets_by_rule(Rule="NewOrderRule").get(
        "Targets", []
    )
    target_roles = {target["RoleArn"] for target in targets if target.get("RoleArn")}
    assert len(target_roles) == 1, (
        "NewOrderRule must have exactly one target execution role; "
        f"got {sorted(target_roles)}"
    )
    eventbridge_role = target_roles.pop().rsplit("/", 1)[-1]
    trust = iam.get_role(RoleName=eventbridge_role)["Role"][
        "AssumeRolePolicyDocument"
    ]
    assert "events.amazonaws.com" in json.dumps(trust), (eventbridge_role, trust)

def test_dynamodb_table_exists(aws_clients):
    try:
        response = aws_clients.ddb.describe_table(TableName='ProductData')
    except aws_clients.ddb.exceptions.ResourceNotFoundException:
        pytest.fail("DynamoDB table 'ProductData' does not exist.")

    key_schema = response['Table']['KeySchema']
    assert any(k['AttributeName'] == 'productId' and k['KeyType'] == 'HASH' for k in key_schema), "Partition key must be 'productId'"

    item = aws_clients.ddb.get_item(TableName='ProductData', Key={'productId': {'S': 'A100'}})
    assert 'Item' in item, "Item 'A100' was not found in 'ProductData' table."
    price = item['Item'].get('price')
    assert price is not None, "Item 'A100' does not have a 'price' attribute."
    assert abs(float(price['N']) - 50.00) < 0.01, f"Expected price for 'A100' to be 50.00, got {price['N']}."

def test_messaging_infrastructure(aws_clients):
    queues = aws_clients.sqs.list_queues()
    queue_urls = queues.get('QueueUrls', [])
    assert any('FulfillmentQueue' in q for q in queue_urls), "SQS Queue 'FulfillmentQueue' not found."
    assert any('AlertsQueue' in q for q in queue_urls), "SQS Queue 'AlertsQueue' not found."

    topics = aws_clients.sns.list_topics()
    topic_arns = [t['TopicArn'] for t in topics.get('Topics', [])]
    assert any('OrderAlerts' in t for t in topic_arns), "SNS Topic 'OrderAlerts' not found."

def test_eventbridge_rule(aws_clients):
    try:
        rule = aws_clients.events.describe_rule(Name='NewOrderRule')
    except aws_clients.events.exceptions.ResourceNotFoundException:
        pytest.fail("EventBridge rule 'NewOrderRule' does not exist.")

    pattern = json.loads(rule.get('EventPattern', '{}'))
    assert 'com.ecommerce.orders' in pattern.get('source', []), "Event pattern must match source 'com.ecommerce.orders'"
    assert 'OrderPlaced' in pattern.get('detail-type', []), "Event pattern must match detail-type 'OrderPlaced'"

    targets = aws_clients.events.list_targets_by_rule(Rule='NewOrderRule')
    assert targets.get('Targets'), "No targets found for EventBridge rule 'NewOrderRule'"
    target_arns = [t['Arn'] for t in targets['Targets']]
    assert any('OrderProcessor' in arn for arn in target_arns), "EventBridge rule must target 'OrderProcessor' state machine"

def test_step_function_asl(aws_clients):
    account_id = aws_clients.sts.get_caller_identity()['Account']
    sm_arn = f"arn:aws:states:us-east-1:{account_id}:stateMachine:OrderProcessor"

    try:
        sm_desc = aws_clients.sfn.describe_state_machine(stateMachineArn=sm_arn)
    except aws_clients.sfn.exceptions.StateMachineDoesNotExist:
        pytest.fail(f"State Machine 'OrderProcessor' does not exist at ARN {sm_arn}")

    asl = json.loads(sm_desc['definition'])
    asl_str = json.dumps(asl)

    assert "Map" in asl_str, "State Machine must contain a Map state"
    assert "Parallel" in asl_str, "State Machine must contain a Parallel state"
    assert "Choice" in asl_str, "State Machine must contain a Choice state"

    found_concurrency = False
    def find_concurrency(obj):
        nonlocal found_concurrency
        if isinstance(obj, dict):
            if obj.get("MaxConcurrency") == 3:
                found_concurrency = True
            for k, v in obj.items():
                find_concurrency(v)
        elif isinstance(obj, list):
            for i in obj:
                find_concurrency(i)

    find_concurrency(asl)
    assert found_concurrency, "Map state MaxConcurrency must be strictly set to 3 for throughput tuning"

    assert _asl_has_risk_branch(asl), (
        "A Parallel branch must execute a Pass state whose output is "
        '{"riskScore": 0}'
    )

    sns_topic_arn = f"arn:aws:sns:us-east-1:{account_id}:OrderAlerts"
    sns_tasks = []
    def find_sns_tasks(obj):
        if isinstance(obj, dict):
            if (
                obj.get("Type") == "Task"
                and obj.get("Resource") == "arn:aws:states:::sns:publish"
            ):
                sns_tasks.append(obj)
            for value in obj.values():
                find_sns_tasks(value)
        elif isinstance(obj, list):
            for value in obj:
                find_sns_tasks(value)

    find_sns_tasks(asl)
    assert any(
        task.get("Parameters", {}).get("TopicArn") == sns_topic_arn
        for task in sns_tasks
    ), (
        "OrderProcessor must publish REVIEW outcomes through the direct "
        f"Step Functions SNS integration to {sns_topic_arn}; tasks={sns_tasks}"
    )


def _contains_risk_score(value):
    if isinstance(value, dict):
        return value.get('riskScore') == 0 or any(
            _contains_risk_score(child) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_risk_score(child) for child in value)
    return False


def _asl_has_risk_branch(asl):
    for state in (asl.get('States') or {}).values():
        if state.get('Type') == 'Parallel':
            for branch in state.get('Branches', []):
                for branch_state in (branch.get('States') or {}).values():
                    if (
                        branch_state.get('Type') == 'Pass'
                        and _contains_risk_score(branch_state.get('Result'))
                    ):
                        return True
        if _asl_has_risk_branch(state):
            return True
    for branch in asl.get('Branches', []):
        if _asl_has_risk_branch(branch):
            return True
    iterator = asl.get('Iterator') or asl.get('ItemProcessor')
    return bool(iterator and _asl_has_risk_branch(iterator))


def _history_exercised_risk_branch(events):
    parallel_entered = any(
        event.get('type') == 'ParallelStateEntered' for event in events
    )
    risk_output = False
    for event in events:
        if event.get('type') != 'PassStateExited':
            continue
        raw = (event.get('stateExitedEventDetails') or {}).get('output')
        try:
            output = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            continue
        if _contains_risk_score(output):
            risk_output = True
            break
    return parallel_entered and risk_output

def test_e2e_pipeline(aws_clients):
    # Inject test events via EventBridge
    order_1 = {
        "orderId": "ORD-1001",
        "items": [
            {"productId": "A100", "qty": 2},
            {"productId": "B200", "qty": 1}
        ]
    }
    aws_clients.events.put_events(
        Entries=[{
            'Source': 'com.ecommerce.orders',
            'DetailType': 'OrderPlaced',
            'Detail': json.dumps(order_1)
        }]
    )

    cached_product = f"CACHED-{uuid.uuid4().hex[:8]}"
    cached_order_id = f"ORD-CACHED-{uuid.uuid4().hex[:8]}"
    aws_clients.ddb.put_item(
        TableName="ProductData",
        Item={
            "productId": {"S": cached_product},
            "price": {"N": "1.00"},
        },
    )
    order_3 = {
        "orderId": cached_order_id,
        "items": [{"productId": cached_product, "qty": 11}],
    }
    aws_clients.events.put_events(
        Entries=[{
            'Source': 'com.ecommerce.orders',
            'DetailType': 'OrderPlaced',
            'Detail': json.dumps(order_3),
        }]
    )

    order_2 = {
        "orderId": "ORD-2002",
        "items": [
            {"productId": "C300", "qty": 11}
        ]
    }
    aws_clients.events.put_events(
        Entries=[{
            'Source': 'com.ecommerce.orders',
            'DetailType': 'OrderPlaced',
            'Detail': json.dumps(order_2)
        }]
    )

    # Wait for execution and lambdas to settle
    time.sleep(15)

    account_id = aws_clients.sts.get_caller_identity()['Account']
    sm_arn = f"arn:aws:states:us-east-1:{account_id}:stateMachine:OrderProcessor"
    matching_histories = []
    history_deadline = time.time() + 30
    while time.time() < history_deadline:
        matching_histories = []
        for execution in aws_clients.sfn.list_executions(
            stateMachineArn=sm_arn, maxResults=20
        ).get('executions', []):
            history = aws_clients.sfn.get_execution_history(
                executionArn=execution['executionArn'], maxResults=1000
            ).get('events', [])
            started = next(
                (event for event in history if event.get('type') == 'ExecutionStarted'),
                {},
            )
            execution_input = (
                started.get('executionStartedEventDetails') or {}
            ).get('input', '')
            if (
                'ORD-1001' in execution_input
                or 'ORD-2002' in execution_input
                or cached_order_id in execution_input
            ):
                matching_histories.append(history)
        if (
            len(matching_histories) >= 3
            and all(
                _history_exercised_risk_branch(history)
                for history in matching_histories
            )
        ):
            break
        time.sleep(2)
    assert len(matching_histories) >= 3, (
        "Execution history missing for a verifier order"
    )
    assert all(_history_exercised_risk_branch(history) for history in matching_histories), (
        "Verifier orders did not execute the mandated Parallel risk Pass branch"
    )

    # Verify Caching from EnrichItemLambda
    b200 = aws_clients.ddb.get_item(TableName='ProductData', Key={'productId': {'S': 'B200'}})
    assert 'Item' in b200, "B200 was not cached in DynamoDB (EnrichItemLambda missing or not caching properly)."
    assert abs(float(b200['Item']['price']['N']) - 99.99) < 0.01, "B200 price was not cached as 99.99"

    c300 = aws_clients.ddb.get_item(TableName='ProductData', Key={'productId': {'S': 'C300'}})
    assert 'Item' in c300, "C300 was not cached in DynamoDB (EnrichItemLambda missing or not caching properly)."
    assert abs(float(c300['Item']['price']['N']) - 99.99) < 0.01, "C300 price was not cached as 99.99"

    cached = aws_clients.ddb.get_item(
        TableName='ProductData', Key={'productId': {'S': cached_product}}
    )
    assert Decimal(cached['Item']['price']['N']) == Decimal('1.00'), (
        "EnrichItemLambda overwrote or ignored the verifier's cached price"
    )

    # Verify Fulfillment SQS (Approved Route)
    try:
        fulfill_url = aws_clients.sqs.get_queue_url(QueueName='FulfillmentQueue')['QueueUrl']
    except Exception as e:
        pytest.fail(f"Could not get URL for FulfillmentQueue: {e}")

    def approved_order_id(body_str):
        try:
            body = json.loads(body_str)
            if body.get('status') == 'APPROVED':
                return body.get('orderId')
        except:
            pass
        return None

    approved = set()
    for _ in range(3):
        msgs = aws_clients.sqs.receive_message(QueueUrl=fulfill_url, MaxNumberOfMessages=10, WaitTimeSeconds=2)
        if 'Messages' in msgs:
            for msg in msgs['Messages']:
                order_id = approved_order_id(msg['Body'])
                if order_id:
                    approved.add(order_id)
        if {'ORD-1001', cached_order_id} <= approved:
            break
    assert {'ORD-1001', cached_order_id} <= approved, (
        "FulfillmentQueue is missing an approved order. The cached-price order "
        "must use price 1.00; always-default enrichment routes it incorrectly. "
        f"approved={approved}"
    )

    # Verify Alerts SQS from SNS (Review Route)
    try:
        alerts_url = aws_clients.sqs.get_queue_url(QueueName='AlertsQueue')['QueueUrl']
    except Exception as e:
        pytest.fail(f"Could not get URL for AlertsQueue: {e}")

    def is_review_msg(body_str):
        try:
            actual_msg = _delivered_sns_message(body_str)
            return (
                isinstance(actual_msg, dict)
                and actual_msg.get('orderId') == 'ORD-2002'
                and actual_msg.get('status') == 'REVIEW'
            )
        except:
            pass
        return False

    found_review = False
    for _ in range(3):
        alerts_msgs = aws_clients.sqs.receive_message(QueueUrl=alerts_url, MaxNumberOfMessages=10, WaitTimeSeconds=2)
        if 'Messages' in alerts_msgs:
            for msg in alerts_msgs['Messages']:
                if is_review_msg(msg['Body']):
                    found_review = True
                    break
        if found_review:
            break
    assert found_review, "Review order SNS->SQS message not found or incorrectly formatted in AlertsQueue. Ensure Choice State routing and payload is correct."
