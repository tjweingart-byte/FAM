/*
 * Does changing the speed still leave the voice alone?
 *
 * `FamAudio.setRate` used to set `playbackRate` on a buffer source, which
 * resamples: 1.5x speech came back a fifth higher. It time-stretches instead
 * now (WSOLA), and the two properties that has to keep are exactly the two
 * nobody can see by reading the code:
 *
 *   1. the pitch does not move, at any speed;
 *   2. one second of playback still consumes `rate * sampleRate` source
 *      samples - which is what `positionSamples`, seek, the scrub bar and
 *      TAIL_MARGIN are all built on, and none of them knows the stretcher
 *      exists.
 *
 * Break either and nothing fails: the app plays, the bar moves, and the
 * episode sounds wrong or the clock drifts. So this drives the real module
 * against a fake AudioContext and a synthetic tone, and measures both.
 *
 * Run: node tools/check_stretch.js
 */
'use strict';

const fs = require('fs');
const path = require('path');

const SOURCE = path.join(__dirname, '..', 'static', 'fam-audio.js');

let now = 0;
let scheduled = [];
let ticks = [];

class FakeBuffer {
  constructor(length, rate) {
    this.length = length;
    this.sampleRate = rate;
    this.duration = length / rate;
    this._data = new Float32Array(length);
  }
  getChannelData() { return this._data; }
}

class FakeContext {
  constructor() { this.state = 'running'; this.destination = {}; }
  get currentTime() { return now; }
  createBuffer(channels, length, rate) { return new FakeBuffer(length, rate); }
  createBufferSource() {
    return {
      buffer: null, playbackRate: { value: 1 }, onended: null,
      connect() {},
      start(when) {
        scheduled.push({ when, buf: this.buffer, rate: this.playbackRate.value });
      },
      stop() {},
    };
  }
  resume() { return Promise.resolve(); }
  suspend() { this.state = 'suspended'; }
  close() { return Promise.resolve(); }
}

global.window = { AudioContext: FakeContext };
global.setInterval = (fn) => { ticks.push(fn); return ticks.length; };
global.clearInterval = () => {};
global.AbortController = function () { this.abort = () => {}; this.signal = {}; };

eval(fs.readFileSync(SOURCE, 'utf8'));
const FamAudio = global.window.FamAudio;

const RATE = 22050;
const SECONDS = 6;
const HZ = 200;                 // inside any speaking voice's range
const tone = new Int16Array(RATE * SECONDS);
for (let i = 0; i < tone.length; i++) {
  tone[i] = Math.round(12000 * Math.sin(2 * Math.PI * HZ * i / RATE));
}

/* Zero crossings. Cheap, and exact enough for a pure tone - which is the
   whole reason the fixture is one. */
function dominantHz(samples, rate) {
  let crossings = 0;
  for (let i = 1; i < samples.length; i++) {
    if (samples[i - 1] < 0 && samples[i] >= 0) crossings++;
  }
  return crossings / (samples.length / rate);
}

function play(speed) {
  scheduled = []; ticks = []; now = 0;
  FamAudio.stop();
  FamAudio.setPitchLock(true);
  FamAudio.playStored(tone, RATE, {});
  FamAudio.setRate(speed);

  // The scheduler only fills LOOKAHEAD ahead of the clock, so a tick that
  // adds nothing means "caught up", not "finished". Finished is a long run.
  let last = -1, quiet = 0;
  for (let i = 0; i < 60000 && quiet < 400; i++) {
    ticks.forEach((fn) => fn());
    now += 0.02;
    quiet = (scheduled.length === last) ? quiet + 1 : 0;
    last = scheduled.length;
  }

  const out = [];
  scheduled.forEach((s) => {
    const d = s.buf.getChannelData();
    for (let i = 0; i < d.length; i++) out.push(d[i]);
  });
  const heard = scheduled.reduce((a, s) => a + s.buf.duration / (s.rate || 1), 0);
  return { hz: dominantHz(out, RATE), heard, ratio: SECONDS / heard };
}

const SPEEDS = [0.5, 0.8, 1, 1.2, 1.5, 2];
const PITCH_TOLERANCE = 12;     // Hz
const RATE_TOLERANCE = 0.02;    // as a fraction of the requested speed

let failed = 0;
for (const speed of SPEEDS) {
  const r = play(speed);
  const pitchOk = Math.abs(r.hz - HZ) <= PITCH_TOLERANCE;
  const rateOk = Math.abs(r.ratio - speed) <= RATE_TOLERANCE;
  if (!pitchOk || !rateOk) failed++;
  console.log(
    '  ' + (pitchOk && rateOk ? 'ok  ' : 'FAIL') +
    '  ' + String(speed) + 'x' +
    '  pitch ' + r.hz.toFixed(1) + 'Hz (want ' + HZ + ')' +
    '  source per second played ' + r.ratio.toFixed(3) + 'x');
}

if (failed) {
  console.error('speed changed the voice, or the clock drifted: ' +
                failed + ' of ' + SPEEDS.length + ' speeds wrong');
  process.exit(1);
}
console.log('checked ' + SPEEDS.length + ' speeds: pitch held, clock exact');
