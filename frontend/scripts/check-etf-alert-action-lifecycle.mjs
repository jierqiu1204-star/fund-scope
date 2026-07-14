import { readFileSync } from "node:fs";
import { join } from "node:path";

const page = readFileSync(
  join(process.cwd(), "app", "short-term", "page.tsx"),
  "utf8"
);
const types = readFileSync(join(process.cwd(), "lib", "types.ts"), "utf8");

const requiredStatusLabels = [
  "仅观察",
  "等待数据",
  "建议待确认",
  "已确认，待执行",
  "部分执行",
  "已执行",
  "已过期",
  "已取消",
  "已被更严格建议替代"
];

const checks = [
  {
    name: "declares lifecycle action, execution, and delivery projections",
    pass:
      types.includes("TrackedPositionActionSummary") &&
      types.includes("TrackedPositionActionTransitionRequest") &&
      types.includes("TrackedPositionNotificationDelivery") &&
      types.includes("exit_state_version") &&
      types.includes("action_history")
  },
  {
    name: "renders every owner-facing lifecycle state",
    pass: requiredStatusLabels.every((label) => page.includes(label))
  },
  {
    name: "reuses one lifecycle panel on mobile and desktop",
    pass: (page.match(/renderActionLifecyclePanel\(/g) ?? []).length >= 3
  },
  {
    name: "calls the narrow transition API with a stable idempotency key and current state version",
    pass:
      page.includes("/actions/${request.actionId}/transitions") &&
      page.includes('"Idempotency-Key"') &&
      page.includes("expected_position_state_version") &&
      page.includes("stableActionRequestKey")
  },
  {
    name: "requires complete execution facts before recording a fill",
    pass: [
      "executed_at",
      "quantity",
      "price",
      "price_source",
      "fees",
      "resulting_shares",
      "close_fact"
    ].every((field) => page.includes(field))
  },
  {
    name: "restricts execution price source to the backend allowlist",
    pass:
      types.includes("TrackedPositionExecutionPriceSource") &&
      page.includes("<select") &&
      [
        "owner_reported",
        "broker_confirmation",
        "trade_statement",
        "owner_broker_statement"
      ].every((value) => page.includes(value))
  },
  {
    name: "refreshes owner projections after a stale-version conflict",
    pass:
      page.includes("response?.status === 409") &&
      page.includes("refreshTrackedLifecycle")
  },
  {
    name: "retries an unknown outcome with the exact original payload",
    pass:
      page.includes("actionRequestPayloads") &&
      page.includes("请求结果未知。请勿修改执行事实，直接重试会复用同一请求。")
  },
  {
    name: "shows one action path with reasons, progress, attempts, and expandable technical context",
    pass:
      page.includes("action_history") &&
      page.includes("contributing_rules") &&
      page.includes("remaining_execution_quantity") &&
      page.includes("notification_envelope_id") &&
      page.includes("<details")
  },
  {
    name: "does not present unloaded holding history as empty",
    pass:
      page.includes("历史与通知尝试未加载") &&
      page.includes("当前已加载范围内暂无关联通知尝试")
  },
  {
    name: "counts only immutable notification delivery events as SMTP attempts",
    pass:
      page.includes('audit.alert_type === "notification_delivery"') &&
      page.includes('recordNumber(attempt.decision_context, "attempt_count")')
  },
  {
    name: "distinguishes normalized targets from broker shares",
    pass:
      page.includes("复权归一化目标") && page.includes("请按券商实际份额填写")
  },
  {
    name: "discloses bounded history pages",
    pass:
      page.includes("action_history_next_cursor") &&
      page.includes("trackedAudit.data?.next_cursor") &&
      page.includes("还有更早记录，当前未加载")
  },
  {
    name: "uses evidence-backed SMTP language",
    pass:
      page.includes("SMTP 已接受") &&
      page.includes("发送失败") &&
      page.includes("状态未知") &&
      page.includes("外部邮件可能重复，但不会重复生成仓位动作") &&
      !page.includes("已送达")
  }
];

const failed = checks.filter((check) => !check.pass);
if (failed.length) {
  console.error("ETF alert action lifecycle checks failed:");
  for (const check of failed) {
    console.error(`- ${check.name}`);
  }
  process.exit(1);
}

console.log("ETF alert action lifecycle checks passed.");
