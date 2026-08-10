# DefyAtrophy

A [Claude Code](https://docs.claude.com/en/docs/claude-code) plugin marketplace by
[Rami Ejleh](https://github.com/ramiejleh). Add it once, then install any plugin listed
below from inside Claude Code.

## Plugins

| Plugin | Description |
| --- | --- |
| [**interactive-pr-review**](./interactive-pr-review) | Interactively review GitHub PRs (comments only) — Claude fetches the PR, writes a holistic overview, groups the diff into logical chunks with neutral descriptions and inline insight bubbles, presents them in an IDE-highlighted review UI, and posts your line- and file-level comments back to GitHub in one click. |
| [**review-gate**](./review-gate) | Stops Claude writing once it has generated more code than you have read. A hook counts the lines and, at your chosen threshold, plants a marker comment in your source that only you can delete — Claude's editing tools and shell are blocked while it is armed — then asks you a question about the changes before writing resumes. |

## Add the marketplace

```
/plugin marketplace add ramiejleh/DefyAtrophy
```

## Install a plugin

```
/plugin install interactive-pr-review@DefyAtrophy
/reload-plugins
```

> You **add** the marketplace by `owner/repo` and **install** from its registered
> *name* — the `name` field in `marketplace.json`. Here both are `DefyAtrophy`, so
> the two commands read the same either way.

## Update

To pick up a newer version after one is published:

```
/plugin marketplace update DefyAtrophy       # refresh the listing
/plugin install interactive-pr-review@DefyAtrophy
```

## Repository structure

```
.
├── .claude-plugin/marketplace.json   # marketplace listing
├── .github/CODEOWNERS                # review owners
├── README.md                         # this file
├── interactive-pr-review/            # a plugin (see its own README)
└── review-gate/                      # a plugin (see its own README)
```

## License

MIT © Rami Ejleh
