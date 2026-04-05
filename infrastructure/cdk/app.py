#!/usr/bin/env python3
"""CDK app entry point — instantiates all VSWE infrastructure stacks."""

import aws_cdk as cdk

from stacks.vpc_stack import VpcStack
from stacks.storage_stack import StorageStack
from stacks.ecs_stack import EcsStack
from stacks.batch_stack import BatchStack
from stacks.lambda_stack import LambdaStack
from stacks.cdn_stack import CdnStack

app = cdk.App()

env = cdk.Environment(
    account=app.node.try_get_context("account") or None,
    region=app.node.try_get_context("region") or "us-east-1",
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
# 3. ECS Fargate (API server + agent tasks)
# ---------------------------------------------------------------------------

ecs_stack = EcsStack(
    app,
    "VsweEcs",
    vpc=vpc_stack.vpc,
    file_system=storage_stack.file_system,
    efs_access_point=storage_stack.efs_access_point,
    dynamo_tables=storage_stack.all_tables,
    artifacts_bucket=storage_stack.artifacts_bucket,
    env=env,
)

# ---------------------------------------------------------------------------
# 4. AWS Batch (ML training jobs)
# ---------------------------------------------------------------------------

batch_stack = BatchStack(
    app,
    "VsweBatch",
    vpc=vpc_stack.vpc,
    file_system=storage_stack.file_system,
    efs_access_point=storage_stack.efs_access_point,
    efs_security_group=vpc_stack.efs_security_group,
    dynamo_tables=storage_stack.all_tables,
    artifacts_bucket=storage_stack.artifacts_bucket,
    env=env,
)

# ---------------------------------------------------------------------------
# 5. Lambda + API Gateway (webhook receiver)
# ---------------------------------------------------------------------------

lambda_stack = LambdaStack(
    app,
    "VsweLambda",
    dynamo_tables=storage_stack.all_tables,
    env=env,
)

# ---------------------------------------------------------------------------
# 6. CloudFront + S3 (frontend CDN)
# ---------------------------------------------------------------------------

cdn_stack = CdnStack(app, "VsweCdn", env=env)

# ---------------------------------------------------------------------------
# Synthesize
# ---------------------------------------------------------------------------

app.synth()
