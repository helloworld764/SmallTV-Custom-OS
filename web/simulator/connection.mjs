// Pure state machine: tested without a browser or server.
export class Connection {
  constructor() {
    this.state = 'unknown';
    this.failures = 0;
    this.lastSuccess = null;
    this.confirmed = null;
  }

  record(ok, now) {
    if (ok) {
      this.failures = 0;
      this.lastSuccess = now;
      this.state = 'online';
    } else {
      this.failures += 1;
      this.state = this.failures >= 3 ? 'offline' : 'stale';
    }
    let notice = null;
    if (this.state === 'online' || this.state === 'offline') {
      if (this.confirmed !== null && this.confirmed !== this.state) {
        notice = this.state === 'online' ? 'Сервер включён' : 'Сервер выключен';
      }
      this.confirmed = this.state;
    }
    return notice;
  }

  age(now) {
    if (this.state === 'online' && now - this.lastSuccess >= 15000) {
      this.state = 'stale';
    }
  }

  get delay() {
    return Math.min(5000 * 2 ** Math.min(this.failures, 3), 30000);
  }
}

export async function checkHealth(fetcher, signal) {
  const response = await fetcher('/health', { cache: 'no-store', signal });
  if (!response.ok) throw new Error('Health HTTP failure');
  const body = await response.json();
  if (body?.status !== 'ok') throw new Error('Invalid health response');
}
