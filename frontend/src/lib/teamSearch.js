export const TEAM_SEARCH_TIMEOUT_MS = 15_000;

function errorFromResponse(data, status) {
  const detail = typeof data.detail === 'string' ? data.detail
    : Array.isArray(data.detail) ? data.detail
      .filter((item) => typeof item?.msg === 'string')
      .map((item) => `${(Array.isArray(item.loc) ? item.loc
        .filter((part) => part !== 'body').join('.') : '') || 'Request'}: ${item.msg}`)
      .join('; ') || null : null;
  const error = new Error(data.error ?? detail ?? `Request failed (${status})`);
  error.status = status;
  return error;
}

export async function requestTeamSearch(query, { signal } = {}) {
  const response = await fetch(`/api/teams/search?q=${encodeURIComponent(query)}`, {
    signal,
    credentials: 'include',
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw errorFromResponse(data, response.status);
  return data;
}

export function createTeamSearchController({
  request = requestTeamSearch,
  timeoutMs = TEAM_SEARCH_TIMEOUT_MS,
  onStart,
  onSuccess,
  onError,
  onFinish,
}) {
  let sequence = 0;
  let active = null;

  function cancel({ notify = true } = {}) {
    sequence += 1;
    if (active) {
      clearTimeout(active.timeout);
      active.controller.abort();
      const error = new Error('Team search cancelled');
      error.name = 'AbortError';
      active.rejectCancellation(error);
      active = null;
    }
    if (notify) onFinish();
  }

  async function start(query) {
    cancel({ notify: false });
    const id = sequence;
    const controller = new AbortController();
    let rejectTimeout;
    let rejectCancellation;
    const timeoutPromise = new Promise((_, reject) => { rejectTimeout = reject; });
    const cancellationPromise = new Promise((_, reject) => { rejectCancellation = reject; });
    const timeout = setTimeout(() => {
      const error = new Error('Team search timed out. Please try again.');
      error.name = 'TimeoutError';
      rejectTimeout(error);
      controller.abort();
    }, timeoutMs);
    active = { controller, timeout, rejectCancellation };
    onStart();

    try {
      const data = await Promise.race([
        request(query, { signal: controller.signal }),
        timeoutPromise,
        cancellationPromise,
      ]);
      if (id === sequence) onSuccess(data);
    } catch (error) {
      if (id === sequence && error.name !== 'AbortError') onError(error);
    } finally {
      clearTimeout(timeout);
      if (id === sequence) {
        active = null;
        onFinish();
      }
    }
  }

  return { start, cancel };
}
