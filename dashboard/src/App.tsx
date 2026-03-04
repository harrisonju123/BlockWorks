import { useState } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Shell } from "./components/layout/Shell";
import { Dashboard } from "./pages/Dashboard";
import { Events } from "./pages/Events";
import { Alerts } from "./pages/Alerts";
import { Benchmarks } from "./pages/Benchmarks";
import { WasteDetails } from "./pages/WasteDetails";
import { Routing } from "./pages/Routing";
import { MCPTracing } from "./pages/MCPTracing";
import { Attestations } from "./pages/Attestations";
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
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <Shell timeRange={timeRange} onTimeRangeChange={setTimeRange}>
          <Routes>
            <Route path="/" element={<Dashboard timeRange={timeRange} />} />
            <Route path="/events" element={<Events timeRange={timeRange} />} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/benchmarks" element={<Benchmarks timeRange={timeRange} />} />
            <Route path="/waste" element={<WasteDetails timeRange={timeRange} />} />
            <Route path="/routing" element={<Routing />} />
            <Route path="/mcp" element={<MCPTracing timeRange={timeRange} />} />
            <Route path="/attestations" element={<Attestations timeRange={timeRange} />} />
          </Routes>
        </Shell>
      </QueryClientProvider>
    </BrowserRouter>
  );
}
