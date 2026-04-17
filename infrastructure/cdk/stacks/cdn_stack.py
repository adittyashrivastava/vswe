"""CDN stack — CloudFront distribution, S3 static hosting, API proxy."""

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_elasticloadbalancingv2 as elbv2,
    aws_s3 as s3,
)
from constructs import Construct


class CdnStack(Stack):
    """CloudFront distribution serving both the React frontend and proxying
    API requests to the ALB.

    - ``/*``       → S3 bucket (static frontend assets)
    - ``/api/*``   → ALB (backend API)
    - ``/ws/*``    → ALB (WebSocket)

    This ensures everything goes through CloudFront's HTTPS — no mixed
    content issues between the frontend and backend.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        alb: elbv2.IApplicationLoadBalancer,
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
        # Origins
        # =====================================================================

        s3_origin = origins.S3BucketOrigin.with_origin_access_control(
            self.frontend_bucket,
        )

        alb_origin = origins.HttpOrigin(
            alb.load_balancer_dns_name,
            protocol_policy=cloudfront.OriginProtocolPolicy.HTTP_ONLY,
        )

        # =====================================================================
        # Cache Policies
        # =====================================================================

        # API: no caching — use the managed CachingDisabled policy
        api_cache_policy = cloudfront.CachePolicy.CACHING_DISABLED

        # =====================================================================
        # CloudFront Distribution
        # =====================================================================

        self.distribution = cloudfront.Distribution(
            self,
            "FrontendDistribution",
            comment="VSWE Frontend",
            default_root_object="index.html",
            # Default: serve static frontend from S3
            default_behavior=cloudfront.BehaviorOptions(
                origin=s3_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
                cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD_OPTIONS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                compress=True,
            ),
            additional_behaviors={
                # Static assets with long cache
                "/assets/*": cloudfront.BehaviorOptions(
                    origin=s3_origin,
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
                # API proxy → ALB (no caching)
                "/api/*": cloudfront.BehaviorOptions(
                    origin=alb_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cache_policy=api_cache_policy,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER,
                ),
                # WebSocket proxy → ALB
                "/ws/*": cloudfront.BehaviorOptions(
                    origin=alb_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cache_policy=api_cache_policy,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER,
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
