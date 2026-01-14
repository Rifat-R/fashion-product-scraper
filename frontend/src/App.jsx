import { useEffect, useRef, useState } from "react";
import "./App.css";

const POLL_INTERVAL_MS = 1000;

function App() {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [scanId, setScanId] = useState(null);
  const [status, setStatus] = useState(null);
  const [sitesTotal, setSitesTotal] = useState(0);
  const [sitesDone, setSitesDone] = useState(0);
  const [total, setTotal] = useState(0);
  const [logs, setLogs] = useState([]);
  const pollRef = useRef(null);
  const scanIdRef = useRef(null);

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const fetchStatus = async () => {
    const currentScanId = scanIdRef.current;
    if (!currentScanId) return;
    const url = new URL("/api/scan/status", window.location.origin);
    url.searchParams.set("scan_id", currentScanId);

    try {
      const response = await fetch(url);
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || "Status failed");
      }
      const data = await response.json();
      setStatus(data.status || null);
      setSitesTotal(data.sites_total || 0);
      setSitesDone(data.sites_done || 0);
      setLogs(data.logs || []);
      setTotal(data.total || 0);
      if (data.status === "complete") {
        stopPolling();
        setLoading(false);
      }
    } catch (err) {
      setError(err.message);
      stopPolling();
      setLoading(false);
    }
  };

  const startScan = async () => {
    if (!query.trim()) {
      setError("Enter a product keyword to scan.");
      return;
    }
    setLoading(true);
    setError(null);
    setLogs([]);
    setTotal(0);

    try {
      const response = await fetch("/api/scan/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: query.trim() }),
      });
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || "Scan failed");
      }
      const data = await response.json();
      setScanId(data.scan_id);
      setSitesTotal(data.sites_total || 0);
      setSitesDone(0);
      setStatus("running");
    } catch (err) {
      setError(err.message);
      setLoading(false);
    }
  };

  const downloadCsv = async () => {
    if (!scanId) return;
    try {
      const response = await fetch(`/api/scan/export?scan_id=${scanId}`);
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || "Export failed");
      }
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      const filename = `scan_${scanId}.csv`;
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setError(err.message);
    }
  };

  const startScanAll = async () => {
    setLoading(true);
    setError(null);
    setLogs([]);
    setTotal(0);

    try {
      const response = await fetch("/api/scan/start-all", {
        method: "POST",
      });
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || "Scan failed");
      }
      const data = await response.json();
      setScanId(data.scan_id);
      setSitesTotal(data.sites_total || 0);
      setSitesDone(0);
      setStatus("running");
    } catch (err) {
      setError(err.message);
      setLoading(false);
    }
  };

  useEffect(() => {
    scanIdRef.current = scanId;
  }, [scanId]);

  useEffect(() => {
    if (!scanId) return undefined;
    stopPolling();
    pollRef.current = setInterval(() => fetchStatus(), POLL_INTERVAL_MS);
    fetchStatus();
    return () => stopPolling();
  }, [scanId]);

  useEffect(() => () => stopPolling(), []);

  return (
    <div className="app">
      <h1>Unified Fashion Scanner</h1>
      <p className="subtitle">
        Run keyword scans or crawl full catalogs, then export CSV.
      </p>
      <div className="controls">
        <input
          type="text"
          placeholder="Search keyword (e.g. linen dress)"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button onClick={startScan} disabled={loading}>
          {loading ? "Scanning..." : "Scan Keyword"}
        </button>
        <button onClick={startScanAll} disabled={loading}>
          Scan All Products
        </button>
        <button className="secondary" onClick={downloadCsv} disabled={!scanId}>
          Download CSV
        </button>
      </div>
      <div className="status">
        {status ? `Status: ${status}` : ""}
        {sitesTotal ? ` • ${sitesDone}/${sitesTotal} sites scanned` : ""}
        {total ? ` • ${total} products captured` : ""}
      </div>
      {error && <div className="error">{error}</div>}
      {logs.length > 0 && (
        <div className="logs">
          <strong>Scan log</strong>
          <ul>
            {logs.map((entry, index) => (
              <li key={`${entry}-${index}`}>{entry}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default App;
