---
name: Archie
description: A Mac app for your documents; answers whose sources open like Quick Look.
colors:
  wall: "#f5f5f7"
  panel: "#ffffff"
  plinth: "#f0f0f2"
  rule: "rgb(60 60 67 / 0.12)"
  rule-strong: "rgb(60 60 67 / 0.22)"
  ink: "#1d1d1f"
  slate: "#6e6e73"
  faint: "#8a8a8e"
  rail: "rgb(242 242 245 / 0.82)"
  rail-ink: "#1d1d1f"
  rail-muted: "#6e6e73"
  rail-active: "rgb(0 0 0 / 0.07)"
  rail-ok: "#248a3d"
  rail-bad: "#d70015"
  accent: "#9e2a4a"
  accent-hover: "#87213e"
  accent-ink: "#ffffff"
  accent-soft: "#f6e8ed"
  bubble: "#9e2a4a"
  bubble-ink: "#ffffff"
  ok: "#248a3d"
  ok-soft: "#e6f4ea"
  warn: "#b25000"
  warn-soft: "#fdf0e3"
  bad: "#d70015"
  bad-soft: "#fde8ea"
typography:
  display:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Display, Segoe UI Variable Display, Segoe UI, system-ui, sans-serif"
    fontSize: "1.75rem"
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "-0.015em"
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Display, Segoe UI Variable Display, Segoe UI, system-ui, sans-serif"
    fontSize: "1.375rem"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "-0.025em"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Segoe UI Variable Text, Segoe UI, system-ui, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 600
    lineHeight: 1.35
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Segoe UI Variable Text, Segoe UI, system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "\"tnum\""
  body-sm:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Segoe UI Variable Text, Segoe UI, system-ui, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Segoe UI Variable Text, Segoe UI, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: 1.35
  mono:
    fontFamily: "ui-monospace, SF Mono, SFMono-Regular, Cascadia Mono, Menlo, Consolas, monospace"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.35
rounded:
  segment: "5px"
  control: "6px"
  segmented: "7px"
  group: "10px"
  sheet: "14px"
  bubble: "18px"
  full: "9999px"
spacing:
  hairline-gap: "2px"
  xs: "4px"
  sm: "8px"
  md: "12px"
  row-x: "16px"
  pane-x: "20px"
  pane-x-wide: "32px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.control}"
    padding: "0 14px"
    height: "32px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-secondary:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.control}"
    padding: "0 14px"
    height: "32px"
  button-secondary-hover:
    backgroundColor: "{colors.plinth}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.accent}"
    rounded: "{rounded.control}"
    padding: "0 10px"
    height: "28px"
  button-ghost-hover:
    backgroundColor: "{colors.accent-soft}"
  button-danger:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.bad}"
    rounded: "{rounded.control}"
    padding: "0 14px"
    height: "32px"
  button-danger-hover:
    backgroundColor: "{colors.bad-soft}"
  input:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 10px"
    height: "32px"
  badge-neutral:
    backgroundColor: "{colors.plinth}"
    textColor: "{colors.slate}"
    typography: "{typography.label}"
    rounded: "{rounded.full}"
    padding: "0 10px"
    height: "22px"
  badge-accent:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent}"
    typography: "{typography.label}"
    rounded: "{rounded.full}"
    padding: "0 10px"
    height: "22px"
  badge-ok:
    backgroundColor: "{colors.ok-soft}"
    textColor: "{colors.ok}"
    rounded: "{rounded.full}"
  badge-warn:
    backgroundColor: "{colors.warn-soft}"
    textColor: "{colors.warn}"
    rounded: "{rounded.full}"
  badge-bad:
    backgroundColor: "{colors.bad-soft}"
    textColor: "{colors.bad}"
    rounded: "{rounded.full}"
  citation-chip:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent}"
    typography: "{typography.label}"
    rounded: "{rounded.full}"
    padding: "0 8px"
    height: "20px"
  citation-chip-hover:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
  segmented-control:
    backgroundColor: "{colors.plinth}"
    rounded: "{rounded.segmented}"
    padding: "2px"
  segmented-option-selected:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.segment}"
    padding: "0 12px"
    height: "24px"
  group:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.group}"
  group-row:
    typography: "{typography.body}"
    padding: "10px 16px"
    height: "44px"
  sidebar-row:
    textColor: "{colors.rail-ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.control}"
    padding: "0 8px"
    height: "32px"
  sidebar-row-active:
    backgroundColor: "{colors.rail-active}"
  user-bubble:
    backgroundColor: "{colors.bubble}"
    textColor: "{colors.bubble-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.bubble}"
    padding: "10px 16px"
  sheet:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sheet}"
    width: "512px"
  quick-look:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.sheet}"
    width: "576px"
---

# Design System: Archie

## Overview

**Creative North Star: "The Document Mac App"**

Archie behaves like a native Mac application in the family of Notes, Mail and System Settings: a translucent source-list sidebar of chatbots, one calm content pane topped by a large title, inset grouped sections on a soft grey ground, and hairline separators instead of borders. Nothing is branded by decoration; the brand lives in one colour, Archie wine, used exactly where macOS uses system blue: actions, selection, links and the person's own words.

The signature is Quick Look for sources. Every answer carries capsule citation chips and a row of source cards; any of them opens a panel that grows from the element that was pressed, showing the passage, file, page and match, and closes on Space or Escape. The second recurring idea is that a change stays lit until noticed: a chatbot whose build finished keeps a wine dot in the sidebar until it is opened, and build progress updates in place.

Density is Mac-comfortable, not dashboard-dense: 13 px controls, 15 px body, 44 px grouped rows, a 736 px reading column in Ask. Depth comes from tonal layering (white on #f5f5f7) and half-pixel rings, with real shadows reserved for things that float. The system refuses the web-admin template (top bar, stat tiles, a dense table as the page) and the dark neon AI console.

**Key Characteristics:**
- Platform type only: SF Pro on Apple devices, Segoe UI Variable on Windows, system-ui elsewhere, on a 12/13/15/17/22/28 ladder with semibold for rank.
- One accent (Archie wine) for actions, selection, links and user bubbles; status hues only where they mean state.
- Inset grouped sections (10 px) with inset hairlines; 6 px push buttons; capsule badges and chips; segmented controls for view switching.
- Translucent sidebar and toolbar over content, with backdrop blur.
- Sheets for modal tasks, side panels for inspection, Quick Look for sources.
- Light and dark are both first-class; dark is a re-mapped token set, not an inversion.

## Colors

A neutral Apple-grey palette carrying a single warm wine accent and Apple's accessible status hues.

### Primary
- **Archie Wine** (accent): primary buttons, the Ask send button, ghost-button text, links, citation chip text, the switch's on state, the focus ring (45% mix) and text selection (22% mix), the streaming caret, the unseen-build dot, and the app mark. In dark mode it lifts to a pink (#ff7a9c) with near-black ink on it.
- **Wine Pressed** (accent-hover): hover state of filled accent buttons only.
- **Wine Wash** (accent-soft): citation chips at rest, accent badges, the chatbot initial tile, step icons in the setup assistant, "Edited" markers, the selected Builds history row.
- **Conversation Wine** (bubble / bubble-ink): the person's own messages, white on wine. In dark it is a deeper wine (#a3304f) so white text keeps contrast.

### Neutral
- **Grouped Ground** (wall): the soft grey behind grouped content: Manage, Settings, the setup assistant, and icon wells in source cards and Quick Look.
- **Content White** (panel): the content pane, grouped sections, sheets, inputs, secondary buttons.
- **Fill Grey** (plinth): hover fills, segmented-control track, neutral badges, skeleton bars, sheet close buttons.
- **Hairline** (rule): separators between rows, toolbar and sidebar edges, the ring around grouped sections.
- **Control Edge** (rule-strong): the half-pixel ring on buttons, inputs, source cards and the ask field; the switch's off track; scrollbar thumb.
- **Label** (ink), **Secondary Label** (slate), **Tertiary Label** (faint): text in three ranks. Tertiary is only for placeholders, decorative icons and non-essential text at 15 px or larger.
- **Source List** (rail, rail-ink, rail-muted, rail-active): the translucent sidebar, its text, section headers and icons, and the row highlight.

### Status
- **Green** (ok / ok-soft), **Orange** (warn / warn-soft), **Red** (bad / bad-soft): status badges, status dots, error notes, the danger button's text, invalid-field rings. rail-ok and rail-bad repeat green and red for the sidebar's service dot.

### Named Rules
**The System Blue Rule.** Archie wine appears only on actions, selection, links, citations and the person's own messages. It never colours a heading, a panel, a border or a decorative surface.

**The State Only Rule.** Green, orange and red mean state and nothing else. A screen with nothing wrong shows no red and no orange.

**The Three Labels Rule.** Text uses ink, slate or faint; never a fourth grey, and never faint for anything a person must read below 15 px.

## Typography

**Display Font:** SF Pro Display (with Segoe UI Variable Display, Segoe UI, system-ui)
**Body Font:** SF Pro Text (with Segoe UI Variable Text, Segoe UI, system-ui)
**Label/Mono Font:** SF Mono (with Cascadia Mono, Menlo, Consolas) for ids, timings and model names

**Character:** The operating system's own voice. Hierarchy comes from size steps and weight (semibold for rank, bold only on the large title), not from a branded face. Numerals are tabular everywhere.

### Hierarchy
- **Display** (700, 28 px, 1.15, -0.015em): the large title at the top of every content pane ("Ask Team handbook") and each step of the setup assistant.
- **Headline** (600, 22 px, 1.25, tight tracking): section titles inside a pane: the Settings stage title, the inline build-progress title, the not-yet-built notice.
- **Title** (600, 17 px, 1.35): sheet titles, empty-state titles, the Ask field's typed text (17 px regular).
- **Body** (400, 15 px, 1.5): answers, passage text, grouped-row labels, descriptions under the large title. Reading column capped at 736 px.
- **Body small** (400 or 500, 13 px, 1.45): controls, buttons, sidebar rows, row details, group headers (semibold, slate), the one-line "how answered" account.
- **Label** (500, 12 px, 1.35): badges, citation chips, Manage tab labels, sidebar section header ("Chatbots", semibold), group footers, captions. Sentence case, never uppercase.

### Named Rules
**The Platform Voice Rule.** No webfont. Every surface uses the system stack so Archie reads as part of the computer it runs on.

**The Sentence Case Rule.** Headers, group titles and labels are sentence case at normal tracking. No uppercase, no letter-spaced small caps.

## Layout

A two-column application window. At 768 px and up, a fixed 260 px sidebar sits beside the content pane; below 768 px the sidebar becomes an off-canvas drawer opened from a toolbar menu button, with a 25% black scrim.

The content pane has a sticky 52 px toolbar (translucent white at 85% with blur, hairline below) in a three-column grid: title leading, the Ask | Manage segmented control centred, trailing area. Content scrolls beneath it. Pane gutters are 20 px, widening to 32 px at 768 px; the large title sits 28 px below the toolbar with 20 px under it.

Ask is a single centred column (max 736 px) with the conversation above and the ask field pinned at the bottom in the same width. Messages are separated by 36 px. Manage sits on the grouped ground with centred icon-over-label tabs, then grouped sections in columns no wider than 672 to 896 px. Grouped rows are at least 44 px tall with 16 px horizontal and 10 px vertical padding. Common gaps are 8 and 12 px; sidebar rows stack 2 px apart.

## Elevation & Depth

Depth is tonal first: white content sits on the #f5f5f7 ground, and edges are drawn with half-pixel rings (`0 0 0 0.5px` of rule or rule-strong) rather than borders. Real shadows appear only on things that sit above the window or are meant to be pressed: buttons get a 1 px lip, the selected segment a small lift, the ask field a soft 16 px float, and sheets and Quick Look a deep, wide drop.

### Shadow Vocabulary
- **Group ring** (`box-shadow: 0 0 0 0.5px var(--color-rule)`): grouped sections, the selected Manage tab, welcome cards.
- **Control ring** (`box-shadow: 0 0 0 0.5px var(--color-rule-strong), 0 1px 1px rgb(0 0 0 / 0.06)`): secondary and danger buttons; source cards use the ring alone.
- **Primary lip** (`box-shadow: 0 1px 1px rgb(0 0 0 / 0.12)`): filled accent buttons.
- **Selected segment** (`box-shadow: 0 1px 2px rgb(0 0 0 / 0.12), 0 0 0 0.5px rgb(0 0 0 / 0.06)`): the chosen option in a segmented control.
- **Ask field float** (`box-shadow: 0 0 0 0.5px var(--color-rule-strong), 0 4px 16px rgb(0 0 0 / 0.06)`): the pinned ask field.
- **Sheet** (`box-shadow: 0 0 0 0.5px rgb(0 0 0 / 0.12), 0 22px 70px rgb(0 0 0 / 0.28)`): dialogs and side panels, over a 28% black backdrop.
- **Quick Look** (`box-shadow: 0 0 0 0.5px rgb(0 0 0 / 0.12), 0 30px 80px rgb(0 0 0 / 0.3)`): the source preview.

### Named Rules
**The Half-Pixel Rule.** Edges are 0.5 px rings in rule or rule-strong. Solid 1 px borders are used only for the sidebar, toolbar and internal separators.

**The Float Means Above Rule.** A diffuse shadow is earned only by something above the window plane: a sheet, Quick Look, or the pinned ask field.

## Shapes

Gently rounded, nested by size: segments (5 px) inside a segmented track (7 px), push buttons, inputs and sidebar rows (6 px), grouped sections, source cards and error notes (10 px; large buttons too), sheets, Quick Look and the ask field (14 px). Badges, citation chips, switches, status dots and the round send button are full capsules or circles. User bubbles are 18 px with the trailing bottom corner tightened to 6 px, as in Messages. The app mark and chatbot initial tiles are 6 px rounded squares.

## Components

### Buttons
Quiet Mac push buttons that confirm the press.
- **Shape:** gently rounded (6 px); small 28 px, regular 32 px, large 44 px with a 10 px radius.
- **Primary:** wine fill, white text, 13 px medium, 14 px side padding, a 1 px lip; hover deepens to Wine Pressed.
- **Secondary:** white with a control ring; hover fills grey. **Ghost:** wine text, no fill, wine wash on hover. **Danger:** white with red text, red wash on hover.
- **Press:** every pressable scales to 0.97 over 160 ms on cubic-bezier(0.23, 1, 0.32, 1). Disabled drops to 40% opacity. The cursor stays the arrow, as on the desktop.

### Badges and Chips
- **Badges:** 22 px capsules, 12 px medium, tinted fill with matching text: neutral, accent, ok, warn, bad. Status words are plain language ("Needs rebuild", "Finished with problems"), never internal state names.
- **Status dots:** 8 px circles with an accessible name, as macOS shows availability.
- **Citation chips:** 20 px capsules, wine on wine wash inline in the answer; hover inverts to white on wine; an unknown citation shows red.

### Grouped Sections
- **Corner Style:** 10 px, clipping their rows.
- **Background:** white, with the group ring, on the grouped ground.
- **Structure:** optional 13 px semibold slate header above, rows divided by hairlines, optional 12 px slate footer below. Rows lead with a 15 px label (13 px detail under it) and trail with the control or value.

### Inputs / Fields
- **Style:** white, 6 px, half-pixel control ring plus a faint inner top shadow; 32 px tall, 15 px text, wine caret, faint placeholder.
- **Focus:** a 3 px wine ring at 35% replaces the outline.
- **Error / Disabled:** invalid fields carry a 1.5 px red ring; disabled at 50% opacity.
- **Switch:** 38 x 22 px capsule, wine when on, Control Edge when off, white 18 px knob sliding 200 ms.
- **Ask field:** a 14 px floating field pinned under the conversation, 17 px text, round 32 px send button in wine (stop button in ink while streaming); focus shows a 3 px wine ring at 30%.

### Navigation
- **Sidebar:** translucent source list (rail at 82% with 2xl backdrop blur and 150% saturation). Wordmark at the top in a 52 px band; a "Chatbots" header; 32 px rows with a 24 px initial tile, name and a trailing state (spinner while building, wine dot for an unseen finished build, status dot when not ready). The foot holds New Chatbot in wine, Status with a live service dot, and Preferences. Active rows take the rail-active fill and medium weight.
- **Toolbar:** 52 px, translucent, with the Ask | Manage segmented control centred.
- **Segmented control:** grey 7 px track with 2 px inset; options 24 px, 13 px medium; the selected one is white with a small lift, the rest slate turning ink on hover. Also used for the theme choice and filters.
- **Manage tabs:** centred icon-over-label buttons (20 px icon, 12 px label, min 80 px); the active tab is white with wine text and the group ring.
- **Mobile:** below 768 px the sidebar slides in from the leading edge over 300 ms on the sheet curve.

### Sheets and Side Panels
Native modal dialogs. Centre sheets are up to 512 px wide, 14 px corners, title 17 px semibold with a round grey close button, actions bottom-trailing; they appear over 240 ms on cubic-bezier(0.32, 0.72, 0, 1), rising 8 px from 98% scale. Side panels (trace, inspection) fill the height with an 8 px inset and slide 24 px in from the trailing edge. Preferences is a sheet of grouped sections.

### Quick Look (signature)
A 576 px panel that grows from the chip or source card that opened it (transform-origin set from that element, scale from 0.5 over 220 ms). Header: 36 px icon well on the grouped ground, file name (15 px semibold), section and pages in slate, and an accent badge with the source label and match percentage. Body: the passage at 15 px relaxed, with markdown tables rendered as hairline-ringed 10 px tables. Footer: "Press Space or Esc to close" and a wine Done link.

### Source Cards
White 10 px cards with the control ring: a 32 px icon well, the wine source label beside the file name (13 px medium), and the section and pages in a 12 px slate line. Two per row from 640 px. Hover fills grey; press opens Quick Look.

### Conversation
User messages are right-aligned wine bubbles (max 85%). Answers are unboxed body text with inline citation chips, then a "Sources" row of source cards, then a one-line slate account of how it was answered with verdict badges and a wine "How Archie found this" link. Streaming shows a pulsing wine dot with the current stage, then a 2 px wine caret. New content settles in (6 px rise, 260 ms).

### Feedback
Empty states are centred: a 40 px faint line icon, a 17 px semibold title, slate body. Skeletons are pulsing grey capsules of decreasing width. Error notes are red text on red wash in a 10 px block.

## Do's and Don'ts

### Do:
- **Do** use the system font stacks and the 12/13/15/17/22/28 ladder; rank with semibold, and reserve bold for the 28 px large title.
- **Do** keep Archie wine to actions, selection, links, citations and user bubbles, the way macOS uses system blue.
- **Do** put settings and lists in inset grouped sections (10 px, hairline-divided rows of at least 44 px) on the grouped ground.
- **Do** draw edges with 0.5 px rings in rule or rule-strong, and save diffuse shadows for sheets, Quick Look and the ask field.
- **Do** give every pressable the 0.97 press scale, and keep fades but drop movement and scaling under reduced motion.
- **Do** open sources with Quick Look from the element that was pressed, closable with Space or Escape.
- **Do** keep a finished build lit in the sidebar until the chatbot is opened.
- **Do** write status in plain language with a status hue, never an internal state name.

### Don't:
- **Don't** build a web-admin layout: no top navigation bar, no stat tiles, no dense table as the page.
- **Don't** drift toward a dark neon AI console; dark mode stays the re-mapped Apple greys.
- **Don't** load a webfont or add a branded display face.
- **Don't** use green, orange or red for anything other than state.
- **Don't** use uppercase or letter-spaced labels, kicker lines above titles, or hard offset shadows.
- **Don't** use faint grey for essential text below 15 px.
- **Don't** add a fourth radius between 10 and 14 px or square off controls.
