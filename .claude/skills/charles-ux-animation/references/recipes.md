# Recipes

Sections: [the curves](#the-curves) · [exit timing in CSS](#exit-timing-in-css) · [modal](#modal) · [drawer and sheet](#drawer-and-sheet) · [popover and dropdown](#popover-and-dropdown) · [tooltip](#tooltip) · [toast](#toast) · [entrances](#entrances-split-then-stagger) · [exits](#exits) · [expanders](#expanders) · [icon swaps](#icon-swaps) · [async buttons](#async-buttons) · [press](#press) · [numbers](#numbers) · [sliders](#sliders) · [carousels](#carousels) · [theme switch](#theme-switch)

Exact values for common components. They follow SKILL.md: ease-out to appear, ease-in to leave, exits 20–30% shorter than entrances, and only `transform`, `opacity` and blurs under 20px animate (plus `grid-template-rows` for an expander). Every one is off under reduced motion. Keep the values and write them in the project's stack.

## The curves

```css
:root {
  --ease-out: cubic-bezier(0.32, 0.72, 0, 1);      /* appearing */
  --ease-in: cubic-bezier(0.55, 0, 1, 0.45);       /* leaving */
  --ease-in-out: cubic-bezier(0.77, 0, 0.175, 1);  /* moving or resizing on screen */
}
```

In a Tailwind v4 project, read [curves](techniques.md#curves) before putting these in `@theme`.

## Exit timing in CSS

The transition declared on the state an element is moving to is the one that runs. Put the entrance's duration and curve on the open state and the shorter exit on the closed state. Every recipe below does this.

## Modal

220ms in, 160ms out: opacity 0 → 1 and scale 0.97 → 1. The overlay uses the same durations and curves. Never slide a modal in from off screen, grow it from 0, or bounce it: a dialog interrupts, so it arrives quietly.

```css
.overlay, .modal {
  opacity: 0;
  pointer-events: none;
  transition: opacity 160ms var(--ease-in), transform 160ms var(--ease-in);
}
.modal { transform: scale(0.97); }
.overlay[data-open], .modal[data-open] {
  opacity: 1;
  pointer-events: auto;
  transition-duration: 220ms;
  transition-timing-function: var(--ease-out);
}
.modal[data-open] { transform: scale(1); }
```

Set `inert` on the modal while it's closed.

## Drawer and sheet

280ms in, 200ms out, sliding all the way back to its edge, because where it goes is the point. The backdrop fades on the same timings, and the drawer's contents arrive with it, in one piece. It slides on a transition, never keyframes, because people close drawers mid-open. A sheet people drag uses a spring, `{ type: "spring", duration: 0.5, bounce: 0 }`, so the release velocity carries into the motion. Dismiss on a fast flick (release velocity, distance ÷ time, above about 0.1px/ms) even when it has barely moved, not only past a distance threshold.

```css
.drawer { transform: translateX(100%); transition: transform 200ms var(--ease-in); }
.drawer[data-open] { transform: translateX(0); transition: transform 280ms var(--ease-out); }
```

Set `inert` on the drawer while it's closed.

## Popover and dropdown

160ms in, 120ms out: fade and scale from 0.95, from the trigger, with a 4px nudge in the direction it opens. Arrow keys move the highlight inside it instantly.

```css
.dropdown {
  transform-origin: var(--radix-dropdown-menu-content-transform-origin, var(--transform-origin, top));
  opacity: 0;
  transform: translateY(-4px) scale(0.95);
  transition: opacity 120ms var(--ease-in), transform 120ms var(--ease-in);
}
.dropdown[data-open] {
  opacity: 1;
  transform: translateY(0) scale(1);
  transition-duration: 160ms;
  transition-timing-function: var(--ease-out);
}
```

## Tooltip

125ms in, 90ms out: fade and scale from 0.97, from the trigger. The first tooltip waits for its hover delay and animates; while one is open, moving to a neighbor shows it at once, with no delay and no animation (Base UI marks this with `data-instant`, Radix with `data-state="instant-open"`). The attributes below are Base UI's; with Radix, animate on `data-state`.

```css
.tooltip {
  transform-origin: var(--transform-origin, var(--radix-tooltip-content-transform-origin));
  transition: opacity 125ms var(--ease-out), transform 125ms var(--ease-out);
}
.tooltip[data-starting-style],
.tooltip[data-ending-style] { opacity: 0; transform: scale(0.97); }
.tooltip[data-ending-style] { transition-duration: 90ms; transition-timing-function: var(--ease-in); }
.tooltip[data-instant],
.tooltip[data-state="instant-open"] { transition-duration: 0ms; }
```

## Toast

240ms in, 170ms out, rising 16px from its screen edge with a fade, and leaving the same way. Stacked toasts shift with `transform` on the same curve, never by re-layout. Swipe-to-dismiss uses a spring. A toast with an action (Undo, Retry) stays at least 5s.

```css
.toast {
  opacity: 0;
  transform: translateY(16px);
  transition: opacity 170ms var(--ease-in), transform 170ms var(--ease-in);
}
.toast[data-open] {
  opacity: 1;
  transform: translateY(0);
  transition-duration: 240ms;
  transition-timing-function: var(--ease-out);
}
```

## Entrances: split, then stagger

Only for content that arrives on its own, never inside a container that's already moving. Never move one wrapper. Split the content into its pieces: a section's heading, copy and actions 80–100ms apart; a hero headline word by word, 80ms apart; copies of one element 30–50ms apart, first 6 only. The softest entrance pairs the fade with an 8px rise and a 2px blur that clears.

```tsx
// Motion
const section = { show: { transition: { staggerChildren: 0.09 } } };
const piece = {
  hide: { opacity: 0, y: 8, filter: "blur(2px)" },
  show: { opacity: 1, y: 0, filter: "blur(0px)", transition: { duration: 0.35, ease: [0.32, 0.72, 0, 1] } },
};

<motion.section variants={section} initial="hide" animate="show">
  {pieces.map((p) => <motion.div key={p.id} variants={piece}>{p.content}</motion.div>)}
</motion.section>
```

```css
/* CSS: set --i on each piece (0, 1, 2…) */
.entrance > * {
  opacity: 0;
  translate: 0 8px;
  filter: blur(2px);
  animation: rise-in 350ms var(--ease-out) forwards;
  animation-delay: calc(var(--i, 0) * 90ms);
}
@keyframes rise-in { to { opacity: 1; translate: 0 0; filter: blur(0); } }
```

## Exits

Fade with a nudge of about 8px toward where it's going, optionally with a 2px blur, in about 150ms ease-in. A full slide off screen is only for things whose destination is the point (a closing drawer, a swiped-away card). Grouped exits (list items, toasts, tags) use `AnimatePresence mode="popLayout"`, so the siblings close the gap with transforms while it leaves.

```tsx
<motion.div exit={{ opacity: 0, y: -8, filter: "blur(2px)", transition: { duration: 0.15, ease: [0.55, 0, 1, 0.45] } }} />
```

## Expanders

The one layout animation allowed: the rows of a one-cell grid go from `0fr` to `1fr`, so the content's real height animates without measuring. Keep it to the element that expands. It resizes in place, so it uses ease-in-out, 250ms open and 190ms closed.

```css
.expander { display: grid; grid-template-rows: 0fr; transition: grid-template-rows 190ms var(--ease-in-out); }
.expander[data-open] { grid-template-rows: 1fr; transition-duration: 250ms; }
.expander > .inner { min-height: 0; overflow: hidden; }
```

Set `inert` on `.inner` while it's closed.

## Icon swaps

Icons that change with state (play and pause, copy and check, like and liked) cross-fade through a deep scale, never a visibility toggle:

- scale 0.3 → 1: a deep scale reads as a swap, while a timid one (0.8, 0.9) reads as a glitch
- opacity 0 → 1, alongside the scale
- blur 3px → 0
- a spring with no bounce, `{ type: "spring", duration: 0.35, bounce: 0 }`, because icons aren't toys

```tsx
<AnimatePresence mode="popLayout" initial={false}>
  <motion.span
    key={copied ? "check" : "copy"}
    initial={{ scale: 0.3, opacity: 0, filter: "blur(3px)" }}
    animate={{ scale: 1, opacity: 1, filter: "blur(0px)" }}
    exit={{ scale: 0.3, opacity: 0, filter: "blur(3px)" }}
    transition={{ type: "spring", duration: 0.35, bounce: 0 }}
  >
    {copied ? <CheckIcon /> : <CopyIcon />}
  </motion.span>
</AnimatePresence>
```

Without Motion, render both icons at once, one absolutely positioned over the other, and swap classes to cross-fade them with `cubic-bezier(0.25, 0.1, 0, 1)` over about 300ms. Nothing unmounts, so both directions animate, and the icon left in normal flow sets the size. Use it on state toggles, actions revealed on hover, contextual toolbars and loading-to-success indicators; never on permanent navigation icons or text labels.

## Async buttons

States: idle → pending → success or error, plus retry and reset. Design the transition for each pair, and build a dev-only playground that shows every state, with a switch that forces the failure, so the error path gets designed too.

- The button disables on press and keeps its size: the label, the spinner and the check sit in one grid cell, so a swap never changes the width.
- The spinner appears only after 300ms and stays at least 500ms once shown.
- Each swap cross-fades both at once in 150ms, the leaving one scaling to 0.9 as it fades and the arriving one growing from 0.9.
- Success swaps to the check. An error swaps back to the label and puts the message beside the button.
- Hide the inactive layers from screen readers (`aria-hidden`) and announce the result in an `aria-live` region.

```css
.async-button { display: inline-grid; }
.async-button > * {
  grid-area: 1 / 1;
  place-self: center;
  opacity: 0;
  scale: 0.9;
  transition: opacity 150ms var(--ease-out), scale 150ms var(--ease-out);
}
.async-button[data-state="idle"] > .label,
.async-button[data-state="pending"] > .spinner,
.async-button[data-state="success"] > .check { opacity: 1; scale: 1; }
```

```js
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Show the spinner only after 300ms, then keep it at least 500ms.
async function run(button, action) {
  button.disabled = true;
  let shownAt = 0;
  const timer = setTimeout(() => { button.dataset.state = "pending"; shownAt = performance.now(); }, 300);
  const holdSpinner = () => (shownAt ? wait(Math.max(0, 500 - (performance.now() - shownAt))) : null);
  try {
    await action();
    clearTimeout(timer);
    await holdSpinner();
    button.dataset.state = "success";
  } catch (error) {
    clearTimeout(timer);
    await holdSpinner();
    button.dataset.state = "idle"; // and show the message beside the button
    throw error;
  } finally {
    button.disabled = false;
  }
}
```

## Press

The press rule from SKILL.md, with the opt-out built into the button:

```tsx
function Button({ still = false, className, ...props }) {
  return (
    <button
      className={cn(
        "transition-[scale] duration-150 ease-[cubic-bezier(0.32,0.72,0,1)]",
        !still && "active:not-disabled:scale-[0.96]",
        className,
      )}
      {...props}
    />
  );
}
```

## Numbers

Roll only the digits that change. Each digit is a 0–9 column in a window one digit tall with `overflow: hidden`, moved with `translateY` on the ease-in-out curve over 300–400ms: up when the value grows, down when it shrinks. A carry from 9 to 0 keeps rolling the same way (give the column a second 0 after the 9). Set the number in `tabular-nums` so widths never shift.

## Sliders

- The whole track is the target. The label and the value sit inside it, the value in `tabular-nums` or a monospace face so the digits don't jump.
- A click snaps to the nearest 10% and springs there. A drag is continuous and 1:1, with no easing.
- The handle appears on hover or drag by scaling in on its x axis, not by fading. When it passes the label or the value, they fade and shrink out of its way, then spring back.
- Hover or drag brightens the track slightly. Ticks at every 10% show only on hover.
- Past 0 or 100%, after a small dead zone, the whole control stretches toward the pull with diminishing returns, and springs back on release ([rubber banding](techniques.md#rubber-banding)).
- Holding the value for about 800ms turns it into an input for an exact number.
- Keyboard: `role="slider"` with `aria-valuenow`, `aria-valuemin` and `aria-valuemax`; arrow keys move one step, Shift+arrows or Page Up and Page Down move 10%, Home and End go to the ends. Reduced motion drops the spring and the stretch.

## Carousels

- A drag is 1:1; on release, a spring carries the velocity to the nearest item.
- In 3D, leave more space around the center item than between the items behind it, so rotated items never intersect, even on a slow drag.
- Items farther from the center fade through their wrapper, not child by child ([cross-fades and groups](techniques.md#cross-fades-and-groups)).

## Theme switch

Turn transitions off for the frame the theme changes, then back on, so the colors don't each fade at their own speed:

```css
.theme-switching *,
.theme-switching *::before,
.theme-switching *::after { transition: none !important; }
```

```js
const root = document.documentElement;
root.classList.add("theme-switching");
applyTheme(next);
requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove("theme-switching")));
```
