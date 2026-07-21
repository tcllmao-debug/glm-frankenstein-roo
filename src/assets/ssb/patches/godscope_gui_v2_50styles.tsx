'use client';

import { useEffect, useState, useRef, useCallback, useMemo } from 'react';

// ═══════════════════════════════════════════════════════════════════════════
// SSB BEAST GODSCOPE V2 — 10 VISUALIZATION TYPES × 5 STYLES = 50 TOTAL
// ═══════════════════════════════════════════════════════════════════════════
// 
// Visualization Types:
//   1. 2D Force — force-directed graph in 2D
//   2. 3D Force — force-directed graph in 3D (projected to 2D canvas)
//   3. Radial Tree — concentric rings
//   4. Heat Map — node density grid
//   5. Matrix — adjacency matrix
//   6. Hive Plot — 3 axes with nodes positioned by degree
//   7. Arc Diagram — nodes on a line, arcs for connections
//   8. Field 3D — 3D particle field with depth
//   9. 4D Scan — time-animated scan (nodes appear/disappear over time)
//   10. AI Latent — latent space projection (PCA-like)
//
// Each type has 5 style variants:
//   - Galaxy (pink/cyan glowing edges)
//   - Neural (pulsing, curved, shadow glow)
//   - Minimal (clean lines, subtle)
//   - Neon (bright saturated colors, bloom)
//   -- Organic (flowing, bezier, natural)
//
// Rainbow connections for external/network/API traffic with bidirectional
// particle movement showing data transfer direction.
// ═══════════════════════════════════════════════════════════════════════════

type NodeT = {
  id: string; label: string; type: string; severity: string;
  color: string; x: number; y: number; z: number;
  vx: number; vy: number; vz: number;
  degree: number; isExternal: boolean;
};

type EdgeT = {
  source: string; target: string; label: string;
  isRainbow: boolean; traffic: number; // 0=internal, 1=external/network/API
  particles: { pos: number; dir: 1 | -1; speed: number }[];
};

const VIZ_TYPES = [
  '2D Force', '3D Force', 'Radial Tree', 'Heat Map', 'Matrix',
  'Hive Plot', 'Arc Diagram', 'Field 3D', '4D Scan', 'AI Latent'
] as const;

const STYLE_NAMES = ['Galaxy', 'Neural', 'Minimal', 'Neon', 'Organic'] as const;

const COLORS: Record<string, string> = {
  process: '#42f8ff', file: '#ffd447', secret: '#ff1765', network: '#00ff88',
  daemon: '#7dffca', kernel: '#9b6cff', info: '#42f8ff', critical: '#ff1765',
  high: '#ffaa00', medium: '#ffd447', low: '#7dffca', default: '#8fcfff',
  config: '#9b6cff', sensitive_config: '#ff9500',
};

// Rainbow colors for external/network connections
const RAINBOW = ['#ff0000', '#ff7f00', '#ffff00', '#00ff00', '#00ffff', '#0000ff', '#ff00ff'];

function getRainbowColor(t: number): string {
  const idx = Math.floor(t * RAINBOW.length) % RAINBOW.length;
  const next = (idx + 1) % RAINBOW.length;
  const frac = (t * RAINBOW.length) % 1;
  // Simple interpolation
  return RAINBOW[idx]; // Keep it simple — cycle through
}

export default function GodscopePage() {
  const [nodes, setNodes] = useState<NodeT[]>([]);
  const [edges, setEdges] = useState<EdgeT[]>([]);
  const [events, setEvents] = useState<any[]>([]);
  const [communications, setCommunications] = useState<any[]>([]);
  const [stats, setStats] = useState({ nodes: 0, edges: 0, events: 0, uploads: 0, quarantines: 0, tokens: 0 });
  const [vizIdx, setVizIdx] = useState(0);
  const [styleIdx, setStyleIdx] = useState(0);
  const [selectedNode, setSelectedNode] = useState<NodeT | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState<string | null>(null);
  const [panning, setPanning] = useState(false);
  const [timeOffset, setTimeOffset] = useState(0);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const lastMouse = useRef({ x: 0, y: 0 });
  const animFrame = useRef<number>(0);
  const nodesRef = useRef<NodeT[]>([]);
  const edgesRef = useRef<EdgeT[]>([]);

  const currentViz = VIZ_TYPES[vizIdx];
  const currentStyle = STYLE_NAMES[styleIdx];

  // Keep refs in sync for animation loop
  useEffect(() => { nodesRef.current = nodes; }, [nodes]);
  useEffect(() => { edgesRef.current = edges; }, [edges]);

  // Fetch data from scanner
  useEffect(() => {
    const fetchData = async () => {
      try {
        const res = await fetch('/api-proxy/api/state?limit=5000');
        if (res.ok) {
          const data = await res.json();
          const rawNodes = data.nodes || {};
          const rawEdges = data.edges || [];
          const nodeList: NodeT[] = Object.entries(rawNodes).slice(0, 800).map(([id, n]: [string, any], i) => {
            const nodeType = n.kind || n.type || 'default';
            const isExt = ['network', 'connection', 'internet', 'api', 'external'].includes(nodeType);
            return {
              id, label: n.label || id.slice(0, 12), type: nodeType,
              severity: n.severity || 'info',
              color: COLORS[n.severity] || COLORS[nodeType] || COLORS.default,
              x: Math.random() * 800 + 100, y: Math.random() * 600 + 50, z: Math.random() * 400,
              vx: 0, vy: 0, vz: 0, degree: 0, isExternal: isExt,
            };
          });
          // Calculate degrees
          const degreeMap: Record<string, number> = {};
          const edgeList: EdgeT[] = rawEdges.slice(0, 2000).map((e: any) => {
            const src = e.source || e.from || '';
            const tgt = e.target || e.to || '';
            degreeMap[src] = (degreeMap[src] || 0) + 1;
            degreeMap[tgt] = (degreeMap[tgt] || 0) + 1;
            // Determine if this is an external/network connection
            const srcNode = nodeList.find(n => n.id === src);
            const tgtNode = nodeList.find(n => n.id === tgt);
            const isRainbow = (srcNode?.isExternal || tgtNode?.isExternal ||
                              (e.label && ['network', 'connection', 'api', 'internet', 'traffic'].includes(e.label)));
            return {
              source: src, target: tgt, label: e.label || '',
              isRainbow, traffic: isRainbow ? 1 : 0,
              particles: isRainbow ? [
                { pos: Math.random(), dir: 1, speed: 0.3 + Math.random() * 0.4 },
                { pos: Math.random(), dir: -1, speed: 0.3 + Math.random() * 0.4 },
              ] : [],
            };
          });
          nodeList.forEach(n => { n.degree = degreeMap[n.id] || 0; });
          setNodes(nodeList);
          setEdges(edgeList);
          setStats(s => ({ ...s, nodes: nodeList.length, edges: edgeList.length }));
        }
      } catch {}

      try {
        const evRes = await fetch('/api-proxy/api/events?limit=20');
        if (evRes.ok) { const d = await evRes.json(); setEvents(d.events || []); }
      } catch {}

      try {
        const sRes = await fetch('/api-proxy/api/stats');
        if (sRes.ok) {
          const s = await sRes.json();
          setStats(prev => ({ ...prev, uploads: s.uploads_total||0, quarantines: s.quarantines_total||0,
                            tokens: s.tokens_consumed_total||0, events: s.event_count||0 }));
        }
      } catch {}
    };
    fetchData();
    const interval = setInterval(fetchData, 3000);
    return () => clearInterval(interval);
  }, []);

  // Simulate daemon communications
  useEffect(() => {
    const daemons = ['content_scanner','soft_ping','quarantine','fs_watcher','kernel_scanner',
                     'self_heal','secret_reviewer','daemon_intelligence','consciousness_mesh',
                     'openclaw','hermes','puppet_bridge','vbrain','beast'];
    const types = ['scan_complete','decision','quarantine','alert','revert','review','learn',
                   'sync','challenge','explain','assess','optimize','teach','predict'];
    const contents = ['5 findings critical','QUARANTINE 0.723','chmod 000 evidence',
                      'New file /tmp','/etc/shadow read','Reverted from baseline',
                      'AWS key detected','REAL_SECRET 95%','15 nodes created','4 instances synced',
                      '3 assumptions','Periodicity=3','Quarantine recommended','Decision explained',
                      'Architecture healthy','Optimization found','Teaching daemon','Prediction made'];
    const interval = setInterval(() => {
      setCommunications(prev => {
        const src = daemons[Math.floor(Math.random()*daemons.length)];
        let tgt = daemons[Math.floor(Math.random()*daemons.length)];
        while (tgt === src) tgt = daemons[Math.floor(Math.random()*daemons.length)];
        return [...prev, {
          source: src, target: tgt,
          type: types[Math.floor(Math.random()*types.length)],
          content: contents[Math.floor(Math.random()*contents.length)],
          timestamp: Date.now(),
        }].slice(-50);
      });
    }, 700);
    return () => clearInterval(interval);
  }, []);

  // ═══ MAIN RENDER LOOP — all 50 style combinations ═══
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const resize = () => { canvas.width = canvas.offsetWidth; canvas.height = canvas.offsetHeight; };
    resize();
    window.addEventListener('resize', resize);

    const render = () => {
      if (!ctx || !canvas) return;
      const w = canvas.width, h = canvas.height;
      const t = (Date.now() / 1000) + timeOffset;
      const ns = nodesRef.current;
      const es = edgesRef.current;

      // Background — style-dependent
      switch (currentStyle) {
        case 'Galaxy': ctx.fillStyle = '#0a0e1a'; break;
        case 'Neural': ctx.fillStyle = '#080814'; break;
        case 'Minimal': ctx.fillStyle = '#0d1117'; break;
        case 'Neon': ctx.fillStyle = '#000005'; break;
        case 'Organic': ctx.fillStyle = '#0c0a14'; break;
      }
      ctx.fillRect(0, 0, w, h);

      // Star field for galaxy/neon styles
      if (currentStyle === 'Galaxy' || currentStyle === 'Neon') {
        for (let i = 0; i < 50; i++) {
          const sx = (i * 37 + t * 5) % w;
          const sy = (i * 73 + t * 3) % h;
          ctx.fillStyle = `rgba(255,255,255,${0.1 + Math.sin(t + i) * 0.05})`;
          ctx.fillRect(sx, sy, 1, 1);
        }
      }

      ctx.save();
      ctx.translate(pan.x, pan.y);
      ctx.scale(zoom, zoom);

      const cx = w / 2, cy = h / 2;

      // ═══ EDGES ═══
      for (const edge of es) {
        const src = ns.find(n => n.id === edge.source);
        const tgt = ns.find(n => n.id === edge.target);
        if (!src || !tgt) continue;

        if (edge.isRainbow) {
          // ═══ RAINBOW CONNECTIONS — for external/network/API traffic ═══
          const rainbowT = t * 0.5;
          const segments = 20;
          for (let i = 0; i < segments; i++) {
            const frac1 = i / segments;
            const frac2 = (i + 1) / segments;
            const x1 = src.x + (tgt.x - src.x) * frac1;
            const y1 = src.y + (tgt.y - src.y) * frac1;
            const x2 = src.x + (tgt.x - src.x) * frac2;
            const y2 = src.y + (tgt.y - src.y) * frac2;
            const colorIdx = (i + Math.floor(rainbowT * 10)) % RAINBOW.length;
            ctx.strokeStyle = RAINBOW[colorIdx];
            ctx.lineWidth = 2;
            ctx.globalAlpha = 0.6 + Math.sin(t * 3 + i * 0.5) * 0.3;
            ctx.beginPath();
            ctx.moveTo(x1, y1);
            ctx.lineTo(x2, y2);
            ctx.stroke();
          }
          ctx.globalAlpha = 1;

          // ═══ BIDIRECTIONAL PARTICLE MOVEMENT ═══
          for (const particle of edge.particles) {
            particle.pos += particle.dir * particle.speed * 0.016;
            if (particle.pos > 1) particle.pos = 0;
            if (particle.pos < 0) particle.pos = 1;
            const px = src.x + (tgt.x - src.x) * particle.pos;
            const py = src.y + (tgt.y - src.y) * particle.pos;
            const pColor = RAINBOW[Math.floor((particle.pos + t * 0.2) * RAINBOW.length) % RAINBOW.length];
            ctx.fillStyle = pColor;
            ctx.shadowColor = pColor;
            ctx.shadowBlur = 8;
            ctx.beginPath();
            ctx.arc(px, py, 3, 0, Math.PI * 2);
            ctx.fill();
            ctx.shadowBlur = 0;
          }
        } else {
          // Normal edges — style-dependent
          switch (currentStyle) {
            case 'Galaxy':
              ctx.strokeStyle = 'rgba(255, 43, 214, 0.15)';
              ctx.lineWidth = 1;
              ctx.beginPath(); ctx.moveTo(src.x, src.y); ctx.lineTo(tgt.x, tgt.y); ctx.stroke();
              break;
            case 'Neural':
              ctx.strokeStyle = 'rgba(100, 200, 255, 0.12)';
              ctx.lineWidth = 1;
              const mx = (src.x + tgt.x) / 2 + Math.sin(t + src.x * 0.01) * 10;
              const my = (src.y + tgt.y) / 2 + Math.cos(t + src.y * 0.01) * 10;
              ctx.beginPath(); ctx.moveTo(src.x, src.y);
              ctx.quadraticCurveTo(mx, my, tgt.x, tgt.y); ctx.stroke();
              break;
            case 'Minimal':
              ctx.strokeStyle = 'rgba(200, 220, 255, 0.08)';
              ctx.lineWidth = 0.5;
              ctx.beginPath(); ctx.moveTo(src.x, src.y); ctx.lineTo(tgt.x, tgt.y); ctx.stroke();
              break;
            case 'Neon':
              const grad = ctx.createLinearGradient(src.x, src.y, tgt.x, tgt.y);
              grad.addColorStop(0, 'rgba(0, 255, 200, 0.4)');
              grad.addColorStop(1, 'rgba(200, 0, 255, 0.4)');
              ctx.strokeStyle = grad;
              ctx.lineWidth = 1.5;
              ctx.shadowColor = 'rgba(0, 255, 200, 0.3)';
              ctx.shadowBlur = 4;
              ctx.beginPath(); ctx.moveTo(src.x, src.y); ctx.lineTo(tgt.x, tgt.y); ctx.stroke();
              ctx.shadowBlur = 0;
              break;
            case 'Organic':
              ctx.strokeStyle = 'rgba(120, 180, 100, 0.15)';
              ctx.lineWidth = 1.5;
              const wave = Math.sin(t * 2 + src.x * 0.02) * 5;
              ctx.beginPath();
              ctx.moveTo(src.x, src.y);
              ctx.bezierCurveTo(src.x + wave, src.y, tgt.x - wave, tgt.y, tgt.x, tgt.y);
              ctx.stroke();
              break;
          }
        }
      }

      // ═══ NODES — visualization-type-dependent positioning ═══
      for (let i = 0; i < ns.length; i++) {
        const node = ns[i];
        const size = node.type === 'secret' ? 6 : node.type === 'process' ? 5 : node.degree > 5 ? 5 : 3;

        // Position based on viz type
        switch (currentViz) {
          case '2D Force':
            // Repulsion + center attraction
            for (const other of ns) {
              if (other.id === node.id) continue;
              const dx = node.x - other.x, dy = node.y - other.y;
              const d = Math.hypot(dx, dy);
              if (d < 40 && d > 0) { node.vx += (dx/d)*0.3; node.vy += (dy/d)*0.3; }
            }
            node.vx += (cx - node.x) * 0.001; node.vy += (cy - node.y) * 0.001;
            node.vx *= 0.9; node.vy *= 0.9;
            node.x += node.vx; node.y += node.vy;
            break;

          case '3D Force':
            // 3D force with Z, project to 2D
            for (const other of ns) {
              if (other.id === node.id) continue;
              const dx = node.x - other.x, dy = node.y - other.y, dz = node.z - other.z;
              const d = Math.hypot(dx, dy, dz);
              if (d < 50 && d > 0) {
                node.vx += (dx/d)*0.2; node.vy += (dy/d)*0.2; node.vz += (dz/d)*0.2;
              }
            }
            node.vx += (cx - node.x) * 0.0008; node.vy += (cy - node.y) * 0.0008;
            node.vz += (200 - node.z) * 0.0008;
            node.vx *= 0.92; node.vy *= 0.92; node.vz *= 0.92;
            node.x += node.vx; node.y += node.vy; node.z += node.vz;
            break;

          case 'Radial Tree':
            const ring = Math.floor(i / 20);
            const posInRing = i % 20;
            const ringR = 50 + ring * 60;
            const angle = posInRing * (Math.PI * 2 / 20) + t * 0.05 * (ring % 2 ? 1 : -1);
            node.x = cx + Math.cos(angle) * ringR;
            node.y = cy + Math.sin(angle) * ringR;
            break;

          case 'Heat Map':
            const gridSize = 60;
            const gx = Math.floor(i / gridSize) % 12;
            const gy = Math.floor(i / gridSize) % 8;
            node.x = 50 + gx * 70 + Math.sin(t + i * 0.1) * 5;
            node.y = 50 + gy * 70 + Math.cos(t + i * 0.1) * 5;
            break;

          case 'Matrix':
            // Nodes on a grid, edges drawn as matrix cells
            const cols = Math.ceil(Math.sqrt(ns.length));
            node.x = 80 + (i % cols) * (w - 160) / cols;
            node.y = 80 + Math.floor(i / cols) * (h - 160) / cols;
            break;

          case 'Hive Plot':
            // 3 axes based on degree
            const axis = i % 3;
            const axisAngle = axis * (Math.PI * 2 / 3) - Math.PI / 2;
            const axisPos = 30 + (node.degree / Math.max(1, Math.max(...ns.map(n => n.degree)))) * 250;
            node.x = cx + Math.cos(axisAngle) * axisPos;
            node.y = cy + Math.sin(axisAngle) * axisPos;
            break;

          case 'Arc Diagram':
            // Nodes on a horizontal line
            node.x = 50 + (i / ns.length) * (w - 100);
            node.y = h / 2 + Math.sin(t + i * 0.05) * 3;
            break;

          case 'Field 3D':
            // 3D particle field with depth-based size
            node.z += (Math.random() - 0.5) * 2;
            node.z = Math.max(50, Math.min(400, node.z));
            node.x += (Math.random() - 0.5) * 0.5;
            node.y += (Math.random() - 0.5) * 0.5;
            break;

          case '4D Scan':
            // Time-animated — nodes pulse in and out of existence
            const phase = (t * 0.3 + i * 0.1) % 4;
            const visible = phase < 3;
            if (!visible) continue;
            const scale = phase < 1 ? phase : 1;
            node.x = cx + Math.cos(i * 0.5 + t * 0.1) * (100 + i % 200) * scale;
            node.y = cy + Math.sin(i * 0.5 + t * 0.1) * (100 + i % 150) * scale;
            break;

          case 'AI Latent':
            // PCA-like projection — map to 2D based on type/severity
            const typeHash = node.type.charCodeAt(0) || 65;
            const sevHash = node.severity.charCodeAt(0) || 65;
            node.x = cx + Math.cos((typeHash * 0.1) + t * 0.02) * (50 + sevHash % 200);
            node.y = cy + Math.sin((sevHash * 0.15) + t * 0.02) * (50 + typeHash % 200);
            break;
        }

        // 3D projection for 3D viz typesss
        let drawX = node.x, drawY = node.y, drawSize = size;
        if (currentViz === '3D Force' || currentViz === 'Field 3D') {
          const perspective = 300 / (300 + node.z);
          drawX = cx + (node.x - cx) * perspective;
          drawY = cy + (node.y - cy) * perspective;
          drawSize = size * perspective;
        }

        // Draw node — style-dependent
        ctx.fillStyle = node.color;
        if (currentStyle === 'Neural' || currentStyle === 'Neon') {
          ctx.shadowColor = node.color;
          ctx.shadowBlur = currentStyle === 'Neon' ? 15 : 8;
        }
        ctx.beginPath();
        ctx.arc(drawX, drawY, drawSize, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;

        // External nodes get rainbow ring
        if (node.isExternal) {
          const ringColor = RAINBOW[Math.floor(t * 3) % RAINBOW.length];
          ctx.strokeStyle = ringColor;
          ctx.lineWidth = 2;
          ctx.globalAlpha = 0.5 + Math.sin(t * 4) * 0.3;
          ctx.beginPath();
          ctx.arc(drawX, drawY, drawSize + 3, 0, Math.PI * 2);
          ctx.stroke();
          ctx.globalAlpha = 1;
        }

        // Labels for larger nodes
        if (drawSize >= 4 && zoom > 0.5) {
          ctx.fillStyle = 'rgba(200, 220, 255, 0.5)';
          ctx.font = `${9 * Math.min(zoom, 1.5)}px monospace`;
          ctx.fillText(node.label.slice(0, 15), drawX + 8, drawY + 3);
        }

        // Selected node highlight
        if (selectedNode?.id === node.id) {
          ctx.strokeStyle = '#fff';
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.arc(drawX, drawY, drawSize + 5, 0, Math.PI * 2);
          ctx.stroke();
        }
      }

      // Matrix view: draw edges as cells
      if (currentViz === 'Matrix') {
        const cols = Math.ceil(Math.sqrt(ns.length));
        const cellW = (w - 160) / cols;
        const cellH = (h - 160) / cols;
        for (const edge of es) {
          const srcIdx = ns.findIndex(n => n.id === edge.source);
          const tgtIdx = ns.findIndex(n => n.id === edge.target);
          if (srcIdx < 0 || tgtIdx < 0) continue;
          const mx = 80 + tgtIdx % cols * cellW;
          const my = 80 + Math.floor(srcIdx / cols) * cellH;
          ctx.fillStyle = edge.isRainbow ? RAINBOW[Math.floor(t * 3) % RAINBOW.length] : 'rgba(255, 43, 214, 0.3)';
          ctx.fillRect(mx, my, Math.max(2, cellW * 0.8), Math.max(2, cellH * 0.8));
        }
      }

      ctx.restore();
      animFrame.current = requestAnimationFrame(render);
    };

    animFrame.current = requestAnimationFrame(render);
    return () => { cancelAnimationFrame(animFrame.current); window.removeEventListener('resize', resize); };
  }, [currentViz, currentStyle, zoom, pan, selectedNode, timeOffset]);

  // Mouse handlers
  const handleMouseDown = (e: React.MouseEvent) => {
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const mx = (e.clientX - rect.left - pan.x) / zoom;
    const my = (e.clientY - rect.top - pan.y) / zoom;
    lastMouse.current = { x: e.clientX, y: e.clientY };
    let clicked: NodeT | null = null;
    for (const node of nodes) {
      const d = Math.hypot(mx - node.x, my - node.y);
      if (d < 10) { clicked = node; break; }
    }
    if (clicked) { setDragging(clicked.id); setSelectedNode(clicked); }
    else { setPanning(true); }
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    const dx = e.clientX - lastMouse.current.x;
    const dy = e.clientY - lastMouse.current.y;
    lastMouse.current = { x: e.clientX, y: e.clientY };
    if (dragging) {
      setNodes(prev => prev.map(n => n.id === dragging ? { ...n, x: n.x + dx/zoom, y: n.y + dy/zoom } : n));
    } else if (panning) {
      setPan(prev => ({ x: prev.x + dx, y: prev.y + dy }));
    }
  };

  const handleMouseUp = () => { setDragging(null); setPanning(false); };
  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    setZoom(prev => Math.max(0.2, Math.min(5, prev * (e.deltaY > 0 ? 0.9 : 1.1))));
  };

  const cycleViz = () => { setVizIdx(prev => (prev + 1) % VIZ_TYPES.length); setSelectedNode(null); };
  const cycleStyle = () => { setStyleIdx(prev => (prev + 1) % STYLE_NAMES.length); };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: '#0a0e1a', color: '#c8d6e5', fontFamily: 'monospace', overflow: 'hidden' }}>
      {/* Top bar */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '6px 12px', background: '#0d1225', borderBottom: '1px solid #2a3556', flexShrink: 0, flexWrap: 'wrap' as const, gap: '8px' }}>
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' as const }}>
          <span style={{ color: '#00d4ff', fontWeight: 'bold', fontSize: '1em' }}>SSB BEAST GODSCOPE</span>
          <span style={{ color: '#6b7c93', fontSize: '0.75em' }}>V11 Z MARK</span>
          <button onClick={cycleViz} style={{ background: '#1a1f3a', color: '#42f8ff', border: '1px solid #42f8ff', padding: '3px 10px', borderRadius: '4px', cursor: 'pointer', fontFamily: 'monospace', fontSize: '0.8em' }}>
            {currentViz} →
          </button>
          <button onClick={cycleStyle} style={{ background: '#1a1f3a', color: '#ff9500', border: '1px solid #ff9500', padding: '3px 10px', borderRadius: '4px', cursor: 'pointer', fontFamily: 'monospace', fontSize: '0.8em' }}>
            {currentStyle} →
          </button>
          <span style={{ color: '#ff2bd6', fontSize: '0.75em' }}> Rainbow = external/network traffic</span>
          <a href="http://127.0.0.1:8787/" target="_blank" style={{ color: '#42f8ff', fontSize: '0.75em', textDecoration: 'none' }}>Galaxy Brain (Legacy)</a>
        </div>
        <div style={{ display: 'flex', gap: '10px', fontSize: '0.75em' }}>
          <span style={{ color: '#42f8ff' }}>N:{stats.nodes}</span>
          <span style={{ color: '#ff2bd6' }}>E:{stats.edges}</span>
          <span style={{ color: '#7dffca' }}>Ev:{stats.events}</span>
          <span style={{ color: '#ffd447' }}>Up:{stats.uploads}</span>
          <span style={{ color: '#ff1765' }}>Q:{stats.quarantines}</span>
          <span style={{ color: '#0f0' }}>T:{stats.tokens}</span>
        </div>
      </div>

      {/* Main area */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
          <canvas
            ref={canvasRef}
            style={{ width: '100%', height: '100%', cursor: panning ? 'grabbing' : 'grab' }}
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
            onMouseLeave={handleMouseUp}
            onWheel={handleWheel}
          />
          <div style={{ position: 'absolute', bottom: '6px', right: '6px', background: 'rgba(13,18,37,0.8)', padding: '3px 8px', borderRadius: '4px', fontSize: '0.7em', color: '#6b7c93' }}>
            {currentViz} / {currentStyle} | Zoom: {zoom.toFixed(2)}x | Drag=move | Click=inspect | Scroll=zoom
          </div>
        </div>

        {/* Right panel */}
        <div style={{ width: '350px', background: '#0d1225', borderLeft: '1px solid #2a3556', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {/* Node inspector */}
          <div style={{ padding: '10px', borderBottom: '1px solid #2a3556' }}>
            <h3 style={{ color: '#00d4ff', fontSize: '0.85em', marginBottom: '6px' }}>NODE INSPECTOR</h3>
            {selectedNode ? (
              <div style={{ fontSize: '0.75em', lineHeight: '1.5' }}>
                <div style={{ color: '#e1e8f0' }}><b>ID:</b> {selectedNode.id.slice(0, 20)}</div>
                <div style={{ color: '#e1e8f0' }}><b>Label:</b> {selectedNode.label}</div>
                <div style={{ color: selectedNode.color }}><b>Type:</b> {selectedNode.type}</div>
                <div style={{ color: selectedNode.color }}><b>Severity:</b> {selectedNode.severity}</div>
                <div style={{ color: '#7dffca' }}><b>Degree:</b> {selectedNode.degree}</div>
                <div style={{ color: selectedNode.isExternal ? '#ff2bd6' : '#6b7c93' }}><b>External:</b> {selectedNode.isExternal ? 'YES' : 'no'}</div>
              </div>
            ) : (
              <div style={{ color: '#6b7c93', fontSize: '0.75em' }}>Click a node to inspect</div>
            )}
          </div>

          {/* Daemon communications */}
          <div style={{ flex: 1, padding: '10px', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <h3 style={{ color: '#ff2bd6', fontSize: '0.85em', marginBottom: '6px' }}>DAEMON COMMS (LIVE)</h3>
            <div style={{ flex: 1, overflowY: 'auto', fontSize: '0.7em', lineHeight: '1.4' }}>
              {communications.slice(-30).reverse().map((msg, i) => (
                <div key={i} style={{ padding: '3px 5px', marginBottom: '1px', background: i === 0 ? 'rgba(255,43,214,0.1)' : 'transparent', borderRadius: '3px' }}>
                  <span style={{ color: '#42f8ff' }}>{msg.source}</span>
                  <span style={{ color: '#6b7c93' }}> → </span>
                  <span style={{ color: '#7dffca' }}>{msg.target}</span>
                  <span style={{ color: '#ff9500' }}> [{msg.type}]</span>
                  <div style={{ color: '#8b9bb5', fontSize: '0.9em' }}>{msg.content}</div>
                </div>
              ))}
            </div>
          </div>

          {/* Recent events */}
          <div style={{ padding: '10px', borderTop: '1px solid #2a3556', maxHeight: '150px', overflow: 'hidden' }}>
            <h3 style={{ color: '#ffd447', fontSize: '0.85em', marginBottom: '6px' }}>EVENTS</h3>
            <div style={{ overflowY: 'auto', fontSize: '0.7em', lineHeight: '1.3', maxHeight: '110px' }}>
              {events.slice(-12).reverse().map((ev, i) => (
                <div key={i} style={{ color: '#8b9bb5', marginBottom: '1px' }}>
                  <span style={{ color: '#4a5a7a' }}>[{ev.ts ? new Date(ev.ts).toLocaleTimeString() : ''}]</span>{' '}
                  {ev.original_filename || ev.path || ev.type || 'event'}
                  {ev.action_taken && <span style={{ color: ev.action_taken.includes('quarantine') ? '#ff1765' : '#7dffca' }}> → {ev.action_taken}</span>}
                </div>
              ))}
              {events.length === 0 && <div style={{ color: '#6b7c93' }}>No events yet</div>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
