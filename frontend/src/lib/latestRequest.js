// Cancel obsolete reads and guard against transports that finish after cancellation.
export function createLatestRequest() {
  let version = 0;
  let controller;
  return {
    start() {
      controller?.abort();
      controller = new AbortController();
      const signal = controller.signal;
      const current = ++version;
      return { signal, isCurrent: () => current === version && !signal.aborted };
    },
    cancel() {
      ++version;
      controller?.abort();
    },
  };
}
