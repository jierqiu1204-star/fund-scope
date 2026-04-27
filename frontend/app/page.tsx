"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { api } from "@/lib/api";
import type { TransactionList } from "@/lib/types";

export default function HomePage() {
  const router = useRouter();
  const transactions = useQuery({
    queryKey: ["transactions", "root-check"],
    queryFn: async () => (await api.get<TransactionList>("/api/transactions")).data
  });

  useEffect(() => {
    if (!transactions.data) {
      return;
    }
    if (transactions.data.total === 0) {
      router.replace("/onboarding");
      return;
    }
    router.replace("/portfolio");
  }, [router, transactions.data]);

  return <div className="py-20 text-center text-sm text-ink/60">Preparing your investing workspace…</div>;
}
