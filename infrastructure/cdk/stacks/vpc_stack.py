"""VPC stack — public/private subnets, NAT gateway, EFS mount targets."""

from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    Tags,
)
from constructs import Construct


class VpcStack(Stack):
    """Creates a VPC with public and private subnets across 2 AZs.

    Outputs:
        vpc: ec2.Vpc
        private_subnets: list of private subnets (for ECS, Batch, EFS)
        public_subnets: list of public subnets (for ALB, NAT)
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # -- VPC ---------------------------------------------------------------
        self.vpc = ec2.Vpc(
            self,
            "VsweVpc",
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            max_azs=2,
            nat_gateways=1,  # Single NAT to keep costs low
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
            ],
        )

        Tags.of(self.vpc).add("Project", "vswe")

        # -- VPC Endpoints (reduce NAT costs) ----------------------------------
        self.vpc.add_gateway_endpoint(
            "S3Endpoint",
            service=ec2.GatewayVpcEndpointAwsService.S3,
        )
        self.vpc.add_gateway_endpoint(
            "DynamoEndpoint",
            service=ec2.GatewayVpcEndpointAwsService.DYNAMODB,
        )

        # -- EFS Security Group ------------------------------------------------
        self.efs_security_group = ec2.SecurityGroup(
            self,
            "EfsSg",
            vpc=self.vpc,
            description="Allow NFS traffic for EFS",
            allow_all_outbound=False,
        )
        # Allow NFS inbound from any resource in the VPC
        self.efs_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(2049),
            description="NFS from VPC",
        )
