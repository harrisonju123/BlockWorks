import type { TimeRange } from "../hooks/useStats";
import { StatsBar } from "../components/StatsBar";
import { SpendOverTime } from "../components/charts/SpendOverTime";
import { RequestsOverTime } from "../components/charts/RequestsOverTime";
import { CostDistribution } from "../components/charts/CostDistribution";
import { WasteScore } from "../components/charts/WasteScore";
import { TopTracesTable } from "../components/TopTracesTable";

interface Props {
  timeRange: TimeRange;
}

export function Dashboard({ timeRange }: Props) {
  return (
    <div className="flex flex-col gap-6 max-w-[1600px] mx-auto">
      {/* Row 1: summary stat cards */}
      <StatsBar timeRange={timeRange} />

      {/* Row 2: time-series charts side by side */}
      <div className="grid grid-cols-2 gap-4">
        <SpendOverTime timeRange={timeRange} />
        <RequestsOverTime timeRange={timeRange} />
      </div>

      {/* Row 3: cost breakdown + waste score */}
      <div className="grid grid-cols-3 gap-4">
        {/* Cost distribution gets more real estate since it grows vertically */}
        <div className="col-span-2">
          <CostDistribution timeRange={timeRange} />
        </div>
        <WasteScore timeRange={timeRange} />
      </div>

      {/* Row 4: top traces table — full width */}
      <TopTracesTable timeRange={timeRange} />
    </div>
  );
}
