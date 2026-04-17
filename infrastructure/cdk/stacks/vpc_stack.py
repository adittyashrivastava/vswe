"""VPC stack — public/private subnets, fck-nat instance, EFS mount targets."""

from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    Tags,
)
from cdk_fck_nat import FckNatInstanceProvider
from constructs import Construct


class VpcStack(Stack):
    """Creates a VPC with public and private subnets across 2 AZs.

    Uses fck-nat (a t4g.nano EC2 instance) instead of a managed NAT
    Gateway to keep idle costs at ~$3/month instead of ~$32/month.

    Outputs:
        vpc: ec2.Vpc
        private_subnets: list of private subnets (for ECS, Batch, EFS)
        public_subnets: list of public subnets (for ALB, NAT)
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # -- NAT provider (fck-nat: ~$3/mo vs ~$32/mo for NAT Gateway) --------
        nat_provider = FckNatInstanceProvider(
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T4G, ec2.InstanceSize.MICRO,
            ),
        )

        # -- VPC ---------------------------------------------------------------
        self.vpc = ec2.Vpc(
            self,
            "VsweVpc",
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            max_azs=2,
            nat_gateway_provider=nat_provider,
            nat_gateways=1,
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

        # Allow traffic from private subnets to the NAT instance
        nat_provider.security_group.add_ingress_rule(
            ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
            ec2.Port.all_traffic(),
            "Allow all traffic from VPC for NAT",
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
