# AION EMS Zeus UI Style Guide

Version: 12.0.0-beta.1

## Page structure

Each page uses a page header, an optional period/action area, KPI cards, content panels, and a clear empty/loading/error state.

## Spacing

- Page sections: 16–24 px
- Panel padding: 18–24 px
- Card gaps: 12–16 px
- Mobile layouts collapse to one column without overlapping content.

## Cards

Use the shared panel surface, border, radius, and typography. Values and units must remain visually separated. Cards must set `min-width: 0` and allow safe text wrapping.

## Safety

Recommendation-only messaging remains visible but must never overlap analytics content.

## Error handling

A page renderer failure must display the standard recovery panel rather than blanking the entire Zeus interface.


## v12 beta.2 consistency rules

- Pages use a 1480 px maximum content width with responsive side padding.
- Page headers wrap actions cleanly instead of compressing titles.
- Standard panels use consistent padding, 20 px radii, border treatment and shadow.
- KPI, insight and recommendation grids collapse from four/two columns to one column on narrow phones.
- Text, values, tables and controls must wrap or scroll inside their own container; they must never overlap another section.
- Empty, loading and error states use the same isolated panel structure.
- Battery and Kiosk layouts follow the same spacing and responsive rules as the rest of Zeus.
