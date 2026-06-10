/**
 * WebSocket client for VALET server communication.
 */

export type MessageHandler = (msg: Record<string, unknown>) => void;

export interface ValetSocket {
  send(data: Record<string, unknown>): boolean;
  onMessage(handler: MessageHandler): void;
  onConnectionChange(handler: (connected: boolean) => void): void;
  close(): void;
  isConnected(): boolean;
}

export function createSocket(url: string): ValetSocket {
  let ws: WebSocket | null = null;
  let handlers: MessageHandler[] = [];
  let connHandlers: ((c: boolean) => void)[] = [];
  let reconnectDelay = 1000;
  let closed = false;
  let connected = false;

  function setConnected(state: boolean) {
    if (connected === state) return;
    connected = state;
    for (const h of connHandlers) h(state);
  }

  function connect() {
    if (closed) return;

    ws = new WebSocket(url);

    ws.onopen = () => {
      reconnectDelay = 1000;
      console.log("[ws] connected");
      setConnected(true);
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        for (const h of handlers) h(msg);
      } catch {
        console.warn("[ws] bad message", event.data);
      }
    };

    ws.onclose = () => {
      setConnected(false);
      if (!closed) {
        console.log(`[ws] reconnecting in ${reconnectDelay}ms`);
        setTimeout(connect, reconnectDelay);
        // Cap backoff at 4s — the backend usually restarts quickly during dev,
        // and a 30s wait makes the UI feel dead.
        reconnectDelay = Math.min(reconnectDelay * 1.5, 4000);
      }
    };

    ws.onerror = (err) => {
      console.error("[ws] error", err);
      ws?.close();
    };
  }

  connect();

  return {
    send(data) {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(data));
        return true;
      }
      console.warn("[ws] send dropped — socket not open (state=" + ws?.readyState + ")");
      return false;
    },
    onMessage(handler) {
      handlers.push(handler);
    },
    onConnectionChange(handler) {
      connHandlers.push(handler);
    },
    close() {
      closed = true;
      ws?.close();
    },
    isConnected() {
      return connected;
    },
  };
}
