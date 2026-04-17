#!/usr/bin/env python3
"""CDK app entry point — instantiates all VSWE infrastructure stacks."""

import os

import aws_cdk as cdk

from stacks.vpc_stack import VpcStack
from stacks.storage_stack import StorageStack
from stacks.ecs_stack import EcsStack
from stacks.lambda_stack import LambdaStack
from stacks.cdn_stack import CdnStack

app = cdk.App()

env = cdk.Environment(
    account=app.node.try_get_context("account") or os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=app.node.try_get_context("region") or os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)

# ---------------------------------------------------------------------------
# 1. Networking
# ---------------------------------------------------------------------------

vpc_stack = VpcStack(app, "VsweVpc", env=env)

# ---------------------------------------------------------------------------
# 2. Storage (DynamoDB, EFS, S3)
# ---------------------------------------------------------------------------

storage_stack = StorageStack(
    app,
    "VsweStorage",
    vpc=vpc_stack.vpc,
    efs_security_group=vpc_stack.efs_security_group,
    env=env,
)

# ---------------------------------------------------------------------------
# 3. Lambda + API Gateway (webhook receiver + SQS queue)
# ---------------------------------------------------------------------------

lambda_stack = LambdaStack(
    app,
    "VsweLambda",
    dynamo_tables=storage_stack.all_tables,
    env=env,
)

# ---------------------------------------------------------------------------
# 4. ECS Fargate (API server + job tasks)
# ---------------------------------------------------------------------------

ecs_stack = EcsStack(
    app,
    "VsweEcs",
    vpc=vpc_stack.vpc,
    file_system=storage_stack.file_system,
    efs_access_point=storage_stack.efs_access_point,
    dynamo_tables=storage_stack.all_tables,
    artifacts_bucket=storage_stack.artifacts_bucket,
    sqs_queue_url=lambda_stack.queue.queue_url,
    env=env,
)

# ---------------------------------------------------------------------------
# 5. CloudFront + S3 (frontend CDN)
#    Proxies /api/* and /ws/* to the ALB so everything is HTTPS
# ---------------------------------------------------------------------------

cdn_stack = CdnStack(
    app,
    "VsweCdn",
    alb=ecs_stack.alb,
    env=env,
)

# ---------------------------------------------------------------------------
# Synthesize
# ---------------------------------------------------------------------------

app.synth()
