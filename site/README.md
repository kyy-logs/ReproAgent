# ReproAgent product page

A static, light, developer-facing product page. No build step and no external
requests.

Preview:

```powershell
# Option 1: open the file directly
start site\index.html

# Option 2: local server
python -m http.server 8000 --directory site
# open http://localhost:8000/
```

To publish it on GitHub Pages, enable **Settings → Pages → Deploy from a
branch**, choose `main` and the `/site` folder. The page is then served at
<https://kyy-logs.github.io/ReproAgent/>.

Files:

- `index.html` — current evidence-desk page: cool paper, cobalt section rails, ruled evidence blocks, Newsreader + Noto Serif SC headlines, framed Mac-terminal hero with streaming log, report picture-in-picture with pointer-driven tilt, parallax and a scripted macOS cursor drag, Windows/macOS install tabs, macOS install section
- `aider-style.html` — legacy redirect to `index.html`
- `brand-mark.svg` — serif R monogram used by the nav, footer and favicon
- `aider-style.css` — page styles
- `fonts/` — local IBM Plex Sans/Mono, Newsreader and subsetted Noto Serif SC WOFF2 files, with OFL licenses
- `DESIGN.md` — design plan and decisions
- `app.js` — copy buttons, pipeline reveal, platform tabs, the looping terminal demo, pointer tilt and scripted cursor

The page is Chinese-first with English technical terms. Copy and commands target
the current `main` branch. It deliberately avoids dark mode, generic AI gradients,
fake benchmarks, and claims that `DONE` means a verified reproduction.
