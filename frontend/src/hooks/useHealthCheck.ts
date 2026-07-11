"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ConnectionStatus, HealthStatus } from "@/lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const POLL_INTERVAL_MS = 30000;

const INITIAL_STATUS: ConnectionStatus = {
  connected: false,
  lastCheck: null,
  lastSuccess: null,
  failureCount: 0,
};

export default function useHealthCheck() {
  const [status, setStatus] = useState<ConnectionStatus>(INITIAL_STATUS);
  const [lastHealth, setLastHealth] = useState<HealthStatus | null>(null);
  const statusRef = useRef<ConnectionStatus>(INITIAL_STATUS);

  const checkNow = useCallback(async () => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    const timestamp = new Date().toISOString();
    try {
      const res = await fetch(`${API_BASE}/health`, { signal: controller.signal });
      if (!res.ok) throw new Error(`status ${res.status}`);
      const data = (await res.json()) as HealthStatus;
      setLastHealth(data);
      const next: ConnectionStatus = {
        connected: true,
        lastCheck: timestamp,
        lastSuccess: timestamp,
        failureCount: 0,
      };
      statusRef.current = next;
      setStatus(next);
    } catch {
      const next: ConnectionStatus = {
        connected: false,
        lastCheck: timestamp,
        lastSuccess: statusRef.current.lastSuccess,
        failureCount: statusRef.current.failureCount + 1,
      };
      statusRef.current = next;
      setStatus(next);
    } finally {
      clearTimeout(timeout);
    }
  }, []);

  useEffect(() => {
    checkNow();
    const interval = setInterval(checkNow, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [checkNow]);

  return { status, lastHealth, checkNow };
}
