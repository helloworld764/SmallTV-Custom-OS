// In-memory wizard state. Codes and request identifiers never enter URLs/storage.
export class EnrollmentFlow {
  constructor(api, { clock = () => performance.now(), uuid = () => crypto.randomUUID() } = {}) {
    this.api = api;
    this.clock = clock;
    this.uuid = uuid;
    this.reset();
  }
  reset() {
    this.generation = (this.generation || 0) + 1;
    this.preview = null;
    this.code = null;
    this.deadline = 0;
  }
  async createPreview(name, csrf) {
    // Keep the same key on an explicit retry after an ambiguous network error.
    if (!this.preview || this.preview.name !== name) this.preview = { name, id: this.uuid() };
    const generation = this.generation;
    const result = await this.api.createPreview(name, this.preview.id, csrf);
    return generation === this.generation ? result : null;
  }
  async issue(name, csrf) {
    const generation = this.generation;
    const started = this.clock();
    const value = await this.api.enrollmentCode(name, csrf);
    if (generation !== this.generation) return null;
    this.code = value;
    this.deadline = started + value.expires_in * 1000;
    return value;
  }
  remaining() {
    const seconds = Math.max(0, Math.ceil((this.deadline - this.clock()) / 1000));
    if (!seconds && this.code) this.code.code = ''; // Clear expired secret, retain id for status.
    return seconds;
  }
  async check() {
    if (!this.code) return null;
    const generation = this.generation;
    const started = this.clock();
    const result = await this.api.enrollmentStatus(this.code.id);
    if (generation !== this.generation) return null;
    if (result.state !== 'pending') this.code.code = '';
    this.deadline = result.state === 'pending' ? started + result.expires_in * 1000 : 0;
    return result;
  }
  async cancel(csrf) {
    if (!this.code) return;
    const id = this.code.id;
    this.code.code = ''; // Never keep showing a code after cancellation was requested.
    const generation = this.generation;
    await this.api.cancelEnrollment(id, csrf);
    if (generation === this.generation) this.reset();
    // On timeout keep the id so the user can explicitly retry cancellation.
  }
}
