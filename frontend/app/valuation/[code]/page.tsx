import { ValuationDetailClient } from "./valuation-detail-client";

export const dynamicParams = false;

const INDEX_CODES = ["CSI300", "CSI500", "CSI800", "CHINEXT", "SP500", "NDX100"];

export function generateStaticParams() {
  return INDEX_CODES.map((code) => ({ code }));
}

export default async function ValuationDetailPage({
  params
}: {
  params: Promise<{ code: string }>;
}) {
  const { code } = await params;
  return <ValuationDetailClient code={code} />;
}
