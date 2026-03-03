import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Shell } from "./components/layout/Shell";
import { Dashboard } from "./pages/Dashboard";
import type { TimeRange } from "./hooks/useStats";

// Single QueryClient instance for the app lifetime. 30s stale time is set
// per-query in the hooks; here we just disable the default retry noise for
// 4xx errors since those indicate a real problem.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        // Don't retry on 4xx — the API is telling us something meaningful
        if (error instanceof Error && error.message.includes("API error: 4")) {
          return false;
        }
        return failureCount < 2;
      },
    },
  },
});

export default function App() {
  const [timeRange, setTimeRange] = useState<TimeRange>("24h");

  return (
    <QueryClientProvider client={queryClient}>
      <Shell timeRange={timeRange} onTimeRangeChange={setTimeRange}>
        <Dashboard timeRange={timeRange} />
      </Shell>
    </QueryClientProvider>
  );
}
