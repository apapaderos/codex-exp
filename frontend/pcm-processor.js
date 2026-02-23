/**
 * pcm-processor.js — AudioWorklet processor for NEXUS
 *
 * Runs on the dedicated AudioWorklet thread (off the main UI thread).
 * Receives 128-sample float32 blocks from the Web Audio graph at the
 * configured sample rate (16 kHz), converts them to Int16 PCM, and
 * posts them to the main thread via MessagePort.
 *
 * Why AudioWorklet over ScriptProcessorNode:
 *  - Runs on a real-time audio thread — no main-thread jank = fewer dropped frames
 *  - No deprecation warnings in modern Chrome/Safari (tablet browsers)
 *  - Lower and more consistent latency (~8 ms vs ~256 ms for ScriptProcessor)
 *
 * Usage (main thread):
 *   await audioCtx.audioWorklet.addModule('/pcm-processor.js');
 *   const node = new AudioWorkletNode(audioCtx, 'pcm-processor');
 *   node.port.onmessage = (e) => ws.send(e.data);  // ArrayBuffer of Int16 PCM
 *   source.connect(node);
 */

class PcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    // Accumulate samples until we have a full 4096-sample chunk before posting.
    // This matches the WebSocket send cadence and keeps frame sizes consistent
    // for the Azure Speech SDK push stream.
    this._chunkSize = 4096;
    this._buffer    = new Float32Array(this._chunkSize);
    this._writePos  = 0;
  }

  /**
   * Called by the audio thread for every 128-sample render quantum.
   * inputs[0][0] is the mono channel Float32Array of length 128.
   */
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;   // keep processor alive even with no input

    let i = 0;
    while (i < channel.length) {
      const space   = this._chunkSize - this._writePos;
      const toCopy  = Math.min(space, channel.length - i);

      this._buffer.set(channel.subarray(i, i + toCopy), this._writePos);
      this._writePos += toCopy;
      i              += toCopy;

      if (this._writePos === this._chunkSize) {
        this._flush();
      }
    }

    return true;   // returning false would stop the processor
  }

  _flush() {
    // Convert Float32 → Int16 LE PCM in a new ArrayBuffer and transfer it
    // (zero-copy) to the main thread.
    const pcm = new Int16Array(this._chunkSize);
    for (let j = 0; j < this._chunkSize; j++) {
      const s = Math.max(-1, Math.min(1, this._buffer[j]));
      pcm[j]  = s < 0 ? s * 32768 : s * 32767;
    }

    // transferring ownership avoids a structured-clone copy
    this.port.postMessage(pcm.buffer, [pcm.buffer]);

    // Reset write position
    this._writePos = 0;
  }
}

registerProcessor('pcm-processor', PcmProcessor);
