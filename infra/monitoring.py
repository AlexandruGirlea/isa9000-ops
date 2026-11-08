from __future__ import annotations

from datetime import UTC, datetime

import pulumi
from pulumi_azure_native import consumption, monitor

import apps
import core as s
import data

action_group = monitor.ActionGroup(
    "alerts",
    action_group_name=f"{s.prefix}-alerts",
    resource_group_name=data.group_name,
    location="global",
    tags=s.tags,
    enabled=True,
    group_short_name=f"isa-{s.environment}",
    email_receivers=[
        {"name": "operator", "email_address": s.alert_email, "use_common_alert_schema": True}
    ],
)
monitor.MetricAlert(
    "dead-letter-alert",
    rule_name=f"{s.prefix}-dead-letters",
    resource_group_name=data.group_name,
    location="global",
    tags=s.tags,
    enabled=True,
    severity=1,
    evaluation_frequency="PT1M",
    window_size="PT5M",
    scopes=[data.bus.id],
    actions=[{"action_group_id": action_group.id}],
    # This union-typed input needs its Args classes; plain dictionaries are not translated.
    criteria=monitor.MetricAlertSingleResourceMultipleMetricCriteriaArgs(
        odata_type="Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria",
        all_of=[
            monitor.MetricCriteriaArgs(
                name="dead-letters",
                criterion_type="StaticThresholdCriterion",
                metric_namespace="Microsoft.ServiceBus/namespaces",
                metric_name="DeadletteredMessages",
                operator="GreaterThan",
                threshold=0,
                time_aggregation="Maximum",
                dimensions=[{"name": "EntityName", "operator": "Include", "values": ["tasks"]}],
            )
        ],
    ),
)
queries = {
    "failed-runs": (
        "AppTraces | where tostring(Properties.state) == 'failed' "
        "| extend RunId = tostring(Properties.run_id) | where isnotempty(RunId) "
        "| distinct RunId",
        "PT30M",
        3,
    ),
    "server-errors": (
        "AppRequests | where AppRoleName == 'isa9000-web' "
        "and toint(ResultCode) between (500 .. 599)",
        "PT5M",
        5,
    ),
    "denied-access": (
        "AppTraces | where tostring(Properties.event) == 'audit' "
        "and tostring(Properties.outcome) == 'denied' "
        "and tostring(Properties.action) in ('auth.login', 'admin.access', 'worker.callback')",
        "PT5M",
        5,
    ),
    "worker-executions": (
        apps.worker.name.apply(
            lambda name: (
                "ContainerAppSystemLogs_CL "
                f"| where ContainerAppName_s == '{name}' "
                "and Reason_s in ('JobExecutionFailed', 'DeadlineExceeded', 'BackoffLimitExceeded')"
            )
        ),
        "PT5M",
        0,
    ),
}
for name, (query, window, threshold) in queries.items():
    monitor.ScheduledQueryRule(
        name,
        rule_name=f"{s.prefix}-{name}",
        kind="LogAlert",
        enabled=True,
        scopes=[s.workspace.id],
        evaluation_frequency="PT5M",
        window_size=window,
        severity=1,
        skip_query_validation=True,
        actions={"action_groups": [action_group.id]},
        criteria={
            "all_of": [
                {
                    "query": query,
                    "time_aggregation": "Count",
                    "operator": "GreaterThan",
                    "threshold": threshold,
                    "failing_periods": {
                        "number_of_evaluation_periods": 1,
                        "min_failing_periods_to_alert": 1,
                    },
                }
            ]
        },
        **data.common,
        # Azure can report a just-created workspace as missing; the environment takes minutes.
        opts=pulumi.ResourceOptions(depends_on=[s.app_environment]),
    )
consumption.Budget(
    "budget",
    budget_name=f"{s.prefix}-monthly",
    scope=s.group_id,
    amount=s.config.get_float("budgetMonthly") or 100,
    category="Cost",
    time_grain="Monthly",
    time_period={"start_date": datetime.now(UTC).strftime("%Y-%m-01T00:00:00Z")},
    notifications={
        "Actual100Percent": {
            "enabled": True,
            "operator": "GreaterThanOrEqualTo",
            "threshold": 100,
            "threshold_type": "Actual",
            "contact_emails": [s.alert_email],
        }
    },
    opts=pulumi.ResourceOptions(ignore_changes=["timePeriod.startDate"]),
)
