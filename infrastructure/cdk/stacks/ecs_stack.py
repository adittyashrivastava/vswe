"""ECS stack — Fargate cluster, task definitions, ALB, auto-scaling."""

from aws_cdk import (
    Duration,
    Stack,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_efs as efs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_logs as logs,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
)
from constructs import Construct


class EcsStack(Stack):
    """ECS Fargate cluster with API server and agent task definitions.

    - API server: long-running Fargate service behind an ALB
    - Agent task: on-demand Fargate task spawned per session
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc,
        file_system: efs.IFileSystem,
        efs_access_point: efs.IAccessPoint,
        dynamo_tables: list[dynamodb.ITable],
        artifacts_bucket: s3.IBucket,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # =====================================================================
        # Cluster
        # =====================================================================

        self.cluster = ecs.Cluster(
            self,
            "VsweCluster",
            vpc=vpc,
            cluster_name="vswe-cluster",
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )

        # =====================================================================
        # Shared EFS Volume
        # =====================================================================

        efs_volume_config = ecs.Volume(
            name="vswe-efs",
            efs_volume_configuration=ecs.EfsVolumeConfiguration(
                file_system_id=file_system.file_system_id,
                transit_encryption="ENABLED",
                authorization_config=ecs.AuthorizationConfig(
                    access_point_id=efs_access_point.access_point_id,
                    iam="ENABLED",
                ),
            ),
        )

        # =====================================================================
        # IAM — Shared Task Execution Role
        # =====================================================================

        execution_role = iam.Role(
            self,
            "TaskExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
            ],
        )

        # Task role — permissions the container process uses at runtime
        task_role = iam.Role(
            self,
            "TaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        # Grant DynamoDB access
        for table in dynamo_tables:
            table.grant_read_write_data(task_role)

        # Grant S3 access
        artifacts_bucket.grant_read_write(task_role)

        # Grant EFS access
        file_system.grant_read_write(task_role)

        # Allow pulling secrets from SSM Parameter Store
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter", "ssm:GetParameters"],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter/vswe/*"
                ],
            )
        )

        # Allow submitting Batch jobs (agent needs this)
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "batch:SubmitJob",
                    "batch:DescribeJobs",
                    "batch:ListJobs",
                    "batch:TerminateJob",
                ],
                resources=["*"],
            )
        )

        # =====================================================================
        # API Server — Task Definition
        # =====================================================================

        self.api_task_def = ecs.FargateTaskDefinition(
            self,
            "ApiTaskDef",
            family="vswe-api",
            cpu=512,
            memory_limit_mib=1024,
            execution_role=execution_role,
            task_role=task_role,
            volumes=[efs_volume_config],
        )

        api_container = self.api_task_def.add_container(
            "api",
            image=ecs.ContainerImage.from_registry("vswe/api:latest"),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="vswe-api",
                log_retention=logs.RetentionDays.ONE_MONTH,
            ),
            environment={
                "ENV": "production",
                "AWS_REGION": self.region,
                "EFS_MOUNT_PATH": "/efs",
                "S3_BUCKET": artifacts_bucket.bucket_name,
            },
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(60),
            ),
        )

        api_container.add_port_mappings(
            ecs.PortMapping(container_port=8000, protocol=ecs.Protocol.TCP)
        )

        api_container.add_mount_points(
            ecs.MountPoint(
                container_path="/efs",
                source_volume="vswe-efs",
                read_only=False,
            )
        )

        # =====================================================================
        # Agent Task Definition (spawned on-demand)
        # =====================================================================

        self.agent_task_def = ecs.FargateTaskDefinition(
            self,
            "AgentTaskDef",
            family="vswe-agent",
            cpu=1024,
            memory_limit_mib=2048,
            execution_role=execution_role,
            task_role=task_role,
            volumes=[efs_volume_config],
        )

        agent_container = self.agent_task_def.add_container(
            "agent",
            image=ecs.ContainerImage.from_registry("vswe/agent:latest"),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="vswe-agent",
                log_retention=logs.RetentionDays.ONE_MONTH,
            ),
            environment={
                "ENV": "production",
                "AWS_REGION": self.region,
                "EFS_MOUNT_PATH": "/efs",
                "S3_BUCKET": artifacts_bucket.bucket_name,
            },
        )

        agent_container.add_mount_points(
            ecs.MountPoint(
                container_path="/efs",
                source_volume="vswe-efs",
                read_only=False,
            )
        )

        # =====================================================================
        # ALB + Fargate Service (API)
        # =====================================================================

        # Security group for ALB
        alb_sg = ec2.SecurityGroup(
            self,
            "AlbSg",
            vpc=vpc,
            description="ALB security group",
            allow_all_outbound=True,
        )
        alb_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "HTTPS from anywhere"
        )
        alb_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "HTTP from anywhere (redirect)"
        )

        self.alb = elbv2.ApplicationLoadBalancer(
            self,
            "VsweAlb",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_sg,
        )

        # Security group for API service
        api_sg = ec2.SecurityGroup(
            self,
            "ApiSg",
            vpc=vpc,
            description="API service security group",
            allow_all_outbound=True,
        )
        api_sg.add_ingress_rule(
            alb_sg, ec2.Port.tcp(8000), "Traffic from ALB"
        )

        self.api_service = ecs.FargateService(
            self,
            "ApiService",
            cluster=self.cluster,
            task_definition=self.api_task_def,
            desired_count=1,
            security_groups=[api_sg],
            assign_public_ip=False,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
            ),
            capacity_provider_strategies=[
                ecs.CapacityProviderStrategy(
                    capacity_provider="FARGATE_SPOT",
                    weight=2,
                ),
                ecs.CapacityProviderStrategy(
                    capacity_provider="FARGATE",
                    weight=1,
                    base=1,  # Always keep 1 on-demand for reliability
                ),
            ],
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
        )

        # ALB target
        listener = self.alb.add_listener(
            "HttpListener",
            port=80,
            default_action=elbv2.ListenerAction.fixed_response(
                status_code=404,
                content_type="text/plain",
                message_body="Not Found",
            ),
        )

        target_group = listener.add_targets(
            "ApiTarget",
            port=8000,
            targets=[self.api_service],
            health_check=elbv2.HealthCheck(
                path="/health",
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
            ),
            conditions=[elbv2.ListenerCondition.path_patterns(["/*"])],
            priority=1,
        )

        # =====================================================================
        # Auto-Scaling
        # =====================================================================

        scaling = self.api_service.auto_scale_task_count(
            min_capacity=1,
            max_capacity=4,
        )

        scaling.scale_on_cpu_utilization(
            "CpuScaling",
            target_utilization_percent=70,
            scale_in_cooldown=Duration.seconds(300),
            scale_out_cooldown=Duration.seconds(60),
        )

        scaling.scale_on_request_count(
            "RequestScaling",
            requests_per_target=500,
            target_group=target_group,
            scale_in_cooldown=Duration.seconds(300),
            scale_out_cooldown=Duration.seconds(60),
        )
