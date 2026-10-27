---
name: Raqobat AI Assistant
description: A cloth-bound statute volume: spruce cloth, gilt rules, cold paper, and one vermilion ribbon on every passage the law supplies.
colors:
  cloth: "#2C4A42"
  cloth-2: "#36584F"
  cloth-deep: "#20382F"
  cloth-ink: "#14241F"
  gilt: "#D9BC72"
  gilt-hi: "#E6CD8C"
  gilt-dim: "#A98F55"
  paper: "#EFF1EE"
  paper-shade: "#DADFDA"
  surface: "#FFFFFF"
  tint: "#DDE7E1"
  ink: "#17201C"
  ink-soft: "#4A5751"
  line: "#D5DBD6"
  line-strong: "#B8C2BB"
  ribbon: "#C9402F"
  ribbon-deep: "#9E2F24"
  ok: "#386449"
  ok-bg: "#E3EEE5"
  info: "#315F87"
  info-bg: "#E5EEF7"
  warn: "#795D24"
  warn-bg: "#F6ECD3"
  fix: "#865431"
  fix-bg: "#F5E8DF"
  neutral-chip: "#59574F"
  neutral-chip-bg: "#EAE9E5"
  bad: "#A82A1F"
  bad-bg: "#FBE7E4"
typography:
  display:
    fontFamily: "Bodoni Moda, Didot, Georgia, serif"
    fontSize: "clamp(2.6rem, 6.6vw, 6rem)"
    fontWeight: 700
    lineHeight: 1.04
    letterSpacing: "-0.005em"
  headline:
    fontFamily: "Bodoni Moda, Didot, Georgia, serif"
    fontSize: "1.7rem"
    fontWeight: 700
    lineHeight: 1.2
  title:
    fontFamily: "Source Sans 3, system-ui, sans-serif"
    fontSize: "1.2rem"
    fontWeight: 700
    lineHeight: 1.3
  body-read:
    fontFamily: "PT Serif, Georgia, serif"
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1.68
  body-ui:
    fontFamily: "Source Sans 3, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Source Sans 3, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.4
  data:
    fontFamily: "Red Hat Mono, ui-monospace, Menlo, monospace"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.4
rounded:
  none: "0"
  sm: "2px"
  md: "4px"
  card: "6px"
  pill: "999px"
spacing:
  xs: "6px"
  sm: "8px"
  md: "14px"
  lg: "22px"
  xl: "32px"
  section: "112px"
components:
  button-primary:
    backgroundColor: "{colors.cloth}"
    textColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "0 20px"
    height: "42px"
  button-primary-hover:
    backgroundColor: "{colors.cloth-2}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.cloth}"
    rounded: "{rounded.md}"
    padding: "0 20px"
    height: "42px"
  button-plate:
    backgroundColor: "{colors.gilt}"
    textColor: "{colors.cloth-ink}"
    rounded: "{rounded.sm}"
    padding: "0 30px"
    height: "56px"
  button-plate-hover:
    backgroundColor: "{colors.gilt-hi}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "10px 12px"
    height: "44px"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    padding: "24px"
  source-card:
    backgroundColor: "#F8FAF8"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "18px 20px 16px 72px"
  source-ribbon:
    backgroundColor: "{colors.ribbon}"
    textColor: "{colors.surface}"
    width: "30px"
    height: "56px"
  sidebar:
    backgroundColor: "{colors.cloth}"
    textColor: "#E1EAE5"
    width: "252px"
  badge:
    backgroundColor: "{colors.neutral-chip-bg}"
    textColor: "{colors.neutral-chip}"
    rounded: "{rounded.pill}"
    padding: "3px 10px"
---

# Design System: Raqobat AI Assistant

## Overview

**Creative North Star: "The Cloth-Bound Statute Volume"**

One world serves two surfaces. The showcase page is the volume opened on a table: spruce cloth cover, gilt double rule, a pocket label like a library book, two paper leaves with a ribbon lying in the gutter. The working app is the same volume on the desk: a cloth rail down the left, cold paper and white leaves for work, and the ribbon reappearing as a bookmark on each source passage. The world lends type, palette, density and one signature move. It does not lend decoration to navigation, forms or tables, which stay standard so officers never hunt for a control.

The palette shipped muted: the contract's first ultramarine was replaced by spruce cloth after the saturated blue proved hard on the eyes. Treat the contract's blue hexes as retired. Voice is quiet, bookish, official. Density is working-office: 15px interface text, 17px reading text, 4px corners, hairline borders.

**Key Characteristics:**
- Spruce cloth (rail, cover band, primary actions) against cold neutral paper; gilt only as thin rules, marks and the one landing call to action.
- Four voices, four jobs: Bodoni Moda titles, PT Serif for what is read, Source Sans 3 for the interface, Red Hat Mono for data.
- One vermilion device, the ribbon, reserved for "the law supplied this."
- Flat, hairline-bordered leaves; depth only where the book metaphor needs it on the landing page.
- Cloth texture is a faint woven grain, never an image.

## Colors

A restrained cloth-and-paper palette: one deep green-grey, one soft gilt, cold neutrals, and one hot vermilion held back for the ribbon.

### Primary
- **Spruce Cloth** (#2C4A42): sidebar and login rail, landing cover and closing band, primary buttons, links, focus rings, selected tabs. Hover lifts to **Cloth Lift** (#36584F); pressed-dark and footer use **Deep Cloth** (#20382F). **Cloth Ink** (#14241F) is heading text on paper and text on gilt.
- **Soft Gilt** (#D9BC72): thin rules (3px double at 55-65% alpha), brand mark, active nav text and avatar, the landing plate button, selection highlight. Hover **Pale Gilt** (#E6CD8C); shadow-edge **Dim Gilt** (#A98F55). Gilt is never a large fill in the app.

### Secondary
- **Vermilion Ribbon** (#C9402F): the signature only (see Components). **Ribbon Deep** (#9E2F24) is its text and border form (inline citation markers, selected landing tab).

### Neutral
- **Cold Paper** (#EFF1EE): page ground; **Shade Paper** (#DADFDA) page curl and inactive tabs; **Leaf White** (#FFFFFF) cards, header, inputs.
- **Ink** (#17201C) body text; **Soft Ink** (#4A5751) secondary text; **Tint** (#DDE7E1) text on cloth.
- **Hairline** (#D5DBD6) card borders and dividers; **Strong Hairline** (#B8C2BB) input borders and dashed empties.

### Status (app only; each is text on its own tint)
- OK #386449 on #E3EEE5; info #315F87 on #E5EEF7; warning #795D24 on #F6ECD3; needs-fix #865431 on #F5E8DF; neutral #59574F on #EAE9E5; error #A82A1F on #FBE7E4. The landing page adds Verified green #2F6B4E for its stamp.
- The four landing spines use bound-book colors: #6A4A30, #2F4F5A, #7C2C34, #2C4A42, each with gilt lettering.

### Named Rules
**The One Ribbon Rule.** Vermilion marks source-bearing passages and nothing else. It is not an error color (errors use the brick #A82A1F on a pink tint), not a CTA, not decoration.
**The Cloth Not Blue Rule.** The cool accent is green-grey cloth. Reintroducing saturated blue or ultramarine anywhere is drift.
**The Thin Gilt Rule.** Gilt appears as rule, mark or small plate. If it covers more than a button or a rail rule, it is wrong.

## Typography

**Display Font:** Bodoni Moda (Didot, Georgia fallback), weight 700, optical size pinned low (`font-optical-sizing: none` with `opsz` 8 to 38 by size) so thin hairlines survive at small sizes.
**Body Font:** PT Serif (Georgia) for everything the user reads as content; Source Sans 3 (system-ui) for interface.
**Label/Mono Font:** Red Hat Mono for ribbon numbers, folios, file names, credentials, timestamps.

**Character:** A bookbinder's pairing: a high-contrast title face, a sturdy readable text face, and a plain humanist sans for controls. The sans never carries content prose; the serif never labels a button.

### Hierarchy
- **Display** (Bodoni 700, clamp(2.6rem, 6.6vw, 6rem), 1.04): landing H1 only, gilt on cloth with a 1px dark text-shadow for impression.
- **Headline** (Bodoni 700, 1.5-1.7rem app / clamp(2rem, 4.4vw, 3.2rem) landing, 1.1-1.2): app page title, hero card, login heading, landing section titles.
- **Title** (Source Sans 3 700, 1.1-1.2rem, 1.3): card and section headings, answer sub-headings. Landing article titles use PT Serif 700 1.4-1.5rem.
- **Body, reading** (PT Serif 400, 17px app answers / 18px landing, 1.65-1.68): answers, passages, excerpts; capped at 72ch in the app, 44-60ch on the landing.
- **Body, interface** (Source Sans 3 400-500, 15px, 1.55): forms, lists, buttons (600), table cells (14.5px). Landing prose runs 18px.
- **Label** (Source Sans 3 600, 12.5-14px; table headers uppercase at .05em): field labels, badges, definition terms.
- **Data** (Red Hat Mono 600, 11-15px): ribbon numbers, citation markers, folios, credentials, file names. Statistics use PT Serif 700 1.9rem tabular.

### Named Rules
**The Four Voices Rule.** Assign type by job (title, read, operate, measure). A new element picks one of the four and never a fifth family.
**The Pinned Optical Size Rule.** Bodoni is always set with optical sizing off and an explicit `opsz`; never let it default to its hairline-thin large cut at small sizes.

## Layout

App: a 252px cloth rail beside a fluid column; sticky 68px white header; content max 1240px with 28px/32px padding; cards stack on a 22px gap; two-column splits at .85fr/1.15fr; dashboard and role grids 2 and 3 columns. Under 900px the rail becomes a 272px drawer with a dim backdrop, grids collapse to one column, and touch targets rise to 44px. Under 560px padding tightens (card 18px) and source cards shift their ribbon left.

Landing: 1160px wrap with 32px gutters (20px under 820px); sections separated by 112px (88px small); a cloth cover zone, the open volume (two 660px leaves, one 420px leaf per side on mobile), a shelf of four spines beside a reading leaf, a 12-column plate grid alternating image sides, a two-column articles block, a dot-leader colophon, and a closing cloth band. Spacing steps in use: 6, 8, 10, 14, 18, 22, 32.

Minimum control height is 42-44px. Breakpoints: 1020, 900, 820, 560.

## Elevation & Depth

Flat at rest. Hairline borders and tonal steps (cloth, paper, white) carry hierarchy in the app; the only app shadows are a 1px bottom edge on cards and primary buttons and the drawer shadow when open. On the landing page, the book metaphor earns real shadows: a drop-shadow under the open volume, a soft lift under plates and the reading leaf, and spines that rise 8px on hover and 26px when selected.

### Shadow Vocabulary
- **Leaf edge** (`0 1px 0 rgba(20,36,31,.04)`): app cards.
- **Plate lift** (`0 22px 28px -22px rgba(20,36,31,.5)`): landing plates and the reading leaf.
- **Volume drop** (`drop-shadow(0 30px 28px rgba(0,0,0,.5))`): the open volume.
- **Drawer** (`18px 0 40px rgba(20,36,31,.35)`): mobile rail when open.

### Named Rules
**The Flat Work Surface Rule.** Working screens get no lift on cards, lists or forms; the book's depth belongs to the showcase.

## Shapes

Square-leaning: 4px on buttons, inputs, list rows, source cards; 6px on cards and the stat strip; 2px on landing plates and pocket label; pills (999px) only for badges, count chips and the date. Brand mark and seal are square with a 1px gilt border plus a 1px offset outline ring. Status indicators are 9-10px squares, not dots. The loader is the one circle. Frames use a 3px double gilt line at 55-65% alpha. Plates carry two folded corner triangles in deep cloth.

## Components

### Buttons
- **Shape:** 4px corners, 42px tall, 600 weight 15px.
- **Primary:** Spruce Cloth fill, white text, 1px cloth-ink bottom edge, 20px side padding. Hover shifts to Cloth Lift and rises 1px (`.3s cubic-bezier(.16,1,.3,1)`).
- **Secondary / action row:** white fill, cloth text and 1px cloth border; hover fills #EAF0EC.
- **Link buttons:** transparent, cloth 600 13.5px, underline on hover; danger variant uses error brick.
- **Plate (landing only):** gilt fill, cloth-ink text, 56px tall, 2px corners, inset 1px keyline, hover to Pale Gilt and 2px rise. One per view.
- **Disabled:** 55% opacity, not-allowed cursor.

### Inputs / Fields
- 44px min height, white, 1px strong hairline, 4px corners. Focus: border turns cloth with a 3px cloth ring at 22% alpha. Placeholder #66726C. Textareas auto-grow where supported. Checkboxes use cloth accent. File drop: dashed 1.5px strong hairline, tint on hover.

### Navigation
- Rail: cloth with woven grain, 3px double gilt right edge. Items 44px, 15px 500, radius 4px; hover white at 8%; active gilt at 16% with pale-gilt 600 text and gilt icon stroke. 20px stroke icons (1.7 weight, inline SVG). Profile block under a gilt hairline with a square gilt avatar.
- Tabs: bottom 3px cloth bar when selected. Segmented control: cloth border, selected cell cloth fill.

### Cards / Containers
- White, 1px hairline, 6px corners, 24px padding. Tinted sub-panels (#F8FAF8) for sources, workload and diagnostics. Empty states: dashed strong hairline on #F5F7F5. Tasks go amber-red (#FFF8F6, #E8B9B0) when overdue. Stat strip: four cells divided by hairlines, PT Serif numerals.

### Badges
- Pill, 12.5px 600, tinted pair from the status set; late uses error pair.

### Signature: The Ribbon
A vermilion bookmark with a swallow-tail notch (`clip-path` ending in a 50% point 11px above the bottom). In the app it hangs from the top edge of every source card at left 22px, 30px by 56px, carrying the source number in Red Hat Mono 700 white; the card is padded 72px left to clear it. Inline citations in the answer are small 18px squares with a vermilion hairline, ribbon-deep Red Hat Mono numerals. On the login rail a 30 by 150px ribbon hangs at 12% from the right. On the landing it is the full-height 22px ribbon lying in the volume gutter (with a faint highlight stripe), the tail under each spine, and the selected tab. Motion: it drops 26px into place over 1.1s on load and swings -3deg to rest over .9s when the question changes; both off under reduced motion and the swing off on mobile.

### Landing-only pieces
- **Pocket label:** paper card on the cloth cover with dl of credentials in mono and a status square (green on, ribbon red off).
- **Stamp:** bordered verified-green uppercase mark rotated -2deg.
- **Colophon:** serif list with dotted leaders and tabular bold figures.

## Do's and Don'ts

### Do:
- **Do** use Spruce Cloth (#2C4A42) as the only cool accent and Cold Paper (#EFF1EE) as the ground.
- **Do** put every source passage behind a vermilion ribbon carrying its number in Red Hat Mono.
- **Do** set reading text in PT Serif 17px at about 1.68 line-height and keep it under 72ch.
- **Do** keep controls at 42-44px minimum height with a visible 2px cloth focus ring (gilt-hi on cloth).
- **Do** use the Bodoni titles with pinned optical size; keep titles to the page title, hero, login and landing sections.
- **Do** keep motion to transform and opacity with the `cubic-bezier(.16,1,.3,1)` ease, 0.2-0.35s for states, and honor `prefers-reduced-motion`.
- **Do** tint status messages with their paired background; keep numerals tabular.

### Don't:
- **Don't** bring back saturated ultramarine or any cobalt blue; the shipped system is muted on purpose.
- **Don't** use vermilion for errors, buttons, links or decoration.
- **Don't** give working screens card shadows, glass, gradients or rounded-pill buttons; depth is a showcase privilege.
- **Don't** put Bodoni on controls, tables or long text, and don't add a fifth font family.
- **Don't** use gilt as a large fill or as text on paper (contrast); it lives on cloth.
- **Don't** put borders thicker than 1px on cards, or side-stripe accents on list items; the 3px double rule belongs to frames and the rail edge only.
- **Don't** recreate the contract's retired hex values.

## Not Canonized (defects the build carries)

- The login rail's uppercase, wide-tracked gilt line above the headline is a kicker; it is not a pattern for new surfaces.
- The landing plate button retains a navy outline (`rgba(12,23,68,.55)`) and a hard 2px offset edge from the retired blue palette; the answer and passage text color #1A1D28 is a leftover bluish near-black. New work uses Cloth Ink and Ink.
- The glyph arrow in the "back to project" link is a text glyph, not a system icon.
