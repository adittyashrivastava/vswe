"""Lambda stack — webhook handler, API Gateway, SQS queue, DLQ."""

from aws_cdk import (
    Duration,
    Stack,
    aws_apigatewayv2 as apigwv2,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_sqs as sqs,
    aws_dynamodb as dynamodb,
)
from constructs import Construct


class LambdaStack(Stack):
    """Webhook receiver infrastructure.

    - Lambda function: thin handler that validates GitHub webhooks and
      enqueues them to SQS for processing by the ECS agent.
    - API Gateway HTTP API: public endpoint for GitHub webhook delivery.
    - SQS queue + DLQ: durable message buffer between webhook and agent.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        dynamo_tables: list[dynamodb.ITable],
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # =====================================================================
        # Dead Letter Queue
        # =====================================================================

        self.dlq = sqs.Queue(
            self,
            "WebhookDlq",
            queue_name="vswe-webhook-dlq",
            retention_period=Duration.days(14),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
        )

        # =====================================================================
        # Main SQS Queue
        # =====================================================================

        self.queue = sqs.Queue(
            self,
            "WebhookQueue",
            queue_name="vswe-webhook-queue",
            visibility_timeout=Duration.seconds(300),
            retention_period=Duration.days(4),
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=3,
                queue=self.dlq,
            ),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
        )

        # =====================================================================
        # Lambda Function
        # =====================================================================

        self.webhook_handler = lambda_.Function(
            self,
            "WebhookHandler",
            function_name="vswe-webhook-handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../../backend/lambda/webhook"),
            timeout=Duration.seconds(30),
            memory_size=256,
            architecture=lambda_.Architecture.ARM_64,
            environment={
                "SQS_QUEUE_URL": self.queue.queue_url,
                "AWS_REGION_NAME": self.region,
            },
            log_group=logs.LogGroup(
                self, "WebhookHandlerLogs",
                log_group_name="/vswe/lambda/webhook-handler",
                retention=logs.RetentionDays.ONE_MONTH,
            ),
            tracing=lambda_.Tracing.ACTIVE,
        )

        # Grant permissions
        self.queue.grant_send_messages(self.webhook_handler)

        # Grant read access to config table (for webhook secret verification)
        for table in dynamo_tables:
            if table.table_name == "vswe-config":
                table.grant_read_data(self.webhook_handler)
                break

        # Allow reading webhook secret from SSM
        self.webhook_handler.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter/vswe/github-webhook-secret"
                ],
            )
        )

        # =====================================================================
        # API Gateway HTTP API
        # =====================================================================

        self.http_api = apigwv2.CfnApi(
            self,
            "WebhookApi",
            name="vswe-webhook-api",
            protocol_type="HTTP",
            cors_configuration=apigwv2.CfnApi.CorsProperty(
                allow_methods=["POST"],
                allow_origins=["https://github.com"],
                max_age=86400,
            ),
        )

        # Lambda integration
        integration = apigwv2.CfnIntegration(
            self,
            "WebhookIntegration",
            api_id=self.http_api.ref,
            integration_type="AWS_PROXY",
            integration_uri=self.webhook_handler.function_arn,
            payload_format_version="2.0",
        )

        # POST /webhook route
        apigwv2.CfnRoute(
            self,
            "WebhookRoute",
            api_id=self.http_api.ref,
            route_key="POST /webhook",
            target=f"integrations/{integration.ref}",
        )

        # Default stage with auto-deploy
        apigwv2.CfnStage(
            self,
            "WebhookStage",
            api_id=self.http_api.ref,
            stage_name="$default",
            auto_deploy=True,
        )

        # Grant API Gateway permission to invoke the Lambda
        self.webhook_handler.add_permission(
            "ApiGwInvoke",
            principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
            source_arn=f"arn:aws:execute-api:{self.region}:{self.account}:{self.http_api.ref}/*",
        )
