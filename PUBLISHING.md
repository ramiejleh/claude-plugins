# Publishing (maintainer only)

These steps are for **you**, the maintainer. They cover getting this marketplace repo
(`claude-plugins`, whose working copy lives in the `local-marketplace/` folder) onto GitHub
so your team can install `interactive-pr-review` from it, and shipping updates afterward. Your team does **not** need this file — they only need the "For your team"
section of [interactive-pr-review/README.md](interactive-pr-review/README.md).

## What gets published

This repo *is* the marketplace. Its layout:

```
local-marketplace/
├── .claude-plugin/marketplace.json     # the marketplace manifest (lists the plugin)
├── PUBLISHING.md                       # this file
└── interactive-pr-review/              # the plugin itself
    ├── .claude-plugin/plugin.json      # plugin manifest (name, version, components)
    ├── README.md
    ├── commands/  skills/  hooks/  CODEOWNERS
```

`marketplace.json` has a `plugins[]` entry pointing at `./interactive-pr-review`. When a
teammate runs `/plugin marketplace add <you>/<repo>`, Claude Code reads that manifest and
offers every listed plugin for install.

## One-time: put the marketplace on GitHub

Run these from the marketplace root (`local-marketplace/`). It is not a git repo yet.

```bash
cd /path/to/local-marketplace

# 1. Create the repo and first commit
git init
git add .
git commit -m "interactive-pr-review 1.9.0"

# 2. Create the GitHub repo and push (gh does both).
#    --public or --private depending on who should reach it.
gh repo create claude-plugins --private --source=. --remote=origin --push
```

The `<owner>/<repo>` (`ramiejleh/claude-plugins`) is what your team will `marketplace add`.

Then tell your team (see the README's "For your team" section):

```
/plugin marketplace add ramiejleh/claude-plugins
/plugin install interactive-pr-review
```

> **Private repo?** Each teammate must have read access to it on the GitHub account their
> `gh`/Claude Code is authenticated with, or `marketplace add` will fail to fetch it.

## Shipping an update

The version lives in **two** files and they must match, or clients can get confused about
what's installed:

- `interactive-pr-review/.claude-plugin/plugin.json` → `version`
- `.claude-plugin/marketplace.json` → `plugins[0].version`

Steps:

1. Make your changes to the plugin.
2. Bump **both** versions to the same new number (semver): patch for fixes, minor for new
   behavior, major for breaking changes.
3. Sanity-check the manifests parse and the versions agree:

   ```bash
   cd /path/to/local-marketplace
   python3 - <<'PY'
   import json
   p = json.load(open("interactive-pr-review/.claude-plugin/plugin.json"))["version"]
   m = json.load(open(".claude-plugin/marketplace.json"))["plugins"][0]["version"]
   print("plugin.json:", p, "| marketplace.json:", m,
         "| MATCH" if p == m else "| *** MISMATCH — fix before pushing ***")
   PY
   ```

   Optionally also run the builder's validator: `/plugin-builder:validate`.
4. Commit and push:

   ```bash
   git add .
   git commit -m "interactive-pr-review <new-version>"
   git push
   ```

That's it — publishing is just a push. Teammates pick it up with:

```
/plugin marketplace update ramiejleh-plugins
/plugin install interactive-pr-review
```

(`ramiejleh-plugins` is the marketplace `name` in `marketplace.json` — the identifier
`marketplace update` takes, distinct from the `owner/repo` that `marketplace add` takes.)

## Notes

- **Two identifiers, don't mix them up.** You **add** by `owner/repo`
  (`ramiejleh/claude-plugins`); you **update** by the marketplace `name` from
  `marketplace.json` (`ramiejleh-plugins`). They deliberately differ: Claude Code rejects any
  marketplace `name` starting with `claude-` (reserved for official Anthropic marketplaces),
  so the name can't just mirror the repo. (The on-disk folder is still `local-marketplace/`;
  that's just where the working copy lives and doesn't affect any command.)
- **`CODEOWNERS`** currently lists `@claude-market @ramiejleh`. `@claude-market` is a
  leftover from the plugin scaffold and only matters if you ever submit to that public
  marketplace. For a team-internal repo you can drop it and list your own team, or leave it
  — it has no effect on installation.
- **Tagging releases is optional.** `/plugin` installs from the default branch's current
  state, so a plain `git push` is enough. Git tags (`git tag v1.9.0 && git push --tags`)
  are only useful if you want human-readable release markers.
