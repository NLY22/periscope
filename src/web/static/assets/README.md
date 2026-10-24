# Periscope visual assets

## Active waterlight workspace

`panel.css` holds shared layout and the local Noto Sans SC variable font. `gallery.css` composes the silver-blue background, centered Periscope wordmark, question input and white reading surfaces. Georgia is used only for the Latin brand. Colors are porcelain `#f7fafc`, graphite `#24333e`, blue slate `#315d76` and white. The text scale is metadata13, helper14, labels15, excerpts16 and reading/input17. Desktop and mobile share the same hierarchy. Source, report, import and claim interfaces keep their existing backend behavior.

`waterlight.js` is a dependency-free WebGL background. A shallow continuous height field reflects broad silver-blue highlights, with a luminous center behind the wordmark and a gradual fade toward porcelain. Pointer movement gently changes the viewpoint; it does not create detached objects. The canvas ignores pointer events, is hidden from assistive technology and caps its backing buffer at700,000 pixels /1 device scale, with at most24 rendered frames per second. A native toolbar button pauses the background. Hidden tabs, an offscreen hero and reduced motion stop animation. Missing graphics support or context loss reveals a static CSS fallback. Reports and library views dim the background; reading panels use opaque white surfaces. Active asset URLs use content hashes.

`archive-scene.js` is retained from an earlier experiment and is not loaded by the interface. The rotating archive and its controls are no longer active.

The detail pass strengthens the moving reflections while retaining a stable luminous center. Navigation now gives history titles two lines; repeated visible page headings are consolidated, with the main heading retained for assistive technology. Context headings, excerpts, metadata, controls and reading copy use explicit type roles. Mobile suggestions form separate rows and source links have larger touch targets. The home-only motion control, compact import/help layouts and flat source lists share the same palette. Search exposes a clear action and a busy state; labels are bound to the submitted query, and clearing the query invalidates pending results.

The quieter composition removes the home slogan and explanatory chrome, replaces long prompt labels with three short topic chips, and presents recent source titles with platform/date. Full excerpts and original-source links remain in the library and report views. Statistics no longer occupy a visual row, and keyboard hints appear on composer focus. Navigation, import and source labels are shorter. The existing waterlight and source/research behavior are preserved.

The following bitmap assets are retained from earlier visual exploration; they are not requested by the current interface.

## Daylight variant

`optical-lens-daylight.png` was an earlier illustration for the bright glass interface. It was edited from the original `optical-lens-v2.png` with the built-in imagegen tool on 2026-10-22. The original remains as a separate asset.

Final edit prompt:

> Edit this Periscope brand illustration into an exquisite LIGHT THEME product image. Preserve the single layered transparent optical lens, its tilted three-quarter view and placement on the RIGHT half. Replace all dark navy backgrounds and dark water with a luminous pearl-white / very pale ice-blue studio background, a softly reflective frosted glass floor and delicate natural light caustics. The left half must be clean quiet near-white #f4f7ff negative space for real UI text. Keep the lens optically convincing: polished transparent crystal, fine bright silver bevels, rich cobalt and icy cyan refractions, restrained touches of lilac dispersion. Clear, sharply rendered physical materials. Reflections are bright and airy, never black, navy or grey mud. Premium contemporary industrial design, editorial studio render, high-key soft daylight, refined and beautifully dimensional. Do not add objects, text, logos, UI or watermarks. Composition remains landscape 3:2, dramatic glass lens right, clean light negative space left. Aim for elegant precision and carefully controlled colored highlights, not neon outlines or a rainbow toy.

## Original variant

`optical-lens-v2.png` is an original brand illustration for the local research panel. Generated with the built-in imagegen tool on 2026-10-22; copied into this package so the interface has no dependency on the generation directory or a remote image host.

Final generation prompt:

> Use case: stylized-concept. Asset type: a high-end 3D brand illustration used as the full-bleed hero background of Periscope, a personal deep information research application. Create a cinematic wide landscape image, approximately 3:2. On the RIGHT HALF, one large sculptural optical lens formed from several closely layered, thick, transparent polished glass rings, tilted in three-quarter perspective, levitating just above a dark reflective ocean-blue surface. Physically convincing crystal glass, tiny sharp reflections, smooth silver chamfered edge accents, rich cyan and icy lilac dispersion, precise refractions, luminous watery caustics underneath. A small sharp pool of cobalt light, with elegant optical arcs. Premium tactile product photography rendered in 3D, sophisticated and striking, rich depth, not a cartoon. The LEFT HALF is quiet, near-uniform very deep midnight indigo #0b1233, plenty of clear negative space where the real webpage will overlay Chinese headline text. The lower quarter must stay dark and calm for a text-input card overlay. Right side can be vivid periwinkle blue and pale cyan; keep bright highlights within the optical object. Subject large, beautifully composed, occupying right 55 percent of frame. Background gently falls from black indigo on left to deep sapphire right. No text, no letters, no symbols, no user interface, no logos, no watermark, no planets, no wireframe spheres, no space stars. This is an illustration asset, not a UI mockup.
