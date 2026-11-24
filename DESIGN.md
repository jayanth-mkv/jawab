# Jawab design system

One look for everything Jawab ships: the web app, the demo video, deck screenshots, social cards.
The tokens live in `web/static/style.css` (`:root`); the video editor (`bench/record_demo.py`) uses the same values.
The bakery's public site (`/site`) is the one exception: it stands for the business's own website and keeps its warm bakery style.

## Principles

1. **Minimal.** White and soft-grey surfaces, ink type, one accent. If something can go, it goes.
2. **One accent: Jawab red.** Red marks the primary action, the current step, and every icon. Amber only warms red (gradients, the orb). No other decorative colours.
3. **Voice is the hero.** Audio is always visible: the particle orb on the phone, stretching bars while recording.
4. **Calm motion.** Short eases (`cubic-bezier(.2,.7,.2,1)`, 200–800 ms). Nothing bounces. Respect `prefers-reduced-motion`.
5. **On screen at once.** The thing to try is visible without scrolling, on desktop and on a phone.

## Colour

| Token | Hex | Use |
| --- | --- | --- |
| `--red` | `#d7191f` | Primary: actions, current step, icons, links |
| `--red-2` | `#ff4d4f` | Red on dark surfaces |
| `--red-t` | `#fdeeee` | Red tint: icon wells, focus halos, selection |
| `--amber-2` | `#ffad5c` | Warms red in gradients (`--red → #ff7a45`), the orb's highlights |
| `--ink` | `#0b0c10` | Headings, body text, secondary buttons, done states |
| `--sub` | `#4a4f5b` | Secondary text |
| `--mute` | `#868b96` | Hints, meta |
| `--line` / `--line-2` | `#e8eaee` / `#dcdfe5` | Hairlines, borders |
| `--soft` / `--soft-2` | `#f4f5f7` / `#eceef2` | Grouped surfaces, pills, inputs |
| `--bg` | `#ffffff` | Canvas (the `/try` page adds a faint red and amber glow at the top) |
| `--navy` | `#07080c` | The customer phone's dark voice screen |

Status colours (semantic only, never decorative): green `#22c55e` = live / on / verified; amber `#c26100` on `#fdf2e3` = warning.

## Type

- **Figtree** (Google Fonts), weights 400 / 500 / 600 / 700. Fallback: `system-ui, -apple-system, "Segoe UI", Roboto`.
- Headings 600–700, tracking `-0.025em` to `-0.035em`. Display 42 / H1 30–34 / H2 17 / body 15 / meta 12–13.
- Eyebrows: 12px, 600, uppercase, tracking `0.1em`, red, with a small red dot when something is live.
- Gradient emphasis in headlines only: `linear-gradient(90deg, #d7191f, #ff7a45)`.

## Shape and depth

- Radius: pills and round buttons `999px`; cards `22px`; sheets `30–32px`; phone `40–56px`; inputs `14px`.
- Shadow: `--shadow` for cards, `--shadow-lg` for floating devices and sheets. No hard borders on top of shadows.
- Group related controls on a `--soft` surface with white rows inside (settings-list style).

## Icons

- **Outlined only**: 24px grid, `stroke-width: 2`, round caps and joins, `fill: none` (Lucide geometry).
- **Colour: `--red`** by default. On red buttons the icon is white; on dark surfaces `#fff`.
- Icon wells: a `--red-t` circle with the red outline icon inside (`.dot`). Never multi-coloured circles, never emoji.
- The only filled glyphs allowed: the stop square while recording, and status dots.

## Components

- **Buttons**: primary = red pill; secondary = ink pill; tertiary = `--soft` pill. 15px / 600.
- **Answer bar**: `--soft` pill, input on the left, a send button and a mic button with a red→amber gradient ring.
- **Toggle**: green track when on (it is a status), white knob.
- **Step rail**: numbered circles; current = red with a `--red-t` halo, done = ink with a check.
- **Phone**: dark voice screen, particle orb (amber over red; cooler white while the caller speaks), glass chips, white call pill.

## Video and stills

- 1920×1080, 30 fps. Canvas = white with the faint red/amber top glow, same as `/try`.
- Two devices side by side on rounded cards (radius 28, `--shadow-lg`); a key moment expands its side to full screen, zooms in, then returns.
- Captions: ink pill (`#0b0c10` at 94%), white Figtree 600 30px, top centre; the step number sits in a red circle.
- Title cards: white canvas, red eyebrow with dot, ink Figtree 700 headline, `--sub` subline. No other colours.
- Cursor: the system arrow (ink with a white outline) and a red ripple on click.
