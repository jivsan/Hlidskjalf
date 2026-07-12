# Screenshots — v0.3-alpha

**Version:** v0.3-alpha

This release includes the major switch visualizer enhancements (Arista 7050TX integration) and refinements to the cyberpunk / Tokyo Night UI.

All changes from the v0.2-alpha work + new switch section.

**Before (v0.2-alpha) examples (copied from previous version for direct comparison):**
- Fleet: ![Fleet before](fleet.png)
- VM Overview: ![VM overview before](vm-overview.png)
- Node: ![Node before](node.png)

See full previous gallery: [../v0.2-alpha/README.md](../v0.2-alpha/README.md)

**After (v0.3-alpha):** The new switch section and UI refinements. (Real PNGs of the live /switch page with SVG faceplate will be added after testing. This README provides the comparison and description.)

## Comparison: Before vs After

### Fleet / Overview (baseline unchanged visually in this release, but now complemented by switch)
- **Before (v0.2):** Standard table + basic cards.
- **After (v0.3):** Same core, but integrated with the new dedicated switch visualizer for full network view.

### New: Switch Section (/switch) - Major v0.3 Addition
- **Physical Faceplate (SVG):** Renders like the real Arista 7050TX hardware.
  - Accurate port layout (48x 10G-T + 4x SFP+).
  - Clickable ports to set/edit notes.
  - Color-coded status (green=connected, red=down).
  - Blinking LEDs for live activity (cyan IN, pink OUT).
  - LLDP info: shows connected device name and port ("what machine goes where").
  - Fetched interface descriptions from the switch + user notes.

- **Top Talkers:** Live top ports by traffic.
- **Rack-like presentation:** Bezel, ears, industrial look inside the cyberpunk UI.

**Example text rendering of the faceplate (actual is interactive SVG in the app):**

```
[ ARISTA 7050TX-48T-4SFP+  •  RACK 47 ]
[Port grid with status LEDs + blinking activity + LLDP labels]
SFP ports on right
Click any port → edit note (persisted in panel DB)
```

Visit the live panel at `/switch` (after starting the dev servers) to see the real thing.

### Styling Improvements (v0.3)
- More human/Flux-like: cleaner cards, subtle effects, better readability.
- Still cyberpunk Tokyo Night (cyan/pink neon) but refined.

Full before gallery in v0.2-alpha. The switch is the key new "screenshot" feature for v0.3-alpha.

## Comparison: Before vs After

### Fleet / Overview
- **Before (v0.2):** Standard table + basic cards.
- **After (v0.3):** Enhanced with more integrated network awareness; switch section now provides port-level visibility that complements fleet bandwidth.

### New: Switch Section (/switch)
This is the major addition in v0.3-alpha.

- **Physical Faceplate View:** SVG layout that mimics the actual Arista 7050TX-48T-4SFP+ front panel.
  - 48x 10GBASE-T ports (RJ45 style in two rows).
  - 4x SFP+ uplinks on the right.
  - Rack bezel and ear styling for "rack-like" look.
  - Clickable ports: Click to edit notes directly.
  - Status indicators: Green for connected, red for down.
  - Blinking activity LEDs: Cyan for IN, pink for OUT when traffic > threshold (integrated with existing .led styles).

- **LLDP Neighbors:** Shows "what's plugged in" (system name and port) for machine-to-port mapping.

- **Interface Descriptions:** Fetched from the switch and displayed.

- **Editable Notes:** Per-port notes (stored in panel DB). Can supplement or override switch descriptions.

- **Top Talkers:** Sidebar showing top 5 ports by current traffic rate, with LLDP info.

- **Live Updates:** Polls every ~4s for rates and status.

Example of the faceplate (text representation of the SVG):

```
[ ARISTA 7050TX-48T-4SFP+  •  RACK 47 ]
Port 1-24 (top row) ... [blinking LEDs for active ports]
Port 25-48 (bottom) ...
SFP 1-4 (right side)
```

Full interactive SVG is rendered in the UI (see the /switch page after running the panel).

### Styling Improvements
- Refined cyberpunk theme to be more "human" and Flux-panel inspired:
  - Cleaner cards with subtle shadows (not heavy glows).
  - Better readability and spacing.
  - Professional yet neon Tokyo Night (cyan #2de2e6, pink #ff4fa3) without looking AI-generated.
  - Rack bezel effects for the switch faceplate.

### Other UI Polish (from prior work carried into v0.3)
- Blinking network LEDs across the app.
- Dashboard Fleet cards.
- Improved headers and login.

## Capturing Screenshots
To capture real before/after:
1. Run the panel against mock (or real switch).
2. Use browser dev tools or puppeteer to screenshot / and /switch.
3. Add the PNGs here for v0.3-alpha.

**Current screenshots in this folder will be populated after merging the PRs and running a visual pass.**

## Layout of Changes
The v0.3-alpha release focuses on the new dedicated network/switch section while maintaining the Tokyo Night aesthetic.

See main [README.md](../../README.md) and [handoff.md](../../handoff.md) for full release notes.

*These screenshots will document the v0.3-alpha release of the switch integration and UI refinements.*