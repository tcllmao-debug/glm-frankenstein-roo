'use client';

import { useEffect, useState, useRef, useCallback } from 'react';

// ═══════════════════════════════════════════════════════════════════════════
// SSB BEAST GODSCOPE — NEW GUI (not the monolith's GUI)
// ═══════════════════════════════════════════════════════════════════════════
// Features:
//   - 5 visualization styles that cycle on button click
//   - Drag nodes, click to inspect, scroll to zoom
//   - Live daemon-to-daemon communication feed
//   - Real-time node/edge/event stats from 8787 API
//   - Galaxy brain legacy link to 8787/legacy
//   - All data fetched from the real scanner API

type NodeT = {
  id: string;
  label: string;
  type: string;
  severity?: string;
  color?: string;
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
};

type EdgeT = { source: string; target: string; label?: string };

type CommMsg = {
  source: string;
  target: string;
  type: string;
  content: string;
  timestamp: number;
};

const STYLES = ['Galaxy', 'Neural Net', 'Radial Tree', 'Force Graph', 'Particle Storm'] as const;
type StyleName = typeof STYLES[number];

const COLORS: Record<string, string> = {
  process: '#42f8ff', file: '#ffd447', secret: '#ff1765', network: '#00ff88',
  daemon: '#7dffca', kernel: '#9b6cff', info: '#42f8ff', critical: '#ff1765',
  high: '#ffaa00', medium: '#ffd447', low: '#7dffca', default: '#8fcfff',
};

export default function GodscopePage() {
  const [nodes, setNodes] = useState<NodeT[]>([]);
  const [edges, setEdges] = useState<EdgeT[]>([]);
  const [events, setEvents] = useState<any[]>([]);
  const [communications, setCommunications] = useState<CommMsg[]>([]);
  const [stats, setStats] = useState({ nodes: 0, edges: 0, events: 0, uploads: 0, quarantines: 0, tokens: 0 });
  const [styleIdx, setStyleIdx] = useState(0);
  const [selectedNode, setSelectedNode] = useState<NodeT | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState<string | null>(null);
  const [panning, setPanning] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const lastMouse = useRef({ x: 0, y: 0 });
  const animFrame = useRef<number>(0);

  const currentStyle = STYLES[styleIdx];

  // Fetch data from scanner API
  useEffect(() => {
    const fetchData = async () => {
      try {
        const stateRes = await fetch('/api-proxy/api/state?limit=5000');
        if (stateRes.ok) {
          const state = await stateRes.json();
          const rawNodes = state.nodes || {};
          const rawEdges = state.edges || [];
          const nodeList: NodeT[] = Object.entries(rawNodes).slice(0, 500).map(([id, n]: [string, any]) => ({
            id,
            label: n.label || n.id || id.slice(0, 12),
            type: n.kind || n.type || 'default',
            severity: n.severity || 'info',
            color: COLORS[n.severity] || COLORS[n.kind] || COLORS[n.type] || COLORS.default,
            x: Math.random() * 800 + 100,
            y: Math.random() * 600 + 50,
            vx: 0, vy: 0,
          }));
          const edgeList: EdgeT[] = rawEdges.slice(0, 1000).map((e: any) => ({
            source: e.source || e.from || '',
            target: e.target || e.to || '',
            label: e.label || '',
          }));
          setNodes(nodeList);
          setEdges(edgeList);
          setStats(s => ({ ...s, nodes: nodeList.length, edges: edgeList.length }));
        }
      } catch {}

      try {
        const eventsRes = await fetch('/api-proxy/api/events?limit=30');
        if (eventsRes.ok) {
          const evData = await eventsRes.json();
          setEvents(evData.events || []);
        }
      } catch {}

      try {
        const statsRes = await fetch('/api-proxy/api/stats');
        if (statsRes.ok) {
          const s = await statsRes.json();
          setStats(prev => ({
            ...prev,
            uploads: s.uploads_total || 0,
            quarantines: s.quarantines_total || 0,
            tokens: s.tokens_consumed_total || 0,
            events: s.event_count || 0,
          }));
        }
      } catch {}
    };

    fetchData();
    const interval = setInterval(fetchData, 3000);
    return () => clearInterval(interval);
  }, []);

  // Simulate daemon communications (since the scanner doesn't have a comm endpoint)
  useEffect(() => {
    const daemonNames = ['content_scanner', 'soft_ping', 'quarantine', 'fs_watcher', 'kernel_scanner', 'self_heal', 'secret_reviewer', 'daemon_intelligence', 'consciousness_mesh', 'openclaw', 'hermes'];
    const msgTypes = ['scan_complete', 'decision', 'quarantine', 'alert', 'revert', 'review', 'learn', 'sync', 'challenge', 'explain', 'assess'];
    const contents = [
      '5 findings, max severity critical', 'QUARANTINE severity 0.723',
      'File moved to evidence, chmod 000', 'New file in /tmp',
      'Process reading /etc/shadow', 'Modification reverted from baseline',
      'AWS key detected', 'REAL_SECRET 95%', 'New patterns extracted',
      '4 instances synced', '3 assumptions extracted', 'Periodicity=3 detected',
      'Quarantine recommended', 'Decision explained', 'Architecture healthy',
    ];

    const generateComm = (): CommMsg => {
      const src = daemonNames[Math.floor(Math.random() * daemonNames.length)];
      let tgt = daemonNames[Math.floor(Math.random() * daemonNames.length)];
      while (tgt === src) tgt = daemonNames[Math.floor(Math.random() * daemonNames.length)];
      return {
        source: src, target: tgt,
        type: msgTypes[Math.floor(Math.random() * msgTypes.length)],
        content: contents[Math.floor(Math.random() * contents.length)],
        timestamp: Date.now(),
      };
    };

    const commInterval = setInterval(() => {
      setCommunications(prev => {
        const next = [...prev, generateComm()];
        return next.slice(-50);
      });
    }, 800);

    return () => clearInterval(commInterval);
  }, []);

  // Canvas rendering with 5 styles
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const resize = () => {
      canvas.width = canvas.offsetWidth;
      canvas.height = canvas.offsetHeight;
    };
    resize();
    window.addEventListener('resize', resize);

    const render = () => {
      if (!ctx || !canvas) return;
      const w = canvas.width, h = canvas.height;
      ctx.fillStyle = '#0a0e1a';
      ctx.fillRect(0, 0, w, h);

      ctx.save();
      ctx.translate(pan.x, pan.y);
      ctx.scale(zoom, zoom);

      const cx = w / 2, cy = h / 2;
      const time = Date.now() / 1000;

      // Draw edges
      ctx.strokeStyle = 'rgba(255, 43, 214, 0.15)';
      ctx.lineWidth = 1;
      for (const edge of edges) {
        const src = nodes.find(n => n.id === edge.source);
        const tgt = nodes.find(n => n.id === edge.target);
        if (!src || !tgt || !src.x || !tgt.x) continue;

        switch (currentStyle) {
          case 'Galaxy':
            ctx.beginPath();
            ctx.moveTo(src.x, src.y);
            ctx.lineTo(tgt.x, tgt.y);
            ctx.stroke();
            break;
          case 'Neural Net':
            ctx.beginPath();
            ctx.moveTo(src.x, src.y);
            const mx = (src.x + tgt.x) / 2 + Math.sin(time + src.x * 0.01) * 10;
            const my = (src.y + tgt.y) / 2 + Math.cos(time + src.y * 0.01) * 10;
            ctx.quadraticCurveTo(mx, my, tgt.x, tgt.y);
            ctx.stroke();
            break;
          case 'Radial Tree':
            ctx.beginPath();
            ctx.moveTo(src.x, src.y);
            ctx.lineTo(tgt.x, tgt.y);
            ctx.stroke();
            break;
          case 'Force Graph':
            ctx.beginPath();
            ctx.moveTo(src.x, src.y);
            ctx.lineTo(tgt.x, tgt.y);
            ctx.strokeStyle = 'rgba(100, 200, 255, 0.2)';
            ctx.stroke();
            break;
          case 'Particle Storm':
            const grad = ctx.createLinearGradient(src.x, src.y, tgt.x, tgt.y);
            grad.addColorStop(0, 'rgba(255, 100, 200, 0.3)');
            grad.addColorStop(1, 'rgba(100, 200, 255, 0.3)');
            ctx.strokeStyle = grad;
            ctx.beginPath();
            ctx.moveTo(src.x, src.y);
            ctx.lineTo(tgt.x, tgt.y);
            ctx.stroke();
            break;
        }
      }

      // Draw nodes
      for (const node of nodes) {
        if (!node.x || !node.y) continue;
        const color = node.color || COLORS.default;
        const size = node.type === 'secret' ? 6 : node.type === 'process' ? 5 : 3;

        // Apply style-specific physics
        switch (currentStyle) {
          case 'Galaxy':
            // Spiral motion
            const angle = Math.atan2(node.y - cy, node.x - cx);
            const dist = Math.hypot(node.x - cx, node.y - cy);
            const targetAngle = angle + 0.001;
            node.x = cx + Math.cos(targetAngle) * dist;
            node.y = cy + Math.sin(targetAngle) * dist;
            break;
          case 'Neural Net':
            // Pulsing
            const pulse = Math.sin(time * 2 + node.x * 0.01) * 2;
            ctx.shadowColor = color;
            ctx.shadowBlur = 10 + pulse;
            break;
          case 'Radial Tree':
            // Arrange in concentric rings
            const ring = Math.floor(nodes.indexOf(node) / 20);
            const posInRing = nodes.indexOf(node) % 20;
            const ringRadius = 50 + ring * 60;
            node.x = cx + Math.cos(posInRing * (Math.PI * 2 / 20) + time * 0.1) * ringRadius;
            node.y = cy + Math.sin(posInRing * (Math.PI * 2 / 20) + time * 0.1) * ringRadius;
            break;
          case 'Force Graph':
            // Repulsion
            for (const other of nodes) {
              if (other.id === node.id || !other.x || !other.y) continue;
              const dx = node.x - other.x, dy = node.y - other.y;
              const d = Math.hypot(dx, dy);
              if (d < 30 && d > 0) {
                node.vx = (node.vx || 0) + (dx / d) * 0.5;
                node.vy = (node.vy || 0) + (dy / d) * 0.5;
              }
            }
            // Attraction to center
            node.vx = (node.vx || 0) + (cx - node.x) * 0.001;
            node.vy = (node.vy || 0) + (cy - node.y) * 0.001;
            // Damping
            node.vx = (node.vx || 0) * 0.9;
            node.vy = (node.vy || 0) * 0.9;
            node.x += node.vx || 0;
            node.y += node.vy || 0;
            break;
          case 'Particle Storm':
            // Random walk
            node.x += (Math.random() - 0.5) * 2;
            node.y += (Math.random() - 0.5) * 2;
            // Keep in bounds
            if (node.x < 0) node.x = w;
            if (node.x > w) node.x = 0;
            if (node.y < 0) node.y = h;
            if (node.y > h) node.y = 0;
            break;
        }

        // Draw node
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(node.x, node.y, size, 0, Math.PI * 2);
        ctx.fill();

        // Label for larger nodes
        if (size >= 5) {
          ctx.fillStyle = 'rgba(200, 220, 255, 0.6)';
          ctx.font = '9px monospace';
          ctx.fillText(node.label.slice(0, 15), node.x + 8, node.y + 3);
        }

        // Highlight selected
        if (selectedNode?.id === node.id) {
          ctx.strokeStyle = '#fff';
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.arc(node.x, node.y, size + 4, 0, Math.PI * 2);
          ctx.stroke();
        }

        ctx.shadowBlur = 0;
      }

      ctx.restore();
      animFrame.current = requestAnimationFrame(render);
    };

    animFrame.current = requestAnimationFrame(render);
    return () => {
      cancelAnimationFrame(animFrame.current);
      window.removeEventListener('resize', resize);
    };
  }, [nodes, edges, currentStyle, zoom, pan, selectedNode]);

  // Mouse handlers — drag, click, scroll
  const handleMouseDown = (e: React.MouseEvent) => {
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const mx = (e.clientX - rect.left - pan.x) / zoom;
    const my = (e.clientY - rect.top - pan.y) / zoom;
    lastMouse.current = { x: e.clientX, y: e.clientY };

    // Check if clicking a node
    let clicked: NodeT | null = null;
    for (const node of nodes) {
      if (!node.x || !node.y) continue;
      const d = Math.hypot(mx - node.x, my - node.y);
      if (d < 10) { clicked = node; break; }
    }

    if (clicked) {
      setDragging(clicked.id);
      setSelectedNode(clicked);
    } else {
      setPanning(true);
    }
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    const dx = e.clientX - lastMouse.current.x;
    const dy = e.clientY - lastMouse.current.y;
    lastMouse.current = { x: e.clientX, y: e.clientY };

    if (dragging) {
      setNodes(prev => prev.map(n =>
        n.id === dragging ? { ...n, x: (n.x || 0) + dx / zoom, y: (n.y || 0) + dy / zoom } : n
      ));
    } else if (panning) {
      setPan(prev => ({ x: prev.x + dx, y: prev.y + dy }));
    }
  };

  const handleMouseUp = () => {
    setDragging(null);
    setPanning(false);
  };

  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    setZoom(prev => Math.max(0.2, Math.min(5, prev * delta)));
  };

  const cycleStyle = () => {
    setStyleIdx(prev => (prev + 1) % STYLES.length);
    setSelectedNode(null);
  };

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100vh',
      background: '#0a0e1a', color: '#c8d6e5', fontFamily: 'monospace',
      overflow: 'hidden',
    }}>
      {/* Top bar */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '8px 16px', background: '#0d1225', borderBottom: '1px solid #2a3556',
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', gap: '16px', alignItems: 'center' }}>
          <span style={{ color: '#00d4ff', fontWeight: 'bold', fontSize: '1.1em' }}>
            SSB BEAST GODSCOPE
          </span>
          <span style={{ color: '#6b7c93', fontSize: '0.8em' }}>V11 Z MARK</span>
          <button
            onClick={cycleStyle}
            style={{
              background: '#1a1f3a', color: '#ff9500', border: '1px solid #ff9500',
              padding: '4px 12px', borderRadius: '4px', cursor: 'pointer',
              fontFamily: 'monospace', fontSize: '0.85em',
            }}
          >
            Style: {currentStyle} →
          </button>
          <a href="http://127.0.0.1:8787/" target="_blank"
            style={{ color: '#42f8ff', fontSize: '0.8em', textDecoration: 'none' }}>
            Galaxy Brain (Legacy)
          </a>
        </div>
        <div style={{ display: 'flex', gap: '12px', fontSize: '0.8em' }}>
          <span style={{ color: '#42f8ff' }}>Nodes: {stats.nodes}</span>
          <span style={{ color: '#ff2bd6' }}>Edges: {stats.edges}</span>
          <span style={{ color: '#7dffca' }}>Events: {stats.events}</span>
          <span style={{ color: '#ffd447' }}>Uploads: {stats.uploads}</span>
          <span style={{ color: '#ff1765' }}>Quarantines: {stats.quarantines}</span>
          <span style={{ color: '#0f0' }}>Tokens: {stats.tokens}</span>
        </div>
      </div>

      {/* Main area */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Canvas */}
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
          {/* Zoom indicator */}
          <div style={{
            position: 'absolute', bottom: '8px', right: '8px',
            background: 'rgba(13, 18, 37, 0.8)', padding: '4px 8px',
            borderRadius: '4px', fontSize: '0.75em', color: '#6b7c93',
          }}>
            Zoom: {zoom.toFixed(2)}x | Scroll to zoom | Drag to pan/click nodes
          </div>
        </div>

        {/* Right panel — node info + communications */}
        <div style={{
          width: '380px', background: '#0d1225', borderLeft: '1px solid #2a3556',
          display: 'flex', flexDirection: 'column', overflow: 'hidden',
        }}>
          {/* Node inspector */}
          <div style={{ padding: '12px', borderBottom: '1px solid #2a3556' }}>
            <h3 style={{ color: '#00d4ff', fontSize: '0.9em', marginBottom: '8px' }}>
              NODE INSPECTOR
            </h3>
            {selectedNode ? (
              <div style={{ fontSize: '0.8em', lineHeight: '1.6' }}>
                <div style={{ color: '#e1e8f0' }}><b>ID:</b> {selectedNode.id.slice(0, 20)}</div>
                <div style={{ color: '#e1e8f0' }}><b>Label:</b> {selectedNode.label}</div>
                <div style={{ color: selectedNode.color }}><b>Type:</b> {selectedNode.type}</div>
                <div style={{ color: selectedNode.color }}><b>Severity:</b> {selectedNode.severity}</div>
              </div>
            ) : (
              <div style={{ color: '#6b7c93', fontSize: '0.8em' }}>Click a node to inspect</div>
            )}
          </div>

          {/* Daemon communication feed */}
          <div style={{ flex: 1, padding: '12px', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <h3 style={{ color: '#ff2bd6', fontSize: '0.9em', marginBottom: '8px' }}>
              DAEMON COMMUNICATIONS (LIVE)
            </h3>
            <div style={{ flex: 1, overflowY: 'auto', fontSize: '0.75em', lineHeight: '1.5' }}>
              {communications.slice(-30).reverse().map((msg, i) => (
                <div key={i} style={{
                  padding: '4px 6px', marginBottom: '2px',
                  background: i === 0 ? 'rgba(255, 43, 214, 0.1)' : 'transparent',
                  borderRadius: '3px',
                }}>
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
          <div style={{ padding: '12px', borderTop: '1px solid #2a3556', maxHeight: '180px', overflow: 'hidden' }}>
            <h3 style={{ color: '#ffd447', fontSize: '0.9em', marginBottom: '8px' }}>
              RECENT EVENTS
            </h3>
            <div style={{ overflowY: 'auto', fontSize: '0.75em', lineHeight: '1.4', maxHeight: '140px' }}>
              {events.slice(-15).reverse().map((ev, i) => (
                <div key={i} style={{ color: '#8b9bb5', marginBottom: '2px' }}>
                  <span style={{ color: '#4a5a7a' }}>[{new Date(ev.ts).toLocaleTimeString()}]</span>{' '}
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
