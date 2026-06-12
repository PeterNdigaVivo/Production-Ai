import KpiGrid from "@/components/KpiGrid";
import LiveEventFeed from "@/components/LiveEventFeed";

export default function Page() {
  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-semibold">Executive Dashboard</h2>
      <KpiGrid />
      <LiveEventFeed />
    </div>
  );
}
