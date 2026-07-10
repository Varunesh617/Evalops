"use client";

import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import type { ParetoPoint } from "@/lib/api";

interface ParetoChartProps {
  frontier: ParetoPoint[];
  dominated?: ParetoPoint[];
}

interface ChartPoint {
  cost: number;
  quality: number;
  label: string;
}

export default function ParetoChart({ frontier, dominated = [] }: ParetoChartProps) {
  const frontierData: ChartPoint[] = frontier.map((p, i) => ({
    cost: p.objectives.cost_usd ?? 0,
    quality: p.objectives.quality_score ?? 0,
    label: `Pareto ${i + 1}`,
  }));

  const dominatedData: ChartPoint[] = dominated.map((p, i) => ({
    cost: p.objectives.cost_usd ?? 0,
    quality: p.objectives.quality_score ?? 0,
    label: `Trial ${i + 1}`,
  }));

  return (
    <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-6">
      <h3 className="text-lg font-semibold text-zinc-900 dark:text-white mb-4">
        Pareto Frontier — Cost vs Quality
      </h3>
      <div className="h-80">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 10, right: 10, bottom: 10, left: 10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" />
            <XAxis
              type="number"
              dataKey="cost"
              name="Cost (USD)"
              tick={{ fontSize: 12, fill: "#71717a" }}
              label={{ value: "Cost (USD)", position: "bottom", offset: -5, fontSize: 12, fill: "#71717a" }}
            />
            <YAxis
              type="number"
              dataKey="quality"
              name="Quality"
              tick={{ fontSize: 12, fill: "#71717a" }}
              label={{ value: "Quality", angle: -90, position: "insideLeft", offset: 10, fontSize: 12, fill: "#71717a" }}
              domain={[0, 1]}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: "#18181b",
                border: "1px solid #27272a",
                borderRadius: "6px",
                color: "#fafafa",
                fontSize: "12px",
              }}
            />
            <Legend />
            {dominatedData.length > 0 && (
              <Scatter
                name="Dominated"
                data={dominatedData}
                fill="#a1a1aa"
                opacity={0.5}
              />
            )}
            <Scatter
              name="Pareto Optimal"
              data={frontierData}
              fill="#22c55e"
            />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
