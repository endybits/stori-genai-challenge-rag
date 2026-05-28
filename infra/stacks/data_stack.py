from aws_cdk import (
    Stack,
    RemovalPolicy,
    Duration,
    CfnOutput,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
)
from constructs import Construct


class DataStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.corpus_bucket = s3.Bucket(
            self,
            "CorpusBucket",
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=RemovalPolicy.RETAIN,
        )

        self.index_bucket = s3.Bucket(
            self,
            "IndexBucket",
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireOldVersions",
                    noncurrent_version_expiration=Duration.days(30),
                ),
            ],
        )

        self.checkpointer_table = dynamodb.Table(
            self,
            "CheckpointerTable",
            partition_key=dynamodb.Attribute(
                name="thread_id",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="checkpoint_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True,
            ),
            time_to_live_attribute="ttl",
        )

        CfnOutput(self, "CorpusBucketName", value=self.corpus_bucket.bucket_name)
        CfnOutput(self, "IndexBucketName", value=self.index_bucket.bucket_name)
        CfnOutput(self, "CheckpointerTableName", value=self.checkpointer_table.table_name)