"""AWS Batch stack — compute environments, job queues, job definitions."""

from aws_cdk import (
    Stack,
    aws_batch as batch,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_efs as efs,
    aws_iam as iam,
    aws_logs as logs,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
)
from constructs import Construct


class BatchStack(Stack):
    """AWS Batch infrastructure for ML training jobs.

    Compute environments:
        - GPU Spot (g4dn/g5 instances) for training
        - CPU Spot (m5/c5 instances) for data preprocessing

    Job queues:
        - gpu-spot: routes to GPU compute environment
        - cpu-spot: routes to CPU compute environment

    Job definitions with EFS mounts for checkpoint persistence.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc,
        file_system: efs.IFileSystem,
        efs_access_point: efs.IAccessPoint,
        efs_security_group: ec2.ISecurityGroup,
        dynamo_tables: list[dynamodb.ITable],
        artifacts_bucket: s3.IBucket,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # =====================================================================
        # Security Group for Batch instances
        # =====================================================================

        batch_sg = ec2.SecurityGroup(
            self,
            "BatchSg",
            vpc=vpc,
            description="Security group for Batch compute instances",
            allow_all_outbound=True,
        )
        # Allow NFS to EFS
        efs_security_group.add_ingress_rule(
            peer=batch_sg,
            connection=ec2.Port.tcp(2049),
            description="NFS from Batch instances",
        )

        # =====================================================================
        # IAM Roles
        # =====================================================================

        # Instance role for EC2 instances in the compute environment
        instance_role = iam.Role(
            self,
            "BatchInstanceRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonEC2ContainerServiceforEC2Role"
                ),
            ],
        )

        instance_profile = iam.CfnInstanceProfile(
            self,
            "BatchInstanceProfile",
            roles=[instance_role.role_name],
        )

        # Job role — permissions available inside the container
        self.job_role = iam.Role(
            self,
            "BatchJobRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        for table in dynamo_tables:
            table.grant_read_write_data(self.job_role)
        artifacts_bucket.grant_read_write(self.job_role)
        file_system.grant_read_write(self.job_role)

        self.job_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter", "ssm:GetParameters"],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter/vswe/*"
                ],
            )
        )

        # Execution role for Batch/ECS to pull images, write logs
        execution_role = iam.Role(
            self,
            "BatchExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
            ],
        )

        # Service role for Batch itself
        batch_service_role = iam.Role(
            self,
            "BatchServiceRole",
            assumed_by=iam.ServicePrincipal("batch.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSBatchServiceRole"
                ),
            ],
        )

        # =====================================================================
        # Launch Template (EFS mount via user data)
        # =====================================================================

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "yum install -y amazon-efs-utils",
            f"mkdir -p /efs",
            f"mount -t efs -o tls {file_system.file_system_id}:/ /efs",
        )

        launch_template = ec2.LaunchTemplate(
            self,
            "BatchLaunchTemplate",
            user_data=user_data,
        )

        # =====================================================================
        # GPU Spot Compute Environment
        # =====================================================================

        self.gpu_compute_env = batch.CfnComputeEnvironment(
            self,
            "GpuSpotComputeEnv",
            type="MANAGED",
            state="ENABLED",
            service_role=batch_service_role.role_arn,
            compute_resources=batch.CfnComputeEnvironment.ComputeResourcesProperty(
                type="SPOT",
                allocation_strategy="SPOT_CAPACITY_OPTIMIZED",
                minv_cpus=0,
                maxv_cpus=64,
                desiredv_cpus=0,
                instance_types=["g4dn.xlarge", "g4dn.2xlarge", "g5.xlarge", "g5.2xlarge"],
                subnets=[s.subnet_id for s in vpc.private_subnets],
                security_group_ids=[batch_sg.security_group_id],
                instance_role=instance_profile.attr_arn,
                spot_iam_fleet_role=f"arn:aws:iam::{self.account}:role/aws-ec2-spot-fleet-tagging-role",
                launch_template=batch.CfnComputeEnvironment.LaunchTemplateSpecificationProperty(
                    launch_template_id=launch_template.launch_template_id,
                    version="$Latest",
                ),
                tags={"Project": "vswe", "Environment": "production"},
            ),
        )

        # =====================================================================
        # CPU Spot Compute Environment
        # =====================================================================

        self.cpu_compute_env = batch.CfnComputeEnvironment(
            self,
            "CpuSpotComputeEnv",
            type="MANAGED",
            state="ENABLED",
            service_role=batch_service_role.role_arn,
            compute_resources=batch.CfnComputeEnvironment.ComputeResourcesProperty(
                type="SPOT",
                allocation_strategy="SPOT_CAPACITY_OPTIMIZED",
                minv_cpus=0,
                maxv_cpus=32,
                desiredv_cpus=0,
                instance_types=["m5.xlarge", "m5.2xlarge", "c5.xlarge", "c5.2xlarge"],
                subnets=[s.subnet_id for s in vpc.private_subnets],
                security_group_ids=[batch_sg.security_group_id],
                instance_role=instance_profile.attr_arn,
                spot_iam_fleet_role=f"arn:aws:iam::{self.account}:role/aws-ec2-spot-fleet-tagging-role",
                launch_template=batch.CfnComputeEnvironment.LaunchTemplateSpecificationProperty(
                    launch_template_id=launch_template.launch_template_id,
                    version="$Latest",
                ),
                tags={"Project": "vswe", "Environment": "production"},
            ),
        )

        # =====================================================================
        # Job Queues
        # =====================================================================

        self.gpu_queue = batch.CfnJobQueue(
            self,
            "GpuSpotQueue",
            job_queue_name="vswe-gpu-spot",
            state="ENABLED",
            priority=10,
            compute_environment_order=[
                batch.CfnJobQueue.ComputeEnvironmentOrderProperty(
                    compute_environment=self.gpu_compute_env.ref,
                    order=1,
                ),
            ],
        )
        self.gpu_queue.add_dependency(self.gpu_compute_env)

        self.cpu_queue = batch.CfnJobQueue(
            self,
            "CpuSpotQueue",
            job_queue_name="vswe-cpu-spot",
            state="ENABLED",
            priority=5,
            compute_environment_order=[
                batch.CfnJobQueue.ComputeEnvironmentOrderProperty(
                    compute_environment=self.cpu_compute_env.ref,
                    order=1,
                ),
            ],
        )
        self.cpu_queue.add_dependency(self.cpu_compute_env)

        # =====================================================================
        # Job Definitions
        # =====================================================================

        # GPU training job definition
        self.gpu_job_def = batch.CfnJobDefinition(
            self,
            "GpuTrainingJobDef",
            job_definition_name="vswe-gpu-training",
            type="container",
            platform_capabilities=["EC2"],
            retry_strategy=batch.CfnJobDefinition.RetryStrategyProperty(
                attempts=3,
                evaluate_on_exit=[
                    batch.CfnJobDefinition.EvaluateOnExitProperty(
                        action="RETRY",
                        on_reason="Host EC2*",
                    ),
                    batch.CfnJobDefinition.EvaluateOnExitProperty(
                        action="RETRY",
                        on_status_reason="Host EC2*",
                    ),
                    batch.CfnJobDefinition.EvaluateOnExitProperty(
                        action="EXIT",
                        on_exit_code="0",
                    ),
                ],
            ),
            timeout=batch.CfnJobDefinition.TimeoutProperty(
                attempt_duration_seconds=86400,  # 24 hours max
            ),
            container_properties=batch.CfnJobDefinition.ContainerPropertiesProperty(
                image="vswe/training:latest",
                vcpus=4,
                memory=16384,
                resource_requirements=[
                    batch.CfnJobDefinition.ResourceRequirementProperty(
                        type="GPU", value="1"
                    ),
                ],
                job_role_arn=self.job_role.role_arn,
                execution_role_arn=execution_role.role_arn,
                log_configuration=batch.CfnJobDefinition.LogConfigurationProperty(
                    log_driver="awslogs",
                    options={
                        "awslogs-group": "/vswe/batch/gpu-training",
                        "awslogs-region": self.region,
                        "awslogs-stream-prefix": "training",
                    },
                ),
                volumes=[
                    batch.CfnJobDefinition.VolumesProperty(
                        name="vswe-efs",
                        efs_volume_configuration=batch.CfnJobDefinition.EfsVolumeConfigurationProperty(
                            file_system_id=file_system.file_system_id,
                            transit_encryption="ENABLED",
                            authorization_config=batch.CfnJobDefinition.AuthorizationConfigProperty(
                                access_point_id=efs_access_point.access_point_id,
                                iam="ENABLED",
                            ),
                        ),
                    ),
                ],
                mount_points=[
                    batch.CfnJobDefinition.MountPointsProperty(
                        container_path="/efs",
                        source_volume="vswe-efs",
                        read_only=False,
                    ),
                ],
                environment=[
                    batch.CfnJobDefinition.EnvironmentProperty(
                        name="EFS_MOUNT_PATH", value="/efs"
                    ),
                    batch.CfnJobDefinition.EnvironmentProperty(
                        name="AWS_REGION", value=self.region
                    ),
                    batch.CfnJobDefinition.EnvironmentProperty(
                        name="S3_BUCKET", value=artifacts_bucket.bucket_name
                    ),
                ],
            ),
        )

        # CPU preprocessing job definition
        self.cpu_job_def = batch.CfnJobDefinition(
            self,
            "CpuPreprocessJobDef",
            job_definition_name="vswe-cpu-preprocess",
            type="container",
            platform_capabilities=["EC2"],
            retry_strategy=batch.CfnJobDefinition.RetryStrategyProperty(
                attempts=2,
                evaluate_on_exit=[
                    batch.CfnJobDefinition.EvaluateOnExitProperty(
                        action="RETRY",
                        on_reason="Host EC2*",
                    ),
                ],
            ),
            timeout=batch.CfnJobDefinition.TimeoutProperty(
                attempt_duration_seconds=14400,  # 4 hours max
            ),
            container_properties=batch.CfnJobDefinition.ContainerPropertiesProperty(
                image="vswe/preprocess:latest",
                vcpus=2,
                memory=8192,
                job_role_arn=self.job_role.role_arn,
                execution_role_arn=execution_role.role_arn,
                log_configuration=batch.CfnJobDefinition.LogConfigurationProperty(
                    log_driver="awslogs",
                    options={
                        "awslogs-group": "/vswe/batch/cpu-preprocess",
                        "awslogs-region": self.region,
                        "awslogs-stream-prefix": "preprocess",
                    },
                ),
                volumes=[
                    batch.CfnJobDefinition.VolumesProperty(
                        name="vswe-efs",
                        efs_volume_configuration=batch.CfnJobDefinition.EfsVolumeConfigurationProperty(
                            file_system_id=file_system.file_system_id,
                            transit_encryption="ENABLED",
                            authorization_config=batch.CfnJobDefinition.AuthorizationConfigProperty(
                                access_point_id=efs_access_point.access_point_id,
                                iam="ENABLED",
                            ),
                        ),
                    ),
                ],
                mount_points=[
                    batch.CfnJobDefinition.MountPointsProperty(
                        container_path="/efs",
                        source_volume="vswe-efs",
                        read_only=False,
                    ),
                ],
                environment=[
                    batch.CfnJobDefinition.EnvironmentProperty(
                        name="EFS_MOUNT_PATH", value="/efs"
                    ),
                    batch.CfnJobDefinition.EnvironmentProperty(
                        name="AWS_REGION", value=self.region
                    ),
                    batch.CfnJobDefinition.EnvironmentProperty(
                        name="S3_BUCKET", value=artifacts_bucket.bucket_name
                    ),
                ],
            ),
        )

        # Log groups for Batch jobs
        logs.LogGroup(
            self,
            "GpuTrainingLogGroup",
            log_group_name="/vswe/batch/gpu-training",
            retention=logs.RetentionDays.ONE_MONTH,
        )
        logs.LogGroup(
            self,
            "CpuPreprocessLogGroup",
            log_group_name="/vswe/batch/cpu-preprocess",
            retention=logs.RetentionDays.ONE_MONTH,
        )
