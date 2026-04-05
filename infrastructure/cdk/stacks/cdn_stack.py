"""CDN stack — CloudFront distribution, S3 static hosting, OAI."""

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
)
from constructs import Construct


class CdnStack(Stack):
    """CloudFront distribution for the React frontend.

    - S3 bucket: private, serves static assets only through CloudFront
    - OAI: Origin Access Identity restricts direct S3 access
    - CloudFront: HTTPS, gzip/brotli compression, SPA routing
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # =====================================================================
        # S3 Bucket — Frontend Static Assets
        # =====================================================================

        self.frontend_bucket = s3.Bucket(
            self,
            "FrontendBucket",
            bucket_name=f"vswe-frontend-{self.account}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # =====================================================================
        # Origin Access Identity
        # =====================================================================

        oai = cloudfront.OriginAccessIdentity(
            self,
            "FrontendOai",
            comment="OAI for VSWE frontend bucket",
        )
        self.frontend_bucket.grant_read(oai)

        # =====================================================================
        # CloudFront Distribution
        # =====================================================================

        self.distribution = cloudfront.Distribution(
            self,
            "FrontendDistribution",
            comment="VSWE Frontend",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3Origin(
                    self.frontend_bucket,
                    origin_access_identity=oai,
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
                cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD_OPTIONS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                compress=True,
            ),
            additional_behaviors={
                "/assets/*": cloudfront.BehaviorOptions(
                    origin=origins.S3Origin(
                        self.frontend_bucket,
                        origin_access_identity=oai,
                    ),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy(
                        self,
                        "AssetsCachePolicy",
                        cache_policy_name="vswe-assets-long-cache",
                        default_ttl=Duration.days(365),
                        max_ttl=Duration.days(365),
                        min_ttl=Duration.days(1),
                        enable_accept_encoding_gzip=True,
                        enable_accept_encoding_brotli=True,
                    ),
                    compress=True,
                ),
            },
            # SPA fallback — return index.html for any 403/404 from S3
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(0),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(0),
                ),
            ],
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
        )

        # =====================================================================
        # Outputs
        # =====================================================================

        CfnOutput(
            self,
            "DistributionDomainName",
            value=self.distribution.distribution_domain_name,
            description="CloudFront distribution domain name",
        )

        CfnOutput(
            self,
            "FrontendBucketName",
            value=self.frontend_bucket.bucket_name,
            description="S3 bucket for frontend deployment",
        )
