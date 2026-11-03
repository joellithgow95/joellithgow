/**
 * Click music — a sitewide Easter egg ported from the Peripherals mouse app
 * (`ClickMusic.swift`). With it on, every click plays a short random note:
 * a bright voice for a left click and a lower voice for a right click. On a
 * touch screen, where there is no right click, a tap on the left half of the
 * screen plays the bright voice and a tap on the right half the low one.
 *
 * Each theme has its own scale pair and instrument. Everything is synthesized
 * with Web Audio (no samples to fetch), and the AudioContext is only made on
 * the first click after the setting is on, so a visitor who never turns it
 * on pays nothing. Off by default; the choice is kept in localStorage.
 */

export type ClickHand = 'left' | 'right';

export const STORAGE_KEY = 'portfolio-click-music';
/** Fired on `window` whenever the setting changes; `detail` is the new value. */
export const CHANGE_EVENT = 'click-music-change';

interface NotePartial {
  /** Frequency multiple of the fundamental. */
  ratio: number;
  /** Peak gain relative to the note's. */
  gain: number;
  /** Seconds to fall to silence. */
  decay: number;
  type?: OscillatorType;
}

export interface ClickVoice {
  label: string;
  /** MIDI note numbers (60 = middle C). Left is the brighter voice, right sits lower. */
  left: number[];
  right: number[];
  partials: NotePartial[];
}

// Scales from Peripherals' ClickScale.
const E_MAJOR = [64, 66, 68, 69, 71, 73, 75, 76];
const CSHARP_MINOR = [61, 63, 64, 66, 68, 69, 71, 73]; // the relative minor
const E_MINOR = [64, 66, 67, 69, 71, 72, 74, 76]; // the parallel minor
const E_MAJOR_PENTATONIC = [64, 66, 68, 71, 73, 76];
const CSHARP_MINOR_PENTATONIC = [61, 64, 66, 68, 71, 73];

const up = (notes: number[], semitones: number) => notes.map((n) => n + semitones);

/** One voice per theme, keyed by the theme's `name`. */
export const voices: Record<string, ClickVoice> = {
  // warm & creative: a wooden marimba. The bar's strong 4th partial rings briefly.
  default: {
    label: 'Marimba, E major / C♯ minor',
    left: E_MAJOR,
    right: CSHARP_MINOR,
    partials: [
      { ratio: 1, gain: 1, decay: 0.7 },
      { ratio: 3.93, gain: 0.35, decay: 0.12 },
      { ratio: 9.2, gain: 0.08, decay: 0.04 },
    ],
  },
  // monochrome & clean: a pure celesta-like sine on a scale that never sounds wrong.
  minimal: {
    label: 'Celesta, pentatonic',
    left: up(E_MAJOR_PENTATONIC, 12),
    right: CSHARP_MINOR_PENTATONIC,
    partials: [
      { ratio: 1, gain: 1, decay: 1.1 },
      { ratio: 2, gain: 0.12, decay: 0.3 },
    ],
  },
  // serif & tight: a plucked music box, an octave up, E major against E minor.
  broadsheet: {
    label: 'Music box, E major / E minor',
    left: up(E_MAJOR, 12),
    right: E_MINOR,
    partials: [
      { ratio: 1, gain: 1, decay: 1.0, type: 'triangle' },
      { ratio: 2.76, gain: 0.25, decay: 0.25 },
      { ratio: 5.4, gain: 0.12, decay: 0.1 },
    ],
  },
};

const MASTER_GAIN = 0.22;

export function isEnabled(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'on';
  } catch {
    return false;
  }
}

export function setEnabled(on: boolean): void {
  try {
    if (on) localStorage.setItem(STORAGE_KEY, 'on');
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Private mode or blocked storage: the setting lasts for this page only.
  }
  enabledOverride = on;
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: on }));
}

let enabledOverride: boolean | null = null;
const enabled = () => enabledOverride ?? isEnabled();

export function midiToHz(note: number): number {
  return 440 * 2 ** ((note - 69) / 12);
}

/** A random note from `notes`, never `previous` (unless it is the only one). */
export function pickNote(notes: number[], previous: number | undefined, random = Math.random): number {
  const choices = notes.filter((n) => n !== previous);
  const pool = choices.length ? choices : notes;
  return pool[Math.floor(random() * pool.length)];
}

export function currentVoice(): ClickVoice {
  const theme = document.documentElement.getAttribute('data-theme') || 'default';
  return voices[theme] ?? voices.default;
}

let ctx: AudioContext | null = null;
let master: GainNode | null = null;
const lastNote: Partial<Record<ClickHand, number>> = {};

/** Makes (or wakes) the AudioContext. Call only from inside a user gesture. */
function audio(): { ctx: AudioContext; master: GainNode } | null {
  if (!ctx) {
    const Ctor = window.AudioContext ?? (window as any).webkitAudioContext;
    if (!Ctor) return null;
    ctx = new Ctor() as AudioContext;
    // A gentle compressor so a flurry of clicks doesn't clip.
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -18;
    comp.ratio.value = 6;
    master = ctx.createGain();
    master.gain.value = MASTER_GAIN;
    master.connect(comp).connect(ctx.destination);
  }
  if (ctx.state === 'suspended') void ctx.resume();
  return { ctx, master: master! };
}

/** Plays one note for that hand in the current theme's voice. */
export function play(hand: ClickHand, voice = currentVoice()): void {
  const a = audio();
  if (!a) return;
  const { ctx, master } = a;

  const notes = hand === 'left' ? voice.left : voice.right;
  const note = pickNote(notes, lastNote[hand]);
  lastNote[hand] = note;
  const freq = midiToHz(note);
  const t = ctx.currentTime + 0.005;

  for (const p of voice.partials) {
    const f = freq * p.ratio;
    if (f > ctx.sampleRate / 2) continue;
    const osc = ctx.createOscillator();
    osc.type = p.type ?? 'sine';
    osc.frequency.value = f;
    const env = ctx.createGain();
    env.gain.setValueAtTime(0.0001, t);
    env.gain.exponentialRampToValueAtTime(p.gain, t + 0.004);
    env.gain.exponentialRampToValueAtTime(0.0001, t + p.decay);
    osc.connect(env).connect(master);
    osc.start(t);
    osc.stop(t + p.decay + 0.05);
    osc.onended = () => env.disconnect();
  }
}

/**
 * Listens for clicks sitewide. A mouse plays on press (left button: bright,
 * right button: low) and never blocks the context menu. Touch and pen play on
 * `click`, so a scroll or a long press makes no sound, and choose the voice
 * by which half of the screen was tapped.
 */
export function initClickMusic(): void {
  if ((window as any).__clickMusicInit) return;
  (window as any).__clickMusicInit = true;

  let lastPointerType = 'mouse';

  document.addEventListener(
    'pointerdown',
    (e) => {
      lastPointerType = e.pointerType;
      if (e.pointerType !== 'mouse' || !enabled()) return;
      if (e.button === 0) play('left');
      else if (e.button === 2) play('right');
    },
    { capture: true, passive: true },
  );

  document.addEventListener(
    'click',
    (e) => {
      if (lastPointerType === 'mouse' || !enabled()) return;
      // A keyboard-activated click has no position; leave it silent.
      if (e.detail === 0) return;
      play(e.clientX < window.innerWidth / 2 ? 'left' : 'right');
    },
    { capture: true, passive: true },
  );
}
