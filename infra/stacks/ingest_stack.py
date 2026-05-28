from aws_cdk import (
    Stack,
    CfnOutput,
    RemovalPolicy,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecr as ecr,
    aws_logs as logs,
    aws_s3 as s3,
)
from constructs import Construct


class IngestStack(Stack):

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc,
        corpus_bucket: s3.IBucket,
        index_bucket: s3.IBucket,
        ecr_repository: ecr.IRepository,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cluster = ecs.Cluster(
            self,
            "IngestCluster",
            vpc=vpc,
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )

        log_group = logs.LogGroup(
            self,
            "IngestLogGroup",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        task_definition = ecs.FargateTaskDefinition(
            self,
            "IngestTaskDefinition",
            cpu=2048,
            memory_limit_mib=4096,
            runtime_platform=ecs.RuntimePlatform(
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
                cpu_architecture=ecs.CpuArchitecture.X86_64,
            ),
        )

        # GOOGLE_API_KEY must be provisioned at deploy time — for a real deployment,
        # source it from Secrets Manager and bind via `secrets={}` on this container.
        task_definition.add_container(
            "ingest",
            image=ecs.ContainerImage.from_ecr_repository(ecr_repository, "latest"),
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="ingest",
                log_group=log_group,
            ),
            environment={
                "AWS_REGION": self.region,
                "CORPUS_BUCKET": corpus_bucket.bucket_name,
                "INDEX_BUCKET": index_bucket.bucket_name,
                "MODE": "ingest",
                "EMBEDDING_MODEL": "gemini-embedding-2",
            },
        )

        corpus_bucket.grant_read(task_definition.task_role)
        index_bucket.grant_read_write(task_definition.task_role)

        CfnOutput(self, "IngestClusterArn", value=cluster.cluster_arn)
        CfnOutput(self, "IngestTaskDefinitionArn", value=task_definition.task_definition_arn)
