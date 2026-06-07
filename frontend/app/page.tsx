"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

export default function HomePage() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/short-term");
  }, [router]);

  return <div className="py-20 text-center text-sm text-ink/60">正在进入短线研究...</div>;
}
