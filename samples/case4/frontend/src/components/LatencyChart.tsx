import { useEffect, useRef } from "react";
import * as echarts from "echarts";

interface LatencyChartProps {
  pureModelMs?: number;
  pipelineMs?: number;
}

export function LatencyChart({ pureModelMs, pipelineMs }: LatencyChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const chart = echarts.init(container, undefined, { renderer: "svg" });
    const values = [pureModelMs || 0, pipelineMs || 0];
    chart.setOption({
      animation: false,
      grid: { left: 38, right: 18, top: 22, bottom: 34 },
      xAxis: {
        type: "category",
        data: ["纯模型", "完整流水线"],
        axisLine: { lineStyle: { color: "#cbd5e1" } },
        axisLabel: { color: "#536174", fontSize: 15 },
      },
      yAxis: {
        type: "value",
        name: "ms",
        nameTextStyle: { color: "#536174", fontSize: 14 },
        axisLabel: { color: "#536174", fontSize: 14 },
        splitLine: { lineStyle: { color: "#e6ebf0" } },
      },
      series: [
        {
          type: "bar",
          data: values,
          barWidth: 42,
          itemStyle: { color: "#3e6f9c", borderRadius: [4, 4, 0, 0] },
          label: {
            show: true,
            position: "top",
            color: "#1b2635",
            fontSize: 15,
            formatter: (params: { value: number }) => (params.value ? `${params.value.toFixed(1)} ms` : "-"),
          },
        },
      ],
    });
    const resize = () => chart.resize();
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [pureModelMs, pipelineMs]);

  return <div ref={containerRef} className="latency-chart" aria-label="延迟对比图" />;
}
