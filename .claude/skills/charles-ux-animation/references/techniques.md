# Techniques

Sections: [curves](#curves) · [springs](#springs) · [interruptibility](#interruptibility) · [arcs](#arcs) · [mapping one value to another](#mapping-one-value-to-another) · [rubber banding](#rubber-banding) · [loops](#loops) · [masks and reveals](#masks-and-reveals) · [cross-fades and groups](#cross-fades-and-groups) · [storyboards](#storyboards) · [timelines and live tuning](#timelines-and-live-tuning) · [performance](#performance) · [debugging](#debugging)

## Curves

Start with the defaults: ease-out `cubic-bezier(0.32, 0.72, 0, 1)` for appearing, ease-in `cubic-bezier(0.55, 0, 1, 0.45)` for leaving, and ease-in-out `cubic-bezier(0.77, 0, 0.175, 1)` for moving on screen. Use the library below only when a default feels too soft or too sharp for one element: quart and stronger are snappier, quad and cubic are softer. The bare CSS keywords are too weak for anything that moves; `ease` is fine for hover and color fades.

```css
/* ease-out: appearing, responding to input */
--ease-out-quad:  cubic-bezier(0.25, 0.46, 0.45, 0.94);
--ease-out-cubic: cubic-bezier(0.215, 0.61, 0.355, 1);
--ease-out-quart: cubic-bezier(0.165, 0.84, 0.44, 1);
--ease-out-quint: cubic-bezier(0.23, 1, 0.32, 1);
--ease-out-expo:  cubic-bezier(0.19, 1, 0.22, 1);
--ease-out-circ:  cubic-bezier(0.075, 0.82, 0.165, 1);

/* ease-in-out: moving or resizing on screen */
--ease-in-out-quad:  cubic-bezier(0.455, 0.03, 0.515, 0.955);
--ease-in-out-cubic: cubic-bezier(0.645, 0.045, 0.355, 1);
--ease-in-out-quart: cubic-bezier(0.77, 0, 0.175, 1);
--ease-in-out-quint: cubic-bezier(0.86, 0, 0.07, 1);
--ease-in-out-expo:  cubic-bezier(1, 0, 0, 1);
--ease-in-out-circ:  cubic-bezier(0.785, 0.135, 0.15, 0.86);
```

`linear` is only for constant rates, where the motion is time itself: marquees, tickers, countdowns, a hold-to-delete ring.

In Tailwind v4, overriding `--ease-out`, `--ease-in` and `--ease-in-out` in `@theme` retimes every `ease-*` utility in the app. Do that only when the change is meant for the whole app. For one component, add the curves under new names (`--ease-enter`, `--ease-exit`, `--ease-move`).

## Springs

Use the duration-and-bounce form, `{ type: "spring", duration: 0.5, bounce: 0 }` in Motion. Use the physics form (`stiffness`, `damping`, `mass`) only to match an existing animation; starting points there are 300 and 30 for containers and cards, 350 and 28 for slides and entrances, and 500 and 25 for badges and pop-ins. Bounce stays 0 in product UI, and 0.1–0.3 for playful, physical things. A spring keeps its velocity when it's interrupted, which is why everything that follows a finger uses one.

## Interruptibility

A transition heads from wherever the element is to its newest target, so interrupting it turns it around mid-flight. A keyframe animation plays a fixed timeline, so interrupting it jumps to the start and replays. Anything the person can toggle gets a transition or a spring.

```css
/* Interruptible: closing mid-open turns around from where it is */
.panel { transform: translateX(100%); transition: transform 200ms var(--ease-in); }
.panel[data-open] { transform: translateX(0); transition: transform 280ms var(--ease-out); }

/* Not interruptible: closing mid-open jumps */
.panel[data-open] { animation: panel-in 280ms var(--ease-out) forwards; }
```

## Arcs

Move x straight and lift y by a sine that peaks halfway:

```js
// t: 0 → 1, already eased. peak: px off the straight line, 10–20% of the distance.
function arc(from, to, peak, t) {
  return {
    x: from.x + (to.x - from.x) * t,
    y: from.y + (to.y - from.y) * t - Math.sin(t * Math.PI) * peak,
  };
}
```

In CSS, put x and y on two nested wrappers with different curves over the same duration: the outer moves x with ease-out, the inner moves y with ease-in, `cubic-bezier(0.55, 0, 1, 0.45)`. Or keyframe a midpoint 10–20% of the distance off the straight line. If the installed Motion exports `arc` (`import { arc } from "motion/react"`), pass it as the transition's `path`, for example `path: arc({ strength: 0.35, peak: 0.5 })`; check the export first.

For depth, scale up toward 1.3 at the top of the arc and back, and fade in a larger shadow on a pseudo-element at the same time, rather than animating `box-shadow`:

```css
.flyer { position: relative; }
.flyer::after {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  box-shadow: 0 24px 32px rgb(0 0 0 / 0.22);
  opacity: 0;
}
.flyer.is-flying::after { animation: lift 360ms var(--ease-in-out); }
@keyframes lift { 50% { opacity: 1; } }
```

## Mapping one value to another

Most effects that respond to scroll, the pointer or a value are one number driving another:

```js
// Clamp the fraction, not the result.
const mapRange = (value, [inLow, inHigh], [outLow, outHigh], clamp = true) => {
  let t = (value - inLow) / (inHigh - inLow);
  if (clamp) t = Math.min(1, Math.max(0, t));
  return outLow + t * (outHigh - outLow);
};
```

- To ease a mapping, map to 0–1, run that through an easing function, then map it to the output.
- A collapsing header maps scroll 0–128px to the large title's scale, 1 → 0.5, from its left edge.
- A pointer tilt maps the pointer's position across the card to rotateY −14° → 14° and rotateX 14° → −14°, centered on 0 so the middle means no tilt, smoothed with a spring.
- Dock magnification maps each icon's distance from the pointer, eased, to a scale from 1 up to the maximum.
- A value mapped to a color mixes through several stops (green → amber → red), in OKLCH so the midpoints don't go muddy.

Whenever you're about to write an if/else ladder to change a visual across a range, write a mapRange instead.

## Rubber banding

Past a bound, the pull goes further with diminishing returns toward a ceiling it never reaches, after a dead zone where it simply holds at the bound:

```js
const DEAD_ZONE = 12;    // px of overshoot that just holds at the bound
const RESISTANCE = 150;  // px of pull that reaches half the ceiling; larger feels stiffer
const CEILING = 64;      // px the stretch approaches but never reaches

const over = Math.max(0, pull - DEAD_ZONE);
const stretch = CEILING * (over / (over + RESISTANCE));
```

On release, spring back to the bound. Why the dead zone: people overshoot on purpose to reach 0 or 100%, and that shouldn't stretch anything.

## Loops

Drive loops with a sine wave: it's smooth at every point and repeats with no seam.

```js
// t in seconds, frequency in cycles per second, phase in cycles (0–1).
const wave = (t, frequency, phase = 0) => Math.sin(2 * Math.PI * (frequency * t + phase)); // −1 … 1
const y = wave(t, 0.8) * 10;                                         // drift ±10px
const scale = mapRange(wave(t, 0.8, 0.25), [-1, 1], [0.98, 1.02]);  // out of phase with y
```

- For many items, give each the same wave with its own phase (`index × 0.08` cycles) instead of a delay; it reads like breathing.
- For organic drift, average octaves: `(sin(2πt) + 0.5·sin(4πt) + 0.25·sin(8πt)) / 1.75`.
- To cycle a color, drive the hue within a narrow range (±20°), never around the whole wheel.
- Pause every loop off screen, with an `IntersectionObserver` or `animation-play-state: paused`.

## Masks and reveals

- `clip-path` gives crisp edges: a wipe goes from `inset(0 100% 0 0)` to `inset(0)`, an iris from `circle(0% at 50% 50%)` to `circle(75% at 50% 50%)`.
- `mask-image` gives soft edges. Fade the ends of a scrolling row instead of clipping them hard: `mask-image: linear-gradient(to right, transparent, #000 14%, #000 86%, transparent)`.
- Fade content out (a "read more" fade) with a mask, never with an overlay painted in the background color: the overlay breaks on any other background, including dark mode.
- To put something into a slot, clip away the part that's inside with `clip-path: inset()` as it crosses the opening, and draw the slot's front edge above it.

## Cross-fades and groups

- Two images cross-fading at partial opacity let the background through, so the midpoint looks washed out. Put both in a group with `isolation: isolate` and give both `mix-blend-mode: plus-lighter`; their opacities (a and 1 − a) then add up to full coverage.
- Fade a group through its wrapper, never child by child, or overlapping children show through each other.
- When two states look disconnected, a 2px blur during the change bridges them.

## Storyboards

A sequence of three or more stages reads like a script:

```tsx
/* ─────────────────────────────────────────────
 * ANIMATION STORYBOARD (ms after the trigger)
 *
 *    0ms   card rises, scale 0.95 → 1
 *  180ms   heading highlights
 *  300ms   rows slide up, 40ms apart
 *  520ms   button fades in
 * ───────────────────────────────────────────── */
const TIMING = {
  card:    0,    // card rises
  heading: 180,  // heading highlights
  rows:    300,  // rows start
  button:  520,  // button fades in
};

const CARD = { from: 0.95, spring: { type: "spring", duration: 0.5, bounce: 0 } };
const ROWS = { stagger: 0.04, offsetY: 8 }; // seconds between rows, px each row rises

const [stage, setStage] = useState(0);
useEffect(() => {
  setStage(0);
  const timers = [TIMING.card, TIMING.heading, TIMING.rows, TIMING.button]
    .map((at, i) => setTimeout(() => setStage(i + 1), at));
  return () => timers.forEach(clearTimeout);
}, [replay]);
// In JSX: animate={{ scale: stage >= 1 ? 1 : CARD.from }} transition={CARD.spring}
```

- The comment matches `TIMING` exactly. Times count from the trigger, not from the previous stage.
- Every value lives in a config object named after its element, so the JSX has no bare numbers.
- One `stage` integer, compared with `>=`, so stages add up. Repeated items are arrays rendered with `.map()`.
- A `replay` value in the effect's dependencies replays the sequence while tuning.

## Timelines and live tuning

- Choreography with overlapping tracks (an envelope flips while a letter rises out of it) is tuned on a scrubbable timeline, such as DialKit's Timeline (React, Solid, Svelte, Vue or plain JavaScript), not with delay sliders and timers.
- Plan the big steps first and group the properties by object, then refine each step.
- Put changes that can't animate (a z-index swap, an element swap, a sound) on the same timeline as events, and scrub to the frame where they land unseen. Too early or too late, and a z-index swap shows as a jump.
- Scrub for the earliest start that doesn't make anything clip or intersect.
- While tuning, expose durations, curves and springs as live controls (DialKit panels), then hard-code the results as named constants and keep the tuning UI out of production. DialKit is a new dependency, so add it only with the person's OK.

## Performance

- Animate `transform` and `opacity`. Keep blur under 20px (it's worst in Safari), and never animate a CSS custom property that's read deep in a tree, because changing it restyles everything below.
- In React, never drive a per-frame animation through state, which re-renders every frame. Use refs and direct style writes, WAAPI, or the library's own loop. In Motion, a full transform string (`animate={{ transform: "translateX(100px)" }}`) can run on the compositor while `x: 100` runs on the main thread, so use the string for things that move while the page is busy.
- CSS transitions keep running smoothly when the main thread is busy; JS engines run on `requestAnimationFrame` and are needed for springs, gestures and values computed live. Use the simplest one that does the job.
- A spinner that feels slow spins faster: perceived speed is real speed to the person waiting.

## Debugging

| Symptom | Fix |
| --- | --- |
| Feels off, can't say why | Record it and scrub frame by frame |
| Jitters at the start or end | `will-change: transform` on that element only |
| Flickers on hover | Animate a child; the parent's hover area stays still |
| Two states look disconnected | A 2px blur during the change |
| Hover animations fire on phones | `@media (hover: hover) and (pointer: fine)` |
| Every color fades on a theme switch | Turn transitions off for the switch ([recipe](recipes.md#theme-switch)) |
| Neighboring tooltips each wait | Delay only the first; the rest open instantly |
| Feels slow | Cut the duration before touching the curve |
