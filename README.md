# ramiejleh-plugins

A [Claude Code](https://docs.claude.com/en/docs/claude-code) plugin marketplace by
[Rami Ejleh](https://github.com/ramiejleh). Add it once, then install any plugin listed
below from inside Claude Code.

## Plugins

| Plugin | Description |
| --- | --- |
| [**interactive-pr-review**](./interactive-pr-review) | Interactively review GitHub PRs (comments only) — Claude fetches the PR, writes a holistic overview, groups the diff into logical chunks with neutral descriptions and inline insight bubbles, presents them in an IDE-highlighted review UI, and posts your line- and file-level comments back to GitHub in one click. |

## Add the marketplace

```
/plugin marketplace add ramiejleh/claude-plugins
```

## Install a plugin

```
/plugin install interactive-pr-review@ramiejleh-plugins
/reload-plugins
```

> You **add** the marketplace by `owner/repo` (`ramiejleh/claude-plugins`) but **install**
> from its registered *name* (`ramiejleh-plugins`, the `name` field in `marketplace.json`).
> They differ because Claude Code reserves the `claude-` prefix for official marketplaces,
> so the name can't match the repo here.

## Update

To pick up a newer version after one is published:

```
/plugin marketplace update ramiejleh-plugins       # refresh the listing
/plugin install interactive-pr-review@ramiejleh-plugins
```

## Access

This repository is **private**. To install, the GitHub account your `gh` / Claude Code is
authenticated as must have **read** access to it — ask the maintainer to add you as a
collaborator (or via a team).

## Repository structure

```
.
├── .claude-plugin/marketplace.json   # marketplace listing
├── .github/CODEOWNERS                # review owners
├── README.md                         # this file
└── interactive-pr-review/            # a plugin (see its own README)
```

## License

MIT © Rami Ejleh
