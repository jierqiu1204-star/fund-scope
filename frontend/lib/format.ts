export function formatCurrency(value: number) {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    maximumFractionDigits: 2
  }).format(value);
}

export function formatPercent(value: number) {
  return `${value.toFixed(2)}%`;
}

export function formatDate(value: string | Date | null | undefined) {
  if (!value) {
    return "暂无";
  }

  const date = value instanceof Date ? value : new Date(value);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric"
  }).format(date);
}

export function eventTypeLabel(eventType: string | null | undefined) {
  switch (eventType) {
    case "dividend":
      return "分红";
    case "manager_change":
      return "经理变更";
    case "size_change":
      return "规模变化";
    case "strategy_change":
      return "策略变化";
    default:
      return "其他";
  }
}

export function percentileTone(percentile: number | null | undefined) {
  if (percentile == null) {
    return "bg-stone-200 text-stone-700";
  }
  if (percentile < 30) {
    return "bg-emerald-100 text-emerald-800";
  }
  if (percentile < 70) {
    return "bg-amber-100 text-amber-900";
  }
  return "bg-rose-100 text-rose-800";
}
