from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    RemovalPolicy,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecr as ecr,
    aws_logs as logs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
)
from constructs import Construct


class ComputeStack(Stack):

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc,
        alb: elbv2.IApplicationLoadBalancer,
        alb_security_group: ec2.ISecurityGroup,
        index_bucket: s3.IBucket,
        checkpointer_table: dynamodb.ITable,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.ecr_repository = ecr.Repository(
            self,
            "Repository",
            repository_name="stori-rag",
            image_scan_on_push=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    max_image_count=10,
                    rule_priority=1,
                    description="Keep most recent 10 images",
                ),
            ],
        )

        cluster = ecs.Cluster(
            self,
            "Cluster",
            vpc=vpc,
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )

        log_group = logs.LogGroup(
            self,
            "AppLogGroup",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        task_definition = ecs.FargateTaskDefinition(
            self,
            "TaskDefinition",
            cpu=1024,
            memory_limit_mib=2048,
            runtime_platform=ecs.RuntimePlatform(
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
                cpu_architecture=ecs.CpuArchitecture.X86_64,
            ),
        )

        # GOOGLE_API_KEY must be provisioned at deploy time — for a real deployment,
        # source it from Secrets Manager and bind via `secrets={}` on this container.
        container = task_definition.add_container(
            "agent",
            image=ecs.ContainerImage.from_ecr_repository(self.ecr_repository, "latest"),
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="agent",
                log_group=log_group,
            ),
            environment={
                "AWS_REGION": self.region,
                "INDEX_BUCKET": index_bucket.bucket_name,
                "CHECKPOINTER_TABLE": checkpointer_table.table_name,
                "MODE": "serve",
                "EMBEDDING_MODEL": "gemini-embedding-2",
            },
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -fsS http://localhost:8000/health || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(10),
                retries=3,
                start_period=Duration.seconds(60),
            ),
        )
        container.add_port_mappings(ecs.PortMapping(container_port=8000))

        index_bucket.grant_read(task_definition.task_role)
        checkpointer_table.grant_read_write_data(task_definition.task_role)

        service_security_group = ec2.SecurityGroup(
            self,
            "ServiceSecurityGroup",
            vpc=vpc,
            description="ALB -> Fargate on port 8000",
            allow_all_outbound=True,
        )
        service_security_group.add_ingress_rule(
            peer=alb_security_group,
            connection=ec2.Port.tcp(8000),
            description="Inbound from ALB only",
        )

        service = ecs.FargateService(
            self,
            "Service",
            cluster=cluster,
            task_definition=task_definition,
            desired_count=1,
            min_healthy_percent=100,
            max_healthy_percent=200,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[service_security_group],
            enable_execute_command=True,
            assign_public_ip=False,
        )

        listener = alb.add_listener("HttpListener", port=80, open=False)
        listener.add_targets(
            "AgentTarget",
            port=8000,
            targets=[service],
            health_check=elbv2.HealthCheck(
                path="/health",
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
                interval=Duration.seconds(30),
                timeout=Duration.seconds(10),
            ),
            deregistration_delay=Duration.seconds(30),
        )

        scaling = service.auto_scale_task_count(min_capacity=1, max_capacity=4)
        scaling.scale_on_cpu_utilization(
            "CpuScaling",
            target_utilization_percent=70,
            scale_in_cooldown=Duration.minutes(5),
            scale_out_cooldown=Duration.minutes(1),
        )

        CfnOutput(self, "EcrRepositoryUri", value=self.ecr_repository.repository_uri)
        CfnOutput(self, "ServiceName", value=service.service_name)