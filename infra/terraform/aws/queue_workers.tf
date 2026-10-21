# Fase 10.6: workers de casos en ECS Fargate con autoescalado por la cola SQS support.emails.
# Desactivado por defecto (queue_workers_enabled). Coste: con desired_count 0 y sin mensajes no
# hay tareas; cada tarea de 0,5 vCPU / 1 GB cuesta ~0,025 $/h mientras existe. Container
# Insights (métrica RunningTaskCount) se cobra por métrica. La imagen la publica la fase 13 (ECR).
#
# Política (la misma idea que KEDA y que scripts/queue_autoscaler.py): mensajes por tarea
# (backlog per task) = visibles / tareas en marcha, con objetivo queue_worker_messages_per_task.
# Con 0 tareas la división no vale: el IF usa la cola entera para arrancar la primera.

locals {
  queue_workers = var.queue_workers_enabled ? 1 : 0
}

resource "aws_sqs_queue" "support_emails_dlq" {
  count                     = local.queue_workers
  name                      = "${local.name}-support-emails-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "support_emails" {
  count                      = local.queue_workers
  name                       = "${local.name}-support-emails"
  visibility_timeout_seconds = 300 # Un caso con LLM tarda segundos o minutos.
  receive_wait_time_seconds  = 10
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.support_emails_dlq[0].arn
    maxReceiveCount     = var.max_deliveries
  })
}

data "aws_vpc" "default" {
  count   = local.queue_workers
  default = true
}

data "aws_subnets" "default" {
  count = local.queue_workers
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default[0].id]
  }
}

resource "aws_security_group" "queue_workers" {
  count       = local.queue_workers
  name        = "${local.name}-queue-workers"
  description = "Workers de casos: sin entrada, solo salida (SQS, Bedrock, base de datos)."
  vpc_id      = data.aws_vpc.default[0].id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_ecs_cluster" "workers" {
  count = local.queue_workers
  name  = "${local.name}-workers"

  setting {
    name  = "containerInsights"
    value = "enabled" # Publica RunningTaskCount, que usa la política de escalado.
  }
}

data "aws_iam_policy_document" "ecs_tasks_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "queue_worker_execution" {
  count              = local.queue_workers
  name               = "${local.name}-queue-worker-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy_attachment" "queue_worker_execution" {
  count      = local.queue_workers
  role       = aws_iam_role.queue_worker_execution[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Rol de la tarea: consumir support.emails y publicar en las colas de los especialistas.
data "aws_iam_policy_document" "queue_worker_task" {
  count = local.queue_workers
  statement {
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
      "sqs:SendMessage",
    ]
    resources = ["arn:aws:sqs:${var.region}:${data.aws_caller_identity.current.account_id}:${local.name}-*"]
  }
}

resource "aws_iam_role" "queue_worker_task" {
  count              = local.queue_workers
  name               = "${local.name}-queue-worker-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "queue_worker_task" {
  count  = local.queue_workers
  role   = aws_iam_role.queue_worker_task[0].id
  policy = data.aws_iam_policy_document.queue_worker_task[0].json
}

resource "aws_ecs_task_definition" "triage_worker" {
  count                    = local.queue_workers
  family                   = "${local.name}-triage-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.queue_worker_execution[0].arn
  task_role_arn            = aws_iam_role.queue_worker_task[0].arn

  container_definitions = jsonencode([
    {
      name       = "triage"
      image      = var.queue_worker_image
      essential  = true
      entryPoint = ["python", "-m", "orchestrator.worker"]
      command    = ["triage", "relay"]
      environment = [
        { name = "ORCHESTRATOR__BROKER", value = "sqs" },
        { name = "SQS__REGION", value = var.region },
      ]
    }
  ])

  lifecycle {
    precondition {
      condition     = var.queue_worker_image != ""
      error_message = "queue_workers_enabled exige queue_worker_image (imagen del orquestador en ECR)."
    }
  }
}

resource "aws_ecs_service" "triage_worker" {
  count           = local.queue_workers
  name            = "triage-worker"
  cluster         = aws_ecs_cluster.workers[0].id
  task_definition = aws_ecs_task_definition.triage_worker[0].arn
  launch_type     = "FARGATE"
  desired_count   = 0

  network_configuration {
    subnets          = data.aws_subnets.default[0].ids
    security_groups  = [aws_security_group.queue_workers[0].id]
    assign_public_ip = true # VPC por defecto sin NAT: IP pública para salir (fase 13: privada + NAT/endpoints).
  }

  # El número de tareas lo decide el autoescalado, no Terraform.
  lifecycle {
    ignore_changes = [desired_count]
  }
}

resource "aws_appautoscaling_target" "triage_worker" {
  count              = local.queue_workers
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.workers[0].name}/${aws_ecs_service.triage_worker[0].name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = 0
  # Techo útil = la cuota del modelo (scripts/capacity_plan.py): más tareas solo dan 429.
  max_capacity = var.queue_worker_max_tasks
}

resource "aws_appautoscaling_policy" "triage_backlog_per_task" {
  count              = local.queue_workers
  name               = "support-emails-backlog-per-task"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.triage_worker[0].service_namespace
  resource_id        = aws_appautoscaling_target.triage_worker[0].resource_id
  scalable_dimension = aws_appautoscaling_target.triage_worker[0].scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value       = var.queue_worker_messages_per_task
    scale_out_cooldown = 30  # Subir rápido...
    scale_in_cooldown  = 120 # ...y bajar despacio (sin oscilar entre ráfagas).

    customized_metric_specification {
      metrics {
        id          = "visible"
        return_data = false
        metric_stat {
          stat = "Average"
          metric {
            namespace   = "AWS/SQS"
            metric_name = "ApproximateNumberOfMessagesVisible"
            dimensions {
              name  = "QueueName"
              value = aws_sqs_queue.support_emails[0].name
            }
          }
        }
      }
      metrics {
        id          = "running"
        return_data = false
        metric_stat {
          stat = "Average"
          metric {
            namespace   = "ECS/ContainerInsights"
            metric_name = "RunningTaskCount"
            dimensions {
              name  = "ClusterName"
              value = aws_ecs_cluster.workers[0].name
            }
            dimensions {
              name  = "ServiceName"
              value = aws_ecs_service.triage_worker[0].name
            }
          }
        }
      }
      metrics {
        id          = "backlog_per_task"
        label       = "Mensajes por tarea"
        expression  = "IF(running > 0, visible / running, visible)"
        return_data = true
      }
    }
  }
}
