# Maintaining the website

The existing Vercel deployment serves `index.html` and `vercel.json` from the repository root. Keep that entry point when updating this project.

The `vercel-site` directory contains a standalone copy of the site. Keep its page and linked documents in sync with the root version.

Section 07 links to `PROJECT.md`, which contains the same overview as the repository README. This separate filename is used because the live site's `README.md` URL returned 404.

When updating the overview, update these four files together:

- `README.md`
- `PROJECT.md`
- `vercel-site/README.md`
- `vercel-site/PROJECT.md`

Guides linked from the overview also need a copy under `vercel-site/docs`.

The evaluation runs separately from the static website. Keep API keys and local experiment logs out of website files. After deployment, check the homepage and each document linked in section 07.
