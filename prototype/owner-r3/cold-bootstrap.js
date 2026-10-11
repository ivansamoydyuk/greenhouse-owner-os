// Only the initial authenticated read path has this deadline. Business requests
// are never retried or timed out by this boundary. Retry creates a new document.
export class ColdBootstrapAttempt {
  constructor({ generation, failed, timeoutMs = 15000, schedule = setTimeout, cancel = clearTimeout }) {
    this.readGeneration = generation;
    this.generation = generation();
    this.failed = failed;
    this.cancel = cancel;
    this.state = 'pending';
    this.timer = schedule(() => this.fail(Error('Загрузка заняла слишком много времени. Повторите загрузку страницы.')), timeoutMs);
  }
  check() {
    if (this.state === 'failed') throw Error('Эта попытка загрузки завершена. Повторите загрузку страницы.');
    if (this.generation !== this.readGeneration()) throw Error('Сессия изменилась во время загрузки. Повторите загрузку страницы.');
  }
  configured() {
    if (this.state !== 'pending') throw Error('Попытка загрузки уже завершена.');
    this.generation = this.readGeneration();
  }
  fail(error) {
    if (this.state !== 'pending') return false;
    this.state = 'failed';
    this.cancel(this.timer);
    this.failed(error);
    return true;
  }
  finish() {
    this.check();
    this.state = 'complete';
    this.cancel(this.timer);
  }
}
