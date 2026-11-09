# Third-party software

The Markdown editors are built from the exact npm dependency tree in
`frontend/package-lock.json` and shipped from `static/lib/` without a runtime CDN.
Rebuild with `cd frontend && npm ci && npm run build`.

| Component | Pinned direct version | License |
| --- | --- | --- |
| Milkdown Crepe | 7.22.2 | MIT |
| CodeMirror `codemirror` | 6.0.2 | MIT |
| CodeMirror Markdown language | 6.5.2 | MIT |
| esbuild (build only) | 0.28.2 | MIT |

The lockfile also includes MIT, BSD-2-Clause, BSD-3-Clause and ISC dependencies,
and DOMPurify 3.4.16 under `(MPL-2.0 OR Apache-2.0)`. The distributed bundle's
retained notices are in `static/lib/markdown_editors.js.LEGAL.txt`; package
sources and individual license files are available from the package versions
specified in the lockfile. The module's own license is LGPL-3.
