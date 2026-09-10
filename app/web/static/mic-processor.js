class Pcm16kProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const settings = options.processorOptions || {};
    this.targetSampleRate = settings.targetSampleRate || 16000;
    this.frameSamples = settings.frameSamples || 512;
    this.ratio = sampleRate / this.targetSampleRate;
    this.inputBuffer = new Float32Array(0);
    this.position = 0;
    this.output = [];
  }

  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input?.length) return true;

    const combined = new Float32Array(this.inputBuffer.length + input.length);
    combined.set(this.inputBuffer);
    combined.set(input, this.inputBuffer.length);

    while (this.position + 1 < combined.length) {
      const left = Math.floor(this.position);
      const fraction = this.position - left;
      const value = combined[left] + (combined[left + 1] - combined[left]) * fraction;
      this.output.push(value);
      this.position += this.ratio;
      if (this.output.length === this.frameSamples) {
        const frame = Float32Array.from(this.output);
        this.output.length = 0;
        this.port.postMessage(frame.buffer, [frame.buffer]);
      }
    }

    const consumed = Math.min(Math.floor(this.position), combined.length - 1);
    this.inputBuffer = combined.slice(consumed);
    this.position -= consumed;
    return true;
  }
}

registerProcessor('pcm-16k-processor', Pcm16kProcessor);
