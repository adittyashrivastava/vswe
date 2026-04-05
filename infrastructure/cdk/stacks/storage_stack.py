"""Storage stack — DynamoDB tables, EFS filesystem, S3 bucket."""

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_dynamodb as dynamodb,
    aws_efs as efs,
    aws_ec2 as ec2,
    aws_s3 as s3,
)
from constructs import Construct


class StorageStack(Stack):
    """Creates all persistent storage resources for VSWE.

    DynamoDB tables (6, all PAY_PER_REQUEST):
        - vswe-sessions
        - vswe-messages
        - vswe-config
        - vswe-jobs
        - vswe-checkpoints
        - vswe-costs

    EFS filesystem for shared workspace storage.
    S3 bucket for artifacts with lifecycle rules.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc,
        efs_security_group: ec2.ISecurityGroup,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # =====================================================================
        # DynamoDB Tables
        # =====================================================================

        # 1. vswe-sessions
        self.sessions_table = dynamodb.Table(
            self,
            "SessionsTable",
            table_name="vswe-sessions",
            partition_key=dynamodb.Attribute(
                name="session_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery=True,
        )
        self.sessions_table.add_global_secondary_index(
            index_name="user_id-created_at-index",
            partition_key=dynamodb.Attribute(
                name="user_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="created_at", type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )
        self.sessions_table.add_global_secondary_index(
            index_name="github_repo-issue-index",
            partition_key=dynamodb.Attribute(
                name="github_repo_full_name", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="github_issue_number", type=dynamodb.AttributeType.NUMBER
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # 2. vswe-messages
        self.messages_table = dynamodb.Table(
            self,
            "MessagesTable",
            table_name="vswe-messages",
            partition_key=dynamodb.Attribute(
                name="session_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="message_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery=True,
        )

        # 3. vswe-config
        self.config_table = dynamodb.Table(
            self,
            "ConfigTable",
            table_name="vswe-config",
            partition_key=dynamodb.Attribute(
                name="config_scope", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # 4. vswe-jobs
        self.jobs_table = dynamodb.Table(
            self,
            "JobsTable",
            table_name="vswe-jobs",
            partition_key=dynamodb.Attribute(
                name="job_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery=True,
        )
        self.jobs_table.add_global_secondary_index(
            index_name="session_id-started_at-index",
            partition_key=dynamodb.Attribute(
                name="session_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="started_at", type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # 5. vswe-checkpoints
        self.checkpoints_table = dynamodb.Table(
            self,
            "CheckpointsTable",
            table_name="vswe-checkpoints",
            partition_key=dynamodb.Attribute(
                name="job_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="checkpoint_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # 6. vswe-costs
        self.costs_table = dynamodb.Table(
            self,
            "CostsTable",
            table_name="vswe-costs",
            partition_key=dynamodb.Attribute(
                name="date", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="cost_entry_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery=True,
        )
        self.costs_table.add_global_secondary_index(
            index_name="category-date-index",
            partition_key=dynamodb.Attribute(
                name="category", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="date", type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )
        self.costs_table.add_global_secondary_index(
            index_name="session_id-created_at-index",
            partition_key=dynamodb.Attribute(
                name="session_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="created_at", type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        self.all_tables = [
            self.sessions_table,
            self.messages_table,
            self.config_table,
            self.jobs_table,
            self.checkpoints_table,
            self.costs_table,
        ]

        # =====================================================================
        # EFS Filesystem
        # =====================================================================

        self.file_system = efs.FileSystem(
            self,
            "VsweEfs",
            vpc=vpc,
            security_group=efs_security_group,
            performance_mode=efs.PerformanceMode.GENERAL_PURPOSE,
            throughput_mode=efs.ThroughputMode.BURSTING,
            encrypted=True,
            removal_policy=RemovalPolicy.RETAIN,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
            ),
        )

        # Access point for ECS/Batch containers
        self.efs_access_point = self.file_system.add_access_point(
            "VsweAccessPoint",
            path="/vswe",
            create_acl=efs.Acl(owner_uid="1000", owner_gid="1000", permissions="755"),
            posix_user=efs.PosixUser(uid="1000", gid="1000"),
        )

        # =====================================================================
        # S3 Bucket for Artifacts
        # =====================================================================

        self.artifacts_bucket = s3.Bucket(
            self,
            "ArtifactsBucket",
            bucket_name="vswe-artifacts",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=False,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                # Move checkpoints to Infrequent Access after 30 days
                s3.LifecycleRule(
                    id="checkpoints-to-ia",
                    prefix="checkpoints/",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                            transition_after=Duration.days(30),
                        ),
                    ],
                ),
                # Delete temporary build artifacts after 7 days
                s3.LifecycleRule(
                    id="temp-cleanup",
                    prefix="tmp/",
                    expiration=Duration.days(7),
                ),
                # Move logs to Glacier after 90 days, delete after 365
                s3.LifecycleRule(
                    id="logs-lifecycle",
                    prefix="logs/",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER,
                            transition_after=Duration.days(90),
                        ),
                    ],
                    expiration=Duration.days(365),
                ),
            ],
        )
