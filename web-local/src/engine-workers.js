// Register before importing the engine. Terminate even workers still initializing.
const workers = new Set();
const NativeWorker = window.Worker;
window.Worker = class extends NativeWorker {
  constructor(...args) { super(...args); workers.add(this); }
  terminate() { workers.delete(this); return super.terminate(); }
};
window.stopEngine = () => { for (const worker of [...workers]) worker.terminate(); };
