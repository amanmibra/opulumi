# Price caps and alerts

Ask these at Gate A, record the answers in `MIGRATION.md`, and build them into the stack. Don't
pick numbers for the user; propose defaults from the cost estimate and let them change them.

## Questions for the user

1. **Monthly budget.** "The estimate is ~$X/month. What monthly budget should alert you?"
   Default: the estimate rounded up plus ~50%.
2. **Who gets alerts.** Email addresses (and Slack/PagerDuty, if they want them through SNS,
   Pub/Sub or an action group).
3. **Thresholds.** Default: 50% and 80% of actual spend, 100% actual, 100% forecast. Ask if they
   want an earlier one.
4. **Hard cap or alerts only.** No hyperscaler stops billing at a dollar amount by itself. Explain
   the options below and ask which one, if any. Default: alerts plus scaling limits, no automatic
   shutdown.
5. **Scaling limits.** "At most how many web and worker instances?" Default: 2-3× the PaaS's
   replica count. This is the cap that actually prevents a runaway bill.
6. **Anomaly alerts.** Default yes, where the cloud offers it (free).

## What a "cap" can mean on each cloud

Budgets lag real spend by several hours, so any automatic action is a backstop, not a precise
limit. Spend can pass the line before it fires.

| | Alerts | Automatic action at a threshold | Structural limits (always on) |
|---|---|---|---|
| **AWS** | `aws.budgets.Budget` notifications (email, SNS) | Budget actions: attach a deny IAM policy or SCP, or stop EC2/RDS instances. For ECS, an SNS-triggered Lambda that sets `desiredCount: 0`. | ECS service `desiredCount` and Application Auto Scaling `maxCapacity`; Fargate vCPU quota; RDS instance class |
| **GCP** | `gcp.billing.Budget` (email to billing admins, monitoring channels, Pub/Sub) | Pub/Sub → Cloud Function that scales Cloud Run to zero or caps `maxInstanceCount`. The documented "disable billing" pattern deletes resources over time; don't offer it for production. | Cloud Run `maxInstanceCount`; per-region quotas you can lower; Cloud SQL tier |
| **Azure** | `consumption.Budget` (email, action group) | Action group → Automation runbook / Function that scales Container Apps to zero. Spending limits exist only on credit-based subscriptions (free trial, Visual Studio) and stop everything when hit. | Container Apps `maxReplicas`; Flexible Server SKU; regional vCPU quota |

Anomaly detection: AWS Cost Anomaly Detection (`aws.costexplorer.AnomalyMonitor` +
`AnomalySubscription`), GCP's built-in cost anomaly alerts, Azure Cost Management anomaly alerts
(subscription scheduled action). Turn on whichever the user said yes to.

## Building it

- Put the budget amount, thresholds and emails in stack config (`budgetMonthlyUsd`,
  `budgetEmails`), not hard-coded, so the user can change them without editing code.
- Make the budget account-, project- or subscription-wide (no tag filter), so resources made by
  hand outside Pulumi still count.
- An automatic action is code that can take production down. Write it, show it to the user, and
  test it on the side hostname by lowering the threshold, then put the threshold back.
- Log retention and registry cleanup policies are cost controls too; set them (see the cloud
  guide).

## Progress line

```
  ✓ 1  Preflight        budget $150/mo · alerts 50/80/100% + forecast → you@… · max 4 web, 2 worker
```
