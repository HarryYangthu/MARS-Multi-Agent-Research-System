import type {
  DiscoveryHypothesis,
  ProximityGraphView,
} from "./types";

const WIDTH = 720;
const HEIGHT = 260;
const COLORS = ["#818cf8", "#a78bfa", "#2dd4bf", "#f59e0b", "#f472b6"];

interface PositionedNode {
  id: string;
  label: string;
  cluster: string;
  x: number;
  y: number;
  color: string;
}

export function ProximityMap({
  graph,
  hypotheses,
}: {
  graph: ProximityGraphView;
  hypotheses: DiscoveryHypothesis[];
}): JSX.Element {
  const labels = new Map(hypotheses.map((item) => [item.hypothesis_id, item.mechanism]));
  const nodes = positionNodes(graph, labels);
  const byId = new Map(nodes.map((node) => [node.id, node]));
  return (
    <div className="overflow-hidden rounded-xl border border-mars-border bg-[#0a0c12]">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={`Proximity graph for round ${graph.round_index}`}
        className="h-auto min-h-56 w-full"
      >
        <defs>
          <radialGradient id={`node-glow-${graph.round_index}`}>
            <stop offset="0%" stopColor="#ffffff" stopOpacity="0.95" />
            <stop offset="100%" stopColor="#818cf8" stopOpacity="0.25" />
          </radialGradient>
        </defs>
        {graph.edges.map((edge) => {
          const left = byId.get(edge.left_id);
          const right = byId.get(edge.right_id);
          if (!left || !right) return null;
          return (
            <g key={`${edge.left_id}:${edge.right_id}`}>
              <line
                x1={left.x}
                y1={left.y}
                x2={right.x}
                y2={right.y}
                stroke={edge.exact_duplicate ? "#fb7185" : "#475569"}
                strokeOpacity={Math.max(0.25, edge.similarity)}
                strokeWidth={edge.exact_duplicate ? 3 : 1 + edge.similarity * 2}
                strokeDasharray={edge.exact_duplicate ? "5 4" : undefined}
              />
              <title>{`Similarity ${edge.similarity.toFixed(2)}${edge.exact_duplicate ? " · exact duplicate" : ""}`}</title>
            </g>
          );
        })}
        {nodes.map((node) => (
          <g key={node.id} transform={`translate(${node.x} ${node.y})`}>
            <circle r="22" fill={node.color} fillOpacity="0.16" stroke={node.color} strokeWidth="1.5" />
            <circle r="5" fill={`url(#node-glow-${graph.round_index})`} />
            <text y="38" textAnchor="middle" fill="#cbd5e1" fontSize="10">
              {truncate(node.label, 18)}
            </text>
            <title>{`${node.label} · ${node.cluster}`}</title>
          </g>
        ))}
      </svg>
    </div>
  );
}

function positionNodes(
  graph: ProximityGraphView,
  labels: Map<string, string>,
): PositionedNode[] {
  const clusterEntries = Object.entries(graph.clusters);
  const columns = Math.max(1, Math.min(3, clusterEntries.length));
  const rows = Math.ceil(clusterEntries.length / columns);
  const nodes: PositionedNode[] = [];
  clusterEntries.forEach(([cluster, ids], clusterIndex) => {
    const column = clusterIndex % columns;
    const row = Math.floor(clusterIndex / columns);
    const centerX = ((column + 0.5) * WIDTH) / columns;
    const centerY = ((row + 0.5) * HEIGHT) / Math.max(1, rows);
    const radius = Math.min(46, 18 + ids.length * 4);
    ids.forEach((id, index) => {
      const angle = ids.length === 1 ? 0 : (index / ids.length) * Math.PI * 2;
      nodes.push({
        id,
        label: labels.get(id) ?? id,
        cluster,
        x: centerX + (ids.length === 1 ? 0 : Math.cos(angle) * radius),
        y: centerY + (ids.length === 1 ? 0 : Math.sin(angle) * radius),
        color: COLORS[clusterIndex % COLORS.length],
      });
    });
  });
  return nodes;
}

function truncate(value: string, limit: number): string {
  return value.length <= limit ? value : `${value.slice(0, limit - 1)}…`;
}
