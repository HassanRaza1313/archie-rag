---
name: Archie
description: A chatbot builder whose answers are captioned like gallery labels, with their provenance.
colors:
  wall: "#fbfbfa"
  panel: "#ffffff"
  plinth: "#f1f2f3"
  rule: "#e3e5e8"
  rule-strong: "#c9cdd3"
  ink: "#23272e"
  slate: "#56616d"
  rail: "#5c1622"
  rail-ink: "#ffffff"
  rail-muted: "#e7cfd3"
  rail-active: "#7d2633"
  accent: "#7a1f2b"
  accent-hover: "#64161f"
  accent-ink: "#ffffff"
  accent-soft: "#f7e9eb"
  ok: "#1f6b46"
  ok-soft: "#e8f3ec"
  warn: "#8a4f00"
  warn-soft: "#fdf3e1"
  bad: "#b42318"
  bad-soft: "#fdecea"
typography:
  display:
    fontFamily: "Hanken Grotesk Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "3rem"
    fontWeight: 600
    lineHeight: 1.04
    letterSpacing: "-0.025em"
  headline:
    fontFamily: "Hanken Grotesk Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "2.125rem"
    fontWeight: 600
    lineHeight: 1.12
    letterSpacing: "-0.025em"
  title:
    fontFamily: "Hanken Grotesk Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: 1.25
  body-lg:
    fontFamily: "Hanken Grotesk Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 400
    lineHeight: 1.45
  body:
    fontFamily: "Hanken Grotesk Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "\"ss01\", \"cv11\", \"tnum\""
  label:
    fontFamily: "Hanken Grotesk Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 600
    lineHeight: 1.35
  mono:
    fontFamily: "JetBrains Mono Variable, ui-monospace, monospace"
    fontSize: "0.8125rem"
    fontWeight: 600
    lineHeight: 1.35
    letterSpacing: "0.025em"
rounded:
  label: "3px"
  badge: "6px"
  control: "8px"
  panel: "12px"
  pill: "9999px"
spacing:
  gutter-mobile: "20px"
  gutter-desktop: "40px"
  label-x: "14px"
  label-y: "12px"
  stack: "24px"
  section: "32px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 16px"
    height: "40px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-secondary:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "0 16px"
    height: "40px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.slate}"
    rounded: "{rounded.control}"
    padding: "0 16px"
    height: "40px"
  button-ghost-hover:
    backgroundColor: "{colors.plinth}"
    textColor: "{colors.ink}"
  button-danger:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.bad}"
    rounded: "{rounded.control}"
    padding: "0 16px"
    height: "40px"
  button-danger-hover:
    backgroundColor: "{colors.bad-soft}"
  button-sm:
    typography: "{typography.label}"
    padding: "0 12px"
    height: "32px"
  button-lg:
    typography: "{typography.body-lg}"
    padding: "0 20px"
    height: "48px"
  gallery-label:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.label}"
    padding: "12px 14px"
  citation-mark:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent}"
    typography: "{typography.mono}"
    rounded: "{rounded.label}"
    padding: "0 6px"
  badge-neutral:
    backgroundColor: "{colors.plinth}"
    textColor: "{colors.slate}"
    typography: "{typography.label}"
    rounded: "{rounded.badge}"
    padding: "0 8px"
    height: "24px"
  badge-ok:
    backgroundColor: "{colors.ok-soft}"
    textColor: "{colors.ok}"
  badge-warn:
    backgroundColor: "{colors.warn-soft}"
    textColor: "{colors.warn}"
  badge-bad:
    backgroundColor: "{colors.bad-soft}"
    textColor: "{colors.bad}"
  badge-accent:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent}"
  input:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "40px"
  nav-item:
    backgroundColor: "transparent"
    textColor: "{colors.rail-muted}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "40px"
  nav-item-active:
    backgroundColor: "{colors.rail-ink}"
    textColor: "{colors.rail}"
  rail:
    backgroundColor: "{colors.rail}"
    textColor: "{colors.rail-ink}"
    width: "256px"
  dialog:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    width: "512px"
---

# Design System: Archie

## Overview

**Creative North Star: "The Well-Kept Gallery"**

Archie runs like a well-kept gallery: quiet walls, a painted rail, and every answer captioned like an object label with its provenance. The wall is near-white and almost silent; the one painted surface is the deep oxblood navigation rail, which also lends its colour to primary actions, selection, focus and citation marks. Everything else is ink and slate on white, separated by hairline rules rather than shadows.

The signature is the tombstone label: a small square-cornered hairline card with an accession mark in mono oxblood, a semibold title, and provenance lines in slate. It captions answer sources hung in the margin beside the answer, the chatbots hung on the home wall, and the welcome steps. Density is moderate: dense where experts read tables and traces, generous where a first-timer is choosing what to do next. The system refuses the grey-sidebar-plus-stat-tiles admin template and the dark neon AI console.

There is one type family, Hanken Grotesk, on a strict six-step ladder; rank comes from size and weight, never from colour or decoration. JetBrains Mono is reserved for ids, scores, timings and accession marks.

**Key Characteristics:**
- Gallery-white wall, one painted oxblood rail, oxblood as the only accent.
- The tombstone label (3px corners, hairline border, mono accession mark) as the reusable signature.
- Flat surfaces divided by hairlines; shadow only on the modal layer.
- One sans family on a 13/15/18/24/34/48 ladder; mono only for machine values.
- State colours appear only where they carry meaning; every pairing meets WCAG AA.
- Full light and dark themes from the same token names.

## Colors

A near-white wall, ink text, and a single deep oxblood carried from the rail into every point of action.

### Primary
- **Gallery Oxblood** (accent): primary buttons, selected switches, focus rings, text caret, link hover, citation marks and accession marks. The only accent hue in the system.
- **Oxblood Deep** (accent-hover): the pressed-in hover state of primary buttons.
- **Oxblood Blush** (accent-soft): citation-mark ground, accent badges, text selection.
- **Accent Ink** (accent-ink): text on oxblood fills.

### Secondary
- **Painted Rail** (rail): the navigation rail's paint. Never used as a content fill on the wall.
- **Rail Wash** (rail-active): hover fill for rail items (at 60%) and rail utility buttons.
- **Rail Rose** (rail-muted): inactive rail text, the chatbot sub-navigation caption, service status.
- **Rail White** (rail-ink): rail type and the active nav pill, which inverts to white with rail-coloured text.

### Neutral
- **Gallery Wall** (wall): the page background behind everything.
- **Panel White** (panel): labels, controls, dialogs, outcome panels and the mobile header.
- **Plinth** (plinth): the user's own message bubble, ghost-button hover, neutral badges, skeletons and progress tracks.
- **Hairline** (rule): label and panel borders, dividers, stat top rules.
- **Strong Hairline** (rule-strong): control borders, secondary-button borders, the off switch track, scrollbars.
- **Ink** (ink): body and heading text.
- **Slate** (slate): descriptions, provenance lines, captions, placeholders, ghost-button text.

### State
- **Forest** (ok / ok-soft), **Amber** (warn / warn-soft), **Signal Red** (bad / bad-soft): status badges, error notes, failed-build panels and the danger button. Always as a soft ground with the strong hue as text.

### Dark theme
The `.dark` class remaps every token under the same name: wall #15171a, panel #1c1f23, plinth #24282d, rule #30353c, rule-strong #454c55, ink #eceef1, slate #a7b0ba; rail #3a0d15, rail-active #5c1622, rail-muted #d9b9be; accent #e8798a with dark accent-ink #15171a (oxblood is raised to a light rose so it passes AA as text and as a fill), accent-hover #f08f9e, accent-soft #3a1a20; ok #7fd1a4, warn #f3c068, bad #ff9b8f over deep soft grounds.

### Named Rules
**The One Paint Rule.** Oxblood is the only accent. It marks action, selection, focus and provenance; it is never decoration, and no second accent hue is introduced.

**The Meaningful State Rule.** Forest, amber and red appear only when they report a real state (ready, needs rebuild, failed). A neutral thing is never coloured to look lively.

**The Mode Pair Rule.** Every colour is a token with a light and a dark value under one name; components never hard-code a hex.

## Typography

**Display Font:** Hanken Grotesk Variable (with ui-sans-serif, system-ui)
**Body Font:** Hanken Grotesk Variable
**Label/Mono Font:** JetBrains Mono Variable (with ui-monospace), for ids, scores, timings and accession marks only

**Character:** A clear, slightly humanist grotesk doing all the work, like wayfinding type in a museum; the mono is the registrar's hand on the accession card. Stylistic sets ss01 and cv11 and tabular figures are on globally.

### Hierarchy
- **Display** (600, 48px, 1.04, tight tracking): the Welcome wall headline only.
- **Headline** (600, 34px, 1.12, tight tracking): page titles in the page header, the "Ready to answer" outcome, the empty-chat prompt.
- **Title** (600, 24px, 1.25): section headings, chatbot names on the home wall, empty-state titles, stat values.
- **Body large** (400, 18px, 1.45): answer text, the user's messages, the Welcome lede.
- **Body** (400, 15px, 1.5): the default UI size: controls, descriptions, tables, nav items. Descriptions cap at roughly 42rem.
- **Label** (600, 13px, 1.35, sentence case): badges, stat labels, list captions such as "Sources" and "Try asking", provenance lines (at 400).
- **Mono** (600, 13px, slight positive tracking): accession marks (S1), citation marks, scores and timings.

### Named Rules
**The Ladder Rule.** Only the six sizes 13/15/18/24/34/48 exist. Nothing is set between rungs.

**The Weight-Not-Paint Rule.** Rank comes from size and weight (400 or 600; 700 only in the wordmark). Headings are ink, never accent-coloured, and never uppercase.

**The Machine Voice Rule.** Mono is for values a machine produced: ids, scores, timings, source marks. Prose is never set in mono.

## Layout

A fixed 256px rail on the left from 768px up; below that the rail becomes an off-canvas drawer over a 40% black scrim, and a 56px panel-white header carries a menu button and the Wordmark. The wall to the right scrolls independently.

Pages open with a page header (title and description left, actions right, wrapping below) on a 20px gutter on mobile and 40px from 768px, with 32px / 40px top padding. The primary action always sits top right of the wall. Content uses the same gutters; reading surfaces centre at max 72rem (chat), descriptions and prose cap at 42-48rem.

Two-column patterns recur from 1024px: the home wall hangs chatbot labels in a 2-column grid with 24px gaps; chat and build pages pair a fluid main column with a 17rem margin column (sources beside the answer, 40px gap); setup pairs a 15rem section index with the form. Stats sit in 2-up grids rising to 3 or 4 across. Vertical rhythm: 24px between sibling cards, 40px between chat turns, 32px for section padding.

## Elevation & Depth

Flat by default. Depth comes from tonal layering (wall, then panel white, then plinth) and hairline borders, not shadows. The only shadows in the system are the modal dialog's long soft drop and the switch thumb's 1px contact shadow; the modal sits over a 45% ink backdrop.

### Shadow Vocabulary
- **Modal drop** (`box-shadow: 0 24px 60px -20px rgb(0 0 0 / 0.45)`): center and side dialogs only.
- **Thumb contact** (`box-shadow: 0 1px 2px rgb(0 0 0 / 0.25)`): the switch knob.

### Named Rules
**The Hairline Rule.** Surfaces separate with a 1px rule or a tonal step. Cards, labels and panels never carry shadows at rest; highlighting a source uses a 2px oxblood ring, not a lift.

## Shapes

Two corner languages, kept apart on purpose. Labels are nearly square (3px): the tombstone label, home-wall chatbot labels, citation marks and the highlight ring around a source. Controls are gently rounded (8px): buttons, inputs, selects, text areas, nav items, suggestion buttons, error notes. Badges sit between (6px). Large panels and the centre dialog use 12px, as does the user's message bubble; the side dialog is square to the viewport edge. Pills (full round) are reserved for switches, status dots and progress bars.

The Wordmark is a 2px-stroked rounded square enclosing a geometric A.

## Components

### Buttons
Quiet, confident and heavy-weighted; one primary per region.
- **Shape:** gently rounded (8px), 1px border on every variant so sizes align.
- **Primary:** oxblood fill, white semibold text; 40px tall with 16px sides (sm 32px / 12px at 13px; lg 48px / 20px at 18px).
- **Hover / Focus:** colour-only transitions at 150ms; primary deepens to Oxblood Deep, secondary's border darkens to ink. Focus is a 2px oxblood outline at 2px offset everywhere.
- **Secondary:** panel white with a strong hairline border, ink text.
- **Ghost:** transparent, slate text, plinth fill on hover; used for icon-only settings, delete and close actions.
- **Danger:** panel white, red text, red border and soft red fill on hover.
- **Busy / Disabled:** a spinning loader sits before the label and the button disables; disabled drops to 45% opacity with a not-allowed cursor.
- Links that act as buttons take the same classes; a button is never nested in a link.

### Chips (status badges)
- **Style:** 24px tall, 6px corners, 13px semibold, soft tonal ground with the strong tone as text (neutral, accent, ok, warn, bad).
- **State:** words are plain language for non-technical readers ("Needs rebuild", "Declined to answer", "Answered from memory"), never raw state names.

### Cards / Containers
- **Corner Style:** 3px for labels, 12px for outcome and stage panels.
- **Background:** panel white on the wall; failed panels use the soft red ground with no border.
- **Shadow Strategy:** none (see The Hairline Rule).
- **Border:** 1px Hairline.
- **Internal Padding:** labels 12px / 14px; home-wall chatbot labels 20px top, 24px sides; panels 24px rising to 32px.

### Inputs / Fields
- **Style:** panel white, 1px strong hairline, 8px corners, 40px tall (text areas grow with 10px vertical padding and relaxed leading), 15px ink text, slate placeholders, oxblood caret. Controls fill their container unless given a width.
- **Focus:** the border turns oxblood; hover darkens it to slate.
- **Error / Disabled:** `aria-invalid` turns the border red; disabled fades to 60%.
- **Switch:** 44 x 24 pill, oxblood when on, strong hairline when off, white thumb sliding at 150ms.

### Navigation
- **Rail:** oxblood-painted, 256px, Wordmark at the top, main nav (Chatbots, Health), then when inside a chatbot a captioned sub-navigation (Ask, Setup, Builds, Evaluate, Activity), with service status, theme and access-key utilities pinned to the bottom above a 15% white hairline.
- **Items:** 40px tall, 8px corners, 18px icons, 15px rail-rose text; hover fills with Rail Wash at 60% and turns white; the active item inverts to a white pill with rail-coloured semibold text.
- **Mobile:** the rail slides in over 200ms; it is inert and hidden from assistive tech when closed and closes after every navigation.

### Tombstone Label (signature)
The museum object label. A 3px-cornered hairline card on panel white: an optional accession mark in mono oxblood semibold (S1, or "S1 · 82% match"), the title in semibold ink, and provenance lines in 13px slate joined by middle dots. In chat, sources hang in the 17rem margin beside their answer; inline citation marks are small 3px oxblood-blush tabs in mono that invert to oxblood on hover and point at the matching label, which takes a 2px oxblood ring when highlighted. On the home wall each chatbot hangs as a larger label (headline-size name, status badge, a one-line count summary, one status-driven action).

### Dialog
Native modal dialog: centred at max 512px with 12px corners, or a side sheet the full viewport height at max 672px with square corners. Header and footer are separated by hairlines; footer actions align right, primary last.

### Motion
New assistant answers settle in (opacity and 4px rise, 220ms, ease-out-expo `cubic-bezier(0.16, 1, 0.3, 1)`); a blinking oxblood block caret marks streaming text. All motion is disabled under reduced-motion preferences.

## Do's and Don'ts

### Do:
- **Do** caption every answer source with a tombstone label in the margin beside the answer: mono oxblood mark, semibold title, slate provenance.
- **Do** keep oxblood for action, selection, focus and provenance, and put the single primary action top right of the wall.
- **Do** separate surfaces with 1px hairlines and tonal steps (wall, panel, plinth).
- **Do** set type only on the 13/15/18/24/34/48 ladder, ranking with size and weight.
- **Do** use soft-ground status badges with plain-language words for every state a user sees.
- **Do** define every new colour as a light/dark token pair and check it against WCAG AA in both themes.

### Don't:
- **Don't** add a second accent hue or tint neutral content to look lively.
- **Don't** put shadows on cards, labels or panels; shadows belong to the modal layer only.
- **Don't** round labels like controls or controls like labels: labels 3px, controls 8px.
- **Don't** set prose in mono, or set headings in accent colour or uppercase.
- **Don't** add small uppercase kickers or eyebrows above headings; step numbers go in the heading itself ("1. Name it").
- **Don't** build the grey-sidebar-plus-stat-tiles admin template or a dark neon AI console.
- **Don't** mark a label with a coloured left rule; the label is a whole hairline card with its accession mark.
