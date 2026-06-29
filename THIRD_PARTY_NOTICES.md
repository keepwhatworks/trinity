# Third-Party Notices

Trinity Local itself is released under the MIT License (see [`LICENSE`](LICENSE)).

This file credits the third-party components that Trinity Local bundles or
relies on. Only components that are actually present in this repository or
downloaded at runtime are listed. All of them carry permissive licenses
(MIT / BSD / ISC / Apache-2.0 / HPND).

---

## Embedding model (downloaded at runtime, not bundled)

Trinity's real embedding path uses a single model, pulled once via
`trinity-local download-embedder` and then loaded offline from the local
Hugging Face cache. It is **not** vendored in this repository.

| Component | License | Source |
|---|---|---|
| `nomic-ai/modernbert-embed-base` | Apache-2.0 | https://huggingface.co/nomic-ai/modernbert-embed-base |

---

## Bundled browser JavaScript (`src/trinity_local/data/vendor/`)

These 12 minified files are vendored into the package and published into
`~/.trinity/portal_pages/vendor/` for the local launchpad, memory viewer,
and council review pages (all served over `file://` — no CDN).

| Component | Version | License | Copyright | Source |
|---|---|---|---|---|
| Chart.js | 4.4.3 | MIT | (c) Chart.js Contributors | https://www.chartjs.org |
| marked | 12.0.2 | MIT | (c) 2011-2024 Christopher Jeffrey | https://github.com/markedjs/marked |
| petite-vue | 0.4.1 | MIT | (c) Yuxi (Evan) You | https://github.com/vuejs/petite-vue |
| d3-color | 3.1.0 | ISC | Copyright 2010-2022 Mike Bostock | https://d3js.org/d3-color/ |
| d3-dispatch | 3.0.1 | ISC | Copyright 2010-2021 Mike Bostock | https://d3js.org/d3-dispatch/ |
| d3-drag | 3.0.0 | ISC | Copyright 2010-2021 Mike Bostock | https://d3js.org/d3-drag/ |
| d3-force | 3.0.0 | ISC | Copyright 2010-2021 Mike Bostock | https://d3js.org/d3-force/ |
| d3-interpolate | 3.0.1 | ISC | Copyright 2010-2021 Mike Bostock | https://d3js.org/d3-interpolate/ |
| d3-quadtree | 3.0.1 | ISC | Copyright 2010-2021 Mike Bostock | https://d3js.org/d3-quadtree/ |
| d3-selection | 3.0.0 | ISC | Copyright 2010-2021 Mike Bostock | https://d3js.org/d3-selection/ |
| d3-timer | 3.0.1 | ISC | Copyright 2010-2021 Mike Bostock | https://d3js.org/d3-timer/ |
| d3-zoom | 3.0.0 | ISC | Copyright 2010-2021 Mike Bostock | https://d3js.org/d3-zoom/ |

### MIT License (Chart.js, marked, petite-vue)

```
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### ISC License (d3-* modules, Copyright Mike Bostock)

```
Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.
```

---

## Python dependencies (installed via pip, not vendored)

Declared in [`pyproject.toml`](pyproject.toml). All are permissively licensed.

### Required

| Package | License |
|---|---|
| Pillow | HPND (MIT-style) |
| mcp | MIT |
| numpy | BSD-3-Clause |

### Optional (`[mlx]` extra — real embeddings)

| Package | License |
|---|---|
| mlx (Apple Silicon) | MIT |
| mlx-embeddings (Apple Silicon) | **GPL-3.0** |
| sentence-transformers | Apache-2.0 |
| einops | MIT |
| torch | BSD-3-Clause |

> **`mlx-embeddings` is GPL-3.0** (copyleft). It is an *optional* dependency of
> the `[mlx]` extra — Trinity Local (MIT) never bundles or redistributes it; you
> install it yourself by resolving the `[mlx]` extra from your Trinity clone
> (`pip install -e '.[mlx]'`) on Apple Silicon,
> and Trinity imports it at runtime only if present. If you prefer to avoid GPL
> entirely, skip the `[mlx]` extra: the cross-platform `sentence-transformers` /
> `torch` path (Apache-2.0 / BSD-3-Clause) runs the same `modernbert-embed-base`
> model, just slower than the native Apple-MLX backend.

### Optional (`[test]` extra)

| Package | License |
|---|---|
| pytest | MIT |

Each package's full license text ships inside its own distribution; this
table is a summary, not a substitute for those notices.

---

If you believe a component is credited incorrectly or is missing, please open
an issue at https://github.com/keepwhatworks/trinity/issues.
